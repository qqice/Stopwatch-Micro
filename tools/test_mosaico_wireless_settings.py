"""Wireless codec, bounded deadlines and credential policy; no device operations."""
from pathlib import Path
import json, re, subprocess, tempfile, unittest
ROOT=Path(__file__).resolve().parents[1]
COMPILER=Path('C:/Espressif/tools/riscv32-esp-elf/esp-15.2.0_20251204/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe')
class WirelessSettingsTests(unittest.TestCase):
    def test_policy(self):
        code=r'''
#include "main/host/mosaico_display_settings_model.h"
#include "main/host/mosaico_session_model.h"
#include "main/host/mosaico_wifi_settings_model.h"
using namespace MosaicoDisplay;
constexpr bool test() {
 Config c,out; if(c.lockWifiMinutes!=5 || c.lockBleMinutes!=1) return false;
 Model display; if(!rebootSaveReady(display.state)) return false;
 auto changed=display.state.config; changed.lockWifiMinutes=2;
 display.request(changed,100);
 if(rebootSaveReady(display.state) || display.due(1000099)) return false;
 if(!display.due(1000100)) return false;
 display.completed(display.state.revision,-7);
 if(rebootSaveReady(display.state)) return false;
 display.request(changed,2000000);
 if(rebootSaveReady(display.state)) return false;
 display.completed(display.state.revision,0);
 if(!rebootSaveReady(display.state)) return false;
 Blob old=encode(c); old[4]=1; old[9]=old[10]=0; put32(old,20,crc(old));
 if(!decode(old,out) || !(out==c)) return false;
 c.lockWifiMinutes=60; c.lockBleMinutes=30;
 if(!decode(encode(c),out) || !(out==c)) return false;
 c.lockWifiMinutes=0; c.lockBleMinutes=255; c=sanitize(c);
 if(c.lockWifiMinutes!=5 || c.lockBleMinutes!=1 || lockIntervalMs(0xffffffffU,5)!=300000 || lockIntervalMs(60,1)!=3600000) return false;
 Blob bad=encode(c); bad[9]=0; put32(bad,20,crc(bad)); if(decode(bad,out)) return false;
 MosaicoSessions::LockedWindow w; w.intervalMs=120000; std::array<uint32_t,6> receipts{};
 if(!w.start(0xfffffff0U,0,receipts)) return false; w.finish(0x1f30U,true);
 if(w.start(0x1d4afU,0,receipts) || !w.start(0x1d4b0U,0,receipts)) return false;
 auto wifi=MosaicoWifi::encode("network","12345678");
 if(!MosaicoWifi::validBlob(wifi)) return false;
 for(unsigned i=0;i<wifi.size();++i) { auto corrupt=wifi; corrupt[i]^=1; if(MosaicoWifi::validBlob(corrupt)) return false; }
 // A torn/new value with old CRC is rejected as a whole pair.
 auto torn=wifi; torn[4]='X'; if(MosaicoWifi::validBlob(torn)) return false;
 using MosaicoWifi::validCredentials;
 return validCredentials("network","") && validCredentials("network","12345678") &&
 validCredentials("\xe4\xb8\xad", "12345678") && !validCredentials("\xc0\xaf", "12345678") &&
 !validCredentials("bad\n", "12345678") && !validCredentials("", "12345678") &&
 !validCredentials("123456789012345678901234567890123", "12345678") &&
 !validCredentials("network", "1234567") && !validCredentials("network", "1234567\x7f") &&
 validCredentials("network","0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef") &&
 !validCredentials("network","g123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef");
}
static_assert(test());
'''
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'test.cpp'; p.write_text(code)
            r=subprocess.run([str(COMPILER),'-std=c++17','-fsyntax-only','-I'+str(ROOT),str(p)],capture_output=True,text=True)
            log=ROOT/'.artifacts/mosaico/wireless-policy.log'; log.write_text(r.stdout+r.stderr)
            self.assertEqual(r.returncode,0,str(log))
    def test_ram_queue_and_owner_safety(self):
        s=(ROOT/'main/host/network_quota.cpp').read_text()
        queue=s.split('bool NetworkQuota::requestWifiCredentials',1)[1].split('void NetworkQuota::serviceWifiSettings',1)[0]
        self.assertNotIn('nvs_',queue); self.assertNotIn('esp_restart',queue)
        self.assertIn('!_wifi_settings.available',queue)
        self.assertIn('_wifi_settings.pending || _wifi_settings.restartPending',queue)
        owner=s.split('void NetworkQuota::serviceWifiSettings',1)[1].split('void NetworkQuota::setLowClockDiagnostic',1)[0]
        for gate in ['healthPending()', 'copyUiSnapshot', 'imageVerified', 'ReadyReboot', 'BootChecking', 'ESP_OTA_IMG_VALID']:
            self.assertIn(gate,owner)
        self.assertEqual(owner.count('nvs_set_blob'),1)
        self.assertNotIn('nvs_set_str',owner)
        self.assertNotIn('"url"',owner); self.assertNotIn('"token"',owner)
        self.assertIn('MosaicoWifi::scrub(password',owner)
        self.assertIn('MosaicoWifi::scrub(_pending_password',owner)
        self.assertIn('MosaicoDisplay::rebootSaveReady(display)',owner)
        self.assertIn('_wifi_settings.restartPending=false',owner)
        self.assertIn('!updateWindow &&',s)
        self.assertIn('refreshIntervalUs*3/4',s)
    def test_configured_syntax(self):
        db=ROOT/'.artifacts/mosaico/ota-build/compile_commands.json'
        if not db.exists(): self.skipTest('no configured target')
        entries=json.loads(db.read_text()); logs=[]
        for name in ['network_quota.cpp','mosaico_session_monitor.cpp']:
            entry=next((e for e in entries if Path(e['file']).name==name),None)
            if entry is None:
                entry=next(e for e in entries if Path(e['file']).name=='network_quota.cpp')
                entry=dict(entry,command=entry['command'].replace(entry['file'],str(ROOT/'main/host'/name)))
            command=re.sub(r' -o \S+ -c ', ' -fsyntax-only ',entry['command'])
            r=subprocess.run(command,cwd=entry['directory'],capture_output=True,text=True)
            logs.append(name+'\n'+r.stdout+r.stderr)
            log=ROOT/'.artifacts/mosaico/wireless-syntax.log'; log.write_text('\n'.join(logs))
            self.assertEqual(r.returncode,0,str(log))
if __name__=='__main__': unittest.main()
