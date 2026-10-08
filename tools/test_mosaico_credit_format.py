"""Lexical credit display tests; no firmware build or device acceptance."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / 'main/apps/app_codex_micro/view'


class CreditFormatTests(unittest.TestCase):
    def test_actual_cpp_format(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        if not compiler:
            found = sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob(
                '*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
            compiler = str(found[-1]) if found else None
        if not compiler:
            self.skipTest('C++ compiler unavailable')
        code = '#include "' + (VIEW / 'credit_format.h').as_posix() + '"\n' + r'''
constexpr bool matches(const char* input, const char* expected) {
    char out[64]{};
    mosaico_credit::formatBalance(input, out, sizeof(out));
    for (size_t i = 0; i < sizeof(out); ++i) {
        if (out[i] != expected[i]) return false;
        if (!out[i]) return true;
    }
    return false;
}
static_assert(matches("62500.00000000", "62500"));
static_assert(matches("62500.120000", "62500.12"));
static_assert(matches("62500.00000001", "62500.00000001"));
static_assert(matches("12345678901234567890.12345678900", "12345678901234567890.123456789"));
static_assert(matches("0.000", "0"));
static_assert(matches("000.100", "000.1"));
static_assert(matches("-12.5000", "-12.5"));
static_assert(matches("+12.000", "+12"));
static_assert(matches("1000", "1000"));
static_assert(matches("", "--"));
static_assert(matches(nullptr, "--"));
static_assert(matches("inf", "inf"));
static_assert(matches("--", "--"));
static_assert(matches("1.200e3", "1.200e3"));
static_assert(matches("1.200x", "1.200x"));
static_assert(matches("1.2.00", "1.2.00"));
static_assert(matches(".5000", ".5000"));
static_assert(matches("1.", "1."));
static_assert(matches(" 1.000", " 1.000"));
static_assert(matches("1.000 ", "1.000 "));
constexpr bool boundaries() {
    char tiny[3]{};
    mosaico_credit::formatBalance("123.000", tiny, sizeof(tiny));
    if (tiny[0] != '1' || tiny[1] != '2' || tiny[2]) return false;
    char one[1]{'x'};
    mosaico_credit::formatBalance("123", one, sizeof(one));
    if (one[0]) return false;
    mosaico_credit::formatBalance("123", one, 0);
    mosaico_credit::formatBalance("123", nullptr, 0);
    return one[0] == 0;
}
static_assert(boundaries());
'''
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'credits.cpp'
            source.write_text(code, encoding='utf8')
            result = subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra',
                                     '-Werror', '-fsyntax-only', str(source)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_ui_integration(self):
        source = (VIEW / 'view_mosaico.cpp').read_text(encoding='utf8')
        self.assertIn('#include "credit_format.h"', source)
        self.assertIn('char balance[sizeof(bucket.creditBalance)];', source)
        self.assertIn('mosaico_credit::formatBalance(bucket.creditBalance, balance, sizeof(balance));', source)
        self.assertIn('"%s Credits", bucket.creditsUnlimited ? "inf" : balance', source)
        self.assertIn('if (bucket.creditsKnown)', source)


if __name__ == '__main__':
    unittest.main()
