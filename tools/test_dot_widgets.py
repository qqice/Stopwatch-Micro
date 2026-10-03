"""Offline dot checks. Source masks are parsed, not duplicated test assets.

Python geometry is supplementary; the actual C++ constexpr algorithmTest is
static_asserted by the syntax test using the local cross compiler when present.
No target linking, build, flash, LVGL runtime, or display is required.
"""
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "main/apps/app_codex_micro/view"
HEADER = (VIEW / "dot_patterns.h").read_text(encoding="utf-8")
SOURCE = (VIEW / "dot_widgets.cpp").read_text(encoding="utf-8")


class DotWidgetsTests(unittest.TestCase):
    def test_source_glyphs(self):
        glyphs = dict((key, tuple(map(int, rows.split(","))))
                      for key, rows in re.findall(r"\{'(.)',\{([\d,]+)\}\}", HEADER))
        self.assertTrue(set("0123456789.%-?+CKMBTPdhmro ") <= glyphs.keys())
        for rows in glyphs.values():
            self.assertEqual(len(rows), 7)
            self.assertTrue(all(0 <= row < 32 for row in rows))
        for text in ("99.99%", "100%", "Pro200", "999.99B", "?" * 32, "88888888888888888888888888888888"):
            dots = sum(row.bit_count() for char in text for row in glyphs[char])
            self.assertLessEqual(dots, 1120)
        self.assertNotEqual(glyphs["?"], glyphs["0"])

    def test_source_icons(self):
        masks = SOURCE.split("constexpr uint16_t masks[][9] = {", 1)[1].split("};", 1)[0]
        rows = [tuple(map(int, match.split(",")))
                for match in re.findall(r"\{([\d,]+)\}", masks)]
        self.assertEqual(len(rows), 13)
        self.assertEqual(len(set(rows)), 13)
        self.assertNotEqual(rows[11], rows[12])
        for mask in rows:
            self.assertEqual(len(mask), 9)
            self.assertTrue(all(0 <= value < 512 for value in mask))

    def test_text_geometry_reference(self):
        # The same explicit Pro200 geometry is checked by the C++ static_assert.
        pitch = min(8, 408 // 35, 42 // 7)
        diameter = max(1, pitch * 7 // 10)
        self.assertLess(diameter, pitch)
        self.assertLessEqual(34 * pitch + diameter, 408)
        self.assertLessEqual(6 * pitch + diameter, 42)
        for width in (1, 9, 32, 99, 198, 408):
            for height in range(1, 101):
                for count in range(33):
                    columns = count * 6 - 1 if count else 0
                    pitch = min(10, width // columns, height // 7) if columns else 0
                    diameter = max(1, pitch * 7 // 10) if pitch >= 1 else 0
                    drawn_w = (columns - 1) * pitch + diameter if diameter else 0
                    drawn_h = 6 * pitch + diameter if diameter else 0
                    self.assertLessEqual(drawn_w, width)
                    self.assertLessEqual(drawn_h, height)
                    if diameter:
                        self.assertLessEqual(diameter, pitch)
                    if width in (198, 408) and height >= 35 and count in (4, 6):
                        self.assertGreater(diameter, 0)
                        self.assertLess(diameter, pitch)
                    if width in (198, 408) and height >= 35 and count in (15, 17, 32):
                        self.assertGreater(diameter, 0)

    def test_meter_reference(self):
        for rows in range(1, 5):
            for width in (32, 99, 198, 408):
                pitch = min(8, 28 // rows)
                columns = min(200 // rows, width // pitch)
                total = rows * columns
                self.assertLessEqual(total, 200)
                fills = [(total * bp + 5000) // 10000 for bp in range(10001)]
                self.assertEqual((fills[0], fills[-1]), (0, total))
                self.assertEqual(fills, sorted(fills))
                self.assertTrue(any((col + row) % 2 == 0
                                    for col in range(columns) for row in range(rows)))

    def test_actual_cpp_syntax_and_static_assert(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            candidates = sorted(Path("C:/Espressif/tools/riscv32-esp-elf").glob(
                "*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe"))
            compiler = str(candidates[-1]) if candidates else None
        if not compiler:
            self.skipTest("C++ compiler unavailable; actual constexpr gate remains build-time")
        result = subprocess.run([compiler, "-std=c++17", "-fsyntax-only", "-DLV_CONF_SKIP",
                                 "-I" + str(ROOT / "components/lvgl"),
                                 str(VIEW / "dot_widgets.cpp")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
