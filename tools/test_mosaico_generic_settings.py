"""Offline generic settings lease acceptance; no build or device operations."""
from pathlib import Path
import json, re, subprocess, tempfile, unittest
ROOT=Path(__file__).resolve().parents[1]
COMPILER=Path('C:/Espressif/tools/riscv32-esp-elf/esp-15.2.0_20251204/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe')
class GenericSettingsTests(unittest.TestCase):
    def test_actual_cpp_patch_lease_and_base(self):
        code=r'''
#include "main/host/mosaico_display_settings_model.h"
#include "main/debug/serial_debug_transport.h"
#include <initializer_list>
using namespace MosaicoDisplay;
constexpr bool test() {
 Config c; const Config base=c;
 for (const char* field : {"charge_timeout","battery_timeout","charge_brightness","battery_brightness","lock_brightness","burn_in","lock_wifi_minutes","lock_ble_minutes"}) {
  for (int64_t bad : {-1LL,601LL,4294967296LL,9223372036854775807LL}) {
   if(patch(c,field,bad) || !(c==base)) return false;
  }
 }
 if(patch(c,"password",1)||patch(c,nullptr,1)||patch(c,"charge_brightness",9)||patch(c,"burn_in",2)||
    patch(c,"battery_timeout",0)||patch(c,"battery_timeout",120)||patch(c,"lock_wifi_minutes",3)||!(c==base))return false;
 if(!patch(c,"charge_timeout",0)||!patch(c,"burn_in",0)||!patch(c,"lock_brightness",0)||c.burnIn||c.lockBrightness||c.chargeTimeoutSeconds)return false;
 for(int64_t v : {0LL,15LL,30LL,60LL,120LL,300LL,600LL}) if(!patch(c,"charge_timeout",v))return false;
 for(int64_t v : {15LL,30LL,45LL,60LL}) if(!patch(c,"battery_timeout",v))return false;
 for(int64_t v : {1LL,2LL,5LL,10LL,15LL,30LL,60LL}) if(!patch(c,"lock_wifi_minutes",v)||!patch(c,"lock_ble_minutes",v))return false;
 Model m; const Blob original=encode(m.state.config);
 if(m.setTemporary("burn_in",0,29,0)||m.setTemporary("burn_in",0,601,0))return false;
 if(!m.setTemporary("burn_in",0,180,100)||m.state.pending||m.state.revision!=1||m.effective().burnIn||!(m.state.config==base))return false;
 const auto rev=m.runtimeRevision; const auto until=m.leaseUntil;
 if(m.setTemporary("burn_in",2,180,100)||m.runtimeRevision!=rev||m.leaseUntil!=until)return false;
 if(!m.setTemporary("lock_brightness",0,30,200)||m.effective().burnIn||m.effective().lockBrightness||m.state.pending)return false;
 auto s=m.snapshot(201); if(!s.temporary||s.remainingLeaseSeconds!=30||!(s.config==base)||s.effectiveConfig.burnIn)return false;
 if(m.saveTemporary(false,1000)||m.leaseUntil==0||m.state.pending)return false;
 if(m.expire(30000199)||!m.expire(30000200)||m.leaseUntil||!(m.effective()==base)||m.runtimeRevision<=rev)return false;
 for(unsigned i=0;i<original.size();++i) if(encode(m.state.config)[i]!=original[i])return false;
 // Explicit save coalesces the overlay into the GUI base revision, then owner saves base.
 if(!m.setTemporary("burn_in",0,600,40000000)||!m.saveTemporary(true,41000000)||m.leaseUntil||m.state.config.burnIn||!m.state.pending||m.state.revision!=2)return false;
 if(!m.due(42000000))return false;
 auto saved=encode(m.state.config); Config decoded; if(!decode(saved,decoded)||decoded.burnIn)return false;
 m.completed(2,0); if(m.state.pending||m.state.savedRevision!=2)return false;
 // GUI edits cancel lease, including a no-op base edit, without new persistence work.
 if(!m.setTemporary("burn_in",1,30,43000000))return false;
 const auto guiRev=m.state.revision;m.request(m.state.config,44000000);
 if(m.leaseUntil||m.state.revision!=guiRev||m.state.pending||m.effective().burnIn)return false;
 if(!m.setTemporary("burn_in",1,30,45000000)||!m.expire(45000001,true)||m.effective().burnIn)return false;
 // Pending base write remains the base even if a new temporary overlay is active.
 Config next=m.state.config;next.lockBrightness=20;m.request(next,46000000);
 if(!m.setTemporary("lock_brightness",0,30,46000001)||m.state.config.lockBrightness!=20||m.effective().lockBrightness!=0||!m.due(47000000))return false;
 // GUI promotes exactly its selected visible field, not the other temporary field.
 Model gui;
 if(!gui.setTemporary("burn_in",0,30,0)||!gui.setTemporary("lock_brightness",0,30,1))return false;
 if(!gui.requestPersistentField("lock_brightness",5,2)||gui.leaseUntil||!gui.state.config.burnIn||gui.state.config.lockBrightness!=5||!gui.state.pending)return false;
 const auto guiRevision=gui.state.revision;
 if(gui.requestPersistentField("burn_in",2,3)||gui.state.revision!=guiRevision)return false;
 uint32_t value=99;
 for(const char* bad : {"","-1","+1","1x","4294967296","6000","601"}) if(serial_debug_transport::settingsInteger(bad,600,value)||value!=99)return false;
 if(!serial_debug_transport::settingsInteger("0",600,value)||value)return false;
 return true;
}
static_assert(test(),"strict patches, monotonic leases, explicit save and base preservation");
'''
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/'settings.cpp'; source.write_text(code)
            r=subprocess.run([str(COMPILER),'-std=c++17','-fsyntax-only','-I'+str(ROOT),str(source)],capture_output=True,text=True)
            log=ROOT/'.artifacts/mosaico/generic-settings-model.log';log.write_text(r.stdout+r.stderr)
            self.assertEqual(r.returncode,0,str(log))
    def test_runtime_ram_safety_and_base_save_source(self):
        s=(ROOT/'main/host/mosaico_display_settings.cpp').read_text()
        self.assertIn('config = model.state.config; revision = model.state.revision;',s)
        for name,end in [('bool snapshot(Snapshot& out)','Snapshot snapshot()'),('Snapshot snapshot()','bool request'),('bool setTemporary','bool restoreTemporary'),('bool restoreTemporary','bool saveTemporary'),('bool saveTemporary','void service')]:
            body=s.split(name,1)[1].split(end,1)[0]
            self.assertNotIn('nvs_',body);self.assertNotIn('GetHAL()',body)
        self.assertIn('expireLocked(esp_timer_get_time())',s)
        self.assertIn('MosaicoOta::busy() || MosaicoOta::healthPending()',s)
        self.assertIn('if (model.expire(now,unsafeRuntime())) notifyOwner();',s)
        serial=(ROOT/'main/debug/serial_debug.cpp').read_text().split('std::strcmp(command, "display-settings")',1)[0]
        self.assertIn('!std::strcmp(confirm,"CONFIRM")',serial)
        transport=(ROOT/'main/debug/serial_debug_transport.h').read_text().split('inline bool uartAllowed',1)[1]
        self.assertIn('"settings"',transport);self.assertNotIn('"network-config"',transport)
        self.assertIn(r'!::strtok_r(nullptr, " \t", &save)',serial)
    def test_configured_s31_s3_syntax(self):
        logs=[]
        for db in (ROOT/'.artifacts/mosaico/ota-staged-heapdiag-build/compile_commands.json',ROOT/'build/compile_commands.json'):
            if not db.exists():self.skipTest('configured sources unavailable')
            entries=json.loads(db.read_text())
            for name in ('serial_debug.cpp','view_mosaico.cpp','mosaico_display_settings.cpp'):
                # S3 has no Mosaico view/backend; use its include context with MOSAICO_BOARD explicitly for the backend.
                lookup='network_quota.cpp' if name=='mosaico_display_settings.cpp' else name
                entry=next((e for e in entries if e['file'].replace('\\','/').endswith('/'+lookup)),None)
                if entry is None:continue
                command=re.sub(r' -o \S+ -c ', ' -fsyntax-only ',entry['command'])
                if name=='mosaico_display_settings.cpp': command=command.replace(entry['file'],str(ROOT/'main/host'/name))
                r=subprocess.run(command,cwd=entry['directory'],capture_output=True,text=True)
                logs.append(str(db)+' '+name+'\n'+r.stdout+r.stderr)
                log=ROOT/'.artifacts/mosaico/generic-settings-syntax.log';log.write_text('\n'.join(logs))
                self.assertEqual(r.returncode,0,str(log))
if __name__=='__main__':unittest.main()
