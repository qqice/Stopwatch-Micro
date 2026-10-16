"""Source-extracted, device-free closed profile audit regression checks."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPILERS = list(Path('C:/Espressif/tools/riscv32-esp-elf').glob(
    '*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))


class GaugeProfileAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / 'main/hal/mosaico/hal_mosaico.cpp').read_text()
        cls.method = 'bool Hal::gaugeProfileAudit(' + cls.source.split(
            'bool Hal::gaugeProfileAudit(', 1)[1].split('\nbool Hal::gaugeReconcileNominal(', 1)[0]

    def test_readonly_boundaries_and_command(self):
        method = self.method
        self.assertLess(method.index('transaction(gauge_transaction_mutex)'), method.index('lock(battery_mutex)'))
        for forbidden in ('sample_battery', 'gauge_control', 'gauge_access', 'gauge_store',
                          'nvs_', 'gauge_write', 'battery_sample', 'gauge_identity'):
            self.assertNotIn(forbidden, method)
        mac_read = self.source.split('bool gauge_mac_read(', 1)[1].split('\n// Closed selector set', 1)[0]
        self.assertIn('GaugeProfileSelector::GaugingConfig', mac_read)
        self.assertIn('authenticateDmResponse(block, selector)', mac_read)
        self.assertNotIn('0x60', mac_read)  # no checksum/data commit
        model = (ROOT / 'main/hal/mosaico/mosaico_gauge_model.h').read_text()
        self.assertIn('enum class Field : uint16_t { InitialFcc = 0x929D, Design = 0x929F };', model)
        self.assertIn('MaxReasonableFcc = 78;', model)
        serial = (ROOT / 'main/debug/serial_debug.cpp').read_text()
        command = serial.split('if (std::strcmp(command, "gauge-profile") == 0)', 1)[1].split(
            'if (std::strcmp(command, "gauge-boot") == 0)', 1)[0]
        self.assertIn('::strtok_r(nullptr, " \\t", &save)', command)
        self.assertIn('expected=no_arguments readonly=1', command)
        self.assertIn('GetHAL().gaugeProfileAudit', command)
        self.assertIn('DBG HELP gauge-profile no_arguments=1 readonly=1', serial)
        self.assertIn('"gauge-profile"', (ROOT / 'main/debug/serial_debug_transport.h').read_text())
        self.assertIn('cfg_byte0=%u cfg_byte1=%u cedv_hex=%04x fcc_limit=%u', method)
        self.assertIn('(profile[0] >> 8) & 1U', method)

    def test_actual_helper_and_authentication_fake_mac(self):
        if not COMPILERS:
            self.skipTest('configured cross compiler unavailable')
        model = (ROOT / 'main/hal/mosaico/mosaico_gauge_model.h').read_text()
        auth = 'inline uint8_t macChecksum(' + model.split('inline uint8_t macChecksum(', 1)[1].split(
            '\ninline bool validJournal(', 1)[0]
        # Only add constexpr to the unchanged authentication function bodies so
        # fake I2C cases can be executed by the cross compiler, without a device.
        auth = auth.replace('inline ', 'constexpr ')
        helper = 'template <typename Reader>' + self.source.split('template <typename Reader>', 1)[1].split(
            '\nbool gauge_identity()', 1)[0]
        code = r'''
#include <cstdint>
#include <cstddef>
namespace mosaico_gauge {
enum class Field : uint16_t { InitialFcc = 0x929D, Design = 0x929F };
''' + auth + r'''
}
enum class GaugeProfileSelector : uint16_t { GaugingConfig = 0x929B };
''' + helper + r'''
struct FakeMac {
 int mode, calls=0;
 constexpr bool operator()(uint16_t selector, uint8_t block[36]) {
  constexpr uint16_t allowed[]={0x929B,0x929D,0x929F};
  if (selector!=allowed[calls%3]) return false;
  ++calls;
  if(mode==1) return false; // SEALED denial / transport failure
  uint16_t word=selector==0x929B ? (mode==7 ? 0x2A10 : 0x102A) : (selector==0x929D ? 100 : 65);
  if(mode==5 && calls==4) ++word; // independently read config changes
  block[0]=selector;block[1]=selector>>8;
  block[2]=word>>8;block[3]=word;
  block[35]=36;
  block[34]=mosaico_gauge::macChecksum(block,34);
  if(mode==2) ++block[34]; // bad checksum
  if(mode==3) block[35]=6; // wrong length despite valid payload checksum
  if(mode==4) block[0]^=1; // mismatched selector
  if(mode==6) block[0]=block[1]=0xFF; // board-reference unreadable selector
  return true;
 }
};
constexpr bool cases() {
 for(int mode=0;mode<=7;++mode) {
  uint16_t words[3]={0xFFFF,0xFFFF,0xFFFF};const char* reason="";
  int count=0;
  FakeMac mac{mode};
  const bool ok=gauge_profile_read_stable([&](uint16_t s,uint8_t* b) constexpr {
    ++count;return mac(s,b);
  },words,reason);
  if(ok!=(mode==0 || mode==6)) return false;
  if(ok && (count!=6 || words[0]!=0x102A || words[1]!=100 || words[2]!=65)) return false;
  if(!ok && (words[0]!=0xFFFF || words[1]!=0xFFFF || words[2]!=0xFFFF)) return false;
 }
 return true;
}
static_assert(cases(),"closed selectors, six authenticated reads, no invalid result publication");
'''
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory) / 'profile.cpp'
            src.write_text(code)
            run = subprocess.run([str(COMPILERS[0]), '-std=c++17', '-Wall', '-Wextra',
                                  '-Werror', '-fsyntax-only', str(src)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)


if __name__ == '__main__':
    unittest.main()
