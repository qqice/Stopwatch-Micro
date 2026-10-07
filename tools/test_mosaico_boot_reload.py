"""Source integration guards; hardware and HAL policy acceptance are separate."""
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
class BootIntegrationTests(unittest.TestCase):
 def test_startup_before_display_and_network(self):
  source=(ROOT/'main/hal/hal.cpp').read_text(encoding='utf8')
  init=source.split('void Hal::init()',1)[1].split('Hal::Diagnostics',1)[0]
  self.assertLess(init.index('pmic_init();'),init.index('gaugeBootReload('))
  self.assertLess(init.index('gaugeBootReload('),init.index('display_init();'))
  self.assertIn('#ifdef MOSAICO_BOARD',init.split('gaugeBootReload(',1)[0])
 def test_idle_deferred_only_and_rate_limited(self):
  source=(ROOT/'main/host/network_quota.cpp').read_text(encoding='utf8')
  # TWT retained-association also reports phase2, but must not perform the
  # radio-off-only gauge reload. Select the original deferred-gauge branch.
  prefix,tail=source.split('// Deferred preconditions',1)
  idle=prefix.rsplit('setPhase(2);',1)[1]+tail.split('continue;',1)[0]
  self.assertIn('GaugeBootReloadStatus::Deferred',idle)
  self.assertIn('esp_timer_get_time() >= nextGaugeCheckUs',idle)
  self.assertIn('60LL * 1000000',idle)
  self.assertIn('std::min<uint32_t>(waitMs, 60000)',idle)
 def test_usb_status_does_not_trigger_reload(self):
  source=(ROOT/'main/debug/serial_debug.cpp').read_text(encoding='utf8')
  cmd=source.split('if (std::strcmp(command, "gauge-boot") == 0)',1)[1].split('if (std::strcmp(command, "runtime-restart")',1)[0]
  self.assertIn('gaugeBootReloadInfo()',cmd)
  for mutator in ('gaugeBootReload(', 'gaugeAccess(', 'gaugeSetNominalCapacity(', 'gaugeReconcileNominal('):
   self.assertNotIn(mutator,cmd)
 def test_normal_restart_is_explicit_and_not_download(self):
  source=(ROOT/'main/debug/serial_debug.cpp').read_text(encoding='utf8')
  cmd=source.split('if (std::strcmp(command, "runtime-restart") == 0)',1)[1].split('if (std::strcmp(command, "gauge-selftest")',1)[0]
  self.assertIn('"CONFIRM"',cmd); self.assertIn('power.locked || power.phase != 0',cmd)
  self.assertIn('GaugeBootReloadStatus::Critical',cmd); self.assertIn('GetHAL().reboot()',cmd)
  for forbidden in ('FORCE_DOWNLOAD_BOOT','gaugeAccess(', 'gaugeSetNominalCapacity(', 'nvs_flash_erase'):
   self.assertNotIn(forbidden,cmd)
if __name__=='__main__': unittest.main()
