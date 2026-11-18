"""Compile the real API credit display geometry, state and dollar renderer."""
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "main/apps/app_codex_micro/view"


class ApiCreditDisplayTests(unittest.TestCase):
    def test_state_and_dollar_renderer(self):
        compilers = sorted(Path("C:/Espressif/tools/riscv32-esp-elf").glob(
            "*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe"))
        self.assertTrue(compilers)
        case = r"""
#include "api_credit_display.h"
#include "dot_render_model.h"
using namespace mosaico_api_credit;
using namespace mosaico_dot::render;
constexpr bool states() {
    if (present({}, 123).known) return false;
    const QuotaMonitorApiCredit a{true, ApiCreditState::Ok, 5428, 1743000000, 1740000000};
    const auto current = present(a, a.capturedEpoch);
    const auto aged = present(a, a.capturedEpoch + LocalStaleSeconds);
    const auto expired = present(a, a.expiresEpoch);
    const QuotaMonitorApiCredit zero{true, ApiCreditState::Ok, 0, 0, a.capturedEpoch};
    const QuotaMonitorApiCredit noFundsKnown{true, ApiCreditState::Unavailable, 0, 0, 0};
    const QuotaMonitorApiCredit reauth{true, ApiCreditState::Reauth, 5428, a.expiresEpoch, a.capturedEpoch};
    const QuotaMonitorApiCredit forbidden{true, ApiCreditState::Forbidden, 0, 0, 0};
    return current.known && current.fresh && !*current.detail &&
        aged.known && !aged.fresh && *aged.detail == 'S' &&
        !expired.known && !expired.fresh && *expired.detail == 'E' &&
        present(zero, a.capturedEpoch).known &&
        !present(noFundsKnown, a.capturedEpoch).known &&
        present(reauth, a.capturedEpoch).known && !present(reauth, a.capturedEpoch).fresh &&
        *present(reauth, a.capturedEpoch).detail == 'L' &&
        !present(forbidden, a.capturedEpoch).known && *present(forbidden, a.capturedEpoch).detail == 'N';
}
static_assert(states());
constexpr bool geometry() {
    for (int phase : {0, 45, 90, 180, 270, 359}) {
        BoundsSink s{IconSize, IconSize};
        sampleIcon(Accent::Dollar, IconSize, IconSize, phase, true, Rose, s);
        if (!s.valid || s.count < 20 || s.count > 100) return false;
    }
    return Split - 14 == HalfWidth && Split + HalfWidth == 422 &&
        52 + CreditsWidth < Split && 14 + MetaWidth < Split &&
        FooterY + AmountY + AmountHeight < FooterY + DateY &&
        FooterY + DateY + DateHeight <= 350 &&
        supports(19) && iconPixel(Accent::Dollar, 6, 1) &&
        !iconPixel(Accent::Dollar, 5, 1) &&
        iconPixel(Accent::Dollar, 8, 7) &&
        iconChanged(Accent::Dollar, IconSize, IconSize, 0, true, 90, true, Rose) &&
        !iconChanged(Accent::Dollar, IconSize, IconSize, 0, true, 180, true, Rose) &&
        !iconChanged(Accent::Dollar, IconSize, IconSize, 0, false, 90, false, Rose);
}
static_assert(geometry());
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "api_credit.cpp"
            path.write_text(case, encoding="utf8")
            proc = subprocess.run([str(compilers[-1]), "-std=c++17", "-fsyntax-only",
                                   "-I", str(VIEW), "-I", str(ROOT / "main"), str(path)],
                                  text=True, capture_output=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_view_integrates_only_in_the_quota_card(self):
        source = (VIEW / "view_mosaico.cpp").read_text(encoding="utf8")
        self.assertIn("_quota->apiCredit.present && mosaico_credit::isCodex(bucket.id)", source)
        self.assertIn("lv_obj_set_parent(_apiCreditFooter, _cards[i]);", source)
        self.assertIn("Icon::Dollar, IconSize, Rose", source)
        self.assertIn("formatDate(_quota->apiCredit, apiView", source)
        self.assertIn("setMotion(_apiCreditIcon, phase, rotateApi);", source)
        self.assertIn("setMotion(_apiCreditIcon, 0, false);", source)
        model = (VIEW / "api_credit_display.h").read_text(encoding="utf8")
        self.assertIn("%lu.%02lu USD", model)
        self.assertIn("MosaicoClock::shanghaiTime", model)

    def test_production_parser_syntax_with_configured_esp_idf(self):
        db = Path(os.environ.get("MOSAICO_OTA_UI_COMPILE_COMMANDS",
                                 str(ROOT / ".artifacts/mosaico/beacon-native-build-001/compile_commands.json")))
        if not db.is_file():
            self.skipTest("current target compilation database unavailable")
        entry = next(e for e in json.loads(db.read_text()) if e["file"].endswith("quota_monitor.cpp"))
        command = re.sub(r" -o \S+ -c ", " -fsyntax-only ", entry["command"])
        self.assertIn("-fsyntax-only", command)
        proc = subprocess.run(command, cwd=entry["directory"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
