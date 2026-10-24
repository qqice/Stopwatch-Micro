"""Narrow UI binding regression; no device access or calibration claims."""
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
class BatteryDisplayTests(unittest.TestCase):
    def body(self):
        text=(ROOT/'main/apps/app_codex_micro/view/view_mosaico.cpp').read_text(encoding='utf8')
        return text.split('void CodexMicroView::refreshBattery(uint32_t now) {',1)[1].split('void CodexMicroView::refreshClock(',1)[0]
    def test_readable_native_soc_is_not_hidden_by_policy(self):
        b=self.body()
        self.assertIn('physicalBatteryTelemetry(false).native',b)
        self.assertIn('if (display.valid) std::snprintf',b)
        self.assertIn('static_cast<unsigned>(display.reportedSoc)',b)
        self.assertIn('Icon::Battery, color, display.reportedSoc, display.valid',b)
        self.assertIn('telemetry.nominalConfigured ? "*" : "?"',b)
    def test_transient_failure_keeps_last_visible_value(self):
        b=self.body();guard=b.index('if (!display.valid && _batterySeen && _batteryValid)')
        self.assertLess(guard,b.index('char text[80]'))
        self.assertIn('return;',b[guard:b.index('char text[80]')])
        self.assertIn('lv_color_hex(Gray)',b[guard:b.index('char text[80]')])
    def test_power_and_ota_policy_not_replaced_by_display_data(self):
        b=self.body()
        self.assertIn('_chargeSupply.update(telemetry.valid, telemetry.currentMa, telemetry.reportedSoc)',b)
        self.assertIn('manualInstallPowerSafe(telemetry, tud_mounted(), critical)',b)
        for command in ('gaugeContrastApply','gauge_control','gaugeCold','nvs_set','gauge_write'):
            self.assertNotIn(command,b)
if __name__=='__main__':unittest.main()
