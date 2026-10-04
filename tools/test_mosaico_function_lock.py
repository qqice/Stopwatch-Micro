"""Function-only lock and countdown integration, not real sleep/power tests."""
import shutil,subprocess,tempfile,unittest
from pathlib import Path
R=Path(__file__).resolve().parents[1]
HAL=(R/'main/hal/mosaico/hal_mosaico.cpp').read_text(encoding='utf8')
VIEW=(R/'main/apps/app_codex_micro/view/view_mosaico.cpp').read_text(encoding='utf8')
class FunctionLockTests(unittest.TestCase):
 def test_actual_countdown_cpp_edges(self):
  comp=shutil.which('g++') or shutil.which('clang++')
  if not comp:
   found=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
   comp=str(found[-1]) if found else None
  if not comp:self.skipTest('C++ compiler unavailable')
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'time.cpp'
   p.write_text('#include "'+(R/'main/apps/app_codex_micro/view/reset_countdown.h').as_posix()+'"\n'
     'using namespace mosaico_time; static_assert(!countdown(true,10,0,20).known); static_assert(countdown(true,10,11,10).minutes==1); static_assert(countdown(true,10,70,10).minutes==1); static_assert(countdown(true,10,71,10).minutes==2); static_assert(countdown(true,10,3610,10).minutes==60); static_assert(countdown(true,10,86410,10).minutes==1440); static_assert(countdown(true,10,0xffffffffU,0xffffffffULL+60).minutes==0);',encoding='utf8')
   q=subprocess.run([comp,'-std=c++17','-fsyntax-only',str(p)],capture_output=True,text=True)
   self.assertEqual(q.returncode,0,q.stdout+q.stderr)
 def test_paused_driver_guard_is_before_i2c(self):
  cb=HAL.split('void read_touch(',1)[1].split('void gauge_feed()',1)[0]
  self.assertLess(cb.index('touch_idle_polling.load'),cb.index('esp_lcd_touch_read_data'))
  self.assertLess(cb.index('return;'),cb.index('touch_reads.fetch_add'))
  setter=HAL.split('void Hal::setTouchIdlePolling(',1)[1].split('Hal::TouchPollingInfo',1)[0]
  for token in ['lv_timer_pause(timer)','lv_timer_resume(timer)','lv_indev_reset','lv_indev_wait_release','touch_wait_for_release = true']:
   self.assertIn(token,setter)
  self.assertNotIn('pollingPeriodMs(idle)',setter)
 def test_touch_wake_handler_removed(self):
  self.assertNotIn('wakeEvent',VIEW)
  self.assertNotIn('_wakeGesture',VIEW)
  self.assertIn('lv_obj_remove_flag(_overlay, LV_OBJ_FLAG_CLICKABLE)',VIEW)
  toggle=VIEW.split('void CodexMicroView::togglePage()',1)[1].split('void CodexMicroView::wakeDisplay()',1)[0]
  self.assertIn('if (_locked) { wakeDisplay(); return; }',toggle)
 def test_lock_countdown_is_local_minute_update(self):
  self.assertIn('_lockResetIcon = createIcon',VIEW)
  self.assertIn('_lockResetTime = createText',VIEW)
  self.assertIn('formatCountdown(leftTime, leftReset, sizeof(leftReset), true)',VIEW)
  self.assertIn('formatCountdown(rightTime, rightReset, sizeof(rightReset), true)',VIEW)
  self.assertIn('"%llud%lluh%llum"',VIEW)
  self.assertIn('bool minutePrecision = true',VIEW) # same minute precision awake/locked.
  refresh=VIEW.split('void CodexMicroView::refreshQuota(',1)[1].split('void CodexMicroView::refreshHistory',1)[0]
  self.assertIn('const uint64_t epoch =',refresh)
  self.assertNotIn('requestJson(',refresh)
 def test_row_center_and_bounds(self):
  for glyphs in range(1,33):
   columns=max(1,glyphs*6-1);width=columns*max(1,min(6,360//columns))
   left=(480-(28+12+width))//2
   self.assertGreaterEqual(left,20);self.assertLessEqual(left+40+width,460)
  self.assertTrue(148+90<252 and 252+48<334 and 334+36<480)
if __name__=='__main__':unittest.main()
