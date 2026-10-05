"""Pure model execution via compile-time assertions; no board, linking or build."""
from pathlib import Path
import subprocess, tempfile, unittest
ROOT = Path(__file__).resolve().parents[1]
COMPILER = Path('C:/Espressif/tools/riscv32-esp-elf/esp-15.2.0_20251204/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe')
class OrientationTests(unittest.TestCase):
 def test_initial_portrait_is_explicit_checked_transaction(self):
  hal=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()
  init=hal.split('void Hal::lvgl_init()',1)[1].split('bool Hal::lvglLock()',1)[0]
  created=init.index('display = lvgl_port_add_disp')
  swapped=init.index('checked_swap(&rotation_control, false);')
  mirrored=init.index('checked_mirror(&rotation_control, false, false);')
  checked=init.index('if (!rotation_result.ok())')
  self.assertTrue(created < swapped < mirrored < checked)
  self.assertIn('rotation_result.reset();',init[created:swapped])
 def test_actual_cpp_model(self):
  code = r'''
#include "main/hal/mosaico/mosaico_orientation_model.h"
using mosaico_orientation::Model;
constexpr bool test() {
 Model m;
 if(m.sample(1,0,0,0)||m.sample(1,0,0,599)||!m.sample(1,0,0,600)||m.degrees!=270) return false;
 if(m.sample(0,0,1,700)||m.degrees!=270) return false;
 if(m.sample(-1,0,0,800)||m.sample(.7,.7,0,1300)||m.sample(-1,0,0,1400)) return false;
 if(m.sample(-1,0,0,1999)||!m.sample(-1,0,0,2000)||m.degrees!=90) return false;
 if(m.sample(0,-1,0,2100)||!m.sample(0,-1,0,2700)||m.degrees!=180) return false;
 if(m.sample(0,1,0,2800)||!m.sample(0,1,0,3400)||m.degrees!=0) return false;
 if(m.sample(2,0,0,3500)||m.sample(0,0,0,3600)) return false;
 m.sample(1,0,0,4000); m.resetPending();
 if(m.sample(1,0,0,4700)||m.sample(1,0,0,5299)||!m.sample(1,0,0,5300)) return false;
 m.sample(0,1,0,6000); m.sample(0,1,0,5900);
 return !m.sample(0,1,0,6499) && m.sample(0,1,0,6500);
}
static_assert(test());
constexpr bool controlTest() {
 using namespace mosaico_orientation;
 ControlResult c;
 // Forward errors remain latched even if the second callback succeeds/fails.
 if(c.record(-7)!=-7 || c.record(0)!=0 || c.ok() || c.error!=-7) return false;
 c.reset(); c.record(0); c.record(-9);
 if(c.ok() || c.error!=-9) return false;
 c.reset(); c.record(-7); c.record(-9);
 if(c.error!=-7) return false;
 c.reset(); c.record(0);
 if(c.ok()) return false; // Missing callback is never claimed as applied.
 c.record(0);
 return c.ok() && rotationOutcome(true,false)==RotationOutcome::Applied &&
  rotationOutcome(false,true)==RotationOutcome::Restored &&
  rotationOutcome(false,false)==RotationOutcome::Unsafe;
}
static_assert(controlTest());
'''
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'model.cpp';path.write_text(code)
   result=subprocess.run([str(COMPILER),'-std=c++17','-fsyntax-only','-I'+str(ROOT),str(path)],capture_output=True,text=True)
   self.assertEqual(result.returncode,0,result.stdout+result.stderr)
 def test_sensor_only_and_cached_contract(self):
  hal=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()
  motion=hal.split('void motion_task(',1)[1].split('void update_max(',1)[0]
  self.assertNotIn('bmi270_start(', motion)
  self.assertNotIn('get_gyro_data',motion)
  self.assertNotIn('gpio_set_level',motion)
  self.assertIn('idle ? 0x00 : 0x04',motion)
  self.assertIn('portMAX_DELAY',motion)
  self.assertIn('pdMS_TO_TICKS(80)',motion)
  cache=hal.split('Hal::MotionOrientation Hal::motionOrientation()',1)[1].split('void Hal::setMotionIdle',1)[0]
  self.assertNotIn('i2c_',cache)
  lvgl=(ROOT/'components/lvgl/src/indev/lv_indev.c').read_text()
  self.assertIn('lv_display_rotate_point(i->disp, &data->point)',lvgl)
 def test_checked_proxy_transaction(self):
  hal=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()
  self.assertIn('disp_config.control_handle = &rotation_control',hal)
  self.assertIn('disp_config.panel_handle = panel',hal)
  self.assertIn('rotation_result.record(esp_lcd_panel_swap_xy(panel, swap))',hal)
  self.assertIn('rotation_result.record(esp_lcd_panel_mirror(panel, x, y))',hal)
  setter=hal.split('bool Hal::setDisplayOrientation(',1)[1]
  self.assertLess(setter.index('const bool applied = rotation_result.ok()'),setter.index('display_degrees.store(degrees)'))
  self.assertIn('static_cast<lv_display_rotation_t>(oldDegrees / 90)',setter)
  self.assertIn('orientation_healthy.store(false)',setter)
  self.assertIn('lv_timer_pause(timer)',setter)
  read=hal.split('void read_touch(',1)[1].split('void gauge_feed()',1)[0]
  self.assertLess(read.index('!orientation_healthy.load()'),read.index('esp_lcd_touch_read_data'))
 def test_minimum_delay_arithmetic(self):
  # Requested ms is floored to ticks by driver. At worst start just before
  # the boundary: ticks+2 consumes >=ticks+1 full tick intervals.
  for hz in (100,250,1000):
   for ms in (1,2,10,20,150):
    ticks=ms*hz//1000
    self.assertGreaterEqual((ticks+1)*1000/hz,ms)
if __name__=='__main__': unittest.main()
