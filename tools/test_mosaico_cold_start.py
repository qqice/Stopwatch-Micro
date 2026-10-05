"""Source sequencing gates only; optical cold-start acceptance is separate."""
import unittest
from pathlib import Path
R=Path(__file__).resolve().parents[1]
S=(R/'main/hal/mosaico/hal_mosaico.cpp').read_text()
class ColdStartTests(unittest.TestCase):
 def test_dark_panel_until_first_frame(self):
  init=S.split('void Hal::display_init()',1)[1].split('bool Hal::display_ready()',1)[0]
  commands=init.split('darkInit[] = {',1)[1].split('};',1)[0]
  self.assertIn('{0x51, zero, 1, 10}',commands)
  self.assertNotIn('0x29',commands)
  self.assertNotIn('disp_on_off(panel, true)',init)
  self.assertIn('pending_boot_display = true',init)
  ready=S.split('void observe_refresh(',1)[1].split('void observe_flush(',1)[0]
  self.assertLess(ready.index('set_brightness(panel, brightness)'),ready.index('disp_on_off(panel, true)'))
 def test_scoped_factory_reload_only_after_latch(self):
  reload=S.split('Hal::GaugeBootReloadStatus Hal::gaugeBootReload(',1)[1]
  self.assertLess(reload.index('bootHistoryEligible('),reload.index('gauge_store_reload(latch'))
  self.assertLess(reload.index('gauge_store_reload(latch'),reload.index('} reloadScope;'))
  self.assertIn('~BootReloadScope() { boot_factory_reload_scope = false; }',reload)
  pre=S.split('bool gauge_access_preflight(',1)[1].split('uint8_t gauge_observed_security',1)[0]
  self.assertIn('profile.design == mosaico_gauge::FactoryMah && profile.fcc == mosaico_gauge::FactoryMah',pre)
  self.assertEqual(S.count('profile, true)'),1)
 def test_manual_and_restore_policy_not_replaced(self):
  self.assertIn('!ownedUnresolvedRestore && ((operation >> 1) & 3) == 1',S)
  self.assertIn('mosaico_gauge::quietAccess(operation, soc',S)
  self.assertIn('mosaico_gauge::quietFullRestore(operation, soc',S)
if __name__=='__main__':unittest.main()
