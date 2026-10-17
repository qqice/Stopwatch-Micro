"""Host/source contracts for read-only quota widget inspection; no device I/O."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / 'main/apps/app_codex_micro/view'
CPP = (VIEW / 'view_mosaico.cpp').read_text(encoding='utf8')
APP = (VIEW.parent / 'app_codex_micro.cpp').read_text(encoding='utf8')
DOT = (VIEW / 'dot_widgets.cpp').read_text(encoding='utf8')
BODY = CPP.split('bool CodexMicroView::debugQuotaDisplay(', 1)[1].split('\nvoid CodexMicroView::init(', 1)[0]
APP_BODY = APP.split('bool AppCodexMicro::debugQuotaDisplay(', 1)[1].split('\nbool AppCodexMicro::debugSetScreen(', 1)[0]
GETTER = DOT.split('const char* text(lv_obj_t* obj) {', 1)[1].split('\n}', 1)[0]


class QuotaViewProbeTests(unittest.TestCase):
    def test_reads_actual_retained_text_not_reformatted_backend(self):
        self.assertIn('mosaico_dot::text(_lockQuota)', BODY)
        self.assertIn('mosaico_dot::text(_lockResetTime)', BODY)
        self.assertIn('state(obj, Kind::Text)', GETTER)
        self.assertIn('return s ? s->text : nullptr;', GETTER)
        setter = DOT.split('void setText(lv_obj_t* obj,', 1)[1].split('\nlv_obj_t* createMeter(', 1)[0]
        self.assertIn('std::memcpy(s->text, value, sizeof value)', setter)
        self.assertIn('_quotaRevision', BODY)
        self.assertEqual(BODY.count('QuotaMonitorRevision()'), 1)
        self.assertNotIn('_quota->', BODY)
        for field in ('page=%u', 'locked=%u', 'ui_rev=%u', 'backend_rev=%u', 'lock_quota=', 'lock_reset='):
            self.assertIn(field, BODY)

    def test_readonly_and_app_guard_covers_inspection(self):
        for forbidden in ('refreshQuota(', 'wakeDisplay(', 'setText(', 'setMotion(', 'lv_obj_set_',
                          'GetHAL(', 'GetNetworkQuota(', 'activity', 'requestRefresh(', 'http', 'HTTP'):
            self.assertNotIn(forbidden, BODY)
            self.assertNotIn(forbidden, GETTER)
        self.assertLess(APP_BODY.index('LvglLockGuard lock;'), APP_BODY.index('_view->debugQuotaDisplay'))
        self.assertIn('#ifdef MOSAICO_BOARD', APP_BODY)
        self.assertIn('return false;', APP_BODY.split('#else', 1)[1])

    def test_bounded_ascii_skip_and_const_contracts(self):
        self.assertIn('if (out == nullptr || capacity == 0) return false;', BODY)
        self.assertIn('"SKIP"', BODY)
        self.assertIn('if (!ready()) return false;', BODY)
        self.assertIn('if (quota == nullptr || reset == nullptr) return false;', BODY)
        self.assertIn('char quotaText[33]{}, resetText[33]{};', BODY)
        self.assertIn('i < sizeof(dest) - 1 && source[i]', BODY)
        self.assertIn('ch >= 32 && ch <= 126', BODY)
        self.assertIn('std::snprintf(out, capacity,', BODY)
        self.assertIn(') const {', BODY)

    def test_production_body_cpp_syntax(self):
        compilers = sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
        self.assertTrue(compilers, 'Required C++ compiler unavailable')
        code = r'''
#include <cstddef>
#include <cstdint>
#include <cstdio>
struct lv_obj_t {};
namespace mosaico_dot { const char* text(lv_obj_t*); }
uint32_t QuotaMonitorRevision();
struct CodexMicroView {
  bool ready() const;
  bool debugQuotaDisplay(char*, std::size_t) const;
  lv_obj_t *_lockQuota=nullptr, *_lockResetTime=nullptr;
  uint32_t _quotaRevision=0;
  enum class Page { Command }; Page _page=Page::Command;
  bool _locked=false;
};
''' + 'bool CodexMicroView::debugQuotaDisplay(' + BODY
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'probe.cpp'
            source.write_text(code, encoding='utf8')
            result = subprocess.run([str(compilers[-1]), '-std=c++17', '-Wall', '-Wextra', '-Werror', '-fsyntax-only', str(source)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
