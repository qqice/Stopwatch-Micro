"""Host-only compile/extraction tests of OTA boot reservation and ownership."""
from pathlib import Path
import subprocess
import tempfile
import unittest

R=Path(__file__).resolve().parents[1]
N=(R/'main/host/network_quota.cpp').read_text()
COMPILER=Path('C:/Espressif/tools/riscv32-esp-elf/esp-15.2.0_20251204/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe')
class BootBufferTests(unittest.TestCase):
 def compile(self,code):
  with tempfile.TemporaryDirectory() as directory:
   source=Path(directory)/'buffers.cpp';source.write_text(code)
   result=subprocess.run([str(COMPILER),'-std=c++20','-Wall','-Wextra','-Werror','-fsyntax-only','-I'+str(R/'main/host'),str(source)],capture_output=True,text=True)
   log=R/'artifacts/ota-boot-buffer-tests'/f'{self._testMethodName}.log'
   log.parent.mkdir(parents=True,exist_ok=True);log.write_text(result.stdout+result.stderr)
   self.assertEqual(result.returncode,0,str(log))

 def test_actual_allocator_and_reservation_all_failure_boundaries(self):
  allocator=N.split('struct OtaBufferAllocator {',1)[1].split('};\nOtaBufferAllocator otaBufferAllocator;',1)[0]
  allocator=allocator.replace('uint8_t* allocate(', 'constexpr uint8_t* allocate(').replace('void release(', 'constexpr void release(').replace('bool valid(', 'constexpr bool valid(')
  self.compile(r'''
#include "mosaico_ota_buffer_model.h"
using namespace MosaicoOtaBuffer;
constexpr uint32_t MALLOC_CAP_INTERNAL=1,MALLOC_CAP_8BIT=2,MALLOC_CAP_DMA=4,MALLOC_CAP_SPIRAM=8;
struct Fake {
 uint8_t chunk[ChunkCapacity]{},body[BodyCapacity]{};int mode=0,allocations=0,releases=0;bool wrong=false;
 constexpr uint8_t* heap_caps_malloc(size_t size,uint32_t caps){
  ++allocations;
  if(size==ChunkCapacity){if(caps!=7)wrong=true;return mode==1?nullptr:chunk;}
  if(size==BodyCapacity){if(caps!=10)wrong=true;return mode==2?nullptr:body;}
  wrong=true;return nullptr;
 }
 constexpr void heap_caps_free(uint8_t* p){if(p!=chunk&&p!=body)wrong=true;++releases;}
 constexpr bool esp_ptr_in_dram(const uint8_t* p)const{return p==chunk||(p==chunk+ChunkCapacity-1&&mode!=3);}
 constexpr bool esp_ptr_internal(const uint8_t* p)const{return p==chunk||(p==chunk+ChunkCapacity-1&&mode!=5);}
 constexpr bool esp_ptr_dma_capable(const uint8_t* p)const{return mode!=6&&(p==chunk||p==chunk+ChunkCapacity-1);}
 constexpr bool esp_ptr_external_ram(const uint8_t* p)const{return p==body||(p==body+BodyCapacity-1&&mode!=4);}
'''+allocator+r'''
};
constexpr bool cases(){
 for(int mode=0;mode<=6;++mode){Fake a;a.mode=mode;Reservation reservation;
  if(reservation.acquire(a)||a.allocations||a.releases||reservation.error()!=Error::Unprepared)return false;
  if(reservation.prepare(a)!=(mode==0)||a.wrong)return false;
  const int allocations=a.allocations,releases=a.releases;
  if(allocations!=(mode==1?1:2)||releases!=(mode==0||mode==1?0:mode==2?1:2))return false;
  const Error errors[]={Error::None,Error::ChunkAllocation,Error::BodyAllocation,Error::ChunkMemory,Error::BodyMemory,Error::ChunkMemory,Error::ChunkMemory};
  if(reservation.error()!=errors[mode])return false;
  a.mode=0; // Failure is latched for the boot, even after memory recovers.
  if(reservation.prepare(a)!=(mode==0)||bool(reservation.acquire(a))!=(mode==0))return false;
  if(a.allocations!=allocations||a.releases!=releases)return false;
  if(mode==0){auto view=reservation.acquire(a);if(view.chunk!=a.chunk||view.body!=a.body)return false;
   a.mode=3;if(reservation.acquire(a)||reservation.error()!=Error::ChunkMemory)return false;
   a.mode=0;if(reservation.acquire(a)||reservation.prepare(a))return false;
  }
  if(a.valid(Kind::Chunk,nullptr,ChunkCapacity)||a.valid(Kind::Chunk,a.chunk,ChunkCapacity-1)||
     a.valid(Kind::Body,a.body,BodyCapacity+1)||a.valid(Kind::Chunk,a.body,ChunkCapacity)||a.valid(Kind::Body,a.chunk,BodyCapacity))return false;
 }
 Fake a;Reservation r;if(!r.prepare(a))return false;
 a.mode=4;if(r.acquire(a)||r.error()!=Error::BodyMemory||a.allocations!=2||a.releases)return false;
 return sizeof(Reservation)<64;
}
static_assert(cases(),"actual flags exact ranges partial cleanup no publication one boot attempt and allocation-free acquire");
''')

 def test_actual_transfer_buffer_scope_compiles(self):
  update=N.split('void NetworkQuota::updateFirmware()',1)[1]
  prefix=update[update.index('    constexpr size_t BodyCapacity='):update.index('    int used = 0;')]
  self.compile(r'''
#include "mosaico_ota_buffer_model.h"
#include <cstring>
#define ESP_LOGW(...) ((void)0)
#define ESP_LOGI(...) ((void)0)
struct OtaBufferAllocator{bool valid(MosaicoOtaBuffer::Kind,const uint8_t*,size_t){return true;}} otaBufferAllocator;
MosaicoOtaBuffer::Reservation otaBuffers;
bool esp_ptr_in_dram(const void*);bool esp_ptr_internal(const void*);
namespace MosaicoOta {void fail(const char*);}
void updatePrefix(){
'''+prefix+'}\n')

 def test_startup_order_single_owner_and_existing_transfer_contract(self):
  main=(R/'main/main.cpp').read_text()
  self.assertLess(main.index('GetHAL().init()'),main.index('prepareOtaBuffers()'))
  self.assertLess(main.index('prepareOtaBuffers()'),main.index('MosaicoSessions::init()'))
  self.assertLess(main.index('prepareOtaBuffers()'),main.index('GetNetworkQuota().begin()'))
  self.assertEqual(main.count('prepareOtaBuffers()'),1)
  prepare=N.split('bool NetworkQuota::prepareOtaBuffers()',1)[1].split('#endif',1)[0]
  self.assertNotIn('reboot',prepare);self.assertNotIn('restart(',prepare)
  self.assertEqual(N.count('otaBuffers.prepare('),1)
  self.assertEqual(N.count('otaBuffers.acquire('),1)
  update=N.split('void NetworkQuota::updateFirmware()',1)[1].split('void NetworkQuota::wakeForFirmwareUpdate()',1)[0]
  self.assertLess(update.index('ota_boot_buffers_unavailable'),update.index('beginManifest('))
  for forbidden in ('new (std::nothrow)','heap_caps_malloc(', 'heap_caps_free(', 'xTaskCreate(', 'std::mutex'):
   self.assertNotIn(forbidden,update)
  self.assertIn('~BodyScope(){std::memset(p,0,BodyCapacity);}',update)
  self.assertIn('std::memset(body,0,BodyCapacity);',update)
  self.assertEqual(update.count('cJSON_ParseWithLength(body, static_cast<size_t>(used))'),2)
  self.assertIn('MosaicoOta::beginManifest(body)',update)
  self.assertIn('MosaicoOta::writeChunk(offset, chunk, count)',update)
  self.assertNotIn('writeChunk(offset, body',update)
  self.assertIn('count == std::min<uint32_t>(ChunkCapacity, size - offset)',update)
  request=N.split('bool NetworkQuota::requestJson(',1)[1].split('bool NetworkQuota::fetchHistory()',1)[0]
  self.assertIn('used = 0;',request);self.assertIn('body[0] = 0;',request);self.assertIn('body[used] = 0;',request)
  self.assertIn('if (MosaicoOta::takeRequest())',N.split('updateFirmware();',1)[0])
  ota=(R/'main/ota/mosaico_ota.cpp').read_text()
  write=ota.split('bool writeChunk(',1)[1].split('bool finishDownload()',1)[0]
  for guard in ('StorageAdmission storageAdmission','length > 4096','esp_ptr_in_dram(data + length - 1)','downloadPowerSafe()'):
   self.assertIn(guard,write)
  self.assertLess(write.index('chunk_not_internal'),write.index('esp_ota_write('))
  manifest=ota.split('bool beginManifest(',1)[1].split('bool writeChunk(',1)[0]
  self.assertIn('validateDescriptor(json, d, error)',manifest)
  self.assertIn('!currentReady() || !downloadPowerSafe()',manifest)

if __name__=='__main__':unittest.main()
