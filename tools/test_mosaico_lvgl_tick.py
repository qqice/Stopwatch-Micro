"""Offline monotonic LVGL clock/startup tests; no devices or firmware builds."""
from pathlib import Path
import subprocess,tempfile,unittest
R=Path(__file__).resolve().parents[1]
HAL=(R/'main/hal/mosaico/hal_mosaico.cpp').read_text()
C=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
class MonotonicTickTests(unittest.TestCase):
    def compile(self,code,name):
        if not C:self.skipTest('cross compiler unavailable')
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/(name+'.cpp');source.write_text(code)
            result=subprocess.run([str(C[-1]),'-std=c++17','-fsyntax-only','-I'+str(R),str(source)],capture_output=True,text=True)
            log=R/'.artifacts/mosaico'/('lvgl-monotonic-'+name+'.log');log.write_text(result.stdout+result.stderr)
            self.assertEqual(result.returncode,0,str(log))
    def test_actual_cpp_continuity_wrap_and_long_gap(self):
        self.compile(r'''
#include "main/hal/mosaico/mosaico_lvgl_tick_model.h"
#include <initializer_list>
using namespace mosaico_lvgl_tick;
constexpr bool cases() {
 for(uint32_t prior : {0UL,1UL,1234UL,0xfffffff0UL,0xffffffffUL}) {
  for(uint32_t now : {0UL,1UL,419UL,0xfffffff0UL,0xffffffffUL}) {
   const uint32_t offset=startupOffset(prior,now);
   if(tick(now,offset)!=prior)return false;
   for(uint32_t elapsed : {0UL,1UL,10UL,1000UL,60000UL,86400000UL,0x80000000UL})
    if(tick(now+elapsed,offset)!=prior+elapsed)return false;
  }
 }
 const int64_t startUs=4294967295000LL;const uint32_t prior=0xfffffff0UL;
 const uint32_t offset=startupOffset(prior,milliseconds(startUs));
 if(milliseconds(startUs)!=0xffffffffUL || milliseconds(startUs+1000)!=0)return false;
 if(tick(milliseconds(startUs+17000),offset)!=1)return false;
 // Missing every periodic callback for a day does not freeze/drift the public clock.
 const uint32_t later=tick(milliseconds(startUs+86400000000LL),offset);
 if(later-prior!=86400000UL)return false;
 return milliseconds(999)==0 && milliseconds(1000)==1 && MonotonicTimerPeriodMs==1000;
}
static_assert(cases(),"continuous startup, unsigned wrap and long gaps without periodic-clock drift");
''','model')
    def test_actual_startup_switch_on_off_and_publication(self):
        start=HAL.index('void Hal::lvgl_init()')
        prefix=HAL[start:HAL.index('    lvgl_port_display_cfg_t disp_config{};',start)]
        prefix=prefix.replace('void Hal::lvgl_init()','constexpr void Harness::install()')+'}\n'
        fixture=r'''
#include <cstdint>
#include <atomic>
#include "main/hal/mosaico/mosaico_lvgl_tick_model.h"
#include "main/hal/mosaico/mosaico_touch_power_model.h"
constexpr int MALLOC_CAP_INTERNAL=1,MALLOC_CAP_DEFAULT=2,ESP_ERR_TIMEOUT=-1;
constexpr unsigned monotonic_lvgl_tick=7;
struct lvgl_port_cfg_t {int task_priority=0,task_stack=0,task_affinity=0,task_max_sleep_ms=0,task_stack_caps=0;uint32_t timer_period_ms=0;};
template<class T>struct Atomic {T value{};constexpr void store(T next,std::memory_order){value=next;}};
struct Harness {
 Atomic<bool>port_ready;Atomic<uint32_t>lvgl_tick_offset;
 uint32_t lvgl_timer_period_ms=0,prior=0xfffffff0UL;int64_t nowUs=419009;
 bool locked=false,lockOkay=true,initOkay=true,error=false,premature=false;unsigned callback=0;int directLocks=0,legacyLocks=0;
 lvgl_port_cfg_t configured{};
 constexpr int lvgl_port_init(const lvgl_port_cfg_t* c){configured=*c;return initOkay?0:-1;}
 constexpr bool lvgl_port_lock(int){++directLocks;locked=lockOkay;return locked;}
 constexpr bool lvglLock(){++legacyLocks;locked=lockOkay;return locked;}
 constexpr uint32_t lv_tick_get(){if(!locked)premature=true;return prior;}
 constexpr int64_t esp_timer_get_time(){if(!locked)premature=true;return nowUs;}
 constexpr void lv_tick_set_cb(unsigned value){if(!locked||port_ready.value)premature=true;callback=value;}
 constexpr void install();
};
#define ESP_ERROR_CHECK(expr) do {if((expr)!=0){error=true;return;}}while(false)
'''
        checks=r'''
constexpr bool cases() {
 Harness h;h.install();if(h.error||!h.port_ready.value||h.premature)return false;
 if(h.configured.task_max_sleep_ms!=500)return false;
#if CONFIG_MOSAICO_LVGL_MONOTONIC_TICK
 if(h.configured.timer_period_ms!=1000||h.lvgl_timer_period_ms!=1000||h.callback!=7||h.directLocks!=1||h.legacyLocks)return false;
 if(mosaico_lvgl_tick::tick(mosaico_lvgl_tick::milliseconds(h.nowUs),h.lvgl_tick_offset.value)!=h.prior)return false;
 Harness failure;failure.lockOkay=false;failure.install();if(!failure.error||failure.port_ready.value||failure.callback)return false;
#else
 if(h.configured.timer_period_ms!=10||h.lvgl_timer_period_ms!=10||h.callback||h.directLocks||h.legacyLocks!=1)return false;
#endif
 Harness failedInit;failedInit.initOkay=false;failedInit.install();return failedInit.error&&!failedInit.port_ready.value&&!failedInit.callback;
}
static_assert(cases(),"actual startup uses mutex before callback/ready, and OFF retains legacy timing");
'''
        for flag in (0,1):self.compile('#define CONFIG_MOSAICO_LVGL_MONOTONIC_TICK '+str(flag)+'\n'+fixture+prefix+checks,'startup-'+str(flag))
    def test_source_scope_diagnostics_and_existing_deadlines(self):
        startup=HAL.split('void Hal::lvgl_init()',1)[1].split('bool Hal::lvglLock()',1)[0]
        for forbidden in ('lvgl_port_stop','lvgl_port_resume','lv_timer_enable','esp_timer_create','esp_timer_start','lv_tick_inc('):
            self.assertNotIn(forbidden,startup)
        self.assertIn('config.timer_period_ms = mosaico_touch_power::LvglTickPeriodMs;',startup)
        self.assertIn('config.timer_period_ms = mosaico_lvgl_tick::MonotonicTimerPeriodMs;',startup)
        self.assertIn('std::atomic<bool> port_ready',HAL)
        self.assertLess(startup.index('lv_tick_set_cb(monotonic_lvgl_tick)'),startup.index('port_ready.store(true'))
        getter=HAL.split('Hal::TouchPollingInfo Hal::touchPollingInfo()',1)[1].split('void Hal::startLvglUpdate()',1)[0]
        self.assertIn('port_ready.load(std::memory_order_acquire)',getter)
        self.assertIn('info.lvglTimerPeriodMs=lvgl_timer_period_ms',getter)
        touch=HAL.split('void Hal::setTouchIdlePolling(bool idle)',1)[1].split('Hal::TouchPollingInfo',1)[0]
        self.assertIn('lv_timer_pause(timer)',touch);self.assertIn('mosaico_touch_power::AwakePeriodMs',touch)
        self.assertNotIn('CONFIG_MOSAICO_LVGL_MONOTONIC_TICK',touch)
        port=(R/'boards/mosaico/managed_components/espressif__esp_lvgl_port/src/lvgl9/esp_lvgl_port.c').read_text()
        self.assertIn('task_delay_ms = lv_timer_handler()',port);self.assertIn('xEventGroupWaitBits',port)
        self.assertIn('lv_tick_inc(lvgl_port_ctx.timer_period_ms)',port)
        lvgl=(R/'components/lvgl/src/tick/lv_tick.c').read_text()
        self.assertIn('return state_p->tick_get_cb()',lvgl)
        serial=(R/'main/debug/serial_debug.cpp').read_text()
        diagnostics=serial.split('std::strcmp(command, "idle-runtime")',1)[1].split('std::strcmp(command, "display-idle-frequency")',1)[0]
        self.assertIn('lvgl_timer_period_ms=%lu lvgl_tick_source=%s',diagnostics)
        self.assertNotIn('lvgl_tick_ms=10',diagnostics)
        for path in ('main/main.cpp','main/host/network_quota.cpp','main/host/standby_sleep.cpp','main/apps/app_codex_micro/view/view_mosaico.cpp'):
            self.assertNotIn('CONFIG_MOSAICO_LVGL_MONOTONIC_TICK',(R/path).read_text())
        self.assertIn('default n',(R/'main/Kconfig.projbuild').read_text().split('config MOSAICO_LVGL_MONOTONIC_TICK',1)[1])
if __name__=='__main__':unittest.main()
