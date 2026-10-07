"""Pure C++ codec/state assertions and bounded configured-source syntax checks."""
from pathlib import Path
import json
import re
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPILER = Path('C:/Espressif/tools/riscv32-esp-elf/esp-15.2.0_20251204/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe')
BACKEND = (ROOT/'main/host/mosaico_display_settings.cpp').read_text()
NETWORK = (ROOT/'main/host/network_quota.cpp').read_text()

class DisplaySettingsTests(unittest.TestCase):
    def test_actual_cpp_codec_and_save_state(self):
        code = r'''
#include "main/host/mosaico_display_settings_model.h"
#include <initializer_list>
using namespace MosaicoDisplay;
constexpr bool test() {
 Config defaults;
 if(defaults.chargeTimeoutSeconds!=60 || defaults.batteryTimeoutSeconds!=60 ||
    defaults.chargeBrightness!=80 || defaults.batteryBrightness!=80 || defaults.lockBrightness!=8 || !defaults.burnIn) return false;
 for(uint32_t charge : {0U,15U,30U,60U,120U,300U,600U}) {
  for(uint32_t battery : {15U,30U,45U,60U}) {
   Config c{charge,battery,10,100,0,false}; Config out;
   if(!(sanitize(c)==c)||!decode(encode(c),out)||!(out==c)) return false;
  }
 }
 Config c{601,0,0,255,255,true}; c=sanitize(c);
 if(c.chargeTimeoutSeconds!=60 || c.batteryTimeoutSeconds!=60 || c.chargeBrightness!=10 || c.batteryBrightness!=100 || c.lockBrightness!=100) return false;
 for(uint32_t battery : {61U,120U,600U,0xffffffffU}) { c.batteryTimeoutSeconds=battery; if(sanitize(c).batteryTimeoutSeconds!=60) return false; }
 const Blob valid=encode(defaults);
 for(unsigned i=0;i<valid.size();++i) { Blob corrupt=valid; corrupt[i]^=1; Config out{15,15,20,20,3,false}; const Config before=out;
  if(decode(corrupt,out)||!(out==before)) return false;
 }
 Blob bad=valid; bad[4]=3; put32(bad,20,crc(bad)); Config out;
 if(decode(bad,out)) return false;
 bad=valid; bad[5]=2; put32(bad,20,crc(bad)); if(decode(bad,out)) return false;
 bad=valid; put32(bad,16,120); put32(bad,20,crc(bad)); if(decode(bad,out)) return false;
 Model m; if(m.due(2000000)||m.state.pending||m.state.savedRevision) return false;
 c=defaults; c.chargeBrightness=90; m.request(c,100);
 if(m.state.revision!=2 || !m.state.pending || m.due(1000099)||!m.due(1000100)) return false;
 m.attemptedRevision=2;
 if(m.due(2000000)) return false;
 c.batteryBrightness=91; m.request(c,2000000); m.completed(2,0);
 if(m.state.savedRevision!=2 || m.state.revision!=3 || !m.state.pending || !(m.state.config==c) || m.due(2999999)) return false;
 if(!m.due(3000000)) return false;
 m.attemptedRevision=3; m.completed(3,-7);
 if(m.state.pending || m.state.savedRevision!=2 || m.state.error!=-7 || !(m.state.config==c) || m.due(9000000)) return false;
 // A repeated explicit request is a retry event; no autonomous retry loop.
 m.request(c,10000000); if(m.state.revision!=4 || !m.state.pending || m.state.error || !m.due(11000000)) return false;
 m.attemptedRevision=4; m.completed(4,0);
 if(m.state.pending||m.state.savedRevision!=4||m.state.error) return false;
 m.request(c,12000000); if(m.state.pending||m.state.revision!=4) return false;
 c.lockBrightness=9; m.request(c,13000000); c.lockBrightness=10; m.request(c,13500000);
 return !m.due(14499999) && m.due(14500000) && m.state.config.lockBrightness==10;
}
static_assert(test());
'''
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/'settings.cpp'; source.write_text(code)
            result=subprocess.run([str(COMPILER),'-std=c++17','-fsyntax-only','-I'+str(ROOT),str(source)],capture_output=True,text=True)
            log=ROOT/'.artifacts/mosaico/display-settings-constexpr.log'; log.parent.mkdir(parents=True,exist_ok=True)
            log.write_text(result.stdout+result.stderr)
            self.assertEqual(result.returncode,0,str(log))

    def test_callbacks_are_ram_only_and_cache_preserving(self):
        snapshot=BACKEND.split('bool snapshot(Snapshot& out)',1)[1].split('Snapshot snapshot()',1)[0]
        request=BACKEND.split('bool request(Config config)',1)[1].split('void service()',1)[0]
        for callback in (snapshot,request):
            self.assertIn('std::try_to_lock',callback)
            for forbidden in ('nvs_', 'GetHAL(', 'MosaicoOta::', 'vTaskDelay'):
                self.assertNotIn(forbidden,callback)
        self.assertLess(snapshot.index('return false'),snapshot.index('out = model.snapshot(now)'))

    def test_load_never_writes_or_erases(self):
        init=BACKEND.split('void init()',1)[1].split('bool snapshot(',1)[0]
        self.assertIn('NVS_READONLY',init)
        self.assertIn('ESP_ERR_NVS_NOT_FOUND ? ESP_OK',init)
        self.assertIn('size != blob.size() || !decode(blob, config)',init)
        for forbidden in ('nvs_set_', 'nvs_commit', 'nvs_erase'):
            self.assertNotIn(forbidden,init)

    def test_save_one_blob_one_commit_and_ota_defer(self):
        service=BACKEND.split('void service()',1)[1].split('bool claimNetworkOwner()',1)[0]
        self.assertIn('MosaicoOta::busy() || MosaicoOta::healthPending()',service)
        self.assertIn('ota.imageVerified && ota.stage != MosaicoOta::UiStage::Complete',service)
        self.assertIn('state != ESP_OTA_IMG_VALID',service)
        self.assertIn('model.attemptedRevision = revision;',service)
        self.assertEqual(service.count('nvs_set_blob'),1)
        self.assertEqual(service.count('nvs_commit'),1)
        self.assertIn('model.completed(revision, error)',service)
        self.assertNotIn('nvs_erase',BACKEND)

    def test_owners_and_startup_order(self):
        self.assertEqual(BACKEND.count('owner.compare_exchange_strong'),1)
        self.assertIn('pdMS_TO_TICKS(250)',BACKEND)
        self.assertIn('"display_save", 4096, nullptr, 1',BACKEND)
        self.assertIn('const bool ownsDisplaySettings = MosaicoDisplay::claimNetworkOwner();',NETWORK)
        self.assertLess(NETWORK.index('if (ownsDisplaySettings) MosaicoDisplay::service();'),NETWORK.index('MosaicoOta::processLocalRequests();'))
        self.assertEqual(NETWORK.count('MosaicoDisplay::startFallbackOwner();'),7)
        main=(ROOT/'main/main.cpp').read_text()
        self.assertLess(main.index('GetHAL().init();'),main.index('MosaicoDisplay::init();'))
        self.assertLess(main.index('MosaicoDisplay::init();'),main.index('GetNetworkQuota().begin();'))

    def test_writer_lock_handoff_source_and_scheduled_race(self):
        service=BACKEND.split('void service()',1)[1].split('bool claimNetworkOwner()',1)[0]
        claim=BACKEND.split('bool claimNetworkOwner()',1)[1].split('void startFallbackOwner()',1)[0]
        self.assertLess(service.index('writer(writerMutex'),service.index('ownerTask.load() != xTaskGetCurrentTaskHandle()'))
        self.assertLess(service.index('ownerTask.load() != xTaskGetCurrentTaskHandle()'),service.index('MosaicoOta::busy()'))
        self.assertIn('std::lock_guard<std::mutex> writer(writerMutex);',claim)
        self.assertLess(claim.index('writer(writerMutex)'),claim.index('owner.store(Owner::Network)'))
        self.assertLess(claim.index('owner.store(Owner::Network)'),claim.index('ownerTask.store('))
        fallback=BACKEND.split('void startFallbackOwner()',1)[1]
        self.assertIn('if (owner.load() == Owner::Fallback) ownerTask.store(self);',fallback)
        self.assertIn('ulTaskNotifyTake(pdTRUE, portMAX_DELAY); continue;',fallback)
        self.assertNotIn('vTaskDelete',fallback)
        # Controlled schedule mirrors the actual source lock/owner protocol:
        # save holds writer, network claim waits, old fallback is rejected after.
        writer=threading.Lock()
        owner=['fallback']
        entered=threading.Event(); release=threading.Event(); claimed=threading.Event()
        commits=[]
        def save(task, block=False):
            with writer:
                if owner[0]!=task: return
                if block:
                    entered.set()
                    if not release.wait(3): return
                commits.append(task)
        def claim():
            with writer:
                owner[0]='network'
                claimed.set()
        old=threading.Thread(target=save,args=('fallback',True)); old.start()
        self.assertTrue(entered.wait(3))
        new=threading.Thread(target=claim); new.start()
        self.assertFalse(claimed.wait(.03))
        release.set(); old.join(3); new.join(3)
        self.assertFalse(old.is_alive()); self.assertFalse(new.is_alive())
        self.assertTrue(claimed.is_set())
        save('fallback'); save('network')
        self.assertEqual(commits,['fallback','network'])
        # Delayed fallback startup cannot overwrite a completed handoff.
        with writer:
            if owner[0]=='fallback': owner[0]='fallback-task'
        self.assertEqual(owner[0],'network')

    def test_configured_backend_syntax(self):
        db=ROOT/'.artifacts/mosaico/ota-build/compile_commands.json'
        if not db.exists(): self.skipTest('no configured target')
        entries=json.loads(db.read_text()); logs=[]
        for filename in ('network_quota.cpp','serial_debug.cpp','main.cpp','mosaico_display_settings.cpp'):
            lookup='network_quota.cpp' if filename=='mosaico_display_settings.cpp' else filename
            entry=next(e for e in entries if e['file'].replace('\\','/').endswith('/'+lookup))
            command=re.sub(r' -o \S+ -c ', ' -fsyntax-only ',entry['command'])
            if filename=='mosaico_display_settings.cpp':
                command=command.replace(entry['file'],str(ROOT/'main/host/mosaico_display_settings.cpp'))
            result=subprocess.run(command,cwd=entry['directory'],capture_output=True,text=True)
            logs.append(filename+'\n'+result.stdout+result.stderr)
            log=ROOT/'.artifacts/mosaico/display-settings-syntax.log'; log.write_text('\n'.join(logs))
            self.assertEqual(result.returncode,0,str(log))

if __name__=='__main__': unittest.main()
