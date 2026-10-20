"""Offline USB scheduling checks; not detach, PM, or current acceptance."""
from pathlib import Path
import re, subprocess, tempfile, unittest
R = Path(__file__).resolve().parents[1]
C = sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-gcc.exe'))

class UsbIdleTests(unittest.TestCase):
    def test_application_callbacks_both_sdk_profiles_compile(self):
        if not C: self.skipTest('cross compiler unavailable')
        source = (R/'main/hal/mosaico/usb_console.c').read_text()
        source = re.sub(r'^#include <(?:esp_|freertos/|tinyusb|soc/|sdkconfig).*$', '', source, flags=re.M)
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
#define ESP_ERR_NOT_SUPPORTED 0x106
#define ESP_OK 0
typedef int esp_err_t;
typedef int esp_reset_reason_t;
enum {ESP_RST_POWERON=1, ESP_RST_SW=3};
int esp_sleep_enable_usb_wakeup(void), esp_sleep_pd_config(int,int);
#define ESP_PD_DOMAIN_CNNT 1
#define ESP_PD_OPTION_ON 1
void usb_phy_set_otg_suspend_state(bool), usb_phy_clear_otg_wakeup_status(void);
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
            for flags in ([], ['-DCONFIG_IDF_TARGET_ESP32S31=1','-DSOC_PM_SUPPORT_USB_WAKEUP=1','-DSOC_PM_SUPPORT_CNNT_PD=1'], ['-DCONFIG_IDF_TARGET_ESP32S31=1','-DSOC_PM_SUPPORT_USB_WAKEUP=0'], ['-DCONFIG_TINYUSB_SUSPEND_CALLBACK=1','-DCONFIG_TINYUSB_RESUME_CALLBACK=1']):
                out = subprocess.run([str(C[-1]),'-std=c11','-Wall','-Wextra','-Werror','-fsyntax-only',
                    '-I'+str(R/'main/hal/mosaico'),*flags,str(p)],capture_output=True,text=True)
                logs.append(out.stdout+out.stderr)
                self.assertEqual(out.returncode,0,out.stdout+out.stderr)
        (R/'.artifacts/mosaico/usb-idle-callback-compile.log').write_text('\n'.join(logs))

    def test_actual_snapshot_allow_and_recovery_gate(self):
        from tools.test_standby_sleep import function, StandbySleepTests
        usb = (R/'main/hal/mosaico/usb_console.c').read_text()
        standby = (R/'main/host/standby_sleep.cpp').read_text()
        snapshot = function(usb, 'mosaico_usb_snapshot_t mosaico_console_usb_snapshot(')
        methods = '\n'.join(function(standby, sig) for sig in (
            'void refreshRecovery(', 'void usbActivity(', 'bool allow('))
        methods = methods.replace('std::lock_guard<std::mutex> guard(mutex);', '')
        methods = methods.replace('MosaicoOta::busy()', 'false').replace('MosaicoOta::healthPending()', 'false')
        methods = methods.replace('GetHAL().sleepIoRetentionReady()', 'true')
        for sig in ('void refreshRecovery(', 'void usbActivity(', 'bool allow('):
            methods = methods.replace(sig, 'constexpr '+sig)
        code = r'''
#include "main/host/standby_sleep_model.h"
#include "main/hal/mosaico/usb_console.h"
#define atomic_load(p) (*(p))
struct Harness {
 StandbySleep::Model model=StandbySleep::initialModel(true,true);
 bool mounted=false,connected=false,suspended=false,wakeup_ready=true;
 bool phy_hint_requested=true,cnnt_on_configured=true;
 int cnnt_error=0; unsigned bridge_requested=0,bridge_serviced=0,hint_arms=0,wake_clears=0;
 int wakeup_error=0;unsigned mounts=0,unmounts=0,suspends=0,resumes=0,rx_events=0;
 bool wanted=true,applied=true,ready=true,fatal=false,initialized=true,bleActive=false,held=false;
 int64_t now=1000000;
 constexpr Harness() {model.viewLocked=true;model.displaySafe=true;}
 constexpr bool tud_mounted() {return mounted;}
 constexpr bool tud_connected() {return connected;}
 constexpr bool tud_suspended() {return suspended;}
 constexpr int64_t esp_timer_get_time() {return now;}
 constexpr bool initialize() {return true;}
 constexpr void lockRecovery(bool need) {held=need;}
'''+snapshot.replace('mosaico_usb_snapshot_t mosaico_console_usb_snapshot(', 'constexpr mosaico_usb_snapshot_t mosaico_console_usb_snapshot(')+methods+r'''
};
constexpr bool cases() {
 Harness h;
 if(!h.allow(160,true,false)||h.held)return false; // detached wake-ready
 h.connected=true;if(h.allow(160,true,false)||!h.held)return false; // enumeration before mount
 if(h.mosaico_console_usb_snapshot().effective_active)return false; // legacy effective_active stays mounted-only; service_active includes connected
 h.mounted=true;if(h.allow(160,true,false)||!h.held)return false; // active even DTR closed
 h.suspended=true;if(!h.allow(160,true,false)||h.held)return false; // suspended
 h.mounted=false;if(!h.allow(160,true,false)||h.held)return false; // connected suspend before mount
 h.connected=false;h.suspended=false;if(!h.allow(160,true,false)||h.held)return false; // detached
 h.wakeup_ready=false;h.wakeup_error=0x106;
 if(h.allow(160,true,false)||!h.held||h.mosaico_console_usb_snapshot().wakeup_error!=0x106)return false;
 h.wakeup_ready=true;h.allow(160,true,false);
 // Callback immediately invalidates stale allow, acquires existing owner before main runs.
 h.usbActivity();if(h.wanted||!h.held)return false;
 h.mounted=true;h.suspended=true;h.allow(160,true,false);
 if(!h.held)return false; // no early release within 500 ms callback window
 h.now+=500000;h.allow(160,true,false);if(h.held)return false;
 h.mounted=false;h.connected=false;h.suspended=false;return h.allow(160,true,false)&&!h.held;
}
static_assert(cases(),"actual USB snapshot and StandbySleep gate/recovery lifecycle");
'''
        StandbySleepTests().compile(code, 'usb-actual-gate')

    def test_actual_bridge_transitions_and_arm_races(self):
        from tools.test_standby_sleep import function, StandbySleepTests
        usb = (R/'main/hal/mosaico/usb_console.c').read_text()
        service = function(usb, 'void mosaico_console_usb_service_sleep(')
        snap = function(usb, 'mosaico_usb_snapshot_t mosaico_console_usb_snapshot(')
        code = r'''
#include "main/hal/mosaico/usb_console.h"
#define CONFIG_IDF_TARGET_ESP32S31 1
#define SOC_PM_SUPPORT_USB_WAKEUP 1
#define SOC_PM_SUPPORT_CNNT_PD 1
#define atomic_load(p) (*(p))
#define atomic_store(p,v) (*(p)=(v))
#define atomic_fetch_add(p,v) (*(p)+=(v))
struct Harness {
 bool mounted=false,connected=false,suspended=false,wakeup_ready=false;
 bool phy_hint_requested=false,cnnt_on_configured=false,hw_hint=false;
 unsigned bridge_requested=1,bridge_serviced=0,hint_arms=0,wake_clears=0;
 unsigned mounts=0,unmounts=0,suspends=0,resumes=0,rx_events=0;
 int wakeup_error=0,cnnt_error=0,race=0;
 constexpr bool tud_mounted(){return mounted;}
 constexpr bool tud_connected(){return connected;}
 constexpr bool tud_suspended(){return suspended;}
 constexpr void usb_phy_set_otg_suspend_state(bool value){
  hw_hint=value;
  if(value && race){++bridge_requested;if(race==2)connected=true;race=0;}
 }
 constexpr void usb_phy_clear_otg_wakeup_status(){}
'''+service.replace('void mosaico_console_usb_service_sleep(', 'constexpr void mosaico_console_usb_service_sleep(')+snap.replace('mosaico_usb_snapshot_t mosaico_console_usb_snapshot(', 'constexpr mosaico_usb_snapshot_t mosaico_console_usb_snapshot(')+r'''
 constexpr bool safe(){return mosaico_console_usb_snapshot().sleep_safe;}
};
constexpr bool cases(){
 Harness h;
 h.mosaico_console_usb_service_sleep();if(h.safe()||h.hw_hint)return false;
 h.wakeup_ready=true;h.mosaico_console_usb_service_sleep();if(!h.safe()||!h.hw_hint)return false;
 // Initial battery boot arms even without mount/suspend callback.
 h.connected=true;++h.bridge_requested;if(h.safe())return false;
 h.mosaico_console_usb_service_sleep();if(h.safe()||h.hw_hint)return false;
 h.mounted=true;++h.bridge_requested;h.mosaico_console_usb_service_sleep();if(h.safe())return false;
 // Known observation gap: TinyUSB reset clears all state with no task callback.
 // Before first SETUP there is no new generation; indistinguishable from offline.
 h.mounted=false;h.connected=false;h.suspended=false;
 h.mosaico_console_usb_service_sleep();if(!h.safe())return false;
 // First SETUP restores connected and immediately blocks snapshot eligibility.
 h.connected=true;if(h.safe())return false;
 h.mounted=true;
 h.suspended=true;++h.bridge_requested;h.mosaico_console_usb_service_sleep();if(!h.safe())return false;
 h.suspended=false;++h.bridge_requested;h.mosaico_console_usb_service_sleep();if(h.safe()||h.hw_hint)return false;
 h.mounted=false;h.connected=false;++h.bridge_requested;
 h.race=1;h.mosaico_console_usb_service_sleep();if(h.safe()||h.hw_hint)return false;
 h.mosaico_console_usb_service_sleep();if(!h.safe())return false;
 h.race=2;h.mosaico_console_usb_service_sleep();if(h.safe()||h.hw_hint)return false;
 h.connected=false;h.wakeup_ready=false;h.mosaico_console_usb_service_sleep();return !h.safe();
}
static_assert(cases(),"actual PHY owner transition and arm rollback contract");
'''
        StandbySleepTests().compile(code, 'usb-bridge-transitions')

    def test_task_activity_and_public_wake_setup(self):
        from tools.test_standby_sleep import function
        usb = (R/'main/hal/mosaico/usb_console.c').read_text()
        for signature in ('static void resumed_event(', 'static void received(', 'static void coding_changed('):
            self.assertTrue('mosaico_idle_usb_notify()' in function(usb, signature) or 'bridge_event()' in function(usb, signature))
        coding = function(usb, 'static void coding_changed(')
        self.assertIn('bit_rate == 1200', coding)
        self.assertIn('atomic_store(&download_requested, true)', coding)
        self.assertIn('#if CONFIG_IDF_TARGET_ESP32S31 && SOC_PM_SUPPORT_USB_WAKEUP', usb)
        self.assertEqual(usb.count('esp_sleep_enable_usb_wakeup()'), 1)
        self.assertIn('wake_err = ESP_ERR_NOT_SUPPORTED', usb)
        self.assertIn('atomic_store(&wakeup_ready, wake_err == ESP_OK)', usb)
        main = (R/'main/main.cpp').read_text()
        self.assertIn('StandbySleep::usbActivity(); MainIdleWait::usbEvent();', main)
        cli = (R/'tools/mosaico_settings.py').read_text()
        self.assertIn('wakeup_ready wakeup_error sleep_safe', cli)

    def test_real_state_and_sdk_callback_ownership(self):
        source = (R/'main/hal/mosaico/usb_console.c').read_text()
        for token in ('s.mounted = tud_mounted()', 's.connected = tud_connected()',
                      's.suspended = tud_suspended()', 's.effective_active = s.mounted && !s.suspended',
                      'TINYUSB_DEFAULT_CONFIG(device_event)', '.callback_rx = received',
                      '#ifndef CONFIG_TINYUSB_RESUME_CALLBACK', '#ifndef CONFIG_TINYUSB_SUSPEND_CALLBACK'):
            self.assertIn(token, source)
        for name in ('resumed_event', 'received'):
            body = source.split('static void '+name+'(',1)[1].split('\n}',1)[0]
            self.assertIn('bridge_event()',body)
        self.assertIn('bridge_event()',source.split('case TINYUSB_EVENT_ATTACHED:',1)[1].split('break;',1)[0])
        self.assertNotIn('tinyusb_cdcacm_read',source)
        vfs = (R/'boards/mosaico/managed_components/espressif__esp_tinyusb/vfs_tinyusb.c').read_text()
        self.assertNotIn('tinyusb_cdcacm_register_callback',vfs)
        ota = (R/'main/ota/mosaico_ota.cpp').read_text()
        self.assertIn('tud_mounted()',ota)
        self.assertNotIn('effective_active',ota)

if __name__ == '__main__': unittest.main()
