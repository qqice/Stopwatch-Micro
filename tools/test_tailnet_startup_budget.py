"""Bounded offline budget/reservation checks. No device, network or SDK changes."""
from pathlib import Path
import subprocess,tempfile,unittest
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
  for text in ['diagnostic.normalizedBudget < 80 * 1024','diagnostic.largest < 16 * 1024','const auto selftest=ml_noise_selftest()','if (!_client) { finish(StartupDecision::InitFailed,ESP_FAIL); return; }','StartupDecision::StartFailed','MosaicoNandStore::startupInternalReservation()']:
   self.assertIn(text,cpp)
  body=cpp.split('TailnetQuota::StartupDiagnostics TailnetQuota::startupDiagnostics() const',1)[1].split('void TailnetQuota::publishStartupDiagnostics',1)[0]
  self.assertIn('portENTER_CRITICAL',body);self.assertIn('portEXIT_CRITICAL',body)
  for forbidden in ['heap_caps','microlink_','start(','ESP_LOG','_key','_host','_path']:self.assertNotIn(forbidden,body)
if __name__=='__main__':unittest.main()
