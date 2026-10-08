"""Bounded offline retry policy and owner integration; no device traffic."""
from pathlib import Path
import subprocess,tempfile,unittest
R=Path(__file__).resolve().parents[1]
C=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
class WifiRetryTests(unittest.TestCase):
 def test_actual_policy(self):
  if not C:self.skipTest('cross compiler unavailable')
  code=r"""#include "main/host/wifi_retry_model.h"
constexpr bool test(){
 MosaicoWifiRetry::Policy p;
 if(p.remainingMs(100)||p.exhausted(true))return false;
 p.attempted(100,true);
 if(p.remainingMs(100)!=5000 || p.remainingMs(4999100)!=1)return false;
 // Early wake/notification cannot issue another attempt or reset its deadline.
 if(p.remainingMs(2000000)!=3001)return false;
 p.attempted(5000100,true);p.attempted(10000100,true);
 if(!p.exhausted(true)||p.exhausted(false)||p.remainingMs(15000100))return false;
 p.reset();p.attempted(0,false);if(p.remainingMs(0)!=5000)return false;
 p.attempted(5000000,false);if(p.remainingMs(5000000)!=15000)return false;
 p.attempted(20000000,false);if(p.remainingMs(20000000)!=60000)return false;
 p.attempted(80000000,false);if(p.remainingMs(80000000)!=60000)return false;
 p.reset();return !p.exhausted(true)&&!p.remainingMs(1)&&!p.attempts;
}
static_assert(test());"""
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'test.cpp';path.write_text(code)
   r=subprocess.run([str(C[-1]),'-std=c++17','-fsyntax-only','-I'+str(R),str(path)],capture_output=True,text=True)
   self.assertEqual(r.returncode,0,r.stdout+r.stderr)
 def test_owner_budget_before_connect(self):
  s=(R/'main/host/network_quota.cpp').read_text(encoding='utf8')
  branch=s[s.index('if (!_connected) {'):s.index('esp_wifi_connect();',s.index('if (!_connected) {'))+100]
  self.assertLess(branch.index('wifiRetry.exhausted'),branch.index('esp_wifi_connect()'))
  self.assertIn('updateWindow = false;',branch)
  self.assertNotIn('nextRefresh =',branch)
  self.assertIn('useRetryPolicy = !_twt.live()',branch)
  self.assertLess(branch.index('if (useRetryPolicy)'),branch.index('wifiRetry.remainingMs'))
  self.assertIn('wifiRetry.remainingMs',branch)
  self.assertLess(branch.index('if (useRetryPolicy)'),branch.index('selectWifiCandidate'))
  self.assertGreater(branch.index('selectWifiCandidate'),branch.index('useRetryPolicy = !_twt.live()'))
  self.assertIn('if(!locked) wifiRetry.reset();',s)
  self.assertIn('wifiRetry.reset(); _wifi_scan_complete=false; // New configured refresh window',s)
 def test_original_off_path(self):
  s=(R/'main/host/network_quota.cpp').read_text(encoding='utf8')
  pause=s[s.index('if (locked && !updateWindow) {'):s.index('if (locked && !updateWindow) {')+3800]
  self.assertLess(pause.index('GetTailnetQuota().pause()'),pause.index('esp_wifi_disconnect()'))
  self.assertLess(pause.index('esp_wifi_disconnect()'),pause.index('esp_wifi_stop()'))
  self.assertIn('recordWifiRunning(false)',pause)
if __name__=='__main__':unittest.main()
