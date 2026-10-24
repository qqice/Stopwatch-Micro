"""Compile-time pure receiver-boundary tests; no SDK build/device/keys/storage IO."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
COMPILER = Path('C:/Espressif/tools/riscv32-esp-elf/esp-15.2.0_20251204/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe')

FIXTURE = r'''
#include "legacy_factory_mac.h"
namespace legacy = hal_legacy_factory_mac;
constexpr std::array<uint8_t, 8> eui64{0x30,0xed,0xa0,0xff,0xfe,0xf4,0x0b,0xfc};
constexpr std::array<uint8_t, 6> oldIdentity{0x30,0xed,0xa0,0xff,0xfe,0xf4};
constexpr bool equal(const std::array<uint8_t, 6>& a, const std::array<uint8_t, 6>& b) {
    for (unsigned i = 0; i < a.size(); ++i) if (a[i] != b[i]) return false;
    return true;
}
struct Guarded {
    uint8_t before{0xa5};
    std::array<uint8_t, 6> output{1,2,3,4,5,6};
    uint8_t after{0x5a};
};
constexpr bool fullEightWrite() {
    Guarded guarded{};
    unsigned calls = 0;
    const bool ok = legacy::read(guarded.output, [&](uint8_t* received) {
        ++calls;
        for (unsigned i = 0; i < 8; ++i) {
            if (received[i] != 0) return -1;
            received[i] = eui64[i];
        }
        return 0;
    });
    // A six-byte internal receiver makes constexpr evaluation fail at index 6.
    return ok && calls == 1 && equal(guarded.output, oldIdentity) &&
           guarded.before == 0xa5 && guarded.after == 0x5a;
}
constexpr bool nonIeeeSixWrite() {
    Guarded guarded{};
    const std::array<uint8_t, 6> six{0x24,1,2,3,4,5};
    const bool ok = legacy::read(guarded.output, [&](uint8_t* received) {
        if (received[6] != 0 || received[7] != 0) return -1;
        for (unsigned i = 0; i < 6; ++i) received[i] = six[i];
        return 0;
    });
    return ok && equal(guarded.output, six) && guarded.before == 0xa5 && guarded.after == 0x5a;
}
constexpr bool failure(bool writeFirst) {
    Guarded guarded{};
    unsigned calls = 0;
    const bool ok = legacy::read(guarded.output, [&](uint8_t* received) {
        ++calls;
        if (writeFirst) for (unsigned i = 0; i < 8; ++i) received[i] = eui64[i];
        return -1;
    });
    return !ok && calls == 1 && equal(guarded.output, std::array<uint8_t, 6>{}) &&
           guarded.before == 0xa5 && guarded.after == 0x5a;
}
static_assert(fullEightWrite(), "EUI64 receiver and legacy prefix/canaries");
static_assert(nonIeeeSixWrite(), "six-byte SDK write retains identity");
static_assert(failure(false), "failure without write zeros prior output");
static_assert(failure(true), "failure after eight-byte write zeros prior output");
'''


class LegacyFactoryMacTests(unittest.TestCase):
    def test_pure_constexpr_receiver_and_failure_cases(self):
        self.assertTrue(COMPILER.is_file(), 'existing host-side compiler required')
        output = ROOT / 'artifacts/hal-legacy-factory-mac-tests'
        output.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=output) as directory:
            source = Path(directory) / 'receiver.cpp'
            source.write_text(FIXTURE, encoding='utf-8')
            result = subprocess.run([str(COMPILER), '-std=c++17', '-Wall', '-Wextra',
                '-Werror', '-fsyntax-only', '-I', str(ROOT / 'main/hal'), str(source)],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_both_legacy_call_sites_use_injected_safe_receiver(self):
        hal = (ROOT / 'main/hal/hal.cpp').read_text(encoding='utf-8')
        board = (ROOT / 'main/hal/mosaico/hal_mosaico.cpp').read_text(encoding='utf-8')
        body = hal.split('Hal::getFactoryMac()', 1)[1].split('Hal::getFactoryMacString', 1)[0]
        prior = board.split('bool gauge_sealed_prior_verified()', 1)[1].split('bool gauge_store_access', 1)[0]
        for fragment in (body, prior):
            self.assertIn('std::array<uint8_t, 6> mac{};', fragment)
            self.assertIn('hal_legacy_factory_mac::read(mac, [](uint8_t* received)', fragment)
            self.assertIn('return esp_efuse_mac_get_default(received);', fragment)
        self.assertIn('if (!hal_legacy_factory_mac::read', prior)
        self.assertIn('gauge_load_access(access, mac.data())', prior)


if __name__ == '__main__':
    unittest.main()
