"""RTC-backed system clock/NTP integration without hard-off timestamp fiction."""
import unittest
from pathlib import Path
R=Path(__file__).resolve().parents[1]
H=(R/'main/host/system_clock.h').read_text();N=(R/'main/host/network_quota.cpp').read_text()
class SystemClockTests(unittest.TestCase):
 def test_utc_range_and_explicit_local_offset(self):
  self.assertIn('1704067200LL',H);self.assertIn('4102444800LL',H)
  self.assertIn('epoch + 8 * 3600',H);self.assertIn('gmtime_r(',H)
  self.assertIn('std::time(nullptr)',H);self.assertNotIn('nvs_',H)
 def test_sntp_observation_only_and_no_global_timezone_mutation(self):
  callback=H.split('inline void onSntpTime(',1)[1].split('struct Snapshot',1)[0]
  self.assertIn('validEpoch(tv->tv_sec)',callback)
  self.assertNotIn('settimeofday',callback)
  self.assertIn('timeConfig.sync_cb = MosaicoClock::onSntpTime;',N)
  self.assertIn('esp_netif_sntp_init(&timeConfig)',N)
  self.assertNotIn('setenv(',H)
if __name__=='__main__':unittest.main()
