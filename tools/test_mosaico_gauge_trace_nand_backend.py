"""Production bridge conditionals with constexpr boundary adapters; no SDK/device."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/'main/host/gauge_trace_nand_backend.cpp').read_text()
COMPILERS=list(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))

def fixture():
    code=SOURCE.split('namespace GaugeDischargeTrace::NandBackend {\nnamespace {',1)[1].split('void status(EmitLine',1)[0]
    code=code.replace('\n}\n\nbool busy()', '\n\nbool busy()')
    code=code.replace('std::mutex','MockMutex').replace('std::unique_lock<MockMutex>','MockLock').replace('std::lock_guard<MockMutex>','MockLock')
    code=code.replace('std::try_to_lock','0').replace('std::atomic_bool','Atomic<bool>').replace('std::atomic<','Atomic<')
    code=code.replace('constexpr const char*','inline static constexpr const char*')
    code=code.replace('void* memory=heap_caps_calloc(1,sizeof(Blob),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);','Blob* memory=allocate();')
    code=code.replace('auto* scratch=new(memory) Blob;', 'auto* scratch=memory;')
    code=code.replace('auto* copy=new(memory) Blob(snapshot);','auto* copy=memory;*copy=snapshot;')
    code=code.replace('snapshot->~Blob();','').replace('scratch->~Blob();','').replace('copy->~Blob();','').replace('heap_caps_free(memory)','release(memory)')
    code=code.replace('void* memory)', 'Blob* memory)').replace('static_cast<Blob*>(memory)','memory')
    code=code.replace('void* context)', 'bool* context)').replace('*static_cast<bool*>(context)','*context')
    code=code.replace('MosaicoNandStore::formatConfirm(true,progress,&enrolled)','storeFormat(true,&enrolled)').replace('MosaicoNandStore::load(', 'storeLoad(').replace('MosaicoNandStore::save(', 'storeSave(')
    code=code.replace('xTaskCreateWithCaps(formatWorker,','queueTask(')
    code=code.replace('MosaicoOta::busy()', 'otaBusy').replace('MosaicoOta::healthPending()', 'otaPending')
    code=code.replace('GetNetworkQuota().powerStats()', 'this->power').replace('GetCodexMicroBle().diagnostics()', 'this->ble').replace('GetHAL().batteryTelemetry(', 'batteryTelemetry(')
    code=re.sub(r'^(bool|void) (\w+)\(', r'constexpr \1 \2(',code,flags=re.M)
    return r'''
#include "main/host/gauge_discharge_trace_model.h"
#include "main/hal/mosaico/mosaico_nand_store_model.h"
#include <type_traits>
using namespace GaugeDischargeTrace;
using esp_err_t=int;using nvs_handle_t=int;
constexpr int ESP_OK=0,ESP_ERR_NVS_NOT_FOUND=1,ESP_ERR_INVALID_STATE=2,ESP_ERR_NO_MEM=3,ESP_ERR_INVALID_CRC=4;
constexpr int NVS_READONLY=0,NVS_READWRITE=1,MALLOC_CAP_INTERNAL=1,MALLOC_CAP_8BIT=2,pdPASS=1;
constexpr int ESP_OTA_IMG_UNDEFINED=0,ESP_OTA_IMG_VALID=1;using esp_ota_img_states_t=int;
constexpr int pdMS_TO_TICKS(int ms){return ms;}
namespace MosaicoNandStore{
using Payload=Model::Payload;
enum class Stage { Admission, Allocation, Bus, Pins, Device, Identity, Init, Geometry, Erase, Record, Reopen, Complete };
struct Result{int error=0,cleanupError=0;Stage stage=Stage::Admission;Model::Result record{};};
}
template<class T>struct Atomic{
 T value;constexpr Atomic(T v):value(v){}constexpr T load()const{return value;}constexpr void store(T v){value=v;}
};
struct MockMutex{bool available=true;};
struct MockLock{bool held;constexpr MockLock(MockMutex& m,int=0):held(m.available){}constexpr explicit operator bool()const{return held;}};
struct Fixture{
 bool otaBusy=false,otaPending=false,validImage=true,allocationFail=false,taskFail=false,joined=true;
 int freshReads=0,cachedReads=0,allocations=0,frees=0,delays=0,feeds=0,wdtDeletes=0,taskDeletes=0;
 int nvsCalls=0,failNvs=0,diskPolicy=0,stagedPolicy=0,formats=0,saves=0,loads=0,failStore=0;
 int loadError=0,loadCleanup=0;MosaicoNandStore::Stage loadStage=MosaicoNandStore::Stage::Complete;
 bool badReadback=false,ackLost=false,wrong=false,crcDamage=false;
 Blob disk{};Blob* queued=nullptr;
 struct Power{bool locked=true,wifiRunning=false;int phase=2;}power;
 struct Ble{bool advertising=false,connected=false;}ble;
 struct Telemetry{bool valid=true,capacityValid=true,temperatureValid=true,nominalConfigured=true;
  uint16_t voltageMv=4000,temperatureDeciK=2982,designMah=65,fullMah=65,operationStatus=0xa6;}telemetry;
 constexpr Telemetry batteryTelemetry(bool fresh){if(fresh)++freshReads;else ++cachedReads;return telemetry;}
 constexpr const int* esp_ota_get_running_partition(){return validImage?&delays:nullptr;}
 constexpr int esp_ota_get_state_partition(const int*,int* out){*out=validImage?1:0;return 0;}
 constexpr bool nvsTick(){return ++nvsCalls!=failNvs;}
 constexpr int nvs_open(const char*,int,int* h){*h=1;return nvsTick()?0:99;}
 constexpr int nvs_get_u8(int,const char*,uint8_t* out){if(!nvsTick())return 99;if(!diskPolicy)return ESP_ERR_NVS_NOT_FOUND;*out=uint8_t(badReadback?9:diskPolicy);return 0;}
 constexpr int nvs_set_u8(int,const char*,uint8_t value){if(!nvsTick())return 99;stagedPolicy=value;return 0;}
 constexpr int nvs_commit(int){if(!nvsTick())return 99;diskPolicy=stagedPolicy;return ackLost?99:0;}
 constexpr void nvs_close(int){}
 constexpr Blob* allocate(){++allocations;return allocationFail?nullptr:new Blob;}
 constexpr void release(Blob* b){++frees;delete b;}
 constexpr int queueTask(const char*,int stack,Blob* ptr,int priority,void*,int caps){
  if(stack!=8192||priority!=1||caps!=(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT))wrong=true;
  if(taskFail)return 0;queued=ptr;return 1;
 }
 constexpr int esp_task_wdt_add(void*){return joined?0:1;}
 constexpr int esp_task_wdt_reset(){++feeds;if(!joined)wrong=true;return 0;}
 constexpr int esp_task_wdt_delete(void*){++wdtDeletes;if(!joined)wrong=true;return 0;}
 constexpr void vTaskDelay(int ms){delays+=ms;}
 constexpr void vTaskDeleteWithCaps(void*){++taskDeletes;}
 constexpr MosaicoNandStore::Result result(int operation){
  MosaicoNandStore::Result r;r.record.generation=7;
  if(failStore==operation)r.error=55;if(failStore==operation+10)r.cleanupError=56;
  return r;
 }
 constexpr MosaicoNandStore::Result storeFormat(bool authorized,bool* enrolled){
  ++formats;if(!authorized||diskPolicy!=1||freshReads!=2||delays<1000)wrong=true;
  progress(1024,1024,enrolled);return result(1);
 }
 constexpr MosaicoNandStore::Result storeSave(const Blob& b,bool authorized){
  ++saves;if(!authorized)wrong=true;disk=b;return result(2);
 }
 constexpr MosaicoNandStore::Result storeLoad(Blob& b,bool authorized){
  ++loads;if(!authorized)wrong=true;b=disk;if(crcDamage)b[40]^=1;
  auto r=result(3);r.stage=loadStage;
  if(loadError)r.error=loadError;if(loadCleanup)r.cleanupError=loadCleanup;
  return r;
 }
''' +code+r'''
};
constexpr Blob snapshot(){Model m;m.count=1;return encode(m);}
constexpr bool policies(){
 const Blob original=snapshot();
 for(int p=0;p<5;++p){Fixture f;f.diskPolicy=p;f.disk=original;Blob out{};bool selected=false;
  bool ok=f.load(out,selected);
  if(ok!=(p==2)||selected!=(p!=0)||f.formats||f.freshReads)return false;
  if(p==2&&(out!=original||f.loads!=1))return false;
  const int n=f.nvsCalls;f.save(original,selected);if(f.nvsCalls!=n)return false;
 }
 for(int fail=1;fail<=2;++fail){Fixture f;f.failNvs=fail;Blob out;bool selected=false;
  if(f.load(out,selected)||!selected||f.retryable()||f.loads)return false;
 }
 Fixture blocked;blocked.diskPolicy=2;blocked.disk=original;blocked.telemetry.voltageMv=3299;Blob out;bool selected=false;
 if(blocked.load(out,selected)||!selected||!blocked.retryable()||blocked.loads)return false;
 blocked.telemetry.voltageMv=4000;blocked.power.locked=false;blocked.ble.connected=true;
 if(!blocked.load(out,selected)||blocked.freshReads||blocked.retryable())return false;
 return true;
}
constexpr bool migration(){
 const Blob original=snapshot();
 for(int fail=0;fail<=12;++fail){Fixture f;f.failNvs=fail;
  if(!f.beginFormat(original)){if(f.formats||f.saves)return false;continue;}
  if(!f.busy()||f.formats||f.saves||f.freshReads!=1)return false;
  f.formatWorker(f.queued);
  if(f.busy()||f.wrong||f.allocations!=f.frees||f.taskDeletes!=1||f.wdtDeletes!=1)return false;
  if(f.policy.load()==Fixture::Policy::Nand&&(f.disk!=original||f.formats!=1||f.saves!=1||f.diskPolicy!=2))return false;
  if(f.beginFormat(original))return false;
  if(fail&&f.policy.load()==Fixture::Policy::Nand)return false;
  if(!fail&&(f.policy.load()!=Fixture::Policy::Nand||f.generation.load()!=7))return false;
 }
 for(int failure: {1,2,11,12}){Fixture f;f.failStore=failure;
  if(!f.beginFormat(original))return false;f.formatWorker(f.queued);
  if(f.policy.load()==Fixture::Policy::Nand||f.diskPolicy!=1||f.wrong||f.allocations!=f.frees)return false;
  if((failure==1||failure==11)&&f.saves)return false;
 }
 Fixture lost;lost.ackLost=true;if(!lost.beginFormat(original))return false;lost.formatWorker(lost.queued);
 if(lost.formats||lost.diskPolicy!=1||lost.policy.load()!=Fixture::Policy::Fault)return false;
 Fixture changed;if(!changed.beginFormat(original))return false;changed.telemetry.voltageMv=3800;changed.formatWorker(changed.queued);
 if(changed.formats||changed.diskPolicy||changed.nvsCalls!=2)return false;
 Fixture noWdt;noWdt.joined=false;if(!noWdt.beginFormat(original))return false;noWdt.formatWorker(noWdt.queued);
 return !noWdt.feeds&&!noWdt.wdtDeletes&&!noWdt.wrong&&noWdt.policy.load()==Fixture::Policy::Nand;
}
constexpr bool admissionFaults(){
 const Blob original=snapshot();
 for(int kind=0;kind<15;++kind){Fixture f;
  if(kind==0)f.telemetry.valid=false;if(kind==1)f.telemetry.capacityValid=false;
  if(kind==2)f.telemetry.temperatureValid=false;if(kind==3)f.telemetry.voltageMv=3899;
  if(kind==4)f.telemetry.temperatureDeciK=2831;if(kind==5)f.telemetry.temperatureDeciK=3183;
  if(kind==6)f.telemetry.designMah=300;if(kind==7)f.telemetry.fullMah=64;
  if(kind==8)f.telemetry.operationStatus=0xa2;if(kind==9)f.telemetry.operationStatus|=0x400;
  if(kind==10)f.power.locked=false;if(kind==11)f.power.phase=1;
  if(kind==12)f.ble.advertising=true;if(kind==13)f.otaBusy=true;if(kind==14)f.validImage=false;
  if(f.beginFormat(original)||f.formats||f.allocations)return false;
 }
 Fixture task;task.taskFail=true;if(task.beginFormat(original)||task.busy()||task.allocations!=task.frees)return false;
 Fixture oom;oom.allocationFail=true;oom.diskPolicy=2;Blob out{};bool selected=false;
 if(oom.load(out,selected)||!selected||!oom.retryable()||oom.loads)return false;
 Fixture corrupt;corrupt.diskPolicy=2;corrupt.disk=original;corrupt.crcDamage=true;out[0]=42;
 if(corrupt.load(out,selected)||out[0]!=42||!selected||corrupt.retryable())return false;
 Fixture cleanup;cleanup.diskPolicy=2;cleanup.failStore=13;
 if(cleanup.load(out,selected)||cleanup.retryable()||!selected||out[0]!=42)return false;
 return true;
}
constexpr bool storePreIoRetry(){
 const Blob original=snapshot();
 using Stage=MosaicoNandStore::Stage;
 // Only Allocation/NO_MEM, Init/NO_MEM and Bus/{INVALID_STATE,NO_MEM}, with
 // clean teardown, are transient. Other ECC/init/geometry errors or
 // the Admission cleanup-fence stage stays closed and nonretryable.
 for(int stage=0;stage<=11;++stage)for(int error=0;error<=4;++error)for(int cleanup=0;cleanup<=1;++cleanup){
  Fixture f;f.diskPolicy=2;f.disk=original;f.loadStage=Stage(stage);f.loadError=error;f.loadCleanup=cleanup;
  Blob out{};out[0]=42;bool selected=false;
  bool ok=f.load(out,selected);
  const bool expected=!cleanup&&(((Stage(stage)==Stage::Allocation||Stage(stage)==Stage::Init)&&error==ESP_ERR_NO_MEM)||
   (Stage(stage)==Stage::Bus&&(error==ESP_ERR_INVALID_STATE||error==ESP_ERR_NO_MEM)));
  if(!selected||f.retryable()!=expected||ok!=(!error&&!cleanup))return false;
  if(!ok&&out[0]!=42)return false;
  if(expected){
   const int policyReads=f.nvsCalls;f.loadError=0;f.loadStage=Stage::Complete;
   if(!f.load(out,selected)||!selected||f.retryable()||out!=original||f.nvsCalls!=policyReads)return false;
  }
 }
 return true;
}
static_assert(policies(),"absent-only fallback cached policy real errors closed retryable admission no forced reads");
static_assert(migration(),"asynchronous gate durable pending before erase final verified policy last all NVS NAND cleanup faults");
static_assert(admissionFaults(),"format physical OTA radio task allocation gates CRC and cleanup leave caller unchanged");
static_assert(storePreIoRetry(),"exact clean store resource OOM/busy retries; cleanup CRC IO geometry and admission fence remain terminal");
'''

class BackendTests(unittest.TestCase):
    def test_actual_policy_worker_and_faults(self):
        self.assertTrue(COMPILERS)
        code=fixture().replace('#include <type_traits>','#include <type_traits>\n#include <initializer_list>')
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'bridge.cpp';path.write_text(code)
            result=subprocess.run([str(COMPILERS[0]),'-std=c++20','-fconstexpr-ops-limit=200000000','-fsyntax-only','-I',str(ROOT),str(path)],capture_output=True,text=True)
            log=ROOT/'.artifacts/mosaico/gauge-trace-nand-backend.log';log.parent.mkdir(parents=True,exist_ok=True)
            log.write_text(result.stdout+result.stderr)
            self.assertEqual(result.returncode,0,str(log))

    def test_no_autoformat_or_legacy_erase_and_status_stack(self):
        self.assertNotIn('nvs_erase',SOURCE)
        self.assertNotIn('"trace1"',SOURCE)
        self.assertEqual(SOURCE.count('MosaicoNandStore::formatConfirm('),1)
        self.assertIn('static char line[384];',SOURCE)
        self.assertIn('statusMutex,std::try_to_lock',SOURCE)
        self.assertNotIn('esp_task_wdt_reconfigure',SOURCE)
        self.assertNotIn('esp_task_wdt_deinit',SOURCE)
        status=SOURCE.split('void status(EmitLine',1)[1].split('#else',1)[0]
        self.assertNotIn('selectionMutex',status)
        for forbidden in ('nvs_open(', 'nvs_get_', 'nvs_set_', 'batteryTelemetry(', 'MosaicoNandStore::'):
            self.assertNotIn(forbidden,status)

    def test_root_retryable_load_preserves_unready_and_minute_backoff(self):
        source=(ROOT/'main/host/gauge_discharge_trace.cpp').read_text()
        load=source.split('void load()',1)[1].split('bool save(',1)[0]
        self.assertIn('if(!loaded&&NandBackend::retryable())return;',load)
        self.assertLess(load.index('NandBackend::retryable()'),load.index('corrupt=true'))
        self.assertLess(load.index('NandBackend::retryable()'),load.index('ready=true'))
        service=source.split('void service()',1)[1]
        self.assertIn('if(now<nextLoadAttempt)return;',service)
        self.assertIn('if(!ready){nextLoadAttempt=now+60000;return;}',service)

if __name__=='__main__':unittest.main()
