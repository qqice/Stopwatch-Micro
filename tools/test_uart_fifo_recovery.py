"""Fixed 16/15/32 recovery actor/source fixtures. No device or full build."""
from pathlib import Path
import subprocess,tempfile,unittest
R=Path(__file__).resolve().parents[1]
C=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
class FifoRecoveryTests(unittest.TestCase):
 def compile(self,code,name,defines=()):
  if not C:self.skipTest('cross compiler unavailable')
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'fixture.cpp';p.write_text(code)
   if name=='header-order':
    (Path(d)/'sdkconfig.h').write_text('#pragma once\n#define CONFIG_IDF_TARGET_ESP32S31 1\n#define CONFIG_MOSAICO_UART_FIFO_RECOVERY 1\n')
   result=subprocess.run([str(C[-1]),'-std=c++17','-fsyntax-only','-I'+str(R),'-I'+d,*defines,str(p)],capture_output=True,text=True)
   log=R/'.artifacts/mosaico'/('uart-fifo-'+name+'.log');log.write_text(result.stdout+result.stderr)
   self.assertEqual(result.returncode,0,str(log))
 def test_actual_queue_actor_flag_on(self):
  self.compile(r'''#include "main/debug/serial_debug_transport.h"
#include "main/host/uart_fifo_recovery_model.h"
using namespace serial_debug_transport;
constexpr bool actor(){
 unsigned queue=0,peak=0;
 // No task-side drain during all three original 33-byte preambles plus CRC ping.
 // One DATA per 16-byte full batch, one extra timeout, one WAKE per burst.
 for(unsigned burst=0;burst<4;++burst) {
  unsigned bytes=burst<3 ? 33 : 25;
  unsigned events=UartFifoRecovery::burstEvents(bytes);
  for(unsigned event=0;event<events;++event) {
   if(uartQueueSaturated(queue))return false;
   ++queue;if(queue>peak)peak=queue;
  }
 }
 if(peak!=UartFifoRecovery::RecoveryFrameEvents || peak>=UartEventCapacity || peak<=8)return false;
 unsigned budget=UartEventCapacity;
 while(queue && budget){--queue;--budget;}
 return !queue && uartQueueSaturated(UartEventCapacity) && !uartQueueSaturated(UartEventCapacity-1);
}
static_assert(UartFifoRecovery::WakeThreshold==16 && UartFifoRecovery::FullThreshold==15 && UartEventCapacity==32);
static_assert(actor(),"fixed recovery bursts fit queue+matching budget, while old 8 would saturate");
''','actor-on',('-DMOSAICO_BOARD=1','-DCONFIG_IDF_TARGET_ESP32S31=1','-DCONFIG_MOSAICO_UART_FIFO_RECOVERY=1'))
 def test_actual_flag_off_and_s3_capacity_unchanged(self):
  code='#include "main/debug/serial_debug_transport.h"\nstatic_assert(serial_debug_transport::UartEventCapacity==8);'
  self.compile(code,'actor-off',('-DMOSAICO_BOARD=1','-DCONFIG_IDF_TARGET_ESP32S31=1','-DCONFIG_MOSAICO_UART_FIFO_RECOVERY=0'))
  self.compile(code,'actor-s3',('-DCONFIG_IDF_TARGET_ESP32S31=0','-DCONFIG_MOSAICO_UART_FIFO_RECOVERY=1'))
 def test_real_serial_header_sdkconfig_include_order(self):
  # Config enters through the same header as production, NOT -D or -include.
  self.compile('#include "main/debug/serial_debug.h"\nstatic_assert(serial_debug_transport::UartEventCapacity==32);','header-order',('-DMOSAICO_BOARD=1',))
 def test_local_sdk_retention_and_isr_dispatch_proof(self):
  sdk=Path('C:/esp/v6.1/esp-idf/components')
  if not sdk.exists():self.skipTest('local SDK source unavailable')
  uart=(sdk/'esp_driver_uart/src/uart.c').read_text()
  wake=(sdk/'esp_driver_uart/src/uart_wakeup.c').read_text()
  caps=(sdk/'soc/esp32s31/include/soc/soc_caps.h').read_text()
  self.assertIn('SOC_UART_WAKEUP_SUPPORT_FIFO_THRESH_MODE   (1)',caps)
  self.assertIn('SOC_PM_SUPPORT_PMU_CLK_ICG                 (1)',caps)
  retain=wake.split('if (cfg->wakeup_mode != UART_WK_MODE_ACTIVE_THRESH)',1)[1].split('switch (cfg->wakeup_mode)',1)[0]
  for token in ['ESP_PD_DOMAIN_XTAL, ESP_PD_OPTION_ON','SLEEP_UART_ICG(uart_num), ESP_SLEEP_CLOCK_OPTION_UNGATE','ESP_SLEEP_CLOCK_IOMUX, ESP_SLEEP_CLOCK_OPTION_UNGATE']:
   self.assertIn(token,retain)
  isr=uart.split('static void UART_ISR_ATTR uart_rx_intr_handler_default',1)[1].split('esp_err_t uart_wait_tx_done',1)[0]
  self.assertIn('while (1)',isr);self.assertIn('uart_hal_get_intsts_mask',isr)
  self.assertLess(isr.index('uart_intr_status & UART_INTR_RXFIFO_FULL'),isr.index('uart_intr_status & UART_INTR_WAKEUP'))
  self.assertLess(isr.index('uart_hal_read_rxfifo'),isr.index('UART_SELECT_READ_NOTIF'))
  wake_only=isr.split('else if (uart_intr_status & UART_INTR_WAKEUP)',1)[1].split('#endif',1)[0]
  self.assertIn('UART_INTR_WAKEUP',wake_only)
  self.assertNotIn('rxfifo_rst',wake_only);self.assertNotIn('RXFIFO_FULL',wake_only)
  self.assertIn('p_uart_obj[uart_num]->rx_always_timeout_flg = false',uart)
  ll=(sdk/'esp_hal_uart/esp32s31/include/hal/uart_ll.h').read_text()
  self.assertIn('When the data in rxfifo is more than the threshold value',ll)
  sleep_uart=(sdk/'esp_hw_support/sleep_uart.c').read_text()
  suspend=sleep_uart.split('static SLEEP_UART_FN_ATTR void suspend_uart',1)[1].split('/**',1)[0]
  self.assertNotIn('rxfifo_rst',suspend)
  # Code proof is NOT proof of simultaneous hardware flags or observed power.
 def test_exact_owner_profile_and_failure_gate_source(self):
  serial=(R/'main/debug/serial_debug.cpp').read_text();sleep=(R/'main/host/standby_sleep.cpp').read_text();idle=(R/'main/main_idle_wait.cpp').read_text()
  self.assertIn('uart_set_rx_full_threshold(UART_NUM_0,UartFifoRecovery::FullThreshold)',serial)
  self.assertIn('StandbySleep::uartReady(recoveryRxReady)',serial)
  self.assertIn('MainIdleWait::uartOwner(recoveryRxReady)',serial)
  self.assertIn('uart.wakeup_mode=UART_WK_MODE_FIFO_THRESH;uart.rx_fifo_threshold=UartFifoRecovery::WakeThreshold',sleep)
  self.assertIn('uart.wakeup_mode=UART_WK_MODE_ACTIVE_THRESH;uart.rx_edge_threshold=3',sleep)
  self.assertIn('MainIdleWait::uartRecoveryConfigured(err==ESP_OK,err)',sleep)
  self.assertIn('rxReady.load(std::memory_order_acquire) && recoveryReady.load(std::memory_order_acquire)',idle)
  for bad in ['uart_set_rx_timeout(', 'uart_wakeup_clear(', 'GPIO_NUM_59', 'uart_set_always_rx_timeout(']:
   self.assertNotIn(bad,serial+sleep+idle)
  self.assertIn('bytes.size(), 0',serial);self.assertIn('}, 128)',serial)
  self.assertIn('i < serial_debug_transport::UartEventCapacity',serial)
  self.assertIn('MainIdleWait::uartEvent(event.type==UART_WAKEUP,event.type==UART_DATA,0)',serial)
  self.assertIn('clock_gate_readback=0',serial)
if __name__=='__main__':unittest.main()
