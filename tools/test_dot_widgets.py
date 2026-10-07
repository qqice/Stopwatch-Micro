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
    def test_credit_unit_is_credits_and_keeps_bounded_label(self):
        ui=(VIEW/'view_mosaico.cpp').read_text(encoding='utf8')
        self.assertIn('"%s Credits"', ui)
        self.assertNotIn('"%s Points"', ui)
        self.assertIn('lv_obj_set_height(_creditValues[i], 26)', ui)
        self.assertIn('lv_label_set_long_mode(_creditValues[i], LV_LABEL_LONG_MODE_DOTS)', ui)

    def test_upgrade_glyphs(self):
        glyphs = dict((key, tuple(map(int, rows.split(","))))
                      for key, rows in re.findall(r"\{'(.)',\{([\d,]+)\}\}", HEADER + SOURCE))
        for char in "CHECKDOWNLOADUPGRADEREBOOT":
            self.assertIn(char, glyphs)
            self.assertEqual(len(glyphs[char]), 7)
            self.assertNotEqual(glyphs[char], glyphs["?"])
        self.assertIn('static_assert(actionGlyphsTest()', SOURCE)

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
        self.assertEqual(len(rows), 18)
        self.assertEqual(len(set(rows)), 18)
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

    def test_motion_uses_shared_stationary_sampler(self):
        self.assertIn('render::sampleMeter(', SOURCE)
        self.assertIn('render::sampleIcon(', SOURCE)
        self.assertIn('render::meterChanged(', SOURCE)
        self.assertIn('meterColumnColor(', (VIEW/'dot_render_model.h').read_text(encoding='utf8'))
        self.assertNotIn('waveLift', SOURCE)
        self.assertNotIn('liftA', SOURCE)
        self.assertNotIn('-(lit ? lift', SOURCE)
        self.assertIn('s->icon == Icon::Battery && s->valid && m.pulse', SOURCE)
        self.assertNotRegex(SOURCE, r"\blv_(timer_create|anim_start)\s*\(")
        view=(VIEW/'view_mosaico.cpp').read_text(encoding='utf8')
        self.assertIn('% 6000U) * 360U / 6000U', view)
        self.assertIn('% 12000U) * 360U / 12000U', view)

    def test_cached_quota_hue_and_animation_gates_source(self):
        # A source-only integration guard, not a GUI/hardware or frequency test.
        view = (VIEW / "view_mosaico.cpp").read_text(encoding="utf-8")
        self.assertIn("const uint32_t color = levelColor(bp);", view)
        self.assertIn("return stale ? dimCachedColor(color) : color;", view)
        self.assertIn("static_assert(dimCachedColor(Green) == 0x52B88B", view)
        self.assertIn("const uint32_t quotaColor = quotaLevelColor(w.remainingBasisPoints, _quota->stale);", view)
        self.assertIn("setText(value, buf, quotaColor);", view)
        self.assertIn("setText(_lockQuota, lockText, lockKnown ? quotaLevelColor(lockBp, _quota->stale) : Gray);", view)
        self.assertNotRegex(view, r"quotaColor\s*=\s*[^;]*stale\s*\?\s*Gray")
        animation = view.split("void CodexMicroView::updateAnimations(", 1)[1].split("void CodexMicroView::refreshQuota(", 1)[0]
        for gate in ("if (_locked || _suppressed || _slideTo) return;", "_page == Page::Command", "!_quota->stale", "age <= 130", "GetNetworkQuota().connected()",
                     "!lv_obj_has_flag(_cards[i], LV_OBJ_FLAG_HIDDEN)", "window.available && window.remainingBasisPoints > 0"):
            self.assertIn(gate, animation)
        lock = view.split("void CodexMicroView::lockDisplay()", 1)[1].split("void CodexMicroView::", 1)[0]
        self.assertIn("stopAnimations();", lock)

    def test_clip_spans_preserve_inclusive_geometry(self):
        def span(origin, step, extent, count, low, high):
            if step <= 0 or extent <= 0 or count <= 0 or high < low or high < origin:
                return range(0)
            delta = low-origin-extent+1
            first = min(count, (delta+step-1)//step if delta>0 else 0)
            end = min(count, (high-origin)//step+1)
            return range(first,max(first,end))
        for origin in (-480,-20,0,23,480):
            for step in (2,4,8,24):
                for extent in (1,step-1,step,4*step+1):
                    for count in (0,1,7,32):
                        for low in range(origin-12,origin+count*step+12,3):
                            for width in (1,2,7,40):
                                high=low+width-1
                                expected=[i for i in range(count) if origin+i*step<=high and origin+i*step+extent-1>=low]
                                self.assertEqual(list(span(origin,step,extent,count,low,high)),expected)
        # Shared meter/icon sinks retain the exact final primitive clip test.
        for source in ('layer->_clip_area','i = chars.first; i < chars.end','y = rows.first; y < rows.end',
                       'render::sampleMeter(', 'render::sampleIcon('):
            self.assertIn(source,SOURCE)

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
        # Compile actual textLayout/validBounds with dimensions read from the view,
        # guarding both short and maximal honest percentage strings.
        view=(VIEW / "view_mosaico.cpp").read_text(encoding="utf-8")
        w,h,pitch=map(int,re.search(r'_otaPercent = createText\(_otaPage, (\d+), (\d+), (\d+)\)',view).groups())
        checks='#include "dot_patterns.h"\nusing namespace mosaico_dot::detail;\n'
        for count in (len("100%"),len("100.00%")):
            checks+=f'static_assert(validBounds(textLayout({w},{h},{count},{pitch}),{w},{h}) && textLayout({w},{h},{count},{pitch}).diameter>0);\n'
        result=subprocess.run([compiler,"-std=c++17","-fsyntax-only","-x","c++","-I"+str(VIEW),"-"],
                              input=checks,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)



if __name__ == "__main__":
    unittest.main()
