"""Offline auto-LS lease/safety and host recovery checks. No devices/builds."""
from pathlib import Path
import subprocess,tempfile,unittest
from unittest.mock import patch
from types import SimpleNamespace
from tools import serial_debug_test as host
R=Path(__file__).resolve().parents[1]
C=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))

def function(source,signature):
    start=source.index(signature);opening=source.index('{',start);depth=1;end=opening+1
    while depth:
        depth+=(source[end]=='{')-(source[end]=='}');end+=1
    return source[start:end]

class StandbySleepTests(unittest.TestCase):
    def compile(self,code,name):
        if not C:self.skipTest('cross compiler unavailable')
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/(name+'.cpp');source.write_text(code)
            result=subprocess.run([str(C[-1]),'-std=c++17','-fsyntax-only','-I'+str(R),str(source)],capture_output=True,text=True)
            log=R/'.artifacts/mosaico'/('standby-sleep-'+name+'.log');log.write_text(result.stdout+result.stderr)
            self.assertEqual(result.returncode,0,str(log))
    def test_actual_cpp_lease_model(self):
        self.compile(r'''
#include "main/host/standby_sleep_model.h"
using StandbySleep::Model;
constexpr bool cases() {
 Model m;
 if(m.leaseUntil || m.eligible(0,160,true,false))return false;
 m.viewLocked=true;m.displaySafe=true;
 if(m.request(29,0)||m.request(301,0)||m.leaseUntil)return false;
 if(!m.request(180,0)||!m.eligible(0,160,true,false))return false;
 const auto until=m.leaseUntil;
 if(m.request(4294967295U,0)||m.leaseUntil!=until)return false;
 if(m.eligible(0,80,true,false)||m.eligible(0,320,true,false)||m.eligible(0,160,false,false)||m.eligible(0,160,true,true))return false;
 m.displaySafe=false;if(m.eligible(0,160,true,false))return false;m.displaySafe=true;
 m.wake(10);if(!m.uartBlocked(500009)||m.uartBlocked(500010))return false;
 m.uartPending=true;if(!m.uartBlocked(1000000))return false;m.uartPending=false;
 m.service(until-1);if(!m.leaseUntil)return false;
 m.service(until);if(m.leaseUntil)return false;
 if(!m.request(30,until))return false;m.viewLocked=false;m.service(until+1);if(m.leaseUntil)return false;
 m.viewLocked=true;m.request(30,until);m.otaBlocked=true;m.service(until);if(m.leaseUntil)return false;
 m.request(30,until);m.fault=true;m.service(until);if(m.leaseUntil)return false;
 m.fault=false;m.request(300,until);m.off();if(m.leaseUntil)return false;
 m.wake(until);return !m.leaseUntil;
}
static_assert(cases(),"default off, strict bounded TTL, 160-only gates, UART hold, wake/OTA/fault cancellation");
''','model')
    def test_actual_automatic_pause_relock_and_explicit_modes(self):
        self.compile(r'''
#include "main/host/standby_sleep_model.h"
using namespace StandbySleep;
constexpr bool cases() {
 auto fallback=initialModel(false,true);if(fallback.automaticPolicy || fallback.requested(0))return false;
 if(fallback.enableAutomatic(true)||fallback.request(30,0)||fallback.leaseUntil||fallback.automaticPolicy)return false;
 auto old=initialModel(true,false);if(old.automaticPolicy || old.requested(0))return false;
 auto m=initialModel(true,true);m.viewLocked=true;m.displaySafe=true;
 if(!m.automaticPolicy || !m.eligible(0,160,true,false) || m.mode(0)!=Mode::Automatic)return false;
 m.wake(0);if(!m.uartBlocked(499999) || m.uartBlocked(500000))return false;
 m.pause();m.viewLocked=false;
 if(!m.automaticPolicy || m.leaseUntil || m.eligible(500000,160,false,false))return false;
 m.viewLocked=true;m.displaySafe=true;if(!m.eligible(500000,160,true,false))return false;
 // Immediate OTA entry pause preserves normal policy, and ordinary service
 // or repeated allow checks cannot accidentally clear the stored OTA gate.
 m.otaBlocked=true;m.pause();m.displaySafe=true;m.service(500001);
 if(!m.automaticPolicy || m.eligible(500001,160,true,false) || !m.otaBlocked)return false;
 m.service(500002);if(!m.otaBlocked)return false;
 m.otaBlocked=false;if(!m.eligible(500003,160,true,false))return false;
 if(m.eligible(500003,320,true,false)||m.eligible(500003,80,true,false)||m.eligible(500003,160,true,true))return false;
 m.fault=true;m.service(500004);if(!m.automaticPolicy || m.eligible(500004,160,true,false))return false;m.fault=false;
 // A successful finite on replaces auto; expiration MUST NOT resurrect auto.
 if(m.request(29,600000)||!m.automaticPolicy)return false;
 if(!m.request(30,600000)||m.automaticPolicy||m.mode(600000)!=Mode::Diagnostic)return false;
 m.service(30600000);if(m.requested(30600000)||m.automaticPolicy||m.mode(30600000)!=Mode::Off)return false;
 m.request(30,31000000);m.pause();if(m.leaseUntil||m.automaticPolicy)return false;
 if(m.enableAutomatic(false)||m.automaticPolicy)return false;
 if(!m.enableAutomatic(true)||!m.automaticPolicy||m.leaseUntil)return false;
 m.off();m.viewLocked=true;m.displaySafe=true;m.service(99999999);
 if(m.automaticPolicy||m.requested(99999999)||m.eligible(99999999,160,true,false))return false;
 if(!m.enableAutomatic(true))return false;
 m.request(30,100000000);m.otaBlocked=true;m.service(100000001);
 return !m.leaseUntil && !m.automaticPolicy;
}
static_assert(cases(),"auto pauses/resumes, finite cannot become permanent, explicit off is sticky, unsupported profile stays off");
''','automatic-model')
    def test_actual_pm_cache_includes_sleep_bit_and_fault_stops_retry(self):
        source=(R/'main/host/network_quota.cpp').read_text()
        body=function(source,'void NetworkQuota::applyCpuConfig(')
        code=r'''
#define MOSAICO_BOARD 1
#include <cstdint>
using esp_err_t=int;constexpr int ESP_OK=0;
struct esp_pm_config_t {unsigned max_freq_mhz=0,min_freq_mhz=0;bool light_sleep_enable=false;};
namespace StandbySleep {constexpr void beforeConfigure(bool){} constexpr void configured(bool,int){} }
struct NetworkQuota {
 unsigned _cpu_target=320;bool _cpu_light_sleep=false,_sleep_pm_fault=false;int _clock_error=0,calls=0,error=0;
 esp_pm_config_t last{};
 constexpr int esp_pm_configure(const esp_pm_config_t* p) {++calls;last=*p;return error;}
 constexpr void applyCpuConfig(uint32_t,bool);
};
'''+body.replace('void NetworkQuota::','constexpr void NetworkQuota::')+r'''
constexpr bool cases() {
 NetworkQuota q;q.applyCpuConfig(320,false);if(q.calls)return false;
 q.applyCpuConfig(320,true);if(q.calls!=1||!q._cpu_light_sleep||!q.last.light_sleep_enable)return false;
 q.applyCpuConfig(320,true);if(q.calls!=1)return false;
 q.applyCpuConfig(320,false);if(q.calls!=2||q._cpu_light_sleep)return false;
 q.applyCpuConfig(160,true);if(q.calls!=3||q._cpu_target!=160||q.last.min_freq_mhz!=160)return false;
 q.error=-1;q.applyCpuConfig(160,false);if(!q._sleep_pm_fault||!q._cpu_light_sleep||q._clock_error!=-1)return false;
 q.applyCpuConfig(160,false);if(q.calls!=4)return false; // Fail closed, no autonomous busy retries.
 return true;
}
static_assert(cases(),"same-frequency LS enable/disable is not hidden by cached MHz; faults do not busy retry");
'''
        self.compile(code,'pm-cache')
    def test_callbacks_and_production_safety_source(self):
        source=(R/'main/host/standby_sleep.cpp').read_text()
        for signature in ('esp_err_t IRAM_ATTR enterSleep','esp_err_t IRAM_ATTR exitSleep'):
            body=function(source,signature)
            for forbidden in ('printf','ESP_LOG','mutex','lockRecovery','esp_pm_lock','GetHAL','uart_','gpio_','esp_timer','malloc'):
                self.assertNotIn(forbidden,body)
        self.assertIn('DRAM_ATTR std::atomic<uint32_t>',source)
        self.assertIn('is_always_lock_free',source)
        for config in ('CONFIG_PM_PROFILING','CONFIG_PM_LIGHT_SLEEP_CALLBACKS','!CONFIG_PM_SLP_SPIRAM_HALFSLEEP_ENABLED',
                       '!CONFIG_PM_POWER_DOWN_CPU_IN_LIGHT_SLEEP','!CONFIG_ESP_SLEEP_POWER_DOWN_FLASH','!CONFIG_PM_POWER_DOWN_PERIPHERAL_IN_LIGHT_SLEEP'):
            self.assertIn(config,source)
        for token in ('ESP_PD_DOMAIN_VDDSDIO,ESP_PD_OPTION_ON','gpio_wakeup_enable(GPIO_NUM_7,GPIO_INTR_LOW_LEVEL)',
                      'UART_WK_MODE_ACTIVE_THRESH','uart.rx_edge_threshold=3','ESP_PM_NO_LIGHT_SLEEP','esp_pm_dump_locks(stream)'):
            self.assertIn(token,source)
        for forbidden in ('nvs_','xTaskCreate','vTaskDelay','esp_light_sleep_start','esp_timer_create'):
            self.assertNotIn(forbidden,source)
        serial=(R/'main/debug/serial_debug.cpp').read_text()
        self.assertIn('event.type == UART_WAKEUP) StandbySleep::uartWake()',serial)
        self.assertIn('uart_wait_tx_done(UART_NUM_0,0)',serial)
        self.assertIn('_uart_tx.pending() || _uart_line_length || _uart_line_overflow',serial)
        self.assertIn('!ble.advertising && !ble.connected && !MosaicoOta::busy() && !MosaicoOta::healthPending()',serial)
        network=(R/'main/host/network_quota.cpp').read_text()
        otaWake=function(network,'void NetworkQuota::wakeForFirmwareUpdate()')
        self.assertLess(otaWake.index('StandbySleep::cancelForActivity()'),otaWake.index('setCpu(CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ)'))
        view=(R/'main/apps/app_codex_micro/view/view_mosaico.cpp').read_text()
        self.assertIn('standbyDimEligible() && !_settingsAnimating && !_slideTo,_rotationFault',view)
        self.assertIn('StandbySleep::cancelForActivity()',function(view,'void CodexMicroView::wakeDisplay()'))
        self.assertIn('refreshElapsed >= 60000U',view)
    def test_automatic_source_and_ota_atomic_entry_gates(self):
        source=(R/'main/host/standby_sleep.cpp').read_text()
        allow=function(source,'bool allow(')
        self.assertIn('if(MosaicoOta::busy() || MosaicoOta::healthPending())model.otaBlocked=true',allow)
        self.assertNotIn('model.otaBlocked=false',allow)
        self.assertLess(allow.index('model.eligible('),allow.index('initialize()'))
        service=function(source,'void service(')
        self.assertIn('model.otaBlocked=ota || MosaicoOta::busy() || MosaicoOta::healthPending()',service)
        cancel=function(source,'void cancelForActivity()')
        self.assertIn('model.pause()',cancel);self.assertNotIn('model.off()',cancel)
        ota=(R/'main/ota/mosaico_ota.cpp').read_text()
        for flag in ('active','approvedRequest','checkQueued','checking','installQueued','rebootQueued','bootPending'):
            self.assertIn(flag+'.store(true); StandbySleep::otaActivity();',ota)
        action=function(source,'void otaActivity()')
        self.assertIn('model.otaBlocked=true',action);self.assertIn('refreshRecovery(',action)
        serial=(R/'main/debug/serial_debug.cpp').read_text()
        auto=serial.split('if(!std::strcmp(action,"auto"))',1)[1].split('if(std::strcmp(action,"off")',1)[0]
        self.assertIn('!std::strcmp(confirm,"CONFIRM") && !::strtok_r',auto)
        self.assertIn('valid && StandbySleep::enableAutomatic(true)',auto)
        self.assertFalse(host.recovery_retry_safe('debug standby-sleep auto CONFIRM'))
        kconfig=(R/'main/Kconfig.projbuild').read_text()
        self.assertIn('config MOSAICO_STANDBY_AUTO_LIGHT_SLEEP',kconfig);self.assertIn('default n',kconfig)
        self.assertIn('initialModel(STANDBY_SLEEP_SUPPORTED,true)',source)
    def test_host_preamble_crc_and_default_unchanged(self):
        for enabled in (False,True):
            writes=[];flushes=[];events=[]
            def write(data):
                writes.append(data);events.append(('write',data))
            def flush():
                flushes.append(1);events.append(('flush',))
            fake=SimpleNamespace(open=lambda:None,close=lambda:None,write=write,flush=flush,
                readline=lambda:b'DBG RESULT command=ping status=PASS reply=pong\r\n')
            with patch.object(host.serial,'Serial',return_value=fake),patch.object(host.time,'sleep',side_effect=lambda seconds:events.append(('sleep',seconds))) as sleep:
                client=host.DebugClient('TEST-NO-DEVICE',uart=True,wake_preamble=enabled)
                self.assertFalse(fake.dtr);self.assertFalse(fake.rts)
                reply=client.command('debug ping','ping')
                self.assertEqual(reply.status,'PASS')
                burst=b'U'*32+b'\n';frame=host.encode_command('debug ping',True)
                self.assertEqual(writes,([burst]*3 if enabled else [])+[frame])
                self.assertEqual(len(flushes),3*int(enabled));self.assertEqual(sleep.call_count,3*int(enabled))
                self.assertEqual(events,([('write',burst),('flush',),('sleep',0.02)]*3 if enabled else [])+[('write',frame)])
                # Burst lines stay tiny and intentionally cannot be bare commands.
                self.assertEqual(len(burst),33);self.assertFalse(burst.startswith(b'uart '))
                nominal_seconds=3*(len(burst)*10/115200+0.02)
                self.assertGreaterEqual(nominal_seconds,0.05);self.assertLessEqual(nominal_seconds,0.07)
        with self.assertRaises(ValueError):host.DebugClient('TEST-NO-DEVICE',uart=False,wake_preamble=True)
    def test_host_retry_exact_idempotent_only(self):
        client=host.DebugClient.__new__(host.DebugClient);client.wake_preamble=True;client.uart=True
        for text in ('debug ping','debug settings get','dbg settings restore','debug standby-sleep status','debug standby-sleep off'):
            with patch.object(client,'_send_preamble') as preamble,patch.object(client,'_command_once',side_effect=[TimeoutError(),TimeoutError(),host.Result('x','PASS','')]) as once:
                self.assertTrue(host.recovery_retry_safe(text));client.command(text,'x')
                self.assertEqual(once.call_count,3);self.assertEqual(preamble.call_count,3)
        for text in ('debug standby-sleep on 180','debug settings set burn_in 0','debug settings save CONFIRM','debug ota-reboot','debug ota-update CONFIRM_EXTERNAL_POWER','debug ping extra','debug  ping'):
            with patch.object(client,'_send_preamble'),patch.object(client,'_command_once',side_effect=TimeoutError()) as once:
                self.assertFalse(host.recovery_retry_safe(text))
                with self.assertRaises(TimeoutError):client.command(text,'x')
                self.assertEqual(once.call_count,1)
        with patch.object(client,'_send_preamble'),patch.object(client,'_command_once',side_effect=TimeoutError()) as once:
            with self.assertRaises(TimeoutError):client.command('debug standby-sleep off','standby-sleep')
            self.assertEqual(once.call_count,3)
        with patch.object(client,'_send_preamble'),patch.object(client,'_command_once',return_value=host.Result('x','FAIL','')) as once:
            client.command('debug standby-sleep off','x');self.assertEqual(once.call_count,1)
if __name__=='__main__':unittest.main()
