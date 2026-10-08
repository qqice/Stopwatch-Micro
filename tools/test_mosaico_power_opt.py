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
  mosaico,s3=main.rsplit('#else',1)
  self.assertIn('MainIdleWait::wait(GetNetworkQuota().idleLocked(), MosaicoOta::busy() || MosaicoOta::healthPending(), mosaico_console_usb_snapshot().effective_active, GetNetworkQuota().powerStats().wifiRunning, sleepBle.advertising || sleepBle.connected);',' '.join(mosaico.split()))
  self.assertIn('vTaskDelay(pdMS_TO_TICKS(GetNetworkQuota().idleLocked() ? 100 : 1));',s3)
  self.assertNotIn('MainIdleWait::wait',s3)
  comp=shutil.which('g++') or shutil.which('clang++')
  if not comp:
   c=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
   comp=str(c[-1]) if c else None
  if not comp:self.skipTest('C++ compiler unavailable')
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'idle_wait_contract.cpp'
   p.write_text('#include "'+(R/'main/main_idle_wait_model.h').as_posix()+'"\n'+r'''
using namespace MainIdleWait;
constexpr bool timingContract() {
 Gates safe;safe.locked=true;safe.enabled=true;safe.supported=true;
 safe.gpio=true;safe.uart=true;safe.serial=false;safe.view=true;
 if(waitMs(select(safe))!=500)return false;
 // Every readiness/activity safety gate preserves the original locked 20 ms.
 for(unsigned i=0;i<11;++i) {
  auto blocked=safe;
  switch(i) {
   case 0:blocked.enabled=false;break;case 1:blocked.supported=false;break;
   case 2:blocked.gpio=false;break;case 3:blocked.uart=false;break;
   case 4:blocked.button=true;break;case 5:blocked.ota=true;break;
   case 6:blocked.usb=true;break;case 7:blocked.wifi=true;break;
   case 8:blocked.ble=true;break;case 9:blocked.serial=true;break;
   case 10:blocked.view=false;break;
  }
  if(waitMs(select(blocked))!=20)return false;
  blocked.locked=false;
  if(waitMs(select(blocked))!=10)return false;
 }
 Gates boot;if(waitMs(select(boot))!=10)return false;
 boot.locked=true;return select(boot)==Cause::Disabled && waitMs(select(boot))==20;
}
static_assert(timingContract(),"awake=10, disabled or unsafe locked=20, safe opt-in event=500");
''',encoding='utf8')
   q=subprocess.run([comp,'-std=c++17','-fsyntax-only',str(p)],capture_output=True,text=True)
   log=R/'.artifacts/mosaico/main-idle-power-contract.log';log.parent.mkdir(parents=True,exist_ok=True)
   log.write_text(q.stdout+q.stderr,encoding='utf8')
   self.assertEqual(q.returncode,0,str(log))
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
