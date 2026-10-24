"""RAM-only maintenance model and extracted REAL OTA chain on existing Unicorn RV32.
No firmware build, device, cryptography correctness or real scheduler/I2C claim.
"""
from pathlib import Path
import os,sys,struct,subprocess,tempfile,unittest,hashlib
import test_mosaico_gauge_scale_backend as base
ROOT=base.ROOT
RUN=ROOT/'artifacts/ota-maintenance-tests'
CPP=(ROOT/'main/ota/mosaico_ota.cpp').read_text()
def function(signature):
 start=CPP.index(signature);brace=CPP.index('{',start);depth=1;i=brace+1
 while depth:
  if CPP[i]=='{':depth+=1
  elif CPP[i]=='}':depth-=1
  i+=1
 return CPP[start:i]+'\n'
FIXTURE=r'''
#include <cstdint>
#include <cstddef>
#include <cstring>
#include <cstdio>
#include <mutex>
#define INT64_MAX 9223372036854775807LL
#include "mosaico_ota.h"
#include "mosaico_ota_maintenance_model.h"
#include "mosaico_ota_power.h"
template<class T>struct Atomic {T v{};Atomic()=default;Atomic(T x):v(x){}operator T()const{return v;}T load()const{return v;}void store(T x){v=x;}T exchange(T x){T p=v;v=x;return p;}T operator=(T x){return v=x;}T operator+=(T x){return v+=x;}};
struct esp_partition_t {uint32_t address=0x20000,size=0x7d0000;};
using esp_ota_handle_t=unsigned;using esp_err_t=int;constexpr int ESP_OK=0,PSA_ALG_SHA_256=1;
using psa_hash_operation_t=int;
#define PSA_HASH_OPERATION_INIT {0}
struct esp_app_desc_t {char version[32]="new";};
static bool storageBusy=false,targetSame=false,hashFault=false;static uint32_t storageLeaseDepth=0;static uint32_t usbUnmounts=0;static bool usbSuspended=false,usbConnected=true,reconnectInRaw=false;
struct mosaico_usb_snapshot_t {uint32_t mounted,connected,suspended,unmounts;};
static int64_t clockUs=1000000;static bool usb=true,rawOk=true,validSdk=true,scale=false,critical=true,signatureOk=true;
static int erases=0,writes=0,aborts=0,ends=0,selectors=0,restarts=0,rawReads=0,journals=0,telemetryReads=0,historyReads=0,scaleReads=0,policyReads=0;static int rawFault=0,busyReads=0,waitFault=0,delays=0,rawHook=0;static bool detachAtSelector=false,failRawAtEnd=false,expireAtEnd=false,expireAtJournal=false,detachBeforeFinish=false;
static esp_partition_t parts[2]={{0x20000,0x7d0000},{0x800000,0x7d0000}};static const esp_partition_t* boot=&parts[0];
int64_t esp_timer_get_time(){return clockUs;}bool tud_mounted(){return usb;}
mosaico_usb_snapshot_t mosaico_console_usb_snapshot(){return {usb,usbConnected,usbSuspended,usbUnmounts};}
const esp_partition_t* esp_ota_get_running_partition(){return &parts[0];}const esp_partition_t* esp_ota_get_boot_partition(){return boot;}const esp_partition_t* esp_ota_get_next_update_partition(void*){return targetSame?&parts[0]:&parts[1];}
bool esp_ptr_in_dram(const void*){return true;}int esp_ota_begin(const esp_partition_t*,uint32_t,unsigned*h){++erases;*h=1;return 0;}
int esp_ota_abort(unsigned){++aborts;return 0;}int esp_ota_write(unsigned,const uint8_t*,size_t){++writes;return 0;}int esp_ota_end(unsigned){++ends;if(failRawAtEnd)rawOk=false;if(expireAtEnd)clockUs+=600000000;return 0;}
int esp_ota_set_boot_partition(const esp_partition_t*p){++selectors;boot=p;if(detachAtSelector)usb=false;return 0;}
int esp_ota_get_partition_description(const esp_partition_t*,esp_app_desc_t*d){*d={};return 0;}
void esp_restart(){++restarts;}int psa_hash_setup(int*,int){return 0;}int psa_hash_abort(int*){return 0;}int psa_hash_update(int*,const uint8_t*,size_t){return 0;}
int psa_hash_finish(int*,uint8_t*d,size_t,size_t*n){for(int i=0;i<32;++i)d[i]=hashFault?0xbb:0xaa;*n=32;return 0;}
struct Hal {
 enum class GaugeBootReloadStatus {Critical,Applied};
 struct BatteryTelemetry {bool valid=true,capacityValid=false,nominalConfigured=false;uint16_t voltageMv=4200,operationStatus=0x26,fullMah=3000,remainingMah=3000;uint8_t reportedSoc=100;int16_t currentMa=0;};
 struct MaintenancePowerSnapshot {bool valid=false;uint16_t voltageMv=0,temperatureDeciK=0,operationStatus=0,designMah=0,fullMah=0;int16_t currentMa=0,averageCurrentMa=0;int64_t sampledUs=0;};
 struct Boot {GaugeBootReloadStatus status;};
 bool gaugeScaleTrialActive(){++scaleReads;return scale;}Boot gaugeBootReloadInfo(){++historyReads;return {critical?GaugeBootReloadStatus::Critical:GaugeBootReloadStatus::Applied};}
 BatteryTelemetry batteryPolicyTelemetry(bool){++policyReads;BatteryTelemetry b{};b.valid=false;return b;}
 struct PhysicalBatterySnapshot {BatteryTelemetry native;};
 PhysicalBatterySnapshot physicalBatteryTelemetry(bool){++telemetryReads;return {};}
 enum class MaintenancePowerResult { Rejected, Busy, Safe };
 bool tryMaintenancePowerSnapshot(MaintenancePowerSnapshot& s,MaintenancePowerResult* result=nullptr){++rawReads;s={};if(result)*result=MaintenancePowerResult::Rejected;if(busyReads){--busyReads;if(result)*result=MaintenancePowerResult::Busy;return false;}s={true,4200,3000,0x26,3000,3000,0,0,clockUs};
  if(rawFault==1)s.operationStatus=6;if(rawFault==2)s.operationStatus=0x426;if(rawFault==3)s.operationStatus=0x27;if(rawFault==4)s.operationStatus=0x24;
  if(rawFault==5)s.voltageMv=3899;if(rawFault==6)s.temperatureDeciK=3132;if(rawFault==7)s.fullMah=65;if(rawFault==8)s.currentMa=131;
  if(rawFault==9)s.averageCurrentMa=-131;if(rawFault==10)s.sampledUs=clockUs-2000001;if(rawFault==11)s.valid=false;
  if(rawHook==1)clockUs+=30000000;if(rawHook==2)boot=&parts[1];if(rawHook==3)validSdk=false;if(rawHook==4)scale=true;
  if(reconnectInRaw)++usbUnmounts;if(result&&rawOk)*result=MaintenancePowerResult::Safe;return rawOk;}
} hal;Hal& GetHAL(){return hal;}
namespace TouchSleep {struct OtaAdmission {explicit operator bool()const{return true;}};}
namespace StandbySleep {void otaActivity(){}}
namespace mosaico_rx_ingress {void request(bool){}void service(bool){}}
namespace mosaico_rx_observer {void request(bool){}void service(bool){}}
namespace mosaico_wifi_rx_observer {void request(bool){}void service(bool){}struct Snapshot{bool cleanupPending=false;};Snapshot snapshot(){return {};}}
#define pdMS_TO_TICKS(x) (x)
#define portTICK_PERIOD_MS 1
void vTaskDelay(int ms){++delays;clockUs+=int64_t(ms)*1000;if(waitFault==1)++usbUnmounts;if(waitFault==2)clockUs+=30000000;if(waitFault==3)boot=&parts[1];if(waitFault==4)validSdk=false;if(waitFault==5)usb=false;if(waitFault==6)clockUs-=2000;if(waitFault==7)clockUs+=250000;}
namespace MosaicoOtaLayout {bool imageFits(int,int,uint32_t s){return s<=0x7d0000;}}
namespace MosaicoOta {
std::mutex lock,snapshotLock;
struct StorageAdmission {explicit operator bool()const{return !storageBusy && storageLeaseDepth==0;}};
UiSnapshot ui{};Atomic<bool> active=false,pending=false,approvedRequest=false,autoInstall=false,imageReady=false,selected=false,verified=false,checkQueued=false,checking=false,installQueued=false,rebootQueued=false;
Atomic<uint32_t> expectedSize=0,received=0,requestedAtMs=0;Atomic<const esp_partition_t*> cachedRunning=nullptr;
Atomic<UiStage> statusStage=UiStage::Idle;
Atomic<bool> maintenanceMode=false;bool writing=false,hashLive=false,automaticMode=false,usbBypass=false,signedVersion=false;
MosaicoOtaMaintenance::Grant maintenanceGrant;Hal::MaintenancePowerSnapshot maintenancePower;int64_t maintenanceRawUs=0,readySince=0,installSince=0;uint32_t maintenanceRawBytes=0;
esp_ota_handle_t handle=0;const esp_partition_t* target=nullptr;uint8_t expectedHash[32]{};char approvedHash[65]{},reason[96]{};psa_hash_operation_t hash=0;
struct Descriptor {bool versionSigned;uint32_t size;uint8_t hash[32];char sha[65],version[32],fingerprint[17];};
struct InstallPowerEvidence {char text[96]{};};
void captureInstallPower(InstallPowerEvidence*,const Hal::BatteryTelemetry&,bool,bool){}
bool currentReady(){return validSdk;}int currentLayout(){return 1;}int partitionGeometry(const esp_partition_t*){return 1;}int slot(const esp_partition_t*p){return p==&parts[1]?1:0;}
void publish(UiStage stage,const char* error=""){statusStage=stage;ui.stage=stage;std::snprintf(ui.error,sizeof(ui.error),"%s",error);}
bool busy(){return active||approvedRequest||checkQueued||checking||installQueued||rebootQueued;}
uint32_t requestAgeMs(){return uint32_t(clockUs/1000)-requestedAtMs.load();}
bool validateDescriptor(const char* json,Descriptor&d,const char*&e){e="signature_invalid";if(!signatureOk)return false;d={};d.versionSigned=true;d.size=70000;for(int i=0;i<32;++i)d.hash[i]=json[0]=='b'?0xbb:0xaa;for(int i=0;i<64;++i)d.sha[i]=json[0]=='b'?'b':'a';d.sha[64]=0;std::memcpy(d.version,"new",4);return true;}
'''
CASES=r'''
}
static const char* A="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
static const char* B="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
using namespace MosaicoOta;
static void reset(){storageBusy=targetSame=hashFault=false;storageLeaseDepth=0;usbUnmounts=0;usbSuspended=reconnectInRaw=false;usbConnected=true;clockUs=1000000;usb=rawOk=validSdk=signatureOk=true;scale=false;critical=true;rawFault=busyReads=waitFault=delays=rawHook=0;detachAtSelector=failRawAtEnd=expireAtEnd=expireAtJournal=detachBeforeFinish=false;boot=&parts[0];
 erases=writes=aborts=ends=selectors=restarts=rawReads=journals=telemetryReads=historyReads=scaleReads=0,policyReads=0;active=pending=approvedRequest=imageReady=selected=verified=checkQueued=checking=installQueued=rebootQueued=false;
 maintenanceMode=writing=hashLive=automaticMode=usbBypass=false;maintenanceGrant={};maintenancePower={};maintenanceRawUs=readySince=installSince=0;maintenanceRawBytes=0;expectedSize=received=requestedAtMs=0;handle=0;target=nullptr;approvedHash[0]=0;statusStage=UiStage::Idle;}
static int stageImage(){if(!requestMaintenanceUpdate(A,600)||!takeRequest()||!beginManifest("a"))return 1;uint8_t data[4096]{};
 for(uint32_t off=0;off<70000;){uint32_t n=70000-off;if(n>3001)n=3001;if(!writeChunk(off,data,n))return 2;off+=n;}
 if(detachBeforeFinish)usb=false;
 if(!finishDownload())return 3;return 0;}
static int selectImage(){int r=stageImage();if(r)return r;clockUs+=1600000;if(installVerified())return 4;clockUs+=600000;if(!installVerified())return 5;return 0;}
static int run_case(int c){reset();
 if(c==0){if(maintenanceMode||maintenanceGrant.enabled||!downloadPowerSafe()||!automaticPowerSafe()||!installPowerSafe(true)||telemetryReads||historyReads||scaleReads||policyReads)return 1;return 0;}
 if(c==1){if(stageImage()||erases!=1||!verified||!imageReady||rawReads||telemetryReads||historyReads||scaleReads||policyReads||!active)return 1;return 0;}
 if(c==2){if(!requestMaintenanceUpdate(A,600)||!takeRequest()||beginManifest("b")||erases||maintenanceGrant.enabled)return 1;return 0;}
 if(c==3){signatureOk=false;if(!requestMaintenanceUpdate(A,600)||!takeRequest()||beginManifest("a")||erases)return 1;return 0;}
 if(c>=4&&c<=14){rawFault=c-3;if(!requestMaintenanceUpdate(A,600)||erases||!maintenanceGrant.enabled||rawReads)return 1;return 0;}
 if(c==15){rawOk=false;if(!requestMaintenanceUpdate(A,600)||!maintenanceGrant.enabled||rawReads)return 1;return 0;}
 if(c==16){validSdk=false;if(requestMaintenanceUpdate(A,600)||erases)return 1;return 0;}
 if(c==17){usb=false;if(requestMaintenanceUpdate(A,600))return 1;return 0;}
 if(c==18){if(requestMaintenanceUpdate(A,29)||requestMaintenanceUpdate(A,601)||requestMaintenanceUpdate(B,0)||requestMaintenanceUpdate("a",600))return 1;return 0;}
 if(c==19){if(!requestMaintenanceUpdate(A,30)||!takeRequest()||!beginManifest("a"))return 1;clockUs+=7200000000LL;uint8_t d=0;if(!writeChunk(0,&d,1)||writes!=1||!maintenanceGrant.enabled||imageReady)return 2;return 0;}
 if(c==20){if(!requestMaintenanceUpdate(A,30)||!takeRequest()||!beginManifest("a"))return 1;--clockUs;uint8_t d=0;if(writeChunk(0,&d,1)||writes)return 2;return 0;}
 if(c==21){if(!requestMaintenanceUpdate(A,30)||!takeRequest()||!beginManifest("a"))return 1;usb=false;uint8_t d=0;if(writeChunk(0,&d,1)||writes||maintenanceGrant.enabled)return 2;return 0;}
 if(c==22){if(stageImage())return 1;usb=false;clockUs+=2000000;if(installVerified()||selectors||maintenanceGrant.enabled)return 2;return 0;}
 if(c==23){if(selectImage()||!selected||boot!=&parts[1])return 1;usb=false;if(rebootWithFreshPower()||restarts||!selected||!imageReady||!verified||statusStage.load()!=UiStage::ReadyReboot)return 2;return 0;}
 if(c==24){if(selectImage()||!selected||boot!=&parts[1])return 1;clockUs+=7200000000LL;if(!rebootWithFreshPower()||restarts!=1||selectors!=1||erases!=1)return 2;return 0;}
 if(c==25){if(!requestMaintenanceUpdate(A,30))return 1;failLocked("network_ready_timeout");if(maintenanceGrant.enabled||maintenanceMode||active||pending)return 2;if(!request()||erases||rawReads||telemetryReads)return 3;return 0;}
 if(c==26){if(!requestMaintenanceUpdate(A,30))return 1;revokeForNormalRequest();if(maintenanceGrant.enabled||maintenanceMode||active||pending)return 2;return 0;}
 if(c==27){detachAtSelector=true;if(selectImage())return 1;if(rebootWithFreshPower()||!selected||!imageReady||restarts)return 2;failLocked("later_failure");if(!selected||!imageReady||statusStage.load()!=UiStage::ReadyReboot)return 3;return 0;}
 if(c==28){if(!requestMaintenanceUpdate(A,30)||!takeRequest()||!beginManifest("a"))return 1;uint8_t d[3001]{};for(unsigned off=0;off<63021;off+=3001)if(!writeChunk(off,d,3001))return 2;rawOk=false;if(!writeChunk(63021,d,3001)||rawReads||writes!=22)return 3;return 0;}
 if(c==29){if(stageImage())return 1;clockUs+=1600000;installVerified();clockUs+=600000;rawOk=false;if(!installVerified()||selectors!=1||journals!=1||rawReads)return 2;return 0;}
 if(c==30){if(!requestMaintenanceUpdate(A,30))return 1;clockUs+=7200000000LL;if(!takeRequest()||erases||!maintenanceGrant.enabled)return 2;return 0;}
 if(c==31){if(stageImage())return 1;clockUs+=7200000000LL;if(installVerified()||selectors)return 2;clockUs+=600000;if(!installVerified()||selectors!=1)return 3;return 0;}
 if(c==32){if(!requestMaintenanceUpdate(A,30)||!takeRequest()||!beginManifest("a"))return 1;uint8_t d=0;clockUs+=1000000;rawOk=false;if(!writeChunk(0,&d,1)||writes!=1||rawReads)return 2;return 0;}
 if(c==33){scale=true;if(!requestMaintenanceUpdate(A,30)||erases||rawReads||telemetryReads||scaleReads)return 1;return 0;}
 if(c==34){if(selectImage())return 1;revokeForNormalRequest();if(rebootWithFreshPower()||restarts||!selected||!imageReady)return 2;return 0;}
 if(c==35){failRawAtEnd=true;if(stageImage()||!imageReady||rawReads||selectors)return 1;return 0;}
 if(c==36){expireAtEnd=true;if(stageImage()||!imageReady||selectors)return 1;return 0;}
 if(c==37){expireAtJournal=true;if(stageImage())return 1;clockUs+=1600000;installVerified();clockUs+=600000;if(!installVerified()||selectors!=1||journals!=1||!maintenanceGrant.enabled)return 2;return 0;}
 if(c==38){detachBeforeFinish=true;if(stageImage()!=3||ends||imageReady)return 1;return 0;}
 if(c==39){if(stageImage())return 1;processLocalRequests();if(selectors!=1||restarts!=1||!selected||!imageReady)return 2;return 0;}
 if(c==40){if(stageImage())return 1;detachAtSelector=true;processLocalRequests();if(selectors!=1||restarts||!selected||!imageReady||maintenanceGrant.enabled)return 2;return 0;}
 if(c==41){if(!requestMaintenanceUpdate(A,30))return 1;deferUpdate();if(maintenanceMode||maintenanceGrant.enabled||active)return 2;return 0;}
 if(c==42){if(!requestMaintenanceUpdate(A,30))return 1;approveUpdate(A);if(maintenanceMode||maintenanceGrant.enabled||active)return 2;return 0;}
 if(c==43){if(!requestMaintenanceUpdate(A,30))return 1;approveInstall(A);if(maintenanceMode||maintenanceGrant.enabled||active)return 2;return 0;}
 if(c==44){if(selectImage())return 1;requestReboot();rebootQueued=false;if(rebootWithFreshPower()||maintenanceGrant.enabled||restarts)return 2;return 0;}
 if(c==45){if(!requestMaintenanceUpdate(A,30)||!takeRequest()||!beginManifest("a"))return 1;++usbUnmounts;uint8_t d=0;if(writeChunk(0,&d,1)||writes||maintenanceGrant.enabled)return 2;return 0;}
 if(c==46){if(stageImage())return 1;++usbUnmounts;clockUs+=2000000;if(installVerified()||selectors||maintenanceGrant.enabled)return 2;return 0;}
 if(c==47){if(selectImage())return 1;++usbUnmounts;if(rebootWithFreshPower()||restarts||!selected||!imageReady)return 2;
  if(!requestMaintenanceReboot(A,30))return 3;rebootQueued=false;if(!rebootWithFreshPower()||restarts!=1||selectors!=1)return 4;return 0;}
 if(c==48){reconnectInRaw=true;if(!requestMaintenanceUpdate(A,30)||!maintenanceGrant.enabled||erases||rawReads||usbUnmounts)return 1;return 0;}
 if(c==49){usbSuspended=true;if(requestMaintenanceUpdate(A,30)||maintenanceGrant.enabled)return 1;return 0;}
 if(c==50){usbConnected=false;if(requestMaintenanceUpdate(A,30)||maintenanceGrant.enabled)return 1;return 0;}
 if(c==51){if(!requestMaintenanceUpdate(A,30))return 1;storageBusy=true;deferUpdate();if(!maintenanceMode||!maintenanceGrant.enabled||!active||aborts)return 2;storageBusy=false;deferUpdate();if(maintenanceMode||maintenanceGrant.enabled||active)return 3;return 0;}
 if(c==52){if(!requestMaintenanceUpdate(A,30))return 1;char status[256];if(!maintenanceStatus(status,sizeof(status))||!std::strstr(status,"grant=1"))return 2;
  usb=false;if(!maintenanceStatus(status,sizeof(status))||!std::strstr(status,"grant=0")||!maintenanceGrant.enabled)return 3;
  usb=true;++usbUnmounts;if(!maintenanceStatus(status,sizeof(status))||!std::strstr(status,"grant=0")||!maintenanceGrant.enabled)return 4;return 0;}
 if(c==53){if(!requestMaintenanceUpdate(A,30))return 1;char status[256];usbSuspended=true;if(!maintenanceStatus(status,sizeof(status))||!std::strstr(status,"grant=0"))return 2;
  usbSuspended=false;usbConnected=false;if(!maintenanceStatus(status,sizeof(status))||!std::strstr(status,"grant=0")||!maintenanceGrant.enabled)return 3;return 0;}
 if(c==54){busyReads=2;if(!requestMaintenanceUpdate(A,30)||rawReads||delays||maintenanceGrant.lastUs!=1000000)return 1;return 0;}
 if(c==55){for(int f=1;f<=11;++f){reset();busyReads=2;rawFault=f;if(!requestMaintenanceUpdate(A,30)||rawReads||delays||!maintenanceGrant.enabled||erases)return f;}return 0;}
 if(c==56){busyReads=1000;if(!requestMaintenanceUpdate(A,30)||rawReads||delays||clockUs!=1000000||!maintenanceGrant.enabled)return 1;return 0;}
 if(c>=57&&c<=63){busyReads=2;waitFault=c-56;if(!requestMaintenanceUpdate(A,30)||rawReads||delays||!maintenanceGrant.enabled||erases)return 1;return 0;}
 if(c==64){if(!requestMaintenanceUpdate(A,30))return 1;busyReads=1000;if(!maintenancePowerSafe(true)||rawReads||!maintenanceGrant.enabled)return 2;return 0;}
 if(c==65){busyReads=2;rawOk=false;if(!requestMaintenanceUpdate(A,30)||rawReads||delays)return 1;return 0;}
 if(c==66){if(!requestMaintenanceUpdate(A,30))return 1;clockUs+=86400000000LL;busyReads=2;if(!maintenancePowerSafe(true)||rawReads||!maintenanceGrant.enabled)return 2;return 0;}
 if(c>=67&&c<=70){busyReads=2;rawHook=c-66;if(!requestMaintenanceUpdate(A,30)||rawReads||delays||!maintenanceGrant.enabled||erases)return 1;return 0;}
 if(c==71){scale=true;rawOk=false;busyReads=1000;rawFault=11;critical=true;
  if(!request()||!takeRequest()||!beginManifest("a"))return 1;uint8_t d[4096]{};
  for(uint32_t off=0;off<70000;){uint32_t n=70000-off;if(n>4096)n=4096;if(!writeChunk(off,d,n))return 2;off+=n;}
  if(!finishDownload())return 3;clockUs+=1600000;if(installVerified())return 4;clockUs+=600000;
  if(!installVerified()||selectors!=1||rawReads||telemetryReads||historyReads||scaleReads||policyReads)return 5;return 0;}
 if(c==72){usb=false;if(request()||rawReads||telemetryReads||erases)return 1;return 0;}
 if(c==73){storageBusy=true;if(requestMaintenanceUpdate(A,30)||request()||erases||rawReads||telemetryReads)return 1;return 0;}
 if(c==74){if(!requestMaintenanceUpdate(A,30))return 1;storageBusy=true;if(takeRequest()||erases)return 2;storageBusy=false;if(!takeRequest()||!beginManifest("a")||erases!=1)return 3;return 0;}
 if(c==75){storageLeaseDepth=1;if(requestMaintenanceUpdate(A,30)||request()||erases)return 1;storageLeaseDepth=0;if(!requestMaintenanceUpdate(A,30))return 2;return 0;}
 if(c==76){if(!request()||!takeRequest())return 1;usb=false;if(beginManifest("a")||erases||telemetryReads||rawReads)return 2;return 0;}
 if(c==77){targetSame=true;if(!requestMaintenanceUpdate(A,30)||!takeRequest()||beginManifest("a")||erases)return 1;return 0;}
 if(c==78){hashFault=true;if(stageImage()!=3||imageReady||selectors||rawReads)return 1;return 0;}
 if(c==79){if(!requestMaintenanceUpdate(A,30)||!takeRequest())return 1;validSdk=false;if(beginManifest("a")||erases)return 2;return 0;}
 if(c==80){usb=false;critical=true;scale=true;if(!downloadPowerSafe()||!installPowerSafe(true)||telemetryReads!=2||policyReads||historyReads||scaleReads)return 1;return 0;}
 if(c==81){usb=false;usbBypass=true;if(downloadPowerSafe()||installPowerSafe(true)||telemetryReads||policyReads)return 1;return 0;}
 if(c==82){if(!requestMaintenanceUpdate(A,30))return 1;clockUs+=7200000000LL;char text[384];if(!maintenanceStatus(text,sizeof(text))||!std::strstr(text,"grant=1")||!std::strstr(text,"ttl_ms=4294967295")||!std::strstr(text,"grant_lifetime=usb_transaction"))return 2;usb=false;if(!maintenanceStatus(text,sizeof(text))||!std::strstr(text,"grant=0")||!std::strstr(text,"ttl_ms=0"))return 3;return 0;}
 if(c==83){if(!requestMaintenanceUpdate(A,30))return 1;clockUs+=7200000000LL;boot=&parts[1];if(maintenancePowerSafe()||maintenanceGrant.enabled||telemetryReads)return 2;return 0;}
 if(c==84){if(!request()||!takeRequest())return 1;usb=false;if(beginManifest("a")||!usbBypass||active||telemetryReads)return 2;
  ui.stage=UiStage::Available;statusStage=UiStage::Available;std::memcpy(ui.sha256,A,65);ui.signatureVerified=true;
  if(!approveUpdate(A)||!takeRequest()||usbBypass||!active||telemetryReads!=2||policyReads||historyReads)return 3;return 0;}
 return 999;
}
static int model(){using namespace MosaicoOtaMaintenance;Grant g;if(g.enabled||g.check(0,true,1,1,1))return 1;
 if(!g.start(A,30,0,1,1)||!g.check(29999999,true,1,1,1)||!g.check(7200000000LL,true,1,1,1)||!g.enabled)return 2;
 if(!g.start(A,600,100,1,1)||g.check(99,true,1,1,1))return 3;
 if(!g.start(A,600,INT64_MAX-1,1,1)||g.start(A,30,-1,1,1))return 4;
 if(!g.start(A,30,0,1,1)||g.check(1,true,2,1,1))return 5;
 if(!g.start(A,30,0,1,1)||g.check(1,true,1,2,1))return 6;
 Hal::MaintenancePowerSnapshot s{true,3900,2832,0x26,65,65,-130,130,1};if(!powerSafe(s,2000001)||powerSafe(s,2000002))return 7;
 s.voltageMv=4350;s.temperatureDeciK=3131;s.operationStatus=0xa6;if(!powerSafe(s,1))return 8;s.operationStatus=0x86;if(powerSafe(s,1))return 9;
 s.operationStatus=0x26;s.voltageMv=4351;if(powerSafe(s,1))return 10;s.voltageMv=3900;s.temperatureDeciK=2831;if(powerSafe(s,1))return 11;
 s.temperatureDeciK=3000;s.sampledUs=0;if(powerSafe(s,1))return 13;
 if(!refreshDue(false,0,0,66022,0)||refreshDue(false,0,0,63021,0)||!refreshDue(false,1000000,0,1,0))return 12;
 return 0;}
extern "C" volatile int test_results[86];volatile int test_results[86];
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
extern "C" void _start(){for(int i=0;i<85;++i)test_results[i]=run_case(i);test_results[85]=model();test_done();}
'''
class MaintenanceTests(unittest.TestCase):
 def test_actual_chain(self):
  signatures=['bool gaugeSafe()', 'bool activeUsbPower()', 'bool downloadPowerSafe()', 'bool automaticPowerSafe(', 'bool installPowerSafe(', 'bool maintenancePowerSafe(', 'void failLocked(', 'bool reject(', 'bool lowerHex(', 'void hashHex(', 'void revokeForNormalRequest()', 'bool requestLocked(', 'bool request()', 'bool maintenanceDenied(', 'bool requestMaintenanceUpdate(', 'bool requestMaintenanceReboot(', 'bool takeRequest()', 'bool beginManifest(', 'bool writeChunk(', 'bool finishDownload()', 'bool installVerified()', 'bool rebootWithFreshPower()', 'void fail(', 'bool approveUpdate(', 'bool approveInstall(', 'bool requestReboot()', 'void deferUpdate()', 'void processLocalRequests()', 'bool maintenanceStatus(']
  support=base.MOCKS.split('static Hal hal;',1)[0]+base.RV_SUPPORT.split('static esp_partition_t imageParts',1)[0]
  # Mock journal only; production call sequence and selector boundary remain actual.
  support+='extern \"C\" int64_t __divdi3(int64_t a,int64_t b){bool n=(a<0)!=(b<0);uint64_t q=__udivdi3(a<0?uint64_t(-a):uint64_t(a),b<0?uint64_t(-b):uint64_t(b));return n?-int64_t(q):int64_t(q);}'
  code=FIXTURE+'bool recordAttempt(const InstallPowerEvidence&){++journals;if(expireAtJournal)clockUs+=600000000;return true;}\n'+''.join(function(x) for x in signatures)+CASES+support
  RUN.mkdir(parents=True,exist_ok=True)
  with tempfile.TemporaryDirectory(prefix='.ota-maintenance-',dir=ROOT) as td:
   td=Path(td);headers=dict(base.HEADERS)
   headers['cstring']+='\nnamespace std { inline const char* strstr(const char* s,const char* p){for(;*s;++s){const char*a=s;const char*b=p;while(*a&&*b&&*a==*b){++a;++b;}if(!*b)return s;}return nullptr;} }\n'
   for n,s in headers.items():p=td/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(s)
   src=td/'chain.cpp';src.write_text(code);elf=RUN/'chain.elf'
   args=[str(base.CLANG),'--target=riscv32-unknown-elf','-march=rv32im','-mabi=ilp32','-std=c++20','-O1','-Wno-missing-multilib','-ffunction-sections','-fdata-sections','-fno-builtin','-fno-exceptions','-fno-rtti','-nostdinc++','-nostdlib','-I'+str(td),'-I'+str(ROOT/'main/ota'),str(src),'--ld-path='+str(base.CLANG.parent/'ld.lld.exe'),'-Wl,--gc-sections','-Wl,-e,_start','-Wl,-Ttext=0x10000','-o',str(elf)]
   result=subprocess.run(args,capture_output=True,text=True,env=dict(os.environ,TEMP=str(RUN),TMP=str(RUN)));(RUN/'compile.log').write_text(result.stdout+result.stderr);self.assertEqual(result.returncode,0,str(RUN/'compile.log'))
  sys.path.insert(0,str(ROOT/'.artifacts/tools/unicorn-2.1.4-private-001/package'))
  from unicorn import Uc,UC_ARCH_RISCV,UC_MODE_RISCV32
  from unicorn.riscv_const import UC_RISCV_REG_SP,UC_RISCV_REG_PC
  raw=elf.read_bytes();entry=struct.unpack_from('<I',raw,24)[0];phoff=struct.unpack_from('<I',raw,28)[0];phsize,phnum=struct.unpack_from('<HH',raw,42);uc=Uc(UC_ARCH_RISCV,UC_MODE_RISCV32);uc.mem_map(0,32*1024*1024)
  for i in range(phnum):
   kind,off,addr,_,n,_,_,_=struct.unpack_from('<8I',raw,phoff+i*phsize)
   if kind==1:uc.mem_write(addr,raw[off:off+n])
  shoff=struct.unpack_from('<I',raw,32)[0];shsize,shnum=struct.unpack_from('<HH',raw,46);symbols={}
  for i in range(shnum):
   sh=struct.unpack_from('<10I',raw,shoff+i*shsize)
   if sh[1]!=2:continue
   strings=struct.unpack_from('<10I',raw,shoff+sh[6]*shsize);text=raw[strings[4]:strings[4]+strings[5]]
   for pos in range(sh[4],sh[4]+sh[5],sh[9]):
    name,value,_,_,_,_=struct.unpack_from('<IIIBBH',raw,pos);symbols[text[name:].split(b'\0',1)[0].decode()]=value
  uc.reg_write(UC_RISCV_REG_SP,0x1fffff0);uc.emu_start(entry,symbols['test_done'],timeout=20000000,count=100000000)
  values=struct.unpack('<86i',bytes(uc.mem_read(symbols['test_results'],344)))
  (RUN/'execution.log').write_text('Actual extracted OTA functions; mocked SDK/PSA/signature result/locks/raw provider. Not firmware or hardware validation.\n'+f'ELF_SHA256={hashlib.sha256(raw).hexdigest()}\n'+repr(values)+'\n')
  self.assertEqual(uc.reg_read(UC_RISCV_REG_PC),symbols['test_done']);self.assertEqual(values,(0,)*86)
 def test_preserved_security_boundaries(self):
  manifest=function('bool beginManifest(')
  self.assertLess(manifest.index('validateDescriptor('),manifest.index('maintenanceGrant.matches'))
  self.assertLess(manifest.index('maintenanceGrant.matches'),manifest.index('esp_ota_begin('))
  for name in ('downloadPowerSafe','automaticPowerSafe','installPowerSafe'):
   body=function('bool '+name+'(');self.assertIn('activeUsbPower()',body)
   self.assertLess(body.index('activeUsbPower()'),body.index('physicalBatteryTelemetry(true).native'))
   self.assertNotIn('GaugeBootReloadStatus',body);self.assertNotIn('gaugeScaleTrialActive',body)
  self.assertNotIn('nvs_', (ROOT/'main/ota/mosaico_ota_maintenance_model.h').read_text())
  self.assertIn('90000000',CPP);self.assertIn('20000000',CPP)
  power=function('bool maintenancePowerSafe(')
  for forbidden in ('GetHAL','MaintenancePowerResult','powerSafe(', 'vTaskDelay','maintenanceGrant.start'):
   self.assertNotIn(forbidden,power)
  self.assertIn('maintenanceGrant.check',power);self.assertIn('currentReady()',power)
  self.assertIn('usb_power_maintenance_no_gauge=1',CPP)
  admission=CPP.split('struct StorageAdmission {',1)[1].split('\n};',1)[0]
  self.assertIn('std::try_to_lock',admission);self.assertIn('held.owns_lock()&&storageLeaseDepth==0',admission)
  self.assertNotIn('GetHAL',admission)
if __name__=='__main__':unittest.main()

