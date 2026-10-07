"""Bounded offline idle-wait model/source/host checks; no device/build."""
from pathlib import Path
import subprocess,tempfile,unittest
from tools import serial_debug_test as host
R=Path(__file__).resolve().parents[1]
C=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
class IdleWaitTests(unittest.TestCase):
    def test_actual_model_all_gates_and_button_interrupt_grace(self):
        if not C:self.skipTest('cross compiler unavailable')
        code=r'''#include "main/main_idle_wait_model.h"
using namespace MainIdleWait;
constexpr bool cases() {
 Gates g;
 if(waitMs(select(g))!=10)return false;
 g.locked=true;if(select(g)!=Cause::Disabled)return false;
 g.enabled=true;if(select(g)!=Cause::Unsupported)return false;
 g.supported=true;if(select(g)!=Cause::Gpio)return false;
 g.gpio=true;if(select(g)!=Cause::Uart)return false;
 g.uart=true;if(select(g)!=Cause::Serial)return false;
 g.serial=false;if(select(g)!=Cause::View)return false;
 g.view=true;if(waitMs(select(g))!=500)return false;
 for(int i=0;i<7;++i) {
  auto b=g;
  switch(i) {case 0:b.button=true;break;case 1:b.ota=true;break;case 2:b.usb=true;break;
   case 3:b.wifi=true;break;case 4:b.ble=true;break;case 5:b.serial=true;break;case 6:b.view=false;break;}
  if(waitMs(select(b))!=20)return false;
 }
 ButtonGrace b;
 if(b.service(0,false,false))return false;
 // ISR that has already released still forces a 200 ms fast sampling window.
 if(!b.service(100,false,true)||!b.service(299,false,false)||b.service(300,false,false))return false;
 if(!b.service(400,true,true)||!b.service(900,true,false)||!b.service(1099,false,false)||b.service(1100,false,false))return false;
 // Tick wrap, held LOW and repeated bounce retain 20 ms, not IRQ storms.
 if(!b.service(0xfffffff0U,false,true)||!b.service(183,false,false)||b.service(184,false,false))return false;
 return true;
}
static_assert(cases(),"all fallback gates, hold/release/short IRQ/wrap grace");
'''
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'model.cpp';p.write_text(code)
            out=subprocess.run([str(C[-1]),'-std=c++17','-fsyntax-only','-I'+str(R),str(p)],capture_output=True,text=True)
            log=R/'.artifacts/mosaico/main-idle-model.log';log.write_text(out.stdout+out.stderr)
            self.assertEqual(out.returncode,0,str(log))
    def test_extracted_actual_notify_lifecycle(self):
        if not C:self.skipTest('cross compiler unavailable')
        import re
        source=(R/'main/main_idle_wait.cpp').read_text()
        source=re.sub(r'^#include .*$', '',source,flags=re.M)
        source=source.replace('namespace MainIdleWait {\nnamespace {','struct Actual {')
        source=source.replace('}\nLifetime::Lifetime() {','constexpr void begin() {')
        source=source.replace('Lifetime::~Lifetime() {','constexpr void end() {')
        source=source.replace('std::atomic<','Atom<').replace('mainTask=nullptr','mainTask=0')
        source=source.replace('gpio_isr_handler_add(GPIO_NUM_7,gpioInterrupt,nullptr)','gpio_isr_handler_add(GPIO_NUM_7,0,nullptr)')
        source=source.replace('uart_set_select_notif_callback(UART_NUM_0,uartInterrupt)','uart_set_select_notif_callback(UART_NUM_0,1)')
        source=source.replace('uart_set_select_notif_callback(UART_NUM_0,nullptr)','uart_set_select_notif_callback(UART_NUM_0,0)')
        source=source.replace('constexpr UBaseType_t NotifyIndex=1','static constexpr UBaseType_t NotifyIndex=1').replace('constexpr bool fifoRecovery','static constexpr bool fifoRecovery')
        for name in ['gpioInterrupt','uartInterrupt','uartComplete','setEnabled','uartRxConfigured','uartRecoveryConfigured','uartEvent','uartOwner','serialState','viewState','wait','snapshot']:
            source=re.sub(r'(?m)^(void|bool|Snapshot) '+name+r'\(',r'constexpr \1 '+name+'(',source)
        source=source.rstrip();assert source.endswith('}')
        source=source[:-1]+'};'
        helpers=r'''#include <cstdint>
#include <atomic>
#include "main/main_idle_wait.h"
#include "main/host/uart_fifo_recovery_model.h"
using namespace MainIdleWait;
#define CONFIG_MOSAICO_EVENT_IDLE_WAIT 1
#define CONFIG_IDF_TARGET_ESP32S31 1
#define configTASK_NOTIFICATION_ARRAY_ENTRIES 2
#define CONFIG_UART_ISR_IN_IRAM 0
#define CONFIG_MOSAICO_UART_FIFO_RECOVERY 1
#define SOC_UART_WAKEUP_SUPPORT_FIFO_THRESH_MODE 1
#define SOC_PM_SUPPORT_PMU_CLK_ICG 1
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL_ISR(x) do{}while(0)
#define portEXIT_CRITICAL_ISR(x) do{}while(0)
#define portENTER_CRITICAL(x) do{}while(0)
#define portEXIT_CRITICAL(x) do{}while(0)
#define portYIELD_FROM_ISR() do{}while(0)
#define pdMS_TO_TICKS(x) (x)
using TaskHandle_t=int;using UBaseType_t=unsigned;using BaseType_t=int;using portMUX_TYPE=int;
using uart_port_t=int;using uart_select_notif_t=int;
constexpr int GPIO_NUM_6=6,GPIO_NUM_7=7,GPIO_INTR_LOW_LEVEL=4,UART_NUM_0=0;
constexpr int UART_SELECT_READ_NOTIF=1,UART_SELECT_ERROR_NOTIF=2,pdTRUE=1,pdFALSE=0,ESP_OK=0;
template<class T>struct Atom {
 T value;static constexpr bool is_always_lock_free=true;
 constexpr T load(std::memory_order)const{return value;}
 constexpr void store(T x,std::memory_order){value=x;}
 constexpr T exchange(T x,std::memory_order){T old=value;value=x;return old;}
 constexpr T fetch_add(T x,std::memory_order){T old=value;value+=x;return old;}
};
'''
        stub=r'''
 int64_t nowUs=0;int gpioLevel=1,serviceError=0,enableError=0,pending=0,maskCalls=0,rearmCalls=0;
 bool callback=false,driver=true,masked6=false,handler=false;int mux=0;
 constexpr int64_t esp_timer_get_time(){return nowUs;}
 constexpr int xTaskGetCurrentTaskHandle(){return 7;}
 constexpr int gpio_intr_disable(int pin){if(pin==6)masked6=true;else ++maskCalls;return 0;}
 constexpr int gpio_install_isr_service(int){return masked6?serviceError:99;}
 constexpr int gpio_set_intr_type(int,int type){return type==GPIO_INTR_LOW_LEVEL?0:99;}
 constexpr int gpio_isr_handler_add(int,int,void*){handler=true;return 0;}
 constexpr int gpio_isr_handler_remove(int){handler=false;return 0;}
 constexpr int gpio_get_level(int){return gpioLevel;}
 constexpr int gpio_intr_enable(int){++rearmCalls;return enableError;}
 constexpr bool uart_is_driver_installed(int){return driver;}
 constexpr int* uart_get_selectlock(){return &mux;}
 constexpr void uart_set_select_notif_callback(int,int cb){callback=cb!=0;}
 constexpr void vTaskNotifyGiveIndexedFromISR(int task,unsigned index,int* woken){if(task==7&&index==1){++pending;*woken=1;}}
 constexpr int ulTaskNotifyTakeIndexed(unsigned index,int clear,uint32_t ms){
  if(index!=1)return -1;int n=pending;if(n){if(clear)pending=0;}else nowUs+=int64_t(ms)*1000;return n;
 }
 constexpr void vTaskDelay(uint32_t ms){nowUs+=int64_t(ms)*1000;}
'''
        source=source.replace('struct Actual {','struct Actual {'+stub,1)
        cases=r'''
constexpr bool actualCases(){
 Actual a;a.begin();
 if(!a.gpioReady||!a.masked6||a.setEnabled(true))return false;
 a.uartOwner(true);if(a.setEnabled(true))return false;
 a.uartRxConfigured(true,0);if(a.setEnabled(true))return false;
 a.uartRecoveryConfigured(true,0);if(!a.callback||!a.setEnabled(true))return false;
 a.serialState(false,false);a.viewState(true);
 // IRQ before wait is latched, not erased by task-side eligibility sampling.
 int woken=0;a.uartInterrupt(0,UART_SELECT_READ_NOTIF,&woken);
 const auto before=a.nowUs;a.wait(true,false,false,false,false);
 if(a.nowUs!=before||a.pending||!woken||a.uartCount.value!=1||a.eventWaits!=1)return false;
 a.uartInterrupt(0,UART_SELECT_ERROR_NOTIF,&woken);if(a.pending!=1||a.uartCount.value!=2)return false;
 a.wait(true,false,false,false,false);a.wait(true,false,false,false,false);
 if(a.nowUs!=500000||a.maxWaitUs!=500000)return false;
 // Held LOW masks exactly in ISR, no rearm while held, release then grace.
 a.gpioLevel=0;a.gpioInterrupt(nullptr);int rearms=a.rearmCalls;
 a.wait(true,false,false,false,false);
 if(a.requestedMs!=20||a.rearmCalls!=rearms||!a.gpioMasked.value)return false;
 a.gpioLevel=1;a.wait(true,false,false,false,false);
 if(a.rearmCalls!=rearms+1||a.requestedMs!=20)return false;
 a.nowUs+=200000;a.wait(true,false,false,false,false);
 if(a.requestedMs!=500)return false;
 a.setEnabled(false);a.wait(true,false,false,false,false);if(a.requestedMs!=20)return false;
 a.end();if(a.callback||a.handler||a.mainTask||a.gpioReady)return false;
 // Unknown service or rearm errors never allow a long wait.
 Actual b;b.serviceError=23;b.begin();b.uartOwner(true);
 if(b.gpioReady||b.setEnabled(true)||b.error!=23)return false;b.end();
 Actual c;c.begin();c.uartOwner(true);c.uartRxConfigured(true,0);c.uartRecoveryConfigured(true,0);c.setEnabled(true);c.serialState(false,false);c.viewState(true);
 c.enableError=24;c.wait(true,false,false,false,false);
 if(c.gpioReady||c.requestedMs!=20||c.error!=24)return false;c.end();
 if(c.handler||c.callback||c.mainTask||c.handlerInstalled)return false;
 return true;
}
static_assert(actualCases(),"actual ISR notify latch, mask/rearm, release grace, UART callbacks, teardown/failure fallback");
'''
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'actual.cpp';p.write_text(helpers+source+cases)
            out=subprocess.run([str(C[-1]),'-std=c++20','-fsyntax-only','-I'+str(R),str(p)],capture_output=True,text=True)
            log=R/'.artifacts/mosaico/main-idle-actual-mock.log';log.write_text(out.stdout+out.stderr)
            self.assertEqual(out.returncode,0,str(log))
    def test_source_interrupt_ownership_and_no_power_gpio_side_effects(self):
        s=(R/'main/main_idle_wait.cpp').read_text()
        self.assertIn('bool enabled=false',s)
        self.assertIn('configTASK_NOTIFICATION_ARRAY_ENTRIES >= 2',s)
        self.assertIn('!CONFIG_UART_ISR_IN_IRAM',s)
        self.assertLess(s.index('gpio_intr_disable(GPIO_NUM_6)'),s.index('gpio_install_isr_service(0)'))
        self.assertIn('GPIO_INTR_LOW_LEVEL',s)
        self.assertIn('constexpr UBaseType_t NotifyIndex=1',s)
        self.assertIn('ulTaskNotifyTakeIndexed(NotifyIndex,pdTRUE',s)
        for bad in ['gpio_wakeup_disable','gpio_set_level','gpio_set_direction','gpio_hold_',
                    'gpio_reset_pin','ESP_INTR_FLAG_IRAM','xTaskCreate','esp_timer_create','uart_driver_install','uart_driver_delete']:
            self.assertNotIn(bad,s)
        gpio=s[s.index('void gpioInterrupt'):s.index('void uartInterrupt')]
        self.assertLess(gpio.index('gpio_intr_disable'),gpio.index('vTaskNotifyGiveIndexedFromISR'))
        for bad in ['StandbySleep','mutex','printf','uart_read','uartTraffic']:self.assertNotIn(bad,gpio)
        uart=s[s.index('void uartInterrupt'):s.index('Lifetime::Lifetime')]
        self.assertIn('UART_SELECT_READ_NOTIF',uart);self.assertIn('UART_SELECT_ERROR_NOTIF',uart)
        self.assertNotIn('uart_vfs',s)
        serial=(R/'main/debug/serial_debug.cpp').read_text()
        self.assertLess(serial.index('MainIdleWait::uartOwner(false)'),serial.index('uart_driver_delete'))
        self.assertIn('StandbySleep::uartWake(); MainIdleWait::serialState(true,true)',serial)
        self.assertIn('_async_test!=AsyncTest::None',serial)
        self.assertIn('rxUnknown || buffered || txPending',serial)
        self.assertIn('MainIdleWait::viewState(locked && safe && !fault)',(R/'main/host/standby_sleep.cpp').read_text())
    def test_host_retry_allowlist_is_narrow(self):
        self.assertTrue(host.recovery_retry_safe('debug idle-wait status'))
        self.assertTrue(host.recovery_retry_safe('debug idle-wait off'))
        for bad in ['debug idle-wait on','debug idle-wait off extra','debug gpio 7 0']:
            self.assertFalse(host.recovery_retry_safe(bad))
        self.assertIn('"idle-wait"',(R/'main/debug/serial_debug_transport.h').read_text())
if __name__=='__main__':unittest.main()
