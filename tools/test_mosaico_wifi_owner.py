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
        body=source[source.index('void NetworkQuota::cancelWifiScan()'):source.index('bool NetworkQuota::requestWifiCredentials')]+ 'bool NetworkQuota::requestWifiCredentials'+body
        body=re.sub(r'std::unique_lock<std::mutex> guard\([^;]+;', 'MockLock guard(lockAvailable);',body)
        body=re.sub(r'std::lock_guard<std::mutex> guard\([^;]+;', '',body)
        for a,b in {
            'bool NetworkQuota::':'constexpr bool View::', 'void NetworkQuota::':'constexpr void View::',
            'MosaicoOta::UiSnapshot':'OtaSnapshot','MosaicoOta::UiStage::':'Stage::',
            'MosaicoOta::busy()':'otaBusy','MosaicoOta::healthPending()':'healthPending',
            'MosaicoOta::copyUiSnapshot':'copyOta','MosaicoDisplay::snapshot':'copyDisplay',
            '_wifi_scan_done.load()':'_wifi_scan_done','_wifi_scan_status.load()':'_wifi_scan_status','std::strcmp':'compareString','std::memset':'clearBytes','std::strcpy':'copyString','std::memcpy':'copyBytes','MosaicoWifi::scrub':'scrub',
        }.items(): body=body.replace(a,b)
        code='#include "main/host/mosaico_display_settings_model.h"\n#include "main/host/mosaico_wifi_profiles_model.h"\n'+r'''
#include <algorithm>
struct wifi_scan_config_t {bool show_hidden=false;int scan_type=0;struct {struct {int min=0,max=0;} active;}scan_time;};
struct wifi_ap_record_t {char ssid[33]{};signed char rssi=-127;};
struct wifi_config_t {struct {int sae_pwe_h2e=0;struct{bool capable=false;}pmf_cfg;char ssid[32]{},password[64]{};}sta;};
constexpr int WIFI_SCAN_TYPE_ACTIVE=1,WPA3_SAE_PWE_BOTH=1,WIFI_IF_STA=0;
using esp_err_t=int; using nvs_handle_t=int;
constexpr int ESP_OK=0,ESP_ERR_INVALID_STATE=-1,ESP_ERR_INVALID_ARG=-2,ESP_ERR_NO_MEM=-3,ESP_ERR_TIMEOUT=-4,ESP_OTA_IMG_VALID=1,ESP_OTA_IMG_UNDEFINED=0,NVS_READWRITE=1;
using esp_ota_img_states_t=int; struct esp_partition_t {};
enum class Stage {Complete,ReadyReboot,BootChecking,Idle};
struct OtaSnapshot { bool imageVerified=false; Stage stage=Stage::Idle; };
struct MockLock { bool available;constexpr MockLock(bool a):available(a){} constexpr bool owns_lock(){return available;} };
struct View {
 WifiSettingsSnapshot _wifi_settings{}; char _pending_ssid[33]{},_pending_password[65]{};
 MosaicoWifiProfiles::Candidates _wifi_candidates{};
 bool _wifi_scan_started=false,_wifi_scan_cancelled=false,_wifi_scan_done=false,_wifi_scan_fault=false,_wifi_scan_complete=false;
 bool _wifi_scan_handler=true;unsigned _wifi_scan_status=0;int starts=0,startError=0,recordsCalls=0,configs=0;
 wifi_ap_record_t aps[24]{};unsigned apCount=0; char _password[65]{};
 constexpr int esp_wifi_scan_start(wifi_scan_config_t*,bool){++starts;return startError;}
 constexpr int esp_wifi_scan_get_ap_records(unsigned short* count,wifi_ap_record_t* records){++recordsCalls;*count=apCount;for(unsigned i=0;i<apCount;++i)records[i]=aps[i];return 0;}
 constexpr void esp_wifi_disconnect(){}
 constexpr int esp_wifi_set_config(int,wifi_config_t*){++configs;return 0;}
 constexpr bool selectWifiCandidate(bool,int64_t);
 int64_t _wifi_scan_deadline=0,_now=0; int stops=0,clears=0,stopError=0;
 constexpr int64_t esp_timer_get_time(){return _now;}
 constexpr int esp_wifi_scan_stop(){++stops;return stopError;}
 constexpr void esp_wifi_clear_ap_list(){++clears;}
 constexpr void cancelWifiScan(); constexpr void drainWifiScan();
 MosaicoWifiProfiles::Model _wifi_profiles{}; bool _pending_forget=false, _wifi_profiles_persisted=false; char _ssid[33]{};
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
 template<class T> constexpr void scrub(T* p,unsigned n){if constexpr(sizeof(T)==1){for(unsigned i=0;i<n;++i)p[i]=0;}else *p=T{};}
 constexpr void copyString(char* d,const char* s){unsigned i=0;do{d[i]=s[i];}while(s[i++]);}
 constexpr void copyBytes(char* d,const char* s,unsigned n){for(unsigned i=0;i<n;++i)d[i]=s[i];}
 constexpr int compareString(const char* a,const char* b){while(*a && *a==*b){++a;++b;}return *a-*b;}
 template<class T> constexpr void clearBytes(T& a,int,unsigned){for(auto& x:a) for(auto& c:x)c=0;}
 constexpr bool requestWifiForget(const char*);
 constexpr void publishWifiProfiles();
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
 View scan;scan._wifi_profiles.upsert("visible","12345678");scan._wifi_profiles.upsert("absent","12345678");
 scan.apCount=1;scan.copyString(scan.aps[0].ssid,"visible");scan.aps[0].rssi=-40;
 if(scan.selectWifiCandidate(true,0)||scan.starts!=1||!scan._wifi_scan_started)return false;
 if(scan.selectWifiCandidate(true,1)||scan.starts!=1||scan.recordsCalls)return false;
 scan._wifi_scan_done=true;
 if(!scan.selectWifiCandidate(true,2)||scan.configs!=1||scan.clears!=1)return false;
 if(scan.selectWifiCandidate(true,3)||scan.starts!=1)return false; // no absent-AP connects / rescan
 View failedScan;failedScan._wifi_profiles.upsert("visible","");failedScan.startError=-1;
 if(failedScan.selectWifiCandidate(true,0)||failedScan._wifi_scan_started||!failedScan._wifi_scan_complete)return false;
 View badStatus;badStatus._wifi_profiles.upsert("visible","");badStatus.selectWifiCandidate(true,0);badStatus._wifi_scan_done=true;badStatus._wifi_scan_status=1;
 if(badStatus.selectWifiCandidate(true,1)||badStatus.recordsCalls||badStatus.clears!=1)return false;
 View late;late._wifi_profiles.upsert("visible","");late.selectWifiCandidate(true,0);late._now=4000000;late.selectWifiCandidate(true,4000000);
 if(!late._wifi_scan_cancelled||late.starts!=1)return false;
 late.selectWifiCandidate(true,4000001);if(late.starts!=1)return false;
 late._wifi_scan_done=true;late.selectWifiCandidate(true,4000002);
 if(late.starts!=1||late.recordsCalls||late._wifi_scan_started)return false; // canceled DONE is drained, never ranked
 View lifecycle;lifecycle._wifi_scan_started=true;lifecycle._wifi_candidates.count=6;
 lifecycle.cancelWifiScan();if(lifecycle.stops!=1||!lifecycle._wifi_scan_started)return false;
 lifecycle.cancelWifiScan();if(lifecycle.stops!=1)return false; // no duplicate stop/DONE
 lifecycle.drainWifiScan();if(lifecycle.clears||!lifecycle._wifi_scan_started)return false;
 lifecycle._wifi_scan_done=true;lifecycle.drainWifiScan();
 if(lifecycle._wifi_scan_started||lifecycle._wifi_candidates.count||lifecycle.clears!=1)return false;
 View missing;missing._wifi_scan_started=true;missing.stopError=-1;missing.cancelWifiScan();missing._now=3000000;missing.drainWifiScan();
 if(!missing._wifi_scan_started||!missing._wifi_scan_fault||missing._wifi_settings.scanError!=ESP_ERR_TIMEOUT)return false;
 missing._wifi_settings.available=true;if(!missing.requestWifiRestart())return false; // repair path still alive
 View completed;completed._wifi_scan_started=true;completed._wifi_scan_done=true;completed.cancelWifiScan();
 if(completed.stops||completed._wifi_scan_started||completed.clears!=1)return false;
 View multi;multi._wifi_settings.available=true;
 const char* names[]={"a","b","c","d","e","f"};
 for(auto name:names){if(!multi.requestWifiCredentials(name,"12345678"))return false;multi.serviceWifiSettings();}
 if(multi._wifi_settings.count!=6||multi.sets!=6||multi.requestWifiCredentials("g","12345678"))return false;
 multi._wifi_candidates.count=6;multi._wifi_candidates.next=5;
 if(!multi.requestWifiCredentials("a","") )return false;multi.serviceWifiSettings();
 if(multi._wifi_candidates.count||!multi._wifi_scan_complete||multi.sets!=6||multi._wifi_profiles.entries[0].password[0]!='1')return false;
 for(unsigned i=0;i<5;++i){if(!multi.requestWifiForget(names[i]))return false;multi.serviceWifiSettings();}
 if(multi._wifi_settings.count!=1||multi.requestWifiForget("f")||multi.sets!=11)return false;
 return true;
}
static_assert(traces(),"production WiFi queue/save/restart fault traces");
'''
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'owner.cpp';p.write_text(code)
            result=subprocess.run([str(COMPILER),'-std=c++20','-fsyntax-only','-I'+str(ROOT),str(p)],capture_output=True,text=True)
            log=ROOT/'.artifacts/mosaico/wifi-owner-traces.log';log.write_text(result.stdout+result.stderr)
            self.assertEqual(result.returncode,0,str(log))

if __name__=='__main__':unittest.main()
