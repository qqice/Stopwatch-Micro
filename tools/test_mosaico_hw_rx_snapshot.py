"""Offline owner fault shim; never contacts a device or links the Wi-Fi driver."""
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CPP = (ROOT / "main/host/network_quota.cpp").read_text()
FIELDS = "rx_fcs_err rx_abort rx_abort_fcs_pass nrx_err_pwrdrop rx_mpdu rx_end_cnt rx_datasuc rx_buf_fullcnt rx_fifo_ovfcnt rx_tkip_errcnt rx_lastunmatch_err nrx_err_bboff nrx_err_agcexit rxhung_statis txhung_statis rxtxhung rxtxpanic".split()
COMPILERS = sorted(Path("C:/Espressif/tools/riscv32-esp-elf").glob("*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe"))

class HwRxTests(unittest.TestCase):
    def test_owner_fault_shim(self):
        if not COMPILERS:
            self.skipTest("cross compiler unavailable")
        start = CPP.index("bool NetworkQuota::requestHwRxSnapshot(")
        end = CPP.index("\n#endif", start)
        methods = CPP[start:end]
        methods = methods.replace("bool NetworkQuota::", "constexpr bool NetworkQuota::")
        methods = methods.replace("MosaicoHwRx::Snapshot NetworkQuota::", "constexpr MosaicoHwRx::Snapshot NetworkQuota::")
        methods = methods.replace("void NetworkQuota::", "constexpr void NetworkQuota::")
        methods = methods.replace("MosaicoOta::blocksTwt()", "otaBlocked()")
        harness = r'''
#include "mosaico_hw_rx_snapshot.h"
#define portENTER_CRITICAL(x) ((void)0)
#define portEXIT_CRITICAL(x) ((void)0)
constexpr int ESP_OK=0, ESP_ERR_INVALID_STATE=259;
template<class T> struct Atomic {
 T value{};
 constexpr T load() const { return value; }
 constexpr void store(T v) { value=v; }
 constexpr bool compare_exchange_strong(T& expected,T desired) {
  if(value==expected){value=desired;return true;} expected=value;return false;
 }
};
struct wifi_ap_record_t {};
using esp_test_hw_rx_statistics_t=MosaicoHwRx::Snapshot;
struct NetworkQuota {
 Atomic<bool> _hw_rx_pending{}, _hw_rx_owner_ready{true}, _wifi_running{true}, _connected{true};
 MosaicoHwRx::Snapshot _hw_rx_cache;
 int _task_handle=1, notifications=0, apCalls=0, getterCalls=0, clocks=0;
 bool blocked=false, blockAfterAp=false;
 int apError=0, getterError=0;
 constexpr bool otaBlocked() { return blocked; }
 constexpr int esp_wifi_sta_get_ap_info(wifi_ap_record_t*) {
  ++apCalls; if(blockAfterAp)blocked=true; return apError;
 }
 constexpr int esp_test_get_hw_rx_statistics(esp_test_hw_rx_statistics_t* raw) {
  ++getterCalls;
  *raw={};
'''
        harness += "\n".join("  raw->"+f+"=37;" for f in FIELDS)
        harness += r'''
  return getterError;
 }
 constexpr int64_t esp_timer_get_time() { return ++clocks; }
 constexpr void xTaskNotifyGive(int) { ++notifications; }
 constexpr bool requestHwRxSnapshot(uint64_t&);
 constexpr MosaicoHwRx::Snapshot hwRxSnapshot() const;
 constexpr void serviceHwRxSnapshot();
};
''' + methods + r'''
constexpr bool faults() {
 NetworkQuota n;
 n.serviceHwRxSnapshot();
 if(n.getterCalls || n.apCalls || n.clocks || n.hwRxSnapshot().valid)return false;
 uint64_t id=0;
 if(!n.requestHwRxSnapshot(id) || id!=1 || n.hwRxSnapshot().valid)return false;
 uint64_t busy=99;
 if(n.requestHwRxSnapshot(busy) || busy!=0)return false;
 n.serviceHwRxSnapshot(); auto a=n.hwRxSnapshot();
 if(!a.valid || a.pending || a.processedId!=id || a.sampleSequence!=1 || a.error || !a.getterCalled || a.beforeUs!=1 || a.afterUs!=2 || a.rx_fcs_err!=37 || a.rxtxpanic!=37)return false;
 n.serviceHwRxSnapshot(); if(n.getterCalls!=1)return false;
 n.getterError=42;
 if(!n.requestHwRxSnapshot(id) || n.hwRxSnapshot().valid)return false;
 n.serviceHwRxSnapshot(); a=n.hwRxSnapshot();
 if(a.valid || a.pending || a.processedId!=id || a.sampleSequence!=1 || a.error!=42 || !a.getterCalled || a.rx_fcs_err || a.rxtxpanic)return false;
 n.getterError=0;
 if(!n.requestHwRxSnapshot(id))return false;
 n.blocked=true; n.serviceHwRxSnapshot(); a=n.hwRxSnapshot();
 if(a.valid || a.getterCalled || a.processedId!=id || n.getterCalls!=2 || a.beforeUs || a.afterUs)return false;
 if(n.requestHwRxSnapshot(id) || id!=4 || n.hwRxSnapshot().valid || n.hwRxSnapshot().processedId!=id)return false;
 n.blocked=false; n._wifi_running.store(false);
 if(!n.requestHwRxSnapshot(id))return false;
 auto apCount=n.apCalls; n.serviceHwRxSnapshot();
 if(n.getterCalls!=2 || n.apCalls!=apCount || n.hwRxSnapshot().valid)return false;
 n._wifi_running.store(true); n._connected.store(false);
 n.requestHwRxSnapshot(id); n.serviceHwRxSnapshot();
 if(n.getterCalls!=2 || n.apCalls!=apCount)return false;
 n._connected.store(true); n.apError=7;
 n.requestHwRxSnapshot(id); n.serviceHwRxSnapshot();
 if(n.getterCalls!=2 || n.hwRxSnapshot().valid)return false;
 n.apError=0; n.blockAfterAp=true;
 n.requestHwRxSnapshot(id); n.serviceHwRxSnapshot();
 if(n.getterCalls!=2 || n.hwRxSnapshot().valid || n.hwRxSnapshot().getterCalled)return false;
 n.blocked=false; n.blockAfterAp=false; n._hw_rx_owner_ready.store(false);
 // Stale nonnull task handle must not admit requests after owner exit/init failure.
 n._task_handle=1;
 if(n.requestHwRxSnapshot(id) || n.hwRxSnapshot().valid || n.hwRxSnapshot().pending)return false;
 if(n.notifications)return false;
 return true;
}
static_assert(faults(), "owner gating / stale cache / identity regression");
'''
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/"faults.cpp"
            source.write_text(harness)
            run = subprocess.run([str(COMPILERS[-1]), "-std=c++17", "-fsyntax-only", "-I"+str(ROOT/"main/host"), str(source)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout+run.stderr)

    def test_source_contract_and_strict_tokens(self):
        serial=(ROOT/"main/debug/serial_debug.cpp").read_text()
        start=serial.index('std::strcmp(command, "hw-rx")')
        block=serial[start:serial.index("\n#endif",start)]
        self.assertIn('!action || extra',block)
        self.assertIn('std::strcmp(action, "snapshot") && std::strcmp(action, "status")',block)
        self.assertIn('if (r.valid)',block)
        self.assertIn('"hw-rx"',(ROOT/"main/debug/serial_debug_transport.h").read_text())
        self.assertNotIn("esp_private",(ROOT/"main/host/network_quota.h").read_text())
        self.assertIn("sizeof(esp_test_hw_rx_statistics_t) >= 86",CPP)
        self.assertEqual(CPP.count("esp_test_get_hw_rx_statistics(&raw)"),1)
        region=CPP[CPP.index("bool NetworkQuota::requestHwRxSnapshot("):CPP.index("void NetworkQuota::wait(")]
        for forbidden in ("esp_test_clr", "esp_wifi_set", "malloc", "promiscuous", "esp_test_enable"):
            self.assertNotIn(forbidden,region)
        admission=region[:region.index("MosaicoHwRx::Snapshot NetworkQuota::hwRxSnapshot()")]
        self.assertNotIn("xTaskNotifyGive",admission)
        self.assertNotIn("_task_handle",admission)
        self.assertIn("_hw_rx_owner_ready.load()",admission)
        header=(ROOT/"main/host/network_quota.h").read_text()
        self.assertIn("_hw_rx_owner_ready{false}",header)
        task=CPP[CPP.index("void NetworkQuota::task("):CPP.index("void NetworkQuota::run(")]
        self.assertLess(task.index("->run()"),task.index("_hw_rx_owner_ready.store(false)"))
        self.assertLess(task.index("_hw_rx_owner_ready.store(false)"),task.index("vTaskDelete"))
        run=CPP[CPP.index("void NetworkQuota::run("):]
        ready=run.index("_hw_rx_owner_ready.store(true)")
        self.assertLess(run.index("esp_wifi_start()"),ready)
        self.assertLess(run.index("const bool ownsDisplaySettings"),ready)
        self.assertLess(ready,run.index("while (true)"))
        for field in FIELDS:
            self.assertIn("sample."+field+" = raw."+field,region)

if __name__ == "__main__":
    unittest.main()
