"""Exercise the firmware's wake gate and window identity with offline data."""
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "main/apps/app_codex_micro/view"


class QuotaWakeReelTests(unittest.TestCase):
    def test_gate_and_window_identity_in_cpp(self):
        compilers = sorted(Path("C:/Espressif/tools/riscv32-esp-elf").glob(
            "*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe"))
        self.assertTrue(compilers)
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory) / "reel.cpp"
            case.write_text(r'''
#include "quota_wake_reel.h"
using namespace mosaico_quota_wake;
constexpr bool wake() {
    Gate g;
    g.begin(1000, 7, 60, true);
    if (g.eligible(true, 999, 8, 61) || g.eligible(true, 1001, 8, 60) ||
        g.eligible(true, 1001, 7, 61) || g.eligible(false, 1001, 8, 61)) return false;
    if (!g.eligible(true, 1001, 8, 61)) return false;
    g.cancel();
    if (g.eligible(true, 1002, 9, 62)) return false;
    g.begin(2000, 9, 63, false);
    return !g.eligible(true, 2001, 10, 64);
}
static_assert(wake());
constexpr bool values() {
    const Reading before{{Value{"57219", 100000, 10080, true},
                          Value{"88%", 8000, 300, false}}, 50};
    return changed(before, Value{"57216", 100000, 10080, true}, 1600) &&
        changed(before, Value{"89%", 8000, 300, false}, 1600) &&
        !changed(before, Value{"57219", 100000, 10080, true}, 1600) &&
        !changed(before, Value{"57216", 100001, 10080, true}, 1600) &&
        !changed(before, Value{"57216", 100000, 10080, true}, 100000) &&
        !changed(before, Value{"0%", 100000, 10080, false}, 1600);
}
static_assert(values());
''', encoding="utf8")
            r = subprocess.run([str(compilers[-1]), "-std=c++17", "-fsyntax-only",
                                "-I", str(VIEW), "-I", str(ROOT / "main"), str(case)],
                               text=True, capture_output=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_ui_arms_before_radio_and_cached_redraw(self):
        src = (VIEW / "view_mosaico.cpp").read_text(encoding="utf8")
        wake = src.split("void CodexMicroView::wakeDisplay()", 1)[1].split(
            "void CodexMicroView::lockDisplay()", 1)[0]
        self.assertLess(wake.index("_wakeReelGate.begin"), wake.index("setLocked(false)"))
        self.assertLess(wake.index("_wakeReelGate.begin"), wake.index("refreshQuota("))
        self.assertIn("_wakeReelGate.eligible(fresh, _quota->receivedAtUs", src)
        self.assertIn("if (wakeUpdate) _wakeReelGate.cancel();", src)
        self.assertIn("if (awakeQuota && fresh) _lastVisibleQuota", src)


if __name__ == "__main__":
    unittest.main()
