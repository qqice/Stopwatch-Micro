"""Compile the real Credits selection and reel policies; no device I/O."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "main/apps/app_codex_micro/view"


class CreditTakeoverTests(unittest.TestCase):
    def test_production_model(self):
        compilers = sorted(Path("C:/Espressif/tools/riscv32-esp-elf").glob(
            "*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe"))
        self.assertTrue(compilers, "RISC-V compiler required")
        code = r'''
#include "credit_takeover.h"
#include "dot_patterns.h"
using namespace mosaico_credit;
constexpr QuotaMonitorBucket codex() {
    QuotaMonitorBucket b{};
    constexpr char id[] = "codex";
    for (unsigned i = 0; i < sizeof(id); ++i) b.id[i] = id[i];
    b.windows[1] = {true, 0, WeeklyMinutes, 1700};
    return b;
}
constexpr bool modeEdges() {
    auto b = codex();
    if (exhaustedWeeklyWindow(b, 1600) != 1 || exhaustedWeeklyWindow(b, 1700) != -1) return false;
    if (balanceKind(b) != Balance::Unknown) return false;
    b.creditsKnown = true;
    if (balanceKind(b) != Balance::Unknown) return false; // Legacy v2 does not confirm availability.
    b.creditsAvailabilityKnown = true;
    if (balanceKind(b) != Balance::Empty) return false;
    b.creditsAvailable = true;
    if (balanceKind(b) != Balance::Unknown) return false; // Availability alone is not a numeric balance.
    b.creditBalance[0] = '0'; b.creditBalance[1] = '.'; b.creditBalance[2] = '9';
    if (balanceKind(b) != Balance::Finite || !takeover(balanceKind(b))) return false;
    b.creditBalance[2] = '0';
    if (balanceKind(b) != Balance::Empty) return false;
    b.creditBalance[2] = 'x';
    if (balanceKind(b) != Balance::Unknown) return false;
    b.creditsUnlimited = true;
    if (balanceKind(b) != Balance::Unlimited) return false;
    b.windows[0] = {true, 0, 300, 1660};
    if (exhaustedWeeklyWindow(b, 1600) != 1) return false;
    b.windows[0].durationMinutes = WeeklyMinutes;
    if (exhaustedWeeklyWindow(b, 1600) != -1) return false;
    b.windows[0].available = false; b.windows[1].remainingBasisPoints = 1;
    if (exhaustedWeeklyWindow(b,1600) != -1) return false;
    b.windows[1].remainingBasisPoints = 0; b.windows[1].resetEpoch = 0;
    if (exhaustedWeeklyWindow(b,1600) != -1) return false;
    b.windows[1].resetEpoch = 1700; b.id[0] = 'x';
    return exhaustedWeeklyWindow(b,1600) == -1;
}
static_assert(modeEdges());
static_assert(integerDrop("62500", "62420"));
static_assert(integerDrop("100", "099"));
static_assert(integerDrop("1000000000000000000000000000000", "999999999999999999999999999999"));
static_assert(!integerDrop("062420", "62420") && !integerDrop("0", "0") && !integerDrop("100","180"));
static_assert(!integerDrop("inf","100") && !integerDrop("100","--") && !integerDrop("100","99.9"));
constexpr bool updates() {
    const Reading first{"62500", 1000, 1700}, down{"62420", 1001, 1700};
    const Reading conflicting{"62490", 1000, 1700}, rolled{"62420", 1001, 1800};
    if (!rollDecrease(first, down, true, true)) return false;
    if (rollDecrease(first, down, false, true) || rollDecrease(first, down, true, false) ||
        rollDecrease(first, conflicting, true, true) || rollDecrease(first, rolled, true, true)) return false;
    const auto invalid = animationReference(first, conflicting);
    if (invalid.capturedEpoch != 1000 || invalid.resetEpoch || invalid.digits[0]) return false;
    if (animationReference(invalid, conflicting).resetEpoch || rollDecrease(invalid,down,true,true)) return false;
    return animationReference(invalid, down).resetEpoch == 1700 &&
           animationReference(first, first).capturedEpoch == 1000;
}
static_assert(updates());
static_assert(column("99", 2, 3, 0) == ' ' && column("99", 2, 3, 1) == '9');
static_assert(lift(70, 1, 45) == 0 && lift(70, 0, 45) > 0);
static_assert(lift(70, 3, duration(4)) == 70);
static_assert(duration(ReelMaxDigits) < 2000);
static_assert(mosaico_dot::detail::textLayout(160, 70, MaxBalanceDigits, 4).diameter == 0 ||
              mosaico_dot::detail::validBounds(
                  mosaico_dot::detail::textLayout(160, 70, MaxBalanceDigits, 4), 160, 70));
'''
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "credits.cpp"
            source.write_text(code, encoding="utf8")
            result = subprocess.run(
                [str(compilers[-1]), "-std=c++17", "-Wall", "-Wextra", "-Werror", "-fsyntax-only",
                 "-I" + str(ROOT / "main"), "-I" + str(VIEW), str(source)],
                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_view_boundaries(self):
        view = (VIEW / "view_mosaico.cpp").read_text(encoding="utf8")
        paint = (VIEW / "dot_widgets.cpp").read_text(encoding="utf8")
        lock = view.split("    if (lockCredits) {", 1)[1].split("    char lockReset", 1)[0]
        self.assertNotIn("setTextReel", lock)
        self.assertIn('setText(_lockQuota, shown, gold);', lock)
        self.assertIn('_creditReading = {}; // A hidden balance change is never replayed on wake.', view)
        self.assertIn('if (takeoverWindow) lv_obj_add_flag(_windowBars[i][j], LV_OBJ_FLAG_HIDDEN);', view)
        self.assertIn('sameMotion', view)
        self.assertIn('if (old == next) glyphAt(next, y0);', paint)
        self.assertIn('layer->_clip_area = saved;', paint)


if __name__ == "__main__":
    unittest.main()
