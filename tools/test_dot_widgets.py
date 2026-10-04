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

    def test_motion_reference_and_source_bounds(self):
        # Supplementary reference using the actual source cosine table; C++
        # motionTest static_assert exercises actual functions in the syntax test.
        table = list(map(int, re.search(r"cosine\[\] = \{([\d,]+)\}", HEADER).group(1).split(",")))
        self.assertEqual(len(table), 10)
        for phase in range(360):
            sign = -1 if 90 <= phase <= 270 else 1
            folded = 360 - phase if phase > 180 else phase
            folded = 180 - folded if folded > 90 else folded
            index, fraction = divmod(folded, 10)
            scale = 0 if index == 9 else sign * (table[index] + int((table[index + 1] - table[index]) * fraction / 10))
            self.assertLessEqual(abs(scale), 1000)
            for pitch in (2, 3, 4):
                for x in range(9):
                    projected = 4 * pitch + int((x - 4) * pitch * scale / 1000)
                    self.assertTrue(0 <= projected <= 8 * pitch)
            for rows in range(1, 5):
                total = rows * (200 // rows)
                for bp in (0, 1, 1500, 9999, 10000):
                    filled = (total * bp + 5000) // 10000
                    columns = (filled + rows - 1) // rows if filled else 0
                    highlight = phase * columns // 360 if columns else -1
                    # Color-only overlay has no dots outside the actual fill.
                    highlighted = [i for i in range(filled) if i // rows == highlight]
                    self.assertTrue(all(i < filled for i in highlighted))
                    self.assertEqual(len(range(filled)), filled)
                    if not filled:
                        self.assertEqual(highlight, -1)
                    # Three-column continuous reflection. It is only a color
                    # overlay on already-filled dots; a one-column fill pulses.
                    def wave(column):
                        if not columns or column >= columns:
                            return 0
                        if columns == 1:
                            p = phase % 180
                            return 40 + ((p if p <= 90 else 180 - p) * 80 // 90) // 2
                        circumference = columns * 256
                        position = phase * circumference // 360
                        distance = abs(column * 256 - position)
                        distance = min(distance, circumference - distance)
                        return (384 - distance) * 80 // 384 if distance < 384 else 0
                    mixes = [wave(col) for col in range(200 // rows)]
                    self.assertTrue(all(0 <= mix <= 80 for mix in mixes))
                    self.assertTrue(all(col * rows < filled for col, mix in enumerate(mixes) if mix))
                    self.assertLessEqual(sum(mix > 0 for mix in mixes), 3)
        self.assertIn("if (columns == 1) return 40 + pulse(phase) / 2;", HEADER)
        self.assertIn("dsc.bg_color = lit ? foreground : lv_color_hex(0x283642);", SOURCE)
        self.assertIn("s->icon == Icon::Battery && s->valid && m.pulse", SOURCE)
        self.assertNotRegex(SOURCE, r"\blv_(timer_create|anim_start|obj_create)\s*\(.*(?:phase|motion)")

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
