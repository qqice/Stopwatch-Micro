"""Pure bounded profiles and owner integration, no hardware or NVS traffic."""
from pathlib import Path
import json,shlex,subprocess,tempfile,unittest
R=Path(__file__).resolve().parents[1]
C=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
class ProfilesTests(unittest.TestCase):
 def test_model(self):
  code=r'''#include "main/host/mosaico_wifi_profiles_model.h"
#include "main/host/wifi_retry_model.h"
using namespace MosaicoWifiProfiles;
constexpr bool test(){
 Model m; if(!m.upsert("one","12345678")||!m.upsert("one","")||m.count!=1||m.entries[0].password[0]!='1'||m.forget("one"))return false;
 if(!m.upsert("two","")||!m.upsert("three","abcdefgh")||!m.upsert("four","")||!m.upsert("five","")||!m.upsert("six","")||m.upsert("seven","")||m.count!=6)return false;
 if(m.upsert("bad\n","")||m.upsert("bad\xc0\x80","")||m.upsert("x","short"))return false;
 auto b=encode(m);Model d;if(!decodeModel(b,d)||d.count!=6)return false;
 b[42]^=1;if(decodeModel(b,d))return false;
 b=encode(m);b[3]=2;if(decodeModel(b,d))return false;
 b=encode(m);b[4]=7;if(decodeModel(b,d))return false;
 m.lastSuccess=1;Visible aps[4]={ {"one",-20},{"two",-90},{"six",-30},{"unremembered",0} };
 auto ranklist=rank(m,aps,4);if(ranklist.count!=3||ranklist.indices[0]!=1||ranklist.indices[1]!=0||ranklist.indices[2]!=5)return false;
 if(rank(m,aps,0).count)return false;
 if(!m.forget("one")||m.lastSuccess!=0||m.count!=5||m.entries[5].password[0])return false;
 MosaicoWifiRetry::Policy p;for(unsigned i=0;i<3;++i)p.attempted(i*5000000,true);if(!p.exhausted(true))return false;
 // Migration is validated and preserves old blob format, without mutating it.
 auto old=MosaicoWifi::encode("legacy","abcdefgh"); if(!MosaicoWifi::validBlob(old))return false;
 return true;
}
static_assert(test());'''
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'profiles.cpp';p.write_text(code)
   result=subprocess.run([str(C[-1]),'-std=c++17','-fsyntax-only','-I'+str(R),str(p)],capture_output=True,text=True)
   self.assertEqual(result.returncode,0,result.stdout+result.stderr)
 def test_integration(self):
  s=(R/'main/host/network_quota.cpp').read_text()
  scan=s[s.index('bool NetworkQuota::selectWifiCandidate'):s.index('bool NetworkQuota::requestWifiCredentials')]
  self.assertIn('esp_wifi_scan_start(&scan,false)',scan)
  self.assertIn('now+4000000',scan);self.assertIn('records[24]',scan)
  self.assertIn('esp_wifi_clear_ap_list()',scan);self.assertNotIn('nvs_',scan)
  self.assertIn('if(_wifi_scan_started && _wifi_scan_cancelled) return false;',scan)
  self.assertIn('_wifi_scan_status.load()==0',scan)
  self.assertIn('_wifi_scan_fault) return false',scan)
  begin=s[s.index('void NetworkQuota::begin()'):s.index('void NetworkQuota::task')]
  self.assertIn('if(profilesRead==ESP_ERR_NVS_NOT_FOUND)',begin)
  self.assertIn('ok=endpointOk;',begin)
  self.assertIn('if(!loaded) { MosaicoWifi::scrub(_ssid',begin)
  config=s[s.index('bool NetworkQuota::configure'):s.index('void NetworkQuota::begin')]
  self.assertIn('if(profileRead==ESP_ERR_NVS_NOT_FOUND)',config)
  event=s[s.index('void NetworkQuota::scanEvent'):s.index('void NetworkQuota::cancelWifiScan')]
  self.assertIn('if(!owner._wifi_scan_started.load()) return;',event)
  self.assertNotIn('exchange(event.scan_id)',event)

  service=s[s.index('void NetworkQuota::serviceWifiSettings'):s.index('#if SOC_WIFI_HE_SUPPORT',s.index('void NetworkQuota::serviceWifiSettings'))]
  self.assertLess(service.index('MosaicoOta::busy()'),service.index('nvs_open'))
  self.assertLess(service.index('nvs_commit'),service.rindex('std::lock_guard'))
  self.assertIn('changed || !_wifi_profiles_persisted',service);self.assertIn('wifi_profiles',service)
  self.assertIn('useRetryPolicy = !_twt.live()',s)
if __name__=='__main__':unittest.main()
