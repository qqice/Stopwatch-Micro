"""Cold-start automatic-lock gate and local locked-cache delivery; no device I/O."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / 'main/apps/app_codex_micro/view'
CPP = (VIEW / 'view_mosaico.cpp').read_text(encoding='utf8')
UPDATE = CPP.split('void CodexMicroView::update(', 1)[1]


class FirstQuotaLockTests(unittest.TestCase):
    def test_production_gate_and_update_fragments(self):
        compilers = sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob(
            '*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
        self.assertTrue(compilers, 'Required C++ compiler unavailable')
        revision = UPDATE.split('    const uint32_t quotaRevision =', 1)[1].split(
            '    // Rendering/page callbacks', 1)[0]
        revision = '    const uint32_t quotaRevision =' + revision
        auto_lock = next(line for line in UPDATE.splitlines() if 'firstQuotaReady &&' in line)
        known = CPP.split('    const bool lockKnown =', 1)[1].split('    setText(_lockQuota', 1)[0]
        known = '    const bool lockKnown =' + known
        code = r'''
#include "main/apps/app_codex_micro/view/first_quota_lock_gate.h"
#include "main/apps/app_codex_micro/view/quota_display_cache.h"
using namespace mosaico_quota_display;
struct Hal { uint32_t tick=0; constexpr uint32_t millis() const { return tick; } };
struct Harness {
 FirstQuotaLockGate _firstQuotaLockGate;
 QuotaMonitorSnapshot snapshot{};
 bool _locked=false, interacting=false, keepAwake=false, copySucceeds=true;
 uint32_t timeoutSeconds=15, idleElapsed=0, _quotaObservedRevision=UINT32_MAX;
 uint32_t _quotaAttemptedRevision=UINT32_MAX, _quotaAttemptTick=0, _quotaRevision=UINT32_MAX;
 uint32_t revision=0, copies=0, locks=0;
 Hal hal;
 constexpr explicit Harness(uint32_t started=0) : _firstQuotaLockGate(started) {}
 constexpr uint32_t QuotaMonitorRevision() const { return revision; }
 constexpr Hal& GetHAL() { return hal; }
 constexpr void refreshQuota(uint32_t now) {
  ++copies;
  if(!copySucceeds) return;
  _quotaRevision=revision;
  const bool displayKnown = hasSnapshot(snapshot, true);
  const uint64_t epoch = snapshot.capturedEpoch + now/1000;
  const auto& firstWindow = snapshot.buckets[0].windows;
KNOWN
  _firstQuotaLockGate.quotaRendered(lockKnown);
 }
 constexpr void lockDisplay() { _locked=true; ++locks; }
 constexpr void update(uint32_t tick) {
  hal.tick=tick;
  const bool firstQuotaReady = _firstQuotaLockGate.allowsAutoLock(tick);
REVISION
  idleElapsed=tick-_firstQuotaLockGate.startedMs;
AUTO_LOCK
 }
 constexpr void valid(uint16_t bp=8100, bool stale=false) {
  snapshot.bucketCount=1;snapshot.capturedEpoch=100000;snapshot.stale=stale;
  snapshot.buckets[0].windows[0]={true,bp,300,200000};++revision;
 }
};
constexpr bool cases() {
 Harness cold;
 cold.update(15000);cold.update(89999);
 if(cold._locked || cold.copies!=1)return false; // No snapshot, one revision attempt.
 cold.update(90000);if(!cold._locked)return false; // Offline bounded fallback.
 Harness good;good.valid();good.update(1000);
 if(good._locked || !good._firstQuotaLockGate.rendered)return false;
 good.update(15000);if(!good._locked)return false; // Render then normal idle lock.
 Harness late;late.update(15000);late.valid();late.update(16000);
 if(late._locked)return false; // Newly copied cache cannot lock in the same iteration.
 late.update(16001);if(!late._locked)return false;
 for(bool stale : {false,true}) {
  Harness zero;zero.valid(0,stale);zero.update(15000);
  if(zero._locked || !zero._firstQuotaLockGate.rendered)return false;
  zero.update(15001);if(!zero._locked)return false; // 0%, including valid stale cache.
 }
 Harness expired;expired.valid();expired.snapshot.buckets[0].windows[0].resetEpoch=100015;
 expired.update(15000);expired.update(15001);
 if(expired._locked || expired._firstQuotaLockGate.rendered)return false;
 expired.update(90000);if(!expired._locked)return false;
 Harness second;second.valid();second.snapshot.buckets[0].windows[0].available=false;
 second.snapshot.buckets[0].windows[1]={true,0,10080,200000};
 second.update(15000);second.update(15001);if(!second._locked)return false;
 Harness empty;empty.snapshot.capturedEpoch=100000;empty.snapshot.bucketCount=1;
 empty.update(15000);empty.update(15001);
 if(empty._locked || empty._firstQuotaLockGate.rendered)return false;
 Harness manual;manual.lockDisplay();if(!manual._locked)return false;
 // Gate never overrides NEVER, OTA keep-awake or active touch/scrolling.
 for(unsigned mode=0;mode<3;++mode) {
  Harness guarded;guarded.valid();guarded.update(1000);
  if(mode==0)guarded.timeoutSeconds=0;
  if(mode==1)guarded.keepAwake=true;
  if(mode==2)guarded.interacting=true;
  guarded.update(100000);if(guarded._locked)return false;
 }
 Harness locked;locked.lockDisplay();locked.update(1000);
 locked.valid();locked.update(1001);locked.update(1002);
 if(locked.copies!=2 || locked.locks!=1 || !locked._firstQuotaLockGate.rendered)return false;
 locked.copySucceeds=false;++locked.revision;locked.update(1003);locked.update(1004);
 if(locked.copies!=3)return false; // Failed local copy cannot cause a per-frame retry storm.
 locked.copySucceeds=true;locked.update(2002);if(locked.copies!=3)return false;
 locked.update(2003);locked.update(3003);
 if(locked.copies!=4 || locked._quotaObservedRevision!=locked.revision)return false;
 FirstQuotaLockGate wrap(0xfffffff0U);
 if(wrap.allowsAutoLock(0x15f7fU) || !wrap.allowsAutoLock(0x15f80U))return false;
 if(!wrap.allowsAutoLock(0xfffffff0U))return false; // A later full wrap cannot re-close it.
 wrap.quotaRendered(true);wrap.quotaRendered(false);return wrap.allowsAutoLock(0x15f81U);
}
static_assert(cases(), "cold lock, deadline, wrap and locked revision delivery");
'''.replace('KNOWN', known).replace('REVISION', revision).replace('AUTO_LOCK', auto_lock)
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'first_quota_lock.cpp'
            source.write_text(code, encoding='utf8')
            result = subprocess.run([str(compilers[-1]), '-std=c++17', '-fsyntax-only',
                                     '-I'+str(ROOT), '-I'+str(ROOT/'main'), str(source)],
                                    capture_output=True, text=True)
        artifacts = ROOT / 'artifacts/first-quota-lock'
        artifacts.mkdir(parents=True, exist_ok=True)
        (artifacts / 'helper-compile.log').write_text(result.stdout+result.stderr, encoding='utf8')
        self.assertEqual(result.returncode, 0, str(artifacts / 'helper-compile.log'))

    def test_source_order_and_local_only_boundaries(self):
        self.assertIn('CodexMicroView::CodexMicroView() : _firstQuotaLockGate(lv_tick_get())', CPP)
        self.assertEqual(CPP.count('_firstQuotaLockGate.quotaRendered('), 1)
        self.assertLess(CPP.index('setText(_lockResetTime, lockReset, timeColor)'),
                        CPP.index('_firstQuotaLockGate.quotaRendered(lockKnown)'))
        self.assertLess(UPDATE.index('allowsAutoLock(tick)'), UPDATE.index('const uint32_t quotaRevision'))
        self.assertLess(UPDATE.index('const uint32_t quotaRevision'), UPDATE.index('firstQuotaReady &&'))
        block = UPDATE.split('const uint32_t quotaRevision', 1)[1].split('// Rendering/page', 1)[0]
        self.assertNotIn('_locked', block)
        for forbidden in ('refreshHistory', 'requestLockedSessions', 'requestJson', 'requestRefresh', 'wifi'):
            self.assertNotIn(forbidden, block)
        self.assertIn('refreshElapsed >= 60000U && refreshElapsed < 0x80000000U', UPDATE)
        self.assertIn('if (_locked) { ++_lockRefreshCount;requestLockedSessions();renderLockSessions(); }', UPDATE)
        manual = CPP.split('void CodexMicroView::lockDisplay()', 1)[1].split('void CodexMicroView::update', 1)[0]
        self.assertNotIn('_firstQuotaLockGate', manual)
        self.assertIn('if (_locked || !ready() || otaKeepAwake()) return;', manual)
        self.assertIn('lockDisplay(); return _locked;', manual)
        self.assertEqual(CPP.count('_quotaRevision != QuotaMonitorRevision()'), 0)
        init = CPP.split('void CodexMicroView::init(lv_obj_t* parent)', 1)[1].split('// Settings reuse', 1)[0]
        self.assertIn('_quotaObservedRevision = _quotaRevision;', init)
        self.assertNotIn('_quotaObservedRevision = QuotaMonitorRevision()', init)


if __name__ == '__main__':
    unittest.main()
