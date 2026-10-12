"""Haptic deadline/rate-limit model and HAL safety contracts; no board writes."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPILER = Path('C:/Espressif/tools/riscv32-esp-elf/esp-15.2.0_20251204/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe')


class HapticTests(unittest.TestCase):
    def test_actual_cpp_model(self):
        code = r'''
#include "main/hal/mosaico/mosaico_haptic_model.h"
using mosaico_haptic::Model;
constexpr bool test() {
 Model m;
 if(Model::duration(65535)!=50 || Model::strength(255)!=100 || Model::duty(255)!=1023) return false;
 if(!m.begin(0,65535,255) || m.remaining(0)!=50000 || m.remaining(50000)!=0) return false;
 // Frequent presses do not extend the original deadline or duty interval.
 if(m.begin(10000,50,100) || m.deadlineUs!=50000) return false;
 m.stop();m.stop();
 if(m.active || m.remaining(10000)!=0 || m.begin(59999,50,100)) return false;
 if(!m.begin(60000,25,50) || m.deadlineUs!=85000) return false;
 // Previously queued callback at 60ms sees the NEW remaining deadline,
 // not permission to stop. Model failure path cancels without rate reset.
 if(m.remaining(60001)!=24999) return false;
 m.stop();
 if(m.begin(119999,1,1) || !m.begin(120000,50,100)) return false;
 if(m.begin(120001,0,100) || m.active) return false;
 if(!m.begin(180000,50,100) || m.begin(180001,50,0) || m.active) return false;
 // Monotonic regression fails closed; stop never reopens the rate limiter.
 if(m.begin(170000,50,100)) return false;
 for(int64_t t=240000;t<300000;t+=1000) {
   const bool accepted=m.begin(t,25,100);
   if(accepted!=(t==240000)) return false;
 }
 return m.deadlineUs==265000 && m.remaining(300000)==0;
}
static_assert(test());
constexpr bool timerTest() {
 mosaico_haptic::TimerState p;
 if(p.needsPause()) return false;
 p.configuring(); if(!p.needsPause()) return false;
 p.paused(false); if(!p.needsPause()) return false; // Failure cannot pretend quiet.
 p.paused(true); if(p.needsPause()) return false;
 p.paused(true); if(p.needsPause()) return false; // Repeated OFF is idempotent.
 p.resuming(); if(!p.needsPause()) return false;
 p.paused(false); if(!p.needsPause()) return false; // Failed resume cleanup.
 p.paused(true); return !p.needsPause();
}
static_assert(timerTest());
'''
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'model.cpp'
            path.write_text(code)
            result = subprocess.run([str(COMPILER), '-std=c++17', '-fsyntax-only', '-I'+str(ROOT), str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_hal_failure_and_timer_order(self):
        hal = (ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()
        pulse = hal.split('void Hal::vibrate(', 1)[1].split('void Hal::stopVibrate()', 1)[0]
        self.assertLess(pulse.index('haptic_active.store(true'), pulse.index('haptic_attach_locked()'))
        self.assertLess(pulse.index('esp_timer_start_once'), pulse.index('ledc_set_duty'))
        self.assertLess(pulse.index('esp_timer_start_once'), pulse.index('ledc_timer_resume'))
        self.assertLess(pulse.index('ledc_timer_resume'), pulse.index('ledc_set_duty'))
        self.assertLess(pulse.index('ledc_set_duty'), pulse.index('ledc_update_duty'))
        self.assertIn('if (result != ESP_OK)', pulse)
        self.assertIn('haptic_ready.store(false', pulse)
        self.assertIn('haptic_off_locked();', pulse)
        timer = hal.split('void haptic_timeout(', 1)[1].split('esp_err_t haptic_attach_locked()', 1)[0]
        self.assertIn('haptic_model.remaining(esp_timer_get_time())', timer)
        self.assertIn('!esp_timer_is_active(haptic_timer)', timer)
        self.assertIn('haptic_off_locked();', timer)
        self.assertNotIn('start_periodic', hal)

    def test_off_idle_and_sleep_gpio_contract(self):
        hal = (ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()
        off = hal.split('bool haptic_off_locked()', 1)[1].split('void haptic_timeout', 1)[0]
        self.assertLess(off.index('gpio_set_level(GPIO_NUM_8, 0)'), off.index('gpio_config(&motor)'))
        self.assertLess(off.index('gpio_config(&motor)'), off.index('haptic_pause_locked()'))
        self.assertIn('haptic_active.store(!off || paused != ESP_OK', off)
        self.assertIn('paused == ESP_OK', off)
        idle = hal.split('void Hal::setTouchIdlePolling(bool idle)', 1)[1].split('Hal::SleepIoInfo', 1)[0]
        self.assertLess(idle.index('haptic_blocked = idle'), idle.index('if (!port_ready)'))
        self.assertIn('if (idle) haptic_off_locked();', idle)
        self.assertIn('if (haptic_active.load(std::memory_order_acquire)) return false;', hal)
        self.assertIn('if (!haptic_active.load(std::memory_order_acquire)) refresh_sleep_io_locked();', hal)
        self.assertIn('LEDC_SLEEP_MODE_NO_ALIVE_NO_PD', hal)
        self.assertIn('GPIO_NUM_56, GPIO_NUM_45, GPIO_NUM_8', hal)

    def test_init_pauses_automatic_counter_before_channel_attach(self):
        hal = (ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()
        init = hal.split('void haptic_init_locked()', 1)[1].split('// Serialized by the LVGL', 1)[0]
        self.assertLess(init.index('haptic_pwm.configuring()'), init.index('ledc_timer_config(&timer)'))
        self.assertLess(init.index('ledc_timer_config(&timer)'), init.index('haptic_pause_locked()'))
        self.assertLess(init.index('haptic_pause_locked()'), init.index('haptic_attach_locked()'))
        self.assertIn('if (result == ESP_OK) result = paused;', init)
        self.assertIn('const bool off = haptic_off_locked();', init)
        pause = hal.split('esp_err_t haptic_pause_locked()', 1)[1].split('bool haptic_off_locked()', 1)[0]
        self.assertIn('ledc_timer_pause(LEDC_LOW_SPEED_MODE, LEDC_TIMER_1)', pause)
        self.assertIn('haptic_pwm.paused(result == ESP_OK)', pause)


if __name__ == '__main__':
    unittest.main()
