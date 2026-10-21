"""Compile the real one-shot policy and extracted owner scheduling, without SDK/device."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPILERS = sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
SOURCE = (ROOT / 'main/host/network_quota.cpp').read_text(encoding='utf8')


class FirstQuotaRefreshTests(unittest.TestCase):
    def test_actual_policy_and_owner_scheduling(self):
        self.assertTrue(COMPILERS, 'Existing C++ compiler required; no silently skipped acceptance')
        begin = SOURCE.index('#ifdef MOSAICO_BOARD\n        bool firstQuotaBootstrap = false;')
        end = SOURCE.index('        if (locked && !updateWindow) {', begin)
        block = SOURCE[begin:end]
        code = r'''
#include <algorithm>
#include <cstdint>
#include "main/host/first_quota_refresh_policy.h"
#include "main/host/wifi_retry_model.h"
namespace MosaicoTwt {
enum class CycleReason { Cancel, Deadline };
enum class CyclePhase { Start };
}
struct Flag { bool value=false; constexpr bool exchange(bool v) { bool old=value; value=v; return old; } };
struct Twt { bool active=false; constexpr bool live() const { return active; } };
struct Owner {
 MosaicoFirstQuotaRefresh::Policy firstQuotaRefresh;
 MosaicoWifiRetry::Policy wifiRetry;
 Twt _twt;
 Flag _force_refresh;
 bool wasLocked=false, updateWindow=false, _wifi_scan_complete=true, gaugeHold=false;
 bool wifiRunning=true, connected=true, tailnetReady=true;
 unsigned _accepted=0, logs=0, cycleStarts=0;
 int64_t nextRefresh=0, windowDeadline=0, refreshBaseUs=0;
 constexpr void closeWindow(MosaicoTwt::CycleReason) { updateWindow=false; }
 constexpr void recordTwtCycle(MosaicoTwt::CyclePhase) { ++cycleStarts; }
 constexpr void step(bool locked, int64_t now, int64_t refreshIntervalUs) {
 const int64_t updateWindowUs=std::min<int64_t>(90000000,refreshIntervalUs*3/4);
#define ESP_LOGI(...) (++logs)
''' + block + r'''
#undef ESP_LOGI
 }
};
constexpr bool policyTests() {
 MosaicoFirstQuotaRefresh::Policy pending;
 if (!pending.takeOnLock(false) || pending.takeOnLock(false) || pending.takeOnLock(true)) return false;
 MosaicoFirstQuotaRefresh::Policy accepted;
 return !accepted.takeOnLock(true) && !accepted.takeOnLock(false);
}
static_assert(policyTests());
constexpr bool ownerTests() {
 constexpr int64_t now=35000000, interval=300000000;
 Owner pending;
 pending.wifiRetry.attempted(1,true);
 pending.step(true,now,interval);
#ifdef MOSAICO_BOARD
 if (!pending.updateWindow || pending.windowDeadline!=now+90000000 || pending.logs!=1) return false;
 if (pending.wifiRetry.attempts || pending._wifi_scan_complete) return false;
 if (!pending.wifiRunning || !pending.connected || !pending.tailnetReady) return false;
 // Repeated locked notifications do not reset deadline or attempts, even on failure.
 pending.wifiRetry.attempted(now,true);
 pending.step(true,now+1000000,interval);
 if (!pending.updateWindow || pending.windowDeadline!=now+90000000 || pending.wifiRetry.attempts!=1) return false;
 pending.step(true,now+90000000,interval);
 if (pending.updateWindow || pending.nextRefresh!=now+interval || pending.logs!=1) return false;
 // Wake and relock after failure cannot rearm the one-shot.
 pending.step(false,now+100000000,interval);
 pending.step(true,now+101000000,interval);
 if (pending.updateWindow || pending.nextRefresh!=now+101000000+interval || pending.logs!=1) return false;
#else
 if (pending.updateWindow || pending.nextRefresh!=now+interval || pending.logs) return false;
#endif
 Owner accepted; accepted._accepted=1;
 accepted.step(true,now,interval);
 if (accepted.updateWindow || accepted.nextRefresh!=now+interval || accepted.logs) return false;
 accepted.step(false,now+1,interval);
 accepted._accepted=0; // Even a later flag change cannot rearm a consumed first lock.
 accepted.step(true,now+2,interval);
 if (accepted.updateWindow || accepted.logs || accepted.nextRefresh!=now+2+interval) return false;
#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT
 Owner trial; trial._twt.active=true;
 trial.step(true,now,interval);
 if (trial.updateWindow || trial.logs || trial.cycleStarts || trial.nextRefresh!=now+interval) return false;
 trial.step(false,now+1,interval); trial._twt.active=false;
 trial.step(true,now+2,interval);
 if (trial.updateWindow || trial.logs || trial.cycleStarts) return false;
#endif
 // All supported regular intervals retain their normal cadence and window cap.
 for (int minutes=1; minutes<=60; ++minutes) {
  const int64_t configured=int64_t(minutes)*60000000;
  Owner normal; normal._accepted=1;
  normal.step(true,now,configured);
  if (normal.updateWindow || normal.nextRefresh!=now+configured) return false;
  normal.step(true,now+configured,configured);
  if (!normal.updateWindow || normal.nextRefresh!=now+2*configured || normal.logs) return false;
  if (normal.windowDeadline!=now+configured+std::min<int64_t>(90000000,configured*3/4)) return false;
  normal.step(true,normal.windowDeadline,configured);
  if (normal.updateWindow || normal.nextRefresh!=now+2*configured) return false;
#ifdef MOSAICO_BOARD
  Owner bootstrap;
  bootstrap.step(true,now,configured);
  if (!bootstrap.updateWindow || bootstrap.logs!=1 || bootstrap.nextRefresh!=now+configured) return false;
  if (bootstrap.windowDeadline!=now+std::min<int64_t>(90000000,configured*3/4)) return false;
#endif
 }
 return true;
}
static_assert(ownerTests());
'''
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'first_quota.cpp'
            source.write_text(code, encoding='utf8')
            for defines in ([], ['-DMOSAICO_BOARD', '-DSOC_WIFI_HE_SUPPORT=0'],
                            ['-DMOSAICO_BOARD', '-DSOC_WIFI_HE_SUPPORT=1']):
                with self.subTest(defines=defines):
                    result = subprocess.run([str(COMPILERS[-1]), '-std=c++17', '-fsyntax-only',
                                             '-I' + str(ROOT), *defines, str(source)],
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_owner_path_and_bounded_private_logs(self):
        lock = SOURCE.index('firstQuotaBootstrap = firstQuotaRefresh.takeOnLock(_accepted != 0);')
        opening = SOURCE.index('if (locked && !gaugeHold && !updateWindow && (_force_refresh.exchange(false) || now >= nextRefresh))', lock)
        off = SOURCE.index('if (locked && !updateWindow) {', opening)
        self.assertLess(lock, opening)
        self.assertLess(opening, off)
        between = SOURCE[lock:off]
        self.assertNotIn('GetTailnetQuota().pause()', between)
        self.assertNotIn('esp_wifi_stop()', between)
        self.assertIn('wifiRetry.reset(); _wifi_scan_complete=false;', between)
        self.assertIn('#if SOC_WIFI_HE_SUPPORT\n            firstQuotaBootstrap = firstQuotaBootstrap && !_twt.live();', between)
        self.assertIn('windowDeadline = now + updateWindowUs;', between)
        self.assertIn('reason=first_quota_pending budget_ms=%u', between)
        self.assertEqual(SOURCE.count('reason=first_quota_pending'), 1)
        success = SOURCE.split('const bool quotaOk = fetch();', 1)[1].split('++_failures;', 1)[0]
        self.assertIn('if (quotaOk) {', success)
        self.assertIn('if (_accepted == 0)', success)
        self.assertIn('reason=first_quota_accepted boot_elapsed_ms=%llu', success)
        self.assertIn('static_cast<unsigned long long>(esp_timer_get_time() / 1000)', success)
        self.assertEqual(SOURCE.count('reason=first_quota_accepted'), 1)


if __name__ == '__main__':
    unittest.main()
