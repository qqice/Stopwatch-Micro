"""Execute adapted actual owner/request source as constexpr fault traces.

Only platform side effects and locks are stubbed; queue/OTA/save/reboot branches
are extracted from production source. Not a hardware NVS power-loss test.
"""
from pathlib import Path
import re, subprocess, tempfile, unittest
ROOT=Path(__file__).resolve().parents[1]
COMPILER=Path('C:/Espressif/tools/riscv32-esp-elf/esp-15.2.0_20251204/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe')

class WifiOwnerTests(unittest.TestCase):
    def test_actual_owner_fault_traces(self):
        source=(ROOT/'main/host/network_quota.cpp').read_text()
        body=source.split('bool NetworkQuota::requestWifiCredentials',1)[1].split('void NetworkQuota::setLowClockDiagnostic',1)[0]
        body='bool NetworkQuota::requestWifiCredentials'+body
        body=re.sub(r'std::unique_lock<std::mutex> guard\([^;]+;', 'MockLock guard(lockAvailable);',body)
        body=re.sub(r'std::lock_guard<std::mutex> guard\([^;]+;', '',body)
        for a,b in {
            'bool NetworkQuota::':'constexpr bool View::', 'void NetworkQuota::':'constexpr void View::',
            'MosaicoOta::UiSnapshot':'OtaSnapshot','MosaicoOta::UiStage::':'Stage::',
            'MosaicoOta::busy()':'otaBusy','MosaicoOta::healthPending()':'healthPending',
            'MosaicoOta::copyUiSnapshot':'copyOta','MosaicoDisplay::snapshot':'copyDisplay',
            'std::strcpy':'copyString','std::memcpy':'copyBytes','MosaicoWifi::scrub':'scrub',
        }.items(): body=body.replace(a,b)
        code='#include "main/host/mosaico_display_settings_model.h"\n#include "main/host/mosaico_wifi_settings_model.h"\n'+r'''
using esp_err_t=int; using nvs_handle_t=int;
constexpr int ESP_OK=0,ESP_ERR_INVALID_STATE=-1,ESP_ERR_INVALID_ARG=-2,ESP_OTA_IMG_VALID=1,ESP_OTA_IMG_UNDEFINED=0,NVS_READWRITE=1;
using esp_ota_img_states_t=int; struct esp_partition_t {};
enum class Stage {Complete,ReadyReboot,BootChecking,Idle};
struct OtaSnapshot { bool imageVerified=false; Stage stage=Stage::Idle; };
struct MockLock { bool available;constexpr MockLock(bool a):available(a){} constexpr bool owns_lock(){return available;} };
struct View {
 WifiSettingsSnapshot _wifi_settings{}; char _pending_ssid[33]{},_pending_password[65]{};
 int _task_handle=1, notifications=0,opens=0,sets=0,commits=0,restarts=0;
 int openError=0,setError=0,commitError=0,partitionError=0;
 bool lockAvailable=true,otaBusy=false,healthPending=false,otaReadable=true,displayReadable=true;
 OtaSnapshot ota{}; MosaicoDisplay::Model display{}; esp_partition_t partition{};
 constexpr void xTaskNotifyGive(int){++notifications;}
 constexpr bool copyOta(OtaSnapshot& out){out=ota;return otaReadable;}
 constexpr bool copyDisplay(MosaicoDisplay::Snapshot& out){out=display.state;return displayReadable;}
 constexpr const esp_partition_t* esp_ota_get_running_partition(){return &partition;}
 constexpr int esp_ota_get_state_partition(const esp_partition_t*,int* out){*out=ESP_OTA_IMG_VALID;return partitionError;}
 constexpr void esp_restart(){++restarts;}
 constexpr int nvs_open(const char*,int,int* out){++opens;*out=1;return openError;}
 constexpr int nvs_set_blob(int,const char*,const unsigned char*,unsigned){++sets;return setError;}
 constexpr int nvs_commit(int){++commits;return commitError;}
 constexpr void nvs_close(int){}
 template<class T> constexpr void scrub(T* p,unsigned n){for(unsigned i=0;i<n;++i)p[i]=0;}
 constexpr void copyString(char* d,const char* s){unsigned i=0;do{d[i]=s[i];}while(s[i++]);}
 constexpr void copyBytes(char* d,const char* s,unsigned n){for(unsigned i=0;i<n;++i)d[i]=s[i];}
 constexpr bool requestWifiCredentials(const char*,const char*);
 constexpr bool requestWifiRestart();
 constexpr void serviceWifiSettings();
};
'''+body+r'''
constexpr bool traces(){
 View v; if(v.requestWifiCredentials("net","12345678")||v.notifications)return false;
 v._wifi_settings.available=true;
 if(!v.requestWifiCredentials("net","12345678")||v.notifications!=1)return false;
 if(v.requestWifiCredentials("other","abcdefgh")||v._pending_ssid[0]!='n')return false;
 for(int gate=0;gate<5;++gate){
  v.otaBusy=gate==0;v.healthPending=gate==1;v.otaReadable=gate!=2;
  v.ota.imageVerified=gate==3;v.partitionError=gate==4?-1:0;
  v.serviceWifiSettings();if(v.sets||!v._wifi_settings.pending)return false;
 }
 v.otaBusy=v.healthPending=v.ota.imageVerified=false;v.otaReadable=true;v.partitionError=0;
 v.serviceWifiSettings();if(v.sets!=1||v.commits!=1||v._wifi_settings.pending||!v._wifi_settings.rebootRequired||v.restarts)return false;
 for(char c:v._pending_password)if(c)return false;
 if(!v.requestWifiRestart())return false;
 auto c=v.display.state.config;c.lockWifiMinutes=2;v.display.request(c,100);
 v.serviceWifiSettings();if(v.restarts||!v._wifi_settings.restartPending)return false;
 v.display.completed(v.display.state.revision,-7);v.serviceWifiSettings();
 if(v.restarts||v._wifi_settings.restartPending||v._wifi_settings.error||v._wifi_settings.restartError!=-7)return false;
 v.display.request(c,2000000);v.display.completed(v.display.state.revision,0);
 if(!v.requestWifiRestart()||v._wifi_settings.restartError)return false;
 v.serviceWifiSettings();if(v.restarts!=1)return false;
 for(int failure=0;failure<3;++failure){
  View f;f._wifi_settings.available=true;
  f.openError=failure==0?-8:0;f.setError=failure==1?-9:0;f.commitError=failure==2?-10:0;
  if(!f.requestWifiCredentials("net","12345678"))return false;
  f.serviceWifiSettings();
  if(!f._wifi_settings.error||f._wifi_settings.pending||f._wifi_settings.rebootRequired||f.requestWifiRestart()||f.restarts)return false;
  for(char c:f._pending_password)if(c)return false;
 }
 return true;
}
static_assert(traces(),"production WiFi queue/save/restart fault traces");
'''
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'owner.cpp';p.write_text(code)
            result=subprocess.run([str(COMPILER),'-std=c++17','-fsyntax-only','-I'+str(ROOT),str(p)],capture_output=True,text=True)
            log=ROOT/'.artifacts/mosaico/wifi-owner-traces.log';log.write_text(result.stdout+result.stderr)
            self.assertEqual(result.returncode,0,str(log))

if __name__=='__main__':unittest.main()
