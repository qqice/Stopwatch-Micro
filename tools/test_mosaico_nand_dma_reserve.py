"""Portable boot-reserve policy and source integration tests; no SDK build/device.

constexpr cases inject sequential atomics only to test policy. The same header
also instantiates real std::atomic; this is compile validation, not a thread stress
or hardware DMA claim.
"""
from pathlib import Path
import importlib.util
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('nand_store_tests',ROOT/'tools/test_mosaico_nand_store.py')
store=importlib.util.module_from_spec(spec);spec.loader.exec_module(store)

MODEL=r'''
#include "main/hal/mosaico/mosaico_nand_dma_reserve_model.h"
#include <array>
#include <limits>
template<class T> struct SequentialAtomic {
 T value;
 constexpr SequentialAtomic(T v):value(v){}
 constexpr T load(std::memory_order)const{return value;}
 constexpr void store(T v,std::memory_order){value=v;}
 constexpr T exchange(T v,std::memory_order){T old=value;value=v;return old;}
 constexpr bool compare_exchange_strong(T& expected,T desired,std::memory_order){
  if(value==expected){value=desired;return true;}expected=value;return false;
 }
};
using Reserve=MosaicoNandDma::Reservation<SequentialAtomic>;
MosaicoNandDma::Reservation<> realAtomicInstantiation;
struct Allocator {
 alignas(64) std::array<uint8_t,2112> memory{};
 bool allocationFails=false,memoryValid=true;
 unsigned allocations=0,releases=0,validations=0;
 constexpr uint8_t* allocate(size_t alignment,size_t size){
  ++allocations;if(alignment!=64||size!=2112||allocationFails)return nullptr;return memory.data();
 }
 constexpr bool valid(const uint8_t* p,size_t size,size_t alignment){
  ++validations;return memoryValid&&p==memory.data()&&size==2112&&alignment==64;
 }
 constexpr void release(uint8_t* p){if(p==memory.data())++releases;}
};
constexpr bool oneAttempt(){
 Allocator a;Reserve r;
 if(r.isReady()||r.acquire(2052,4,a)||!r.release(nullptr)||a.allocations)return false;
 if(!r.prepare(a)||!r.prepare(a)||a.allocations!=1||!r.wasAttempted())return false;
 auto* p=r.acquire(2052,4,a);if(p!=a.memory.data()||!r.isLeased()||r.acquire(2052,4,a))return false;
 if(!r.release(p)||r.isLeased()||a.releases)return false;
 // Reopen after deinit releases then borrows the identical persistent boot ptr.
 if(r.acquire(2052,4,a)!=p||!r.release(p)||a.allocations!=1||a.releases)return false;
 if(!r.release(nullptr)||!r.isReady())return false;
 Allocator failed;failed.allocationFails=true;Reserve noMemory;
 if(noMemory.prepare(failed)||noMemory.prepare(failed)||failed.allocations!=1||failed.releases||!noMemory.isFaulted()||noMemory.isReady()||noMemory.error()!=MosaicoNandDma::Error::Allocation)return false;
 Allocator invalid;invalid.memoryValid=false;Reserve badRange;
 return !badRange.prepare(invalid)&&!badRange.prepare(invalid)&&invalid.allocations==1&&invalid.releases==1&&!badRange.isReady()&&badRange.isFaulted()&&badRange.error()==MosaicoNandDma::Error::Memory;
}
constexpr bool bounds(){
 Allocator a;Reserve r;if(!r.prepare(a))return false;
 for(size_t alignment:{1,2,4,8,16,32,64})for(size_t size:{1,2052,2080,2112}){
  auto* p=r.acquire(size,alignment,a);if(!p||!r.release(p))return false;
 }
 for(size_t alignment:{0,3,65,128})if(r.acquire(2052,alignment,a)||r.isLeased())return false;
 for(size_t size:{0,2113,4096})if(r.acquire(size,4,a)||r.isLeased())return false;
 if(r.acquire(std::numeric_limits<size_t>::max(),4,a)||r.acquire(2052,std::numeric_limits<size_t>::max(),a))return false;
 return a.allocations==1&&a.releases==0;
}
constexpr bool ownership(){
 Allocator a;Reserve r;if(!r.prepare(a))return false;
 auto* p=r.acquire(2052,4,a);if(!p||r.release(p+1)||!r.isFaulted()||!r.isLeased())return false;
 if(r.acquire(2052,4,a)||r.prepare(a)||a.allocations!=1||a.releases)return false;
 if(!r.release(p)||r.isLeased()||r.isReady())return false;
 Allocator b;Reserve twice;if(!twice.prepare(b))return false;
 p=twice.acquire(2052,4,b);if(!twice.release(p)||twice.release(p)||!twice.isFaulted())return false;
 Allocator c;Reserve early;
 if(early.release(c.memory.data())||early.prepare(c)||c.allocations)return false;
 Allocator d;Reserve broken;if(!broken.prepare(d))return false;
 d.memoryValid=false;if(broken.acquire(2052,4,d)||!broken.isFaulted()||!broken.isLeased()||d.releases)return false;
 return true;
}
static_assert(oneAttempt(),"one boot allocation, no late retry, early failure cleanup, reuse on Reopen");
static_assert(bounds(),"requested page plus DMA alignment stays within independent2112/64 reserve");
static_assert(ownership(),"atomic-exclusive policy, null noop, wrong/double release poisons; no boot ptr free");
'''


class ReserveTests(unittest.TestCase):
    def test_policy_and_real_atomic_instantiation(self):
        with tempfile.TemporaryDirectory(dir=ROOT,prefix='.nand-reserve-') as td:
            source=Path(td)/'test.cpp';source.write_text(MODEL)
            p=subprocess.run([store.COMPILER,'-std=c++20','-Wall','-Wextra','-Werror',
                              '-fsyntax-only','-I',str(ROOT),str(source)],capture_output=True,text=True)
            log=ROOT/'artifacts/nand-dma-reserve-model.log';log.parent.mkdir(exist_ok=True)
            log.write_text(p.stdout+p.stderr)
            self.assertEqual(p.returncode,0,str(log))

    def test_strong_hooks_exact_replacement_and_release_coverage(self):
        if not (store.COMPONENT/'src/nand_impl.c').exists():
            self.skipTest('optional pinned upstream component absent')
        impl=store.compat.patched('nand_impl.c',(store.COMPONENT/'src/nand_impl.c').read_text())
        api=store.compat.patched('nand.c',(store.COMPONENT/'src/nand.c').read_text())
        self.assertEqual(impl.count('temp_buffer = mosaico_nand_dma_acquire('),1)
        self.assertEqual(impl.count('mosaico_nand_dma_release((*handle)->temp_buffer);'),1)
        self.assertEqual(api.count('mosaico_nand_dma_release(handle->temp_buffer);'),1)
        self.assertNotIn('free((*handle)->temp_buffer)',impl)
        self.assertNotIn('free(handle->temp_buffer)',api)
        for text in (impl,api):
            self.assertIn('extern void mosaico_nand_dma_release(void *pointer);',text)
            self.assertNotIn('weak)) void mosaico_nand_dma',text)
        self.assertIn('0x01040104U',api)
        self.assertIn('spi_nand_flash_deinit_device(*handle);\n    *handle = NULL;',api)

    def test_full_range_validation_independent_pool_and_transaction_lease_order(self):
        cpp=(ROOT/'main/hal/mosaico/mosaico_nand_store.cpp').read_text()
        allocator=cpp.split('struct DmaAllocator {',1)[1].split('} dmaAllocator;',1)[0]
        for required in ('MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT|MALLOC_CAP_DMA',
                         'for(size_t i=0;i<size;++i)', 'esp_ptr_internal(pointer+i)',
                         'esp_ptr_dma_capable(pointer+i)','esp_ptr_byte_accessible(pointer+i)'):
            self.assertIn(required,allocator)
        transaction=store.extract_function(cpp,'Result transaction(')
        self.assertLess(transaction.index('StorageLease storageLease'),transaction.index('std::unique_lock'))
        self.assertLess(transaction.index('if(!storageLease)'),transaction.index('heap_caps_calloc'))
        self.assertLess(transaction.index('if(!dmaReservation.isReady())'),transaction.index('spi_bus_initialize'))
        self.assertIn('MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT',transaction)
        self.assertIn('~StorageLease(){if(held)MosaicoOta::releaseStorageLease();}',cpp)
        self.assertNotIn('MosaicoOtaBuffer',cpp)
        self.assertNotIn('prepareDmaBuffer()',transaction)
        status=store.extract_function(cpp,'void dmaStatus(')
        self.assertNotIn('spi_',status)
        self.assertNotIn('heap_caps_',status)
        self.assertNotIn('prepare(',status)


if __name__=='__main__':unittest.main()
