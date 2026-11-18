"""OTA reel model and binding; no flash, network or rendering."""
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "main/apps/app_codex_micro/view"


class OtaReelTests(unittest.TestCase):
    def test_progress_coalesces_without_restarting_timer(self):
        compilers = sorted(Path("C:/Espressif/tools/riscv32-esp-elf").glob(
            "*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe"))
        self.assertTrue(compilers, "RISC-V compiler unavailable")
        harness = r'''
#include "ota_reel_model.h"
using namespace mosaico_ota_reel;
constexpr bool rapid() {
    Progress s;
    if (s.observe(100, 1000, 10, 100, true, false) != Action::Snap) return false;
    if (s.observe(110, 1100, 11, 100, true, true) != Action::Roll) return false;
    if (s.observe(120, 1200, 12, 100, true, true) != Action::Keep) return false;
    if (s.observe(130, 1300, 13, 100, true, true) != Action::Keep) return false;
    if (s.started != 110 || s.latest != 1300 || s.finished(349) ||
        !s.finished(350) || s.offset(230, 600) != 300) return false;
    return s.observe(350, 1400, 14, 100, true, true) == Action::Snap &&
           s.latest == 1400 && !s.active &&
           s.observe(360, 1500, 15, 100, true, true) == Action::Roll;
}
constexpr bool boundaries() {
    Progress s;
    s.observe(0, 100, 10, 1000, true, false);
    s.observe(20, 200, 20, 1000, true, true);
    if (s.observe(40, 10000, 1000, 1000, true, true) != Action::Snap || s.active) return false;
    if (s.observe(50, 0, 0, 2000, true, false) != Action::Snap ||
        s.observe(60, 100, 20, 2000, true, true) != Action::Roll) return false;
    if (s.observe(70, 100, 19, 2000, true, true) != Action::Snap) return false;
    if (s.observe(80, 200, 40, 2000, false, true) != Action::Snap || s.seen || s.active) return false;
    if (s.observe(90, 300, 60, 2000, true, true) != Action::Snap) return false;
    if (s.observe(95, 400, 80, 2000, true, true) != Action::Roll) return false;
    return s.observe(100, 500, 100, 2000, true, false) == Action::Snap && !s.active;
}
static_assert(rapid());
static_assert(boundaries());
'''
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory) / "test.cpp"
            case.write_text(harness, encoding="utf8")
            r = subprocess.run([str(compilers[-1]), "-std=c++17", "-fsyntax-only",
                                "-I", str(VIEW), str(case)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_ui_updates_meter_and_bytes_immediately_and_reuses_cadence(self):
        cpp = (VIEW / "view_mosaico.cpp").read_text(encoding="utf8")
        render = cpp.split("void CodexMicroView::renderOta()", 1)[1].split(
            "void CodexMicroView::touchEvent", 1)[0]
        self.assertIn("otaReelVisible(), !staticChanged", render)
        self.assertIn("setTextReel(_otaPercent, _otaReelFrom)", render)
        self.assertIn("setMeter(_otaMeter,view.bp", render)
        self.assertIn("lv_label_set_text(_otaBytes,text)", render)
        animate = cpp.split("void CodexMicroView::updateAnimations(", 1)[1]
        self.assertLess(animate.index("_motionCadence.sample"), animate.index("_otaReel.offset"))
        self.assertIn("otaPercent(_otaReel.latest, progress", animate)
        self.assertIn('"%u.%02u%%"', cpp.split("void otaPercent(", 1)[1].split("}", 1)[0])


if __name__ == "__main__":
    unittest.main()
