"""Compile-time execution of production request block; no device or network I/O."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPILER = Path('C:/Espressif/tools/riscv32-esp-elf/esp-15.2.0_20251204/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe')


class QuotaRequestPolicyTests(unittest.TestCase):
    def test_actual_request_block(self):
        source = (ROOT / 'main/host/network_quota.cpp').read_text(encoding='utf8')
        start = source.index('#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT\n        if (locked && !updateWindow && _twt.live()) continue;')
        end = source.index('        wait(locked ? 5000 : 60000);', start)
        block = source[start:end] + '        wait(locked ? 5000 : 60000);\n'
        code = r'''
#include "main/host/mosaico_quota_request_policy.h"
#include "main/host/mosaico_twt_model.h"
struct Tailnet { constexpr bool enabled() const { return false; } constexpr bool ready() const { return true; } };
constexpr Tailnet GetTailnetQuota() { return {}; }
constexpr int64_t esp_timer_get_time() { return 100; }
struct Owner {
 struct Trial {
  struct State { uint16_t id=1; MosaicoTwt::Stage stage=MosaicoTwt::Stage::Active;
   bool cleanupPending=false; unsigned fetchAttempts=0,fetchOk=0; uint64_t fetchUs=0; } state;
  bool active=true; constexpr bool live() const { return active; }
 } _twt;
 bool uiLocked=true, afterQuotaLocked=true, afterHistoryLocked=true, powerIdle=true;
 bool quotaResult=true, historyResult=true, updateWindow=true;
 bool _wifi_running=true, _twt_cleanup_failed=false;
 int trialAction=0, services=0, quotaCalls=0, historyCalls=0, waitMs=0;
 unsigned _accepted=0,_failures=0,_history_accepted=0,_history_failures=0,_power_cycles=0;
 uint8_t flags=0; MosaicoTwt::CycleReason reason=MosaicoTwt::CycleReason::None;
 constexpr bool displayLocked() const { return uiLocked; }
 constexpr bool idleLocked() const { return uiLocked && powerIdle; }
 constexpr bool fetch() { ++quotaCalls; uiLocked=afterQuotaLocked; return quotaResult; }
 constexpr bool fetchHistory() { ++historyCalls; uiLocked=afterHistoryLocked; return historyResult; }
 constexpr void serviceTwtTrial(int64_t,bool) {
  ++services;
  if(trialAction==1) _twt.active=false;
  if(trialAction==2 && services==1) ++_twt.state.id;
 }
 constexpr void recordTwtCycle(MosaicoTwt::CyclePhase,MosaicoTwt::CycleReason,uint8_t value) { flags=value; }
 constexpr void publishTwt() {}
 constexpr void closeWindow(MosaicoTwt::CycleReason value) { updateWindow=false; reason=value; }
 constexpr void wait(int value) { waitMs=value; }
 constexpr void run() {
  const bool locked=idleLocked();
  do {
''' + block + r'''
  } while(false);
 }
};
constexpr bool traces() {
 Owner locked; locked.historyResult=false; locked.run();
 if(locked.quotaCalls!=1 || locked.historyCalls || locked._history_failures || locked._power_cycles!=1 ||
    locked.updateWindow || locked.reason!=MosaicoTwt::CycleReason::Success || locked.waitMs) return false;
 Owner failed; failed.quotaResult=false; failed.run();
 if(failed.historyCalls || !failed.updateWindow || failed._power_cycles || failed.waitMs!=5000) return false;
 Owner awake; awake.uiLocked=awake.afterQuotaLocked=awake.afterHistoryLocked=false; awake.run();
 if(awake.quotaCalls!=1 || awake.historyCalls!=1 || awake._history_accepted!=1 || awake._power_cycles || awake.waitMs!=60000) return false;
 Owner awakeFail; awakeFail.uiLocked=awakeFail.afterQuotaLocked=awakeFail.afterHistoryLocked=false;
 awakeFail.historyResult=false; awakeFail.run();
 if(awakeFail.historyCalls!=1 || awakeFail._history_failures!=1) return false;
 Owner wake; wake.afterQuotaLocked=false; wake.run();
 if(wake.historyCalls || wake._power_cycles || !wake.updateWindow) return false;
 Owner sleep; sleep.uiLocked=false; sleep.run();
 if(sleep.historyCalls || sleep._power_cycles) return false;
 // Actual display lock still suppresses history with idle power disabled.
 Owner profile0; profile0.powerIdle=false; profile0.run();
 if(profile0.historyCalls || profile0._power_cycles) return false;
#if SOC_WIFI_HE_SUPPORT
 if(locked.flags!=3 || locked._twt.state.fetchOk!=1 || locked._twt.state.fetchAttempts!=1 ||
    failed.flags!=1 || failed._twt.state.fetchOk || awake.flags!=15 || awake._twt.state.fetchOk!=1 ||
    awakeFail.flags!=7 || awakeFail._twt.state.fetchOk) return false;
 Owner bothFail; bothFail.uiLocked=bothFail.afterQuotaLocked=bothFail.afterHistoryLocked=false;
 bothFail.quotaResult=bothFail.historyResult=false; bothFail.run();
 if(bothFail.flags!=5 || bothFail._twt.state.fetchOk) return false;
 Owner cancelled; cancelled.trialAction=1; cancelled.afterQuotaLocked=false; cancelled.run();
 if(cancelled.historyCalls || cancelled._twt.state.fetchAttempts || cancelled._twt.state.fetchOk || cancelled._power_cycles) return false;
 Owner replaced; replaced.trialAction=2; replaced.run();
 if(replaced.historyCalls || replaced._twt.state.fetchAttempts || replaced._twt.state.fetchOk) return false;
#endif
 return true;
}
static_assert(traces(), "production request policy transitions");
'''
        self.assertTrue(COMPILER.is_file(), 'existing cross compiler required')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'quota.cpp'
            path.write_text(code, encoding='utf8')
            for he_support in (0, 1):
                with self.subTest(he_support=he_support):
                    result = subprocess.run([str(COMPILER), '-std=c++17', '-fsyntax-only',
                                             '-DMOSAICO_BOARD', f'-DSOC_WIFI_HE_SUPPORT={he_support}',
                                             '-I', str(ROOT), str(path)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_existing_budget_deadline_and_cadence(self):
        source = (ROOT / 'main/host/network_quota.cpp').read_text(encoding='utf8')
        self.assertIn('if (locked && updateWindow && now >= windowDeadline) closeWindow(MosaicoTwt::CycleReason::Deadline);', source)
        self.assertIn('closeWindow(MosaicoTwt::CycleReason::RetryBudget)', source)
        self.assertIn('constexpr int64_t UpdateWindowUs    = 90LL * 1000000;', source)
        self.assertIn('updateWindowUs=std::min<int64_t>(UpdateWindowUs, refreshIntervalUs*3/4);', source)
        self.assertIn('wait(locked ? 5000 : 60000);', source)
        self.assertIn('config.timeout_ms               = 7000;', source)
        closer = source.split('auto closeWindow =', 1)[1].split('};', 1)[0]
        self.assertNotIn('nextRefresh', closer)


if __name__ == '__main__':
    unittest.main()
