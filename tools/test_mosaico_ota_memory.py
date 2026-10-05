"""Offline integration guards; physical source location is checked at runtime."""
import unittest
from pathlib import Path
R=Path(__file__).resolve().parents[1]
N=(R/'main/host/network_quota.cpp').read_text()
O=(R/'main/ota/mosaico_ota.cpp').read_text()
class OtaMemoryTests(unittest.TestCase):
 def test_bounded_internal_owner(self):
  update=N.split('void NetworkQuota::updateFirmware()',1)[1]
  self.assertIn('heap_caps_malloc(ChunkCapacity, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)',update)
  self.assertIn('decltype(&heap_caps_free)',update)
  self.assertIn('ChunkCapacity = 4096',update)
  self.assertLess(update.index('ota_buffer_not_internal'),update.index('beginManifest('))
  self.assertNotIn('new (std::nothrow) uint8_t[ChunkCapacity]',update)
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
