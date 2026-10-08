"""Offline USB scheduling checks; not detach, PM, or current acceptance."""
from pathlib import Path
import re, subprocess, tempfile, unittest
R = Path(__file__).resolve().parents[1]
C = sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-gcc.exe'))

class UsbIdleTests(unittest.TestCase):
    def test_application_callbacks_both_sdk_profiles_compile(self):
        if not C: self.skipTest('cross compiler unavailable')
        source = (R/'main/hal/mosaico/usb_console.c').read_text()
        source = re.sub(r'^#include <(?:esp_|freertos/|tinyusb|soc/).*$', '', source, flags=re.M)
        stub = r'''
#include <stdint.h>
#include <stdbool.h>
typedef void* TaskHandle_t;
typedef struct { struct { struct { unsigned bit_rate; } *p_line_coding; } line_coding_changed_data; } cdcacm_event_t;
typedef struct { int id; } tinyusb_event_t;
enum { TINYUSB_EVENT_ATTACHED, TINYUSB_EVENT_DETACHED, TINYUSB_EVENT_SUSPENDED, TINYUSB_EVENT_RESUMED };
typedef struct { void (*event_cb)(tinyusb_event_t*,void*); } tinyusb_config_t;
typedef struct { void (*callback_rx)(int,cdcacm_event_t*); void (*callback_line_coding_changed)(int,cdcacm_event_t*); } tinyusb_config_cdcacm_t;
#define TINYUSB_DEFAULT_CONFIG(cb) { .event_cb = cb }
#define ESP_ERROR_CHECK(x) (void)(x)
#define pdPASS 1
#define pdTRUE 1
#define portMAX_DELAY 0xffffffff
#define pdMS_TO_TICKS(x) (x)
#define ESP_ERR_NO_MEM -1
#define TINYUSB_CDC_ACM_0 0
#define REG_SET_BIT(a,b) (void)0
bool tud_mounted(void), tud_connected(void), tud_suspended(void);
void esp_restart(void), xTaskNotifyGive(TaskHandle_t), vTaskDelay(unsigned);
unsigned ulTaskNotifyTake(int,unsigned);
int xTaskCreate(void (*)(void*),const char*,unsigned,void*,int,TaskHandle_t*);
int tinyusb_driver_install(const tinyusb_config_t*), tinyusb_cdcacm_init(const tinyusb_config_cdcacm_t*);
int tinyusb_console_init(int), esp_reset_reason(void);
'''
        logs = []
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'usb.c'; p.write_text(stub+source)
            for flags in ([], ['-DCONFIG_TINYUSB_SUSPEND_CALLBACK=1','-DCONFIG_TINYUSB_RESUME_CALLBACK=1']):
                out = subprocess.run([str(C[-1]),'-std=c11','-Wall','-Wextra','-Werror','-fsyntax-only',
                    '-I'+str(R/'main/hal/mosaico'),*flags,str(p)],capture_output=True,text=True)
                logs.append(out.stdout+out.stderr)
                self.assertEqual(out.returncode,0,out.stdout+out.stderr)
        (R/'.artifacts/mosaico/usb-idle-callback-compile.log').write_text('\n'.join(logs))

    def test_real_state_and_sdk_callback_ownership(self):
        source = (R/'main/hal/mosaico/usb_console.c').read_text()
        for token in ('s.mounted = tud_mounted()', 's.connected = tud_connected()',
                      's.suspended = tud_suspended()', 's.effective_active = s.mounted && !s.suspended',
                      'TINYUSB_DEFAULT_CONFIG(device_event)', '.callback_rx = received',
                      '#ifndef CONFIG_TINYUSB_RESUME_CALLBACK', '#ifndef CONFIG_TINYUSB_SUSPEND_CALLBACK'):
            self.assertIn(token, source)
        for name in ('resumed_event', 'received'):
            body = source.split('static void '+name+'(',1)[1].split('\n}',1)[0]
            self.assertIn('mosaico_idle_usb_notify()',body)
        self.assertIn('mosaico_idle_usb_notify()',source.split('case TINYUSB_EVENT_ATTACHED:',1)[1].split('break;',1)[0])
        self.assertNotIn('tinyusb_cdcacm_read',source)
        vfs = (R/'boards/mosaico/managed_components/espressif__esp_tinyusb/vfs_tinyusb.c').read_text()
        self.assertNotIn('tinyusb_cdcacm_register_callback',vfs)
        ota = (R/'main/ota/mosaico_ota.cpp').read_text()
        self.assertIn('tud_mounted()',ota)
        self.assertNotIn('effective_active',ota)

if __name__ == '__main__': unittest.main()
