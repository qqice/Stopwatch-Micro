"""Bounded actual C++ cadence checks; no firmware build, device or network."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / 'main/apps/app_codex_micro/view'


class DotAnimationCadenceTests(unittest.TestCase):
    def test_existing_gates_and_wallclock_periods(self):
        app = VIEW.parent
        self.assertIn('UiRefreshPeriodMs = 33;', (app / 'app_codex_micro.h').read_text(encoding='utf8'))
        self.assertIn('now - _last_ui_update_ms >= UiRefreshPeriodMs', (app / 'app_codex_micro.cpp').read_text(encoding='utf8'))
        self.assertIn('Cause::Awake ? 10', (ROOT / 'main/main_idle_wait_model.h').read_text(encoding='utf8'))
        self.assertIn('vTaskDelay(1);', (ROOT / 'main/hal/hal.cpp').read_text(encoding='utf8'))
        self.assertIn('CONFIG_FREERTOS_HZ=1000', (ROOT / 'sdkconfig').read_text(encoding='utf8'))
        source = (VIEW / 'view_mosaico.cpp').read_text(encoding='utf8')
        motion = source.split('void CodexMicroView::updateAnimations(uint32_t tick) {', 1)[1].split('void CodexMicroView::refreshQuota', 1)[0]
        self.assertIn('!(_locked || _suppressed || _slideTo || _rotationFault || _rotationPhase != RotationPhase::Idle)', motion)
        self.assertIn('if (!_motionCadence.sample(tick, eligible)) return;', motion)
        for period in (12000, 6000, 1600):
            self.assertIn(f'((tick - _motionEpoch) % {period}U) * 360U / {period}U', motion)
        for gate in ('otaMotion', '_batteryValid && _batteryCharging', 'quotaBodyCovered(_settingsOpen, _settingsAnimating)', 'window.remainingBasisPoints > 0', 'percentKnown(window, epoch)'):
            self.assertIn(gate, motion)

    def test_actual_cpp_deadlines_poll_jitter_wrap_and_suspend(self):
        compilers = sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
        self.assertTrue(compilers, 'local compiler required for actual model validation')
        code = r'''#include "main/apps/app_codex_micro/view/dot_animation_cadence.h"
using mosaico_dot::AnimationCadence;
constexpr bool edgeCases() {
 AnimationCadence c;
 if(!c.sample(0,true)||c.sample(49,true)||!c.sample(50,true))return false;
 // A late call produces only one update, including repeated same-tick calls.
 if(!c.sample(1024,true)||c.sample(1024,true)||c.sample(1049,true)||!c.sample(1050,true))return false;
 // Each existing inhibit uses the same eligibility boundary: no locked updates.
 for(int gate=0;gate<5;++gate) {
  if(c.sample(2000,false)||c.sample(200000,false))return false;
  if(!c.sample(200001,true)||c.sample(200001,true)||c.sample(200050,true)||!c.sample(200051,true))return false;
 }
 AnimationCadence wrap;
 if(!wrap.sample(0xfffffff0U,true)||wrap.sample(33,true)||!wrap.sample(34,true))return false;
 if(wrap.sample(83,true)||!wrap.sample(84,true))return false;
 return AnimationCadence::PeriodMs==50;
}
// Feed the real 33ms UI gate from 10ms awake wait + 1ms watchdog feed.
// Poll jitter is bounded so the UI sampling interval stays below 50ms.
constexpr bool uiPoll(bool jitter) {
 AnimationCadence c;
 unsigned ui=0,start=0,last=0,count=0,naive=0,naiveLast=0;
 for(unsigned tick=0,i=0;tick<60000;tick+=11+(jitter?i%4:0),++i) {
  if(tick-ui<33)continue;
  ui=tick;
  if(!count)start=tick;
  if(c.sample(tick,true)){++count;last=tick;}
  if(tick-naiveLast>=50){++naive;naiveLast=tick;}
 }
 // No accumulated polling drift: exactly one sample per elapsed 50ms slot.
 return count==1+(ui-start)/50 && last<=ui && count>=1199 && naive<count;
}
static_assert(edgeCases(),"50ms, one-shot lateness, all inhibits, resume and uint32 wrap");
static_assert(uiPoll(false),"actual 33ms UI / 11ms awake polling retains 20Hz average");
static_assert(uiPoll(true),"jitter retains deadline remainder, not last=tick drift");
'''
        log = ROOT / '.artifacts/mosaico/dot-animation-cadence.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'cadence.cpp'
            path.write_text(code, encoding='utf8')
            result = subprocess.run([str(compilers[-1]), '-std=c++17', '-fsyntax-only', '-I' + str(ROOT), str(path)], capture_output=True, text=True)
        log.write_text(result.stdout + result.stderr + f'\ncompiler_exit={result.returncode}\n', encoding='utf8')
        self.assertEqual(result.returncode, 0, str(log))


if __name__ == '__main__':
    unittest.main()
