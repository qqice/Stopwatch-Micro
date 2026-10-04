"""Offline integration guards, not electrical or optical acceptance tests."""
import shutil,subprocess,tempfile,unittest
from pathlib import Path
R=Path(__file__).resolve().parents[1]
HAL=(R/'main/hal/mosaico/hal_mosaico.cpp').read_text(encoding='utf8')
NET=(R/'main/host/network_quota.cpp').read_text(encoding='utf8')
VIEW=(R/'main/apps/app_codex_micro/view/view_mosaico.cpp').read_text(encoding='utf8')
class PowerOptTests(unittest.TestCase):
 def test_actual_cpp_policy(self):
  comp=shutil.which('g++') or shutil.which('clang++')
  if not comp:
   c=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
   comp=str(c[-1]) if c else None
  if not comp:self.skipTest('C++ compiler unavailable')
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'policy.cpp'
   p.write_text('#include "'+(R/'main/hal/mosaico/mosaico_touch_power_model.h').as_posix()+'"\n'
    'using namespace mosaico_touch_power; constexpr bool check(){ unsigned n=0; for(unsigned p=0;p<64;++p){ if(unusedGate(p))++n; if(bool(UnusedGateMask&(1ULL<<p))!=unusedGate(p))return false; }return n==3;} static_assert(check()); static_assert(pollingPeriodMs(true)==0 && pollingPeriodMs(false)==10 && LvglTickPeriodMs==10);',encoding='utf8')
   q=subprocess.run([comp,'-std=c++17','-fsyntax-only',str(p)],capture_output=True,text=True)
   self.assertEqual(q.returncode,0,q.stdout+q.stderr)
 def test_gates_are_latched_low_before_output(self):
  init=HAL.split('void Hal::i2c_init()',1)[1].split('void Hal::i2c_detect()',1)[0]
  self.assertLess(init.index('gpio_set_level(pin, 0)'),init.index('gpio_config(&unused)'))
  self.assertIn('{GPIO_NUM_56, GPIO_NUM_45, GPIO_NUM_8}',init)
  self.assertIn('gpio_set_level(GPIO_NUM_60, 0)',init)
  self.assertNotIn('GPIO_NUM_57',init)
 def test_timer_transition_and_tick_scaling(self):
  setter=HAL.split('void Hal::setTouchIdlePolling(bool idle)',1)[1].split('Hal::TouchPollingInfo',1)[0]
  for token in ['lvglLock()', 'touch_idle_polling.load', 'lv_timer_set_period', 'lv_timer_reset', 'lv_timer_ready','lvgl_port_task_wake']:
   self.assertIn(token,setter)
  self.assertIn('config.timer_period_ms = mosaico_touch_power::LvglTickPeriodMs',HAL)
  lib=(R/'boards/mosaico/managed_components/espressif__esp_lvgl_port/src/lvgl9/esp_lvgl_port.c').read_text(encoding='utf8')
  self.assertIn('lv_tick_inc(lvgl_port_ctx.timer_period_ms)',lib)
  self.assertIn('lvgl_port_ctx.timer_period_ms * 1000',lib)
 def test_view_transitions_and_s3_delay(self):
  lock=VIEW.split('void CodexMicroView::lockDisplay()',1)[1].split('bool CodexMicroView::lockForDebug()',1)[0]
  wake=VIEW.split('void CodexMicroView::wakeDisplay()',1)[1].split('void CodexMicroView::lockDisplay()',1)[0]
  self.assertIn('setTouchIdlePolling(true)',lock); self.assertIn('setTouchIdlePolling(false)',wake)
  self.assertLess(wake.index('setLocked(false)'),wake.index('lv_obj_add_flag(_overlay'))
  main=(R/'main/main.cpp').read_text(encoding='utf8')
  self.assertIn('idleLocked() ? 20 : 10',main)
  self.assertIn('idleLocked() ? 100 : 1',main)
 def test_optin_is_bounded_and_committed(self):
  setter=NET.split('bool NetworkQuota::setIdleCpuFrequency(',1)[1].split('#endif',1)[0]
  self.assertIn('mhz != 160 && mhz != 320',setter)
  self.assertLess(setter.index('nvs_commit'),setter.index('_idle_cpu_mhz = mhz'))
  self.assertIn('observed != mhz',setter)
  self.assertIn('(savedIdle != 160 && savedIdle != 320)) savedIdle = 320',NET)
  diag=NET.split('void NetworkQuota::setDiagnosticIdleFrequency(',1)[1].split('bool NetworkQuota::setIdleCpuFrequency(',1)[0]
  self.assertNotIn('nvs_',diag)
 def test_wake_clock_serialized_before_redraw(self):
  cpu=NET.split('void NetworkQuota::setCpu(',1)[1].split('void NetworkQuota::setLowClockDiagnostic',1)[0]
  self.assertIn('clockLock(_cpu_mutex)',cpu)
  self.assertIn('else mhz = CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ',cpu)
  locked=NET.split('void NetworkQuota::setLocked(',1)[1].split('void NetworkQuota::setPowerProfile',1)[0]
  self.assertLess(locked.index('setCpu(CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ)'),locked.index('xTaskNotifyGive'))
 def test_diagnostic_320_overrides_saved_160(self):
  diag=NET.split('void NetworkQuota::setDiagnosticIdleFrequency(',1)[1].split('bool NetworkQuota::setIdleCpuFrequency(',1)[0]
  self.assertIn('_diagnostic_override = true',diag)
  self.assertIn('_diagnostic_idle_mhz = mhz',diag)
  self.assertIn('_diagnostic_override.load() ? _diagnostic_idle_mhz.load() : _idle_cpu_mhz.load()',NET)
  setter=NET.split('bool NetworkQuota::setIdleCpuFrequency(',1)[1].split('#endif',1)[0]
  self.assertIn('_diagnostic_override = false',setter)
if __name__=='__main__':unittest.main()
