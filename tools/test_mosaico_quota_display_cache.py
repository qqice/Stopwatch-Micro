"""Execute the production display/cache helpers at compile time; no device I/O."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / 'main/apps/app_codex_micro/view'


class QuotaDisplayCacheTests(unittest.TestCase):
    def test_actual_cache_and_deadline_policy(self):
        compilers = sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
        self.assertTrue(compilers, 'Required embedded compiler unavailable')
        ui = (VIEW / 'view_mosaico.cpp').read_text(encoding='utf8')
        refresh = ui.split('void CodexMicroView::refreshQuota(uint32_t now) {', 1)[1]
        acquisition = refresh.split('    char buf[128];', 1)[0]
        code = '#include "' + (VIEW / 'quota_display_cache.h').as_posix() + '"\n'
        code += '#include "' + (VIEW / 'reset_countdown.h').as_posix() + '"\n'
        code += r'''
using namespace mosaico_quota_display;
struct Harness {
 QuotaMonitorSnapshot snapshot{}, replacement{};
 QuotaMonitorSnapshot* _quota = &snapshot;
 uint32_t _quotaRevision = UINT32_MAX;
 DisplayAge _quotaAge;
 bool copyValid = false;
 constexpr bool CopyQuotaMonitor(QuotaMonitorSnapshot& out, uint32_t now) {
  if (!copyValid) return false;
  out = replacement;
  const uint64_t age=static_cast<uint64_t>(out.ageSecondsAtReceipt)+(now-out.receivedAtMs)/1000U;
  out.ageSeconds=static_cast<uint32_t>(age);out.stale=age>130;out.available=age<=600;
  return true;
 }
 constexpr bool refresh(uint32_t now) {
''' + acquisition + r'''
  return displayKnown;
 }
};
constexpr bool cases() {
 Harness h;
 if(h.refresh(3600000) || h.snapshot.bucketCount || h.snapshot.available)return false; // Cold start.
 h.replacement.bucketCount=1;h.replacement.capturedEpoch=100000;
 h.replacement.revision=7;h.replacement.receivedAtMs=1000;
 h.replacement.buckets[0].windows[0]={true,8123,300,110000};
 h.copyValid=true;
 if(!h.refresh(1000) || h._quotaRevision!=7 || !h.snapshot.available || h.snapshot.stale)return false;
 h.copyValid=false; // Actual failed-copy fallback ages, but retains the validated snapshot.
 for(uint32_t seconds : {601U,3600U}) {
  if(!h.refresh(1000+seconds*1000) || h.snapshot.available || !h.snapshot.stale)return false;
  const auto& w=h.snapshot.buckets[0].windows[0];
  if(w.remainingBasisPoints!=8123 || !percentKnown(w,100000+seconds))return false;
  if(!mosaico_time::countdown(w.available,h.snapshot.capturedEpoch,w.resetEpoch,100000+seconds).known)return false;
 }
 auto& b=h.snapshot.buckets[0];auto& w=b.windows[0];
 for(uint64_t epoch : {110000ULL,110001ULL,999999ULL}) {
  if(percentKnown(w,epoch) || w.remainingBasisPoints!=8123)return false; // No rollover/100 fabrication.
  const auto time=mosaico_time::countdown(w.available,h.snapshot.capturedEpoch,w.resetEpoch,epoch);
  if(!time.known || time.minutes!=0)return false;
 }
 w.resetEpoch=0;
 if(!percentKnown(w,999999) || mosaico_time::countdown(w.available,h.snapshot.capturedEpoch,w.resetEpoch,999999).known)return false;
 b.windows[0].available=false;b.windows[1]={true,9700,10080,120000};
 if(primaryWindow(b,103600)!=1 || b.windows[primaryWindow(b,103600)].remainingBasisPoints!=9700)return false;
 h.copyValid=true;h.replacement.capturedEpoch=110001;h.replacement.receivedAtMs=10001000;
 h.replacement.revision=8;h.replacement.buckets[0].windows[0]={true,9234,300,120000};
 if(!h.refresh(10001000) || h._quotaRevision!=8 || !h.snapshot.available || h.snapshot.stale)return false;
 if(!percentKnown(h.snapshot.buckets[0].windows[0],110001) || h.snapshot.buckets[0].windows[0].remainingBasisPoints!=9234)return false;
 QuotaMonitorSnapshot empty{};
 empty.bucketCount=1;if(hasSnapshot(empty,true))return false;
 empty.capturedEpoch=1;if(hasSnapshot(empty,false))return false;
 empty.bucketCount=0;return !hasSnapshot(empty,true);
}
constexpr bool wrapCases() {
 Harness h;h.copyValid=true;h.replacement.bucketCount=1;h.replacement.capturedEpoch=100000;
 h.replacement.revision=10;h.replacement.receivedAtMs=1000;
 h.replacement.buckets[0].windows[0]={true,8123,300,110000};
 if(!h.refresh(1000))return false;
 uint32_t now=1000;uint64_t elapsed=0;
 // Frequent GUI deltas span a complete 49.7-day cycle, with success/failure interleaved.
 for(unsigned i=0;i<4294;++i) {
  now+=1000000U;elapsed+=1000000ULL;h.copyValid=(i%2)==0;
  if(!h.refresh(now) || h._quotaAge.secondsAt(now)!=elapsed/1000)return false;
  if(elapsed>=11000000 && (h.snapshot.available || !h.snapshot.stale ||
      percentKnown(h.snapshot.buckets[0].windows[0],100000+h._quotaAge.secondsAt(now))))return false;
 }
 // At the original millis timestamp modulo a full cycle, raw copy appears fresh.
 now=1000;h.copyValid=true;
 if(!h.refresh(now) || h.snapshot.available || !h.snapshot.stale)return false;
 const uint64_t epoch=100000+h._quotaAge.secondsAt(now);
 const auto& w=h.snapshot.buckets[0].windows[0];
 if(percentKnown(w,epoch) || w.remainingBasisPoints!=8123)return false;
 const auto time=mosaico_time::countdown(w.available,h.snapshot.capturedEpoch,w.resetEpoch,epoch);
 if(!time.known || time.minutes!=0)return false;
 // Only an actually accepted new revision can restart age/replace the old deadline.
 h.replacement.revision=11;h.replacement.receivedAtMs=now;h.replacement.capturedEpoch=200000;
 h.replacement.buckets[0].windows[0]={true,9234,300,210000};
 if(!h.refresh(now) || h._quotaAge.secondsAt(now)!=0 || !h.snapshot.available || h.snapshot.stale)return false;
 return percentKnown(h.snapshot.buckets[0].windows[0],200000);
}
static_assert(wrapCases(),"millis full wrap and repeated modulo copies cannot revive an old window");
static_assert(cases(),"last validated cache survives age/copy failures without extending reset deadlines");
'''
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'quota_display_cache.cpp'
            source.write_text(code, encoding='utf8')
            result = subprocess.run([str(compilers[-1]), '-std=c++17', '-fsyntax-only', '-I'+str(ROOT/'main'), str(source)], capture_output=True, text=True)
        artifacts = ROOT / 'artifacts/quota-display-cache'
        artifacts.mkdir(parents=True, exist_ok=True)
        (artifacts / 'helper-compile.log').write_text(result.stdout+result.stderr, encoding='utf8')
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)

    def test_homepage_and_lock_use_same_policy(self):
        ui = (VIEW / 'view_mosaico.cpp').read_text(encoding='utf8')
        refresh = ui.split('void CodexMicroView::refreshQuota(uint32_t now) {', 1)[1].split('void CodexMicroView::historyChartEvent', 1)[0]
        self.assertIn('if (!displayKnown || i >= _quota->bucketCount)', refresh)
        self.assertNotIn('_quota->available', refresh)
        self.assertIn('if (percentKnown) { percent(w.remainingBasisPoints', refresh)
        self.assertIn('w.remainingBasisPoints, percentKnown, displayColor', refresh)
        for index in (0, 1):
            self.assertIn(f'percentKnown(b.windows[{index}], epoch)', refresh)
            self.assertIn(f'countdown(displayKnown && firstWindow[{index}].available', refresh)
        self.assertIn('primaryWindow(_quota->buckets[0], epoch)', refresh)
        self.assertIn('lockKnown ? quotaLevelColor(lockBp, _quota->stale) : Gray', refresh)
        self.assertNotIn('"CACHED"', refresh)
        self.assertNotIn('requestJson(', refresh)
        animation = ui.split('void CodexMicroView::updateAnimations(', 1)[1].split('void CodexMicroView::refreshQuota(', 1)[0]
        self.assertIn('mosaico_quota_display::percentKnown(window, epoch)', animation)
        self.assertIn('_quota->available && !_quota->stale && age <= 130', animation)


if __name__ == '__main__':
    unittest.main()
