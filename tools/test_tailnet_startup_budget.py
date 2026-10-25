"""Bounded offline budget/reservation checks. No device, network or SDK changes."""
from pathlib import Path
import subprocess,tempfile,unittest,re
from test_microlink_peer_probe import function
ROOT=Path(__file__).resolve().parents[1]
CXX=Path('C:/Espressif/tools/riscv32-esp-elf/esp-15.2.0_20251204/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe')
class BudgetTests(unittest.TestCase):
 def test_exact_budget_and_real_reservation(self):
  code=r'''
#include "main/host/mosaico_system_log_model.h"
#include "main/hal/mosaico/mosaico_nand_dma_reserve_model.h"
using MosaicoSystemLog::Model::startupBudget;
static_assert(startupBudget(72631,8192)==80823);
static_assert(startupBudget(72631,8192,2112)==82935);
static_assert(startupBudget(72403,8192,2112)==82707);
static_assert(startupBudget(71615,8192,2112)<81920);
static_assert(startupBudget(71616,8192,2112)==81920);
static_assert(startupBudget(81919,0,0)<81920);
static_assert(startupBudget(81920,0,0)==81920);
static_assert(startupBudget(0,SIZE_MAX,SIZE_MAX)==10304);
static_assert(startupBudget(SIZE_MAX,1,1)==SIZE_MAX);
static_assert(startupBudget(SIZE_MAX-2112,0,2112)==SIZE_MAX);
static_assert(startupBudget(123,8193,2113)==123+8192+2112);
static_assert(startupBudget(123,1,2113)==123+1+2112);
static_assert(startupBudget(123,8193,1)==123+8192+1);
template<class T>struct A{T v;constexpr A(T x):v(x){}constexpr T load(std::memory_order)const{return v;}constexpr void store(T x,std::memory_order){v=x;}constexpr T exchange(T x,std::memory_order){T o=v;v=x;return o;}constexpr bool compare_exchange_strong(T&e,T x,std::memory_order){if(v!=e){e=v;return false;}v=x;return true;}};
struct Alloc {alignas(64) std::array<uint8_t,2112> bytes{};bool fail=false,validMemory=true;unsigned allocations=0,validations=0;
 constexpr uint8_t* allocate(size_t,size_t){++allocations;return fail?nullptr:bytes.data();}
 constexpr bool valid(const uint8_t* p,size_t,size_t){++validations;return validMemory&&p==bytes.data();}
 constexpr void release(uint8_t*){}
};
constexpr bool reserve(){MosaicoNandDma::Reservation<A> r{};Alloc a{};
 if(r.isReady())return false;if(!r.prepare(a)||!r.isReady())return false;
 const auto n=a.allocations,v=a.validations;auto credit=[&](){return r.isReady()?MosaicoNandDma::Capacity:0;};
 if(credit()!=2112||a.allocations!=n||a.validations!=v)return false;
 if(r.release(a.bytes.data()+1)||r.isReady()||credit())return false;
 MosaicoNandDma::Reservation<A> f{};Alloc bad{};bad.fail=true;if(f.prepare(bad)||f.isReady())return false;
 MosaicoNandDma::Reservation<A> g{};Alloc invalid{};invalid.validMemory=false;return !g.prepare(invalid)&&!g.isReady();
}
static_assert(reserve());
'''
  with tempfile.TemporaryDirectory(prefix='.tailnet-budget-',dir=ROOT) as td:
   src=Path(td)/'test.cpp';src.write_text(code)
   result=subprocess.run([str(CXX),'-std=c++20','-Werror','-fsyntax-only','-I'+str(ROOT),str(src)],capture_output=True,text=True)
   self.assertEqual(result.returncode,0,result.stdout+result.stderr)
 def test_actual_start_and_getter_contract(self):
  cpp=(ROOT/'main/host/tailscale_transport.cpp').read_text();nand=(ROOT/'main/hal/mosaico/mosaico_nand_store.cpp').read_text()
  self.assertIn('size_t startupInternalReservation(){return dmaReservation.isReady()?MosaicoNandDma::Capacity:0;}',nand)
  for text in ['diagnostic.largest < 16 * 1024','const auto selftest=ml_noise_selftest()','if (!_client) { finish(StartupDecision::InitFailed,ESP_FAIL); return; }','StartupDecision::StartFailed','MosaicoNandStore::startupInternalReservation()']:
   self.assertIn(text,cpp)
  self.assertNotIn('diagnostic.normalizedBudget <',cpp)
  body=cpp.split('TailnetQuota::StartupDiagnostics TailnetQuota::startupDiagnostics() const',1)[1].split('void TailnetQuota::publishStartupDiagnostics',1)[0]
  self.assertIn('portENTER_CRITICAL',body);self.assertIn('portEXIT_CRITICAL',body)
  for forbidden in ['heap_caps','microlink_','start(','ESP_LOG','_key','_host','_path']:self.assertNotIn(forbidden,body)
 def compile_actual(self,code,name):
  directory=ROOT/'artifacts/tailnet-init-cleanup-tests';directory.mkdir(parents=True,exist_ok=True)
  with tempfile.TemporaryDirectory(dir=directory) as td:
   src=Path(td)/(name+'.cpp');src.write_text(code,encoding='utf-8')
   result=subprocess.run([str(CXX),'-std=c++20','-fsyntax-only','-I'+str(ROOT),str(src)],capture_output=True,text=True)
   log=directory/(name+'.log');log.write_text(result.stdout+result.stderr,encoding='utf-8')
   self.assertEqual(result.returncode,0,str(log)+'\n'+result.stderr)
 def test_actual_low_total_reaches_init_and_real_errors_remain_visible(self):
  cpp=(ROOT/'main/host/tailscale_transport.cpp').read_text(encoding='utf-8')
  actual=function(cpp,'void TailnetQuota::start(')
  actual=actual[actual.index('    // Preserve measured free/resident bytes'):actual.rindex('}')]
  actual=actual.replace('MosaicoSystemLog::startupInternalReservation()','8192').replace('MosaicoNandStore::startupInternalReservation()','2112')
  actual=actual.replace('MosaicoSystemLog::Model::startupBudget','startupBudget')
  self.compile_actual(r'''
#define MOSAICO_BOARD 1
#include <cstddef>
#include <cstdint>
#include <initializer_list>
#include "main/host/mosaico_system_log_model.h"
using MosaicoSystemLog::Model::startupBudget;
constexpr int MALLOC_CAP_INTERNAL=1,MALLOC_CAP_8BIT=2,ESP_OK=0,ESP_FAIL=-1,ESP_LOG_ERROR=1;
struct microlink_config_t {const char* auth_key;const char* device_name;bool enable_derp,enable_stun,enable_disco;unsigned max_peers,priority_peer_ip;};
struct Sim {
 enum class StartupDecision{LargestRejected,SelftestFailed,InitFailed,Started,StartFailed};
 struct {size_t actualFree=0,largest=0,normalizedBudget=0,rtcReservation=0,nandReservation=0;} diagnostic;
 unsigned initCalls=0,startCalls=0;bool failInit=false,_pause_requested=false;
 int selftestError=0,startError=0,error=0;StartupDecision decision{};
 size_t freeBytes=69039,largestBytes=38900;int _client=0;const char* _key="not-secret";unsigned _peer=1;
 constexpr void finish(StartupDecision d,int e=0){decision=d;error=e;}
 constexpr size_t heap_caps_get_free_size(int){return freeBytes;}
 constexpr size_t heap_caps_get_largest_free_block(int){return largestBytes;}
 constexpr int ml_noise_selftest(){return selftestError;}
 constexpr void esp_log_level_set(const char*,int){}
 constexpr int microlink_init(const microlink_config_t*){++initCalls;return failInit?0:1;}
 constexpr int microlink_start(int){++startCalls;return startError;}
 constexpr void run(){
'''+actual+r'''
 }
};
constexpr bool cases(){using D=Sim::StartupDecision;
 {Sim s;s.run();if(s.initCalls!=1||s.startCalls!=1||s.decision!=D::Started||s.diagnostic.actualFree!=69039||s.diagnostic.normalizedBudget!=79343)return false;}
 {Sim s;s.failInit=true;s.run();if(s.initCalls!=1||s.startCalls||s.decision!=D::InitFailed||s.error!=ESP_FAIL)return false;}
 {Sim s;s.startError=-12;s.run();if(s.decision!=D::StartFailed||s.error!=-12||!s._pause_requested)return false;}
 {Sim s;s.largestBytes=16383;s.run();if(s.initCalls||s.decision!=D::LargestRejected)return false;}
 {Sim s;s.selftestError=-3;s.run();if(s.initCalls||s.decision!=D::SelftestFailed||s.error!=-3)return false;}
 return true;
}static_assert(cases());
''','startup')
 def test_actual_null_event_cleanup_no_persistent_delete_or_double_free(self):
  base=ROOT/'components/microlink-source/components/microlink/src'
  source=(base/'microlink.c').read_text(encoding='utf-8')
  phase=source.split('    /* Create event group */',1)[1].split('    /* Create queues */',1)[0]
  destroy=function(source,'void microlink_destroy(').replace('void microlink_destroy(', 'constexpr void microlink_destroy(')
  stop=function(source,'esp_err_t microlink_stop(').split('#if CONFIG_IDF_TARGET_ESP32S31',1)[0]
  stop=stop.replace('esp_err_t microlink_stop(', 'constexpr int microlink_stop(')+'++badEvents;return ESP_FAIL;}'
  nvs=function((base/'ml_peer_nvs.c').read_text(encoding='utf-8'),'void ml_peer_nvs_deinit(')
  nvs=nvs.replace('void ml_peer_nvs_deinit(', 'constexpr void ml_peer_nvs_deinit(')
  config=function((base/'ml_config_httpd.c').read_text(encoding='utf-8'),'void ml_config_httpd_deinit(')
  for body in [phase,destroy,nvs,config]:
   for forbidden in ['nvs_erase','nvs_set','ml_peer_nvs_clear','microlink_erase']:self.assertNotIn(forbidden,body)
  self.assertIn('ml->state = ML_STATE_IDLE;',source.split('/* Create event group */',1)[0])
  self.compile_actual(r'''
#include <cstdint>
#include <cstddef>
constexpr int ESP_OK=0,ESP_FAIL=-1,ESP_ERR_INVALID_ARG=-2,ML_STATE_IDLE=0;
#define ESP_LOGE(...) ((void)0)
#define ESP_LOGI(...) ((void)0)
struct microlink_t {int state=ML_STATE_IDLE,lifecycle_expected=0,events=0;int* config_httpd=nullptr;
 int derp_tx_queue=0,disco_rx_queue=0,wg_rx_queue=0,stun_rx_queue=0,coord_cmd_queue=0,peer_update_queue=0,derp_map_lock=0;
 uint8_t machine_private_key[32]={1},wg_private_key[32]={2},disco_private_key[32]={3};};
struct Sim {microlink_t client;int ctx=1,table=2;bool s_initialized=true;int s_nvs=9;int* s_table=&table;
 unsigned closed=0,tableFreed=0,ctxFreed=0,clientFreed=0,badEvents=0,duplicate=0;
 constexpr int xEventGroupCreate(){return 0;}
 constexpr void nvs_close(int){++closed;}
 constexpr void free(int* p){if(p==&table){if(tableFreed++)++duplicate;}else if(p==&ctx){if(ctxFreed++)++duplicate;}else ++duplicate;}
 constexpr void free(microlink_t*){if(clientFreed++)++duplicate;}
 constexpr void ml_config_httpd_deinit(int* p){free(p);}
 constexpr void vQueueDelete(int){++badEvents;}constexpr void vSemaphoreDelete(int){++badEvents;}constexpr void vEventGroupDelete(int){++badEvents;}
 constexpr void memset(uint8_t* p,int value,unsigned n){for(unsigned i=0;i<n;++i)p[i]=value;}
'''+nvs+'\n'+stop+'\n'+destroy+r'''
 constexpr microlink_t* eventPhase(microlink_t* ml){
'''+phase+r'''
 return ml;
 }
};
constexpr bool cases(){
 {Sim s;s.client.config_httpd=&s.ctx;if(s.eventPhase(&s.client)||s.closed!=1||s.tableFreed!=1||s.ctxFreed!=1||s.clientFreed!=1||s.badEvents||s.duplicate||s.s_initialized||s.s_table||s.client.config_httpd)return false;
 if(s.client.machine_private_key[0]||s.client.wg_private_key[0]||s.client.disco_private_key[0])return false;}
 {Sim s;s.s_initialized=false;s.s_table=nullptr;if(s.eventPhase(&s.client)||s.closed||s.tableFreed||s.ctxFreed||s.clientFreed!=1||s.badEvents||s.duplicate)return false;}
 return true;
}static_assert(cases(),"event allocation NULL cleans only initialized resources, no NULL-event access, no double free");
''','event-null-cleanup')
if __name__=='__main__':unittest.main()
