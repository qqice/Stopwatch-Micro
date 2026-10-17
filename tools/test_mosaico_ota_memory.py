"""Offline integration guards; physical source location is checked at runtime."""
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
R=Path(__file__).resolve().parents[1]
N=(R/'main/host/network_quota.cpp').read_text()
O=(R/'main/ota/mosaico_ota.cpp').read_text()
class OtaMemoryTests(unittest.TestCase):
 def test_bounded_internal_owner(self):
  update=N.split('void NetworkQuota::updateFirmware()',1)[1]
  self.assertIn('heap_caps_malloc(ChunkCapacity, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT | MALLOC_CAP_DMA)',update)
  self.assertIn('decltype(&heap_caps_free)',update)
  self.assertIn('ChunkCapacity = 4096',update)
  self.assertLess(update.index('ota_buffer_not_internal'),update.index('beginManifest('))
  self.assertNotIn('new (std::nothrow) uint8_t[ChunkCapacity]',update)
 def test_allocator_caps_exclude_rtc_sdk_candidate(self):
  # Capability evidence only: the failing device pointer is not yet observed.
  sdk=Path('C:/esp/v6.1/esp-idf')
  layout=(sdk/'components/heap/port/esp32s31/memory_layout.c').read_text()
  common=re.search(r'#define ESP32S31_MEM_COMMON_CAPS (.+)',layout).group(1)
  common_caps=set(re.findall(r'MALLOC_CAP_[A-Z0-9_]+',common))
  ram=re.search(r'\[SOC_MEMORY_TYPE_RAM\]\s*= (.+)',layout).group(1)
  rtc=re.search(r'\[SOC_MEMORY_TYPE_RTCRAM\]\s*= (.+)',layout).group(1)
  ram_caps=common_caps|set(re.findall(r'MALLOC_CAP_[A-Z0-9_]+',ram))
  rtc_caps=common_caps|set(re.findall(r'MALLOC_CAP_[A-Z0-9_]+',rtc))
  update=N.split('void NetworkQuota::updateFirmware()',1)[1]
  allocation=re.search(r'heap_caps_malloc\(ChunkCapacity, ([^)]+)\)',update).group(1)
  requested=set(re.findall(r'MALLOC_CAP_[A-Z0-9_]+',allocation))
  old={'MALLOC_CAP_INTERNAL','MALLOC_CAP_8BIT'}
  self.assertTrue(old<=rtc_caps)
  self.assertTrue(requested<=ram_caps)
  self.assertFalse(requested<=rtc_caps)
  self.assertIn('MALLOC_CAP_DMA',requested)
 def test_null_and_endpoint_guards_precede_transfer(self):
  update=N.split('void NetworkQuota::updateFirmware()',1)[1]
  self.assertIn('chunkEnd = chunk ? chunk.get() + ChunkCapacity - 1 : nullptr',update)
  self.assertIn('chunk && esp_ptr_in_dram(chunk.get())',update)
  self.assertIn('chunkEnd && esp_ptr_in_dram(chunkEnd)',update)
  self.assertIn('chunk && esp_ptr_internal(chunk.get())',update)
  self.assertIn('chunkEnd && esp_ptr_internal(chunkEnd)',update)
  self.assertIn('flash source start=%p end=%p dram=%d/%d internal=%d/%d capacity=%u',update)
  self.assertLess(update.index('if (!body || !chunk)'),update.index('if (!esp_ptr_in_dram(chunk.get())'))
  self.assertLess(update.index('ota_buffer_not_internal'),update.index('requestJson("/v1/ota/manifest"'))
  write=O.split('bool writeChunk(',1)[1].split('bool finishDownload()',1)[0]
  self.assertLess(write.index('!data || !length'),write.index('esp_ptr_in_dram(data + length - 1)'))
 def test_public_capacity_uses_strict_layout_and_outer_gate(self):
  header=(R/'main/ota/mosaico_ota.h').read_text()
  self.assertIn('uint32_t imageCapacity();',header)
  expression=re.search(r'uint32_t imageCapacity\(\) \{ return (.*?); \}',O).group(1)
  self.assertEqual(expression,'MosaicoOtaLayout::imageCap(currentLayout())')
  update=N.split('void NetworkQuota::updateFirmware()',1)[1]
  self.assertIn('const uint32_t imageCapacity = MosaicoOta::imageCapacity();',update)
  self.assertIn('fieldsOk = imageCapacity > 0 && manifest',update)
  self.assertIn('sizeValue->valuedouble <= imageCapacity',update)
  self.assertNotIn('0x3E0000',update)
  code='''#include "main/ota/mosaico_ota_layout.h"
using MosaicoOtaLayout::Id;
constexpr uint32_t currentCapacity(Id id) { return EXPRESSION; }
constexpr bool allows(Id id, uint32_t size) { return size>0 && size<=currentCapacity(id); }
static_assert(currentCapacity(Id::DualV1)==0x3e0000);
static_assert(currentCapacity(Id::DualV2)==0x600000);
static_assert(currentCapacity(Id::Unknown)==0);
static_assert(allows(Id::DualV1,0x3e0000) && !allows(Id::DualV1,0x3e0001));
static_assert(allows(Id::DualV2,0x600000) && !allows(Id::DualV2,0x600001));
static_assert(!allows(Id::Unknown,1) && !allows(Id::Unknown,0));
'''.replace('EXPRESSION',expression.replace('currentLayout()','id'))
  compiler='C:/Espressif/tools/riscv32-esp-elf/esp-15.2.0_20251204/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'
  with tempfile.TemporaryDirectory() as directory:
   source=Path(directory)/'capacity.cpp'; source.write_text(code)
   result=subprocess.run([compiler,'-std=c++17','-fsyntax-only','-I'+str(R),str(source)],capture_output=True,text=True)
   self.assertEqual(result.returncode,0,result.stdout+result.stderr)
 def test_backend_rejects_cache_backed_source_before_write(self):
  write=O.split('bool writeChunk(',1)[1].split('bool finishDownload()',1)[0]
  self.assertIn('length > 4096',write)
  self.assertIn('esp_ptr_in_dram(data + length - 1)',write)
  self.assertLess(write.index('chunk_not_internal'),write.index('esp_ota_write('))
  self.assertLess(write.index('esp_ota_write('),write.index('psa_hash_update('))
 def test_wifi_nvs_init_bypassed_not_project_credentials(self):
  start=N.split('wifi_init_config_t init',1)[1].split('bool hadConnection',1)[0]
  self.assertLess(start.index('init.nvs_enable = 0'),start.index('esp_wifi_init(&init)'))
  self.assertIn('esp_wifi_set_storage(WIFI_STORAGE_RAM)',start)
  self.assertIn('#ifdef MOSAICO_BOARD',start.split('init.nvs_enable = 0',1)[0])
 def test_heap_probe_and_breadcrumb_no_normal_activity(self):
  update=N.split('void NetworkQuota::updateFirmware()',1)[1].split('void NetworkQuota::wakeForFirmwareUpdate',1)[0]
  self.assertIn('offset % 65536U',update)
  self.assertEqual(N.count('heap_caps_check_integrity_all(true)'),3)
  self.assertIn('MosaicoOtaBreadcrumb(7, offset)',update)
  self.assertIn('~TraceScope() { MosaicoOtaBreadcrumb(0, 0); }',update)
  panic=(R/'main/ota/panic_capture.cpp').read_text()
  self.assertIn('capture.otaPhase = otaPhase',panic)
  self.assertIn('capture.otaOffset = otaOffset',panic)
  self.assertIn('DRAM_ATTR volatile uint32_t',panic)
if __name__=='__main__':unittest.main()
