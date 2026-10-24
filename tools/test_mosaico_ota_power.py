"""Actual C++ constexpr power boundaries and source integration; no board/build."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPILER = Path('C:/Espressif/tools/riscv32-esp-elf/esp-15.2.0_20251204/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe')
CPP = (ROOT / 'main/ota/mosaico_ota.cpp').read_text()


class OtaPowerTests(unittest.TestCase):
    def test_usb_short_circuits_all_gauge_eligibility(self):
        self.assertNotRegex(CPP, r'\bbatteryTelemetry\s*\(')
        for name in ('downloadPowerSafe', 'automaticPowerSafe', 'installPowerSafe'):
            body = CPP.split('bool ' + name + '(', 1)[1].split('\n}', 1)[0]
            self.assertLess(body.index('activeUsbPower()'), body.index('physicalBatteryTelemetry(true).native'))
            self.assertNotIn('GaugeBootReloadStatus', body)
            self.assertNotIn('gaugeScaleTrialActive', body)

    def test_actual_cpp_boundaries(self):
        automatic = CPP.split('bool automaticPowerSafe(', 1)[1].split('bool installPowerSafe(', 1)[0]
        expression = re.search(r'return (b.valid.*?);', automatic, re.S).group(1)
        code = r'''
#include "main/ota/mosaico_ota_power.h"
struct Telemetry {
 bool valid=true, capacityValid=false, nominalConfigured=false;
 uint8_t reportedSoc=0;
 uint16_t voltageMv=3900, remainingMah=0, fullMah=0, operationStatus=6;
 int16_t currentMa=-20;
};
using MosaicoOta::manualInstallPowerSafe;
constexpr bool automatic(const Telemetry& b) { return AUTOMATIC_EXPRESSION; }
constexpr bool test() {
 Telemetry b;
 // Battery voltage is fresh physical evidence, not calibrated capacity/SOC/history.
 if(!manualInstallPowerSafe(b,false,true)||automatic(b))return false;
 b.currentMa=4;if(!automatic(b))return false;
 b.valid=false;if(!manualInstallPowerSafe(b,true,true)||manualInstallPowerSafe(b,false,false))return false;
 b.valid=true;b.voltageMv=0;if(!manualInstallPowerSafe(b,true,true)||manualInstallPowerSafe(b,false,false))return false;
 b.voltageMv=3899;if(manualInstallPowerSafe(b,false,false)||automatic(b))return false;
 b.voltageMv=3900;b.operationStatus=7;
 if(!manualInstallPowerSafe(b,true,true)||manualInstallPowerSafe(b,false,false)||automatic(b))return false;
 b.operationStatus=6;b.reportedSoc=255;
 if(!manualInstallPowerSafe(b,false,true))return false;
 return true;
}
static_assert(test());
'''.replace('AUTOMATIC_EXPRESSION', expression)
        run = ROOT / 'artifacts/ota-power-policy-tests';run.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=run) as directory:
            source = Path(directory) / 'power.cpp';source.write_text(code)
            result = subprocess.run([str(COMPILER), '-std=c++17', '-fsyntax-only', '-I' + str(ROOT), str(source)], capture_output=True, text=True)
            log = ROOT / '.artifacts/mosaico/ota-power-constexpr.log'
            log.parent.mkdir(parents=True, exist_ok=True);log.write_text(result.stdout + result.stderr)
            self.assertEqual(result.returncode, 0, str(log))

    def test_fresh_gate_twice_and_manual_mode_only(self):
        install = CPP.split('bool installVerified()', 1)[1].split('bool requestCheck()', 1)[0]
        self.assertIn('const bool manual = useManualInstallPolicy(usbBypass, automaticMode);', install)
        self.assertIn('!installPowerSafe(manual)', install)
        self.assertIn('!installPowerSafe(manual, &power)', install)
        self.assertLess(install.index('!installPowerSafe(manual, &power)'), install.index('recordAttempt(power)'))
        self.assertLess(install.index('recordAttempt(power)'), install.index('esp_ota_set_boot_partition(target)'))
        self.assertIn('manual ? "install_power_required" : "install_external_power_required"', install)
        gate = CPP.split('bool installPowerSafe(', 1)[1].split('bool exactSlot(', 1)[0]
        self.assertIn('physicalBatteryTelemetry(true).native', gate)
        self.assertIn('if (!manual) return automaticPowerSafe(evidence);', gate)

    def test_journal_evidence_same_commit_old_key_optional(self):
        record = CPP.split('bool recordAttempt(', 1)[1].split('bool requestLocked(', 1)[0]
        self.assertIn('nvs_set_str(handle, "attempt_power", power.text)', record)
        self.assertEqual(record.count('nvs_commit(handle)'), 1)
        status = CPP.split('void status(', 1)[1].split('void healthPoll(', 1)[0]
        self.assertIn('char power[96] = "unknown";', status)
        self.assertLess(status.index('!guard.owns_lock() || busy()'), status.index('nvs_open('))
        self.assertIn('attempt_power=%s', status)

    def test_reboot_fresh_gate_retains_selector_and_image(self):
        reboot = CPP.split('bool rebootWithFreshPower()', 1)[1].split('void processLocalRequests()', 1)[0]
        self.assertIn('installPowerSafe(useManualInstallPolicy(usbBypass, automaticMode))', reboot)
        self.assertLess(reboot.index('installPowerSafe('), reboot.index('esp_restart()'))
        self.assertIn('publish(UiStage::ReadyReboot, "reboot_power_required")', reboot)
        for forbidden in ('selected.store', 'imageReady', 'fail(', 'reject(', 'esp_ota_set_boot_partition'):
            self.assertNotIn(forbidden, reboot)
        process = CPP.split('void processLocalRequests()', 1)[1].split('bool finish()', 1)[0]
        self.assertEqual(process.count('rebootWithFreshPower()'), 2)
        self.assertNotIn('esp_restart()', process)
        self.assertIn('if ((usbBypass || automaticMode || maintenanceMode) && selected.load())', process)
        queued = CPP.split('bool requestReboot()', 1)[1].split('bool rebootWithFreshPower()', 1)[0]
        self.assertNotIn('batteryPolicyTelemetry(', queued)
        self.assertIn('revokeForNormalRequest();', queued)


if __name__ == '__main__':
    unittest.main()
