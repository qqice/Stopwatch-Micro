"""Actual extracted health/barrier RV32 tests; mocked SDK/RTOS, no device/build.
The candidate sequences startup; this does not prove the observed boot cause.
"""
from pathlib import Path
import hashlib, re, unittest, subprocess
import test_microlink_peer_probe as rv32
ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/'.artifacts/private/mosaico/ota-health-startup-tests-001'

PREFIX=r'''
typedef unsigned uint32_t;typedef unsigned short uint16_t;typedef signed char int8_t;typedef unsigned char uint8_t;
typedef int int32_t;typedef long long int64_t;typedef unsigned size_t;
extern "C" long long __divdi3(long long a,long long b){bool negative=(a<0)!=(b<0);unsigned long long x=a<0?-a:a,y=b<0?-b:b,q=0,r=0;for(int i=63;i>=0;--i){r=(r<<1)|((x>>i)&1);if(r>=y){r-=y;q|=1ULL<<i;}}return negative?-static_cast<long long>(q):static_cast<long long>(q);}
namespace std {
 struct mutex{};template<class T>struct lock_guard{lock_guard(T&){};};
 template<class T>struct atomic{T v;atomic(T value):v(value){}T load()const{return v;}void store(T value){v=value;}T exchange(T value){auto old=v;v=value;return old;}};
 int snprintf(char* out,size_t n,const char*,...){if(n)out[0]=0;return 0;}
 int strcmp(const char* a,const char* b){for(;*a&&*a==*b;++a,++b){}return *a-*b;}
 char* strcpy(char* a,const char* b){char* r=a;while((*a++=*b++)){}return r;}
 void* memcpy(void* a,const void* b,size_t n){for(unsigned i=0;i<n;++i)((char*)a)[i]=((const char*)b)[i];return a;}
}
extern "C" void* memset(void* a,int v,size_t n){for(unsigned i=0;i<n;++i)((char*)a)[i]=v;return a;}
extern "C" void* memcpy(void* a,const void* b,size_t n){return std::memcpy(a,b,n);}
typedef int portMUX_TYPE;static int cacheLocks,bad;
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL(p) (++cacheLocks)
#define portEXIT_CRITICAL(p) (--cacheLocks)
#define ESP_OK 0
#define ESP_OTA_IMG_PENDING_VERIFY 1
#define ESP_OTA_IMG_VALID 2
#define NVS_READONLY 0
#define MALLOC_CAP_INTERNAL 1
#define MALLOC_CAP_8BIT 2
#define MALLOC_CAP_SPIRAM 4
#define pdTRUE 1
#define pdMS_TO_TICKS(x) (x)
typedef int esp_err_t;typedef int esp_ota_img_states_t;typedef int nvs_handle_t;typedef int esp_timer_handle_t;
struct esp_partition_t{int subtype;};struct esp_app_desc_t{char version[32];};
struct esp_timer_create_args_t{void(*callback)(void*);const char* name;};
static int64_t clockUs=100000;static esp_partition_t partition{0};static esp_app_desc_t descriptor{};
static bool noPartition;static int actualState=1,stateError,markError,createError,startError;
static int timerCalls,sdkReads,runningReads,nvsCalls,markCalls,timerStarts,timerStops,rollbackCalls,restarts,activityCalls;
static int halCalls,frameCalls,internalCalls,psramCalls,integrityCalls,probeOrder;
static bool halI2c=true,halDisplay=true,halButtons=true,halTouch=true,integrity=true;
static unsigned frameValue=1,internalValue=40000,psramValue=1000000;static int64_t integrityLatency;
static int64_t esp_timer_get_time(){++timerCalls;return clockUs;}
static const esp_app_desc_t* esp_app_get_description(){return &descriptor;}
static const esp_partition_t* esp_ota_get_running_partition(){++runningReads;return noPartition?nullptr:&partition;}
static int esp_ota_get_state_partition(const esp_partition_t*,int* out){++sdkReads;if(!stateError)*out=actualState;return stateError;}
static int slot(const esp_partition_t* p){return p?p->subtype:-1;}
static int nvs_open(const char*,int,int*){++nvsCalls;return 1;}
static int nvs_get_u8(int,const char*,unsigned char*){return 1;}
static int nvs_get_i8(int,const char*,int8_t*){return 1;}
static int nvs_get_str(int,const char*,char*,size_t*){return 1;}
static void nvs_close(int){}
static bool lowerHex(const char*){return false;}
static int esp_timer_create(esp_timer_create_args_t* a,int* h){if(!a->callback)++bad;*h=1;return createError;}
static int esp_timer_start_once(int,int64_t duration){++timerStarts;if(duration!=90000000)++bad;return startError;}
static void esp_timer_stop(int){++timerStops;}
static int esp_ota_mark_app_valid_cancel_rollback(){++markCalls;return markError;}
static int esp_ota_mark_app_invalid_rollback_and_reboot(){++rollbackCalls;return 1;}
static void esp_restart(){++restarts;}
namespace StandbySleep{static void otaActivity(){++activityCalls;}}
struct Hal{struct Diagnostics{bool i2c,display,buttons,touch;};Diagnostics diagnostics(){++halCalls;probeOrder=probeOrder*10+1;return {halI2c,halDisplay,halButtons,halTouch};}};
static Hal hal;static Hal& GetHAL(){return hal;}
static unsigned GetDisplayFrameCount(){++frameCalls;probeOrder=probeOrder*10+2;return frameValue;}
static unsigned heap_caps_get_free_size(unsigned caps){if(caps==3){++internalCalls;probeOrder=probeOrder*10+3;return internalValue;}if(caps!=4)++bad;++psramCalls;probeOrder=probeOrder*10+4;return psramValue;}
static bool heap_caps_check_integrity_all(bool noisy){if(noisy)++bad;++integrityCalls;probeOrder=probeOrder*10+5;clockUs+=integrityLatency;return integrity;}
'''

CASES=r'''
static void reset(){bad=cacheLocks=timerCalls=sdkReads=runningReads=nvsCalls=markCalls=timerStarts=timerStops=rollbackCalls=restarts=activityCalls=0;
 halCalls=frameCalls=internalCalls=psramCalls=integrityCalls=probeOrder=0;clockUs=100000;actualState=1;stateError=markError=createError=startError=0;noPartition=false;
 halI2c=halDisplay=halButtons=halTouch=integrity=true;frameValue=1;internalValue=40000;psramValue=1000000;integrityLatency=0;
 using namespace MosaicoOta;ui={};autoInstall.store(false);verified.store(false);healthInitialized=false;healthySince=lastLoop=lastCheck=0;healthyLoops=0;healthWorking={};healthCache={};bootPending.store(false);preferenceLoaded=false;deadlineTimer=0;}
static void poll(int64_t now,bool ready){clockUs=now;MosaicoOta::healthPoll(ready);}
static int run_case(int id){reset();using namespace MosaicoOta;
 if(id==0){healthPoll(false);auto h=healthCache;return bootPending.load()&&h.pending&&h.sdkRead&&h.sdkState==1&&h.sdkSlot==0&&h.evaluated==1&&!h.passed&&!halCalls&&timerStarts==1?0:1;}
 if(id==1){healthPoll(false);poll(2000000,false);return !healthyLoops&&!halCalls&&healthCache.evaluated==1&&!healthCache.passed?0:2;}
 if(id>=2&&id<=9){healthPoll(false);if(id==2)halI2c=false;if(id==3)halDisplay=false;if(id==4)halButtons=false;if(id==5)halTouch=false;if(id==6)frameValue=0;if(id==7)internalValue=32768;if(id==8)psramValue=0;if(id==9)integrity=false;
  poll(2000000,true);unsigned eval=id<=5?5:id==6?13:id==7?29:id==8?61:125;int order=id<=5?1:id==6?12:id==7?123:id==8?1234:12345;
  if(healthCache.evaluated!=eval||probeOrder!=order||healthySince||healthyLoops||markCalls)return 3;
  if(id<=5&&(healthCache.frames||healthCache.internalFree||healthCache.psramFree))return 4;
  if(id==7&&healthCache.internalFree!=32768)return 5;return bad?6:0;}
 if(id==10){healthPoll(false);poll(2000000,true);int hc=halCalls;poll(2500000,true);if(halCalls!=hc||healthCache.evaluated!=127||healthCache.probeUs!=2000000||healthCache.internalFree!=40000)return 7;poll(4000001,true);if(healthCache.maxLoopGapUs!=1500001||healthyLoops!=1||healthySince!=4000001)return 8;return 0;}
 if(id==11||id==12){healthPoll(false);if(id==12)markError=277;
  for(int i=0;i<20;++i){probeOrder=0;poll(2000000+int64_t(i)*1000000,true);if(markCalls)return 9;}
  poll(22000000,true);if(markCalls!=1||healthCache.markAttempts!=1||healthCache.sdkState!=1||sdkReads!=1||healthCache.markError!=markError)return 10;
  if(id==11){if(bootPending.load()||!healthCache.markSucceeded||timerStops!=1)return 11;}
  else {if(!bootPending.load()||healthCache.markSucceeded||timerStops)return 12;markError=0;poll(23000000,true);if(markCalls!=2||!healthCache.markSucceeded||bootPending.load())return 13;}
  return bad?14:0;}
 if(id==13||id==14){if(id==13)createError=278;else startError=279;healthPoll(false);if(rollbackCalls!=1||restarts!=1||markCalls)return 15;if(id==13&&timerStarts)return 16;return 0;}
 if(id==15){actualState=2;healthPoll(true);if(bootPending.load()||healthCache.sdkState!=2||timerStarts||halCalls||markCalls)return 17;return 0;}
 if(id==16||id==17){stateError=id==16?280:0;noPartition=id==17;healthPoll(true);if(bootPending.load()||timerStarts||halCalls||healthCache.sdkState!=-1)return 18;if(id==17&&sdkReads)return 19;return 0;}
 if(id==18){healthPoll(false);poll(2000000,true);integrityLatency=1600000;poll(3000000,true);int64_t next=clockUs;probeOrder=0;poll(next,true);if(healthCache.loopGapUs!=1600000||healthyLoops!=1||healthCache.healthySinceUs!=next||!healthCache.pending)return 20;return 0;}
 if(id==19){healthPoll(false);poll(2000000,true);int oldTimers=timerCalls,oldSdk=sdkReads,oldHal=halCalls,oldIntegrity=integrityCalls;HealthDiagnostic a,b;copyHealthDiagnostic(a);copyHealthDiagnostic(b);
  if(a.sampledUs!=b.sampledUs||cacheLocks||timerCalls!=oldTimers||sdkReads!=oldSdk||halCalls!=oldHal||integrityCalls!=oldIntegrity)return 21;return 0;}
 if(id==20){healthPoll(false);healthTimeout(nullptr);if(bootPending.load()||!healthCache.timedOut||healthCache.markSucceeded||rollbackCalls!=1)return 22;return 0;}
 if(id==21){healthPoll(false);internalValue=32769;poll(2000000,true);if(healthCache.evaluated!=125||healthCache.passed!=125||healthCache.internalFree!=32769)return 23;return 0;}
 if(id==22){healthPoll(false);internalValue=32768;poll(2000000,true);poll(2010000,true);if(healthCache.probeUs!=2000000||healthCache.internalFree!=32768||internalCalls!=1||(healthCache.passed&16)||!(healthCache.evaluated&16)||healthCache.psramFree||integrityCalls)return 24;return 0;}
 if(id==23){healthPoll(false);poll(2000000,true);poll(3500000,true);if(healthySince!=2000000||healthyLoops!=2||healthCache.loopGapUs!=1500000||!(healthCache.passed&2))return 25;return 0;}
 return 99;}
extern "C" {volatile int test_results[24]={};}
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
extern "C" void _start(){for(int i=0;i<24;++i)test_results[i]=run_case(i);test_done();}
'''


def health_fixture():
    source=(ROOT/'main/ota/mosaico_ota.cpp').read_text()
    header=(ROOT/'main/ota/mosaico_ota.h').read_text()
    decl='struct HealthDiagnostic {'+header.split('struct HealthDiagnostic {',1)[1].split('\n};',1)[0]+'\n};\n'
    state=source[source.index('HealthDiagnostic healthWorking'):source.index('\nbool gaugeSafe()')]
    setup=r'''
namespace MosaicoOta {
std::mutex healthLock,snapshotLock;
std::atomic<bool> bootPending{false},autoInstall{false},verified{false};
esp_timer_handle_t deadlineTimer=0;bool healthInitialized=false,preferenceLoaded=false;
int64_t healthySince=0,lastLoop=0,lastCheck=0;uint32_t healthyLoops=0;
struct UI{char currentVersion[32],signatureShort[17],targetVersion[32],sha256[65];int8_t currentSlot,targetSlot;unsigned revision,progressBasisPoints;bool automaticInstall,signatureVerified,imageVerified;} ui;
enum class UiStage{BootChecking,Complete};static void publish(UiStage){}
'''
    return PREFIX+setup+decl+state+rv32.function(source,'void healthTimeout(')+rv32.function(source,'void copyHealthDiagnostic(')+rv32.function(source,'void healthPoll(')+'}\n'+CASES


def barrier_fixture(mode):
    source=(ROOT/'main/host/network_quota.cpp').read_text()
    body=source.split('void NetworkQuota::run()\n{',1)[1].split('    setCpu(CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ);',1)[0]
    return f'#define MODE {mode}\n'+r'''
#define MOSAICO_BOARD 1
#define pdTRUE 1
#define pdMS_TO_TICKS(x) (x)
static int waits,probes,heavy,bad;static bool pending,markSuccess;
extern "C" {volatile int test_results[1]={99};}
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
namespace MosaicoOta {struct HealthDiagnostic{bool markSucceeded;};bool healthPending(){return pending;}void copyHealthDiagnostic(HealthDiagnostic& h){++probes;h.markSucceeded=markSuccess;}}
static void ulTaskNotifyTake(int clear,unsigned ms){++waits;if(clear!=1||ms!=20)++bad;
 if(MODE==0){markSuccess=true;pending=false;}
 if(MODE==1)pending=false;
 if((MODE==1||MODE==2)&&waits==3){test_results[0]=!heavy&&!markSuccess&&!bad?0:1;test_done();}
}
struct NetworkQuota{void run(){
'''+body+r'''
 ++heavy;}};
extern "C" void _start(){pending=MODE!=3;markSuccess=false;NetworkQuota q;q.run();test_results[0]=(MODE==0?heavy==1&&waits==1&&probes==2:MODE==3&&heavy==1&&!waits&&!probes)?0:2;test_done();}
'''.replace('MODE',str(mode))


def execute(fixture,label,count):
    old_fixture,old_run=rv32.fixture,rv32.RUN
    try:
        rv32.fixture,rv32.RUN=lambda _:fixture(),RUN
        result=rv32.execute('',label,count)
        (RUN/(label+'-execution.log')).write_text('Actual extracted production health or pending startup barrier; mocked SDK/RTOS.\n'+
            f'ELF_SHA256={hashlib.sha256((RUN/(label+".elf")).read_bytes()).hexdigest()}\nresults={result}\n')
        return result
    finally:rv32.fixture,rv32.RUN=old_fixture,old_run


def equivalence_fixture():
    baseline=subprocess.check_output(['git','show','6258a3891d5356f308500e8946d480a461a6703a:main/ota/mosaico_ota.cpp'],cwd=ROOT,text=True)
    reference=rv32.function(baseline,'void healthPoll(').replace('void healthPoll(', 'void healthPollReference(',1)
    prefix=health_fixture().split('static void reset(){',1)[0]
    reset=rv32.function(CASES,'static void reset(')
    return prefix+'namespace MosaicoOta{\n'+reference+'}\n'+reset+r'''
struct Result{int64_t values[25];};
static Result sequence(int scenario,bool reference){reset();using namespace MosaicoOta;
 if(scenario==1)halI2c=false;if(scenario==2)halDisplay=false;if(scenario==3)halButtons=false;if(scenario==4)halTouch=false;
 if(scenario==5)frameValue=0;if(scenario==6)internalValue=32768;if(scenario==7)psramValue=0;if(scenario==8)integrity=false;
 if(scenario==9)markError=277;if(scenario==10)integrityLatency=1600000;if(scenario==11)createError=278;if(scenario==12)startError=279;
 if(scenario==13)actualState=2;if(scenario==14)stateError=280;if(scenario==15)noPartition=true;if(scenario==19)internalValue=32769;
 auto run=reference?healthPollReference:healthPoll;run(false);
 for(int i=0;i<(scenario==20?19:26);++i){clockUs=2000000+int64_t(i)*(scenario==17?1500001:scenario==18?500000:1000000);probeOrder=0;
  bool ready=scenario!=16&&(scenario!=21||i%3!=0);run(ready);}
 return {{bootPending.load(),healthInitialized,healthySince,lastLoop,lastCheck,healthyLoops,timerCalls,sdkReads,runningReads,nvsCalls,
  markCalls,timerStarts,timerStops,rollbackCalls,restarts,activityCalls,halCalls,frameCalls,internalCalls,psramCalls,integrityCalls,
  ui.progressBasisPoints,ui.revision,probeOrder,bad}};}
extern "C" {volatile int test_results[22]={};}
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
extern "C" void _start(){for(int id=0;id<22;++id){auto original=sequence(id,true),current=sequence(id,false);int mismatch=0;
 for(int field=0;field<25;++field)if(original.values[field]!=current.values[field]){mismatch=field+1;break;}test_results[id]=mismatch;}test_done();}
'''


class StartupHealthTests(unittest.TestCase):
    def test_actual_health_all_branches(self):self.assertEqual(execute(health_fixture,'health',24),(0,)*24)
    def test_actual_pending_barrier(self):
        for mode in range(4):
            with self.subTest(mode=mode):self.assertEqual(execute(lambda:barrier_fixture(mode),'barrier'+str(mode),1),(0,))
    def test_original_health_gate_probe_and_state_equivalence(self):
        self.assertEqual(execute(equivalence_fixture,'original-equivalence',22),(0,)*22)
    def test_source_contracts(self):
        ota=(ROOT/'main/ota/mosaico_ota.cpp').read_text();network=(ROOT/'main/host/network_quota.cpp').read_text()
        getter=rv32.function(ota,'void copyHealthDiagnostic(')
        for forbidden in ('heap_','esp_','GetHAL','GetDisplay','nvs_','healthLock','network'):self.assertNotIn(forbidden,getter)
        poll=rv32.function(ota,'void healthPoll(')
        for fixed in ('90000000','1500000','1000000','32768','20000000','healthyLoops >= 20'):self.assertIn(fixed,poll)
        self.assertEqual(poll.count('heap_caps_get_free_size('),2)
        self.assertEqual(poll.count('heap_caps_check_integrity_all(false)'),1)
        self.assertEqual(poll.count('esp_ota_get_state_partition('),1)
        self.assertNotIn('cachedRunning',poll)
        run=network.split('void NetworkQuota::run()\n{',1)[1].split('\nbool NetworkQuota::requestJson(',1)[0]
        self.assertLess(run.index('copyHealthDiagnostic('),run.index('esp_netif_init('))
        self.assertLess(run.index('copyHealthDiagnostic('),run.index('esp_event_loop_create_default('))
        self.assertNotIn('_configured',run.split('setCpu(CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ);',1)[0])
        self.assertIn('ulTaskNotifyTake(pdTRUE,pdMS_TO_TICKS(20));',run)
        self.assertIn('DBG OTA_HEALTH version=',(ROOT/'main/debug/serial_debug.cpp').read_text())
        baseline=subprocess.check_output(['git','show','6258a3891d5356f308500e8946d480a461a6703a:main/host/network_quota.cpp'],cwd=ROOT,text=True)
        self.assertEqual(rv32.function(network,'void NetworkQuota::task('),rv32.function(baseline,'void NetworkQuota::task('))
        baseline_ota=subprocess.check_output(['git','show','6258a3891d5356f308500e8946d480a461a6703a:main/ota/mosaico_ota.cpp'],cwd=ROOT,text=True)
        self.assertEqual(rv32.function(ota,'void baseStatus('),rv32.function(baseline_ota,'void baseStatus('))

if __name__=='__main__':unittest.main(verbosity=2)
