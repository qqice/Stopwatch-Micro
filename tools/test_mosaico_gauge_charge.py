"""Closed read-only charge snapshot: execute actual helper, syntax-check actual HAL."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
COMPILERS=list(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))

class ChargeAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()
        cls.helper='template <typename Reader>\nconstexpr bool gauge_charge_read('+cls.source.split('constexpr bool gauge_charge_read(',1)[1].split('\n// Independent OCV diagnostic backend.',1)[0]
        cls.method='bool Hal::gaugeChargeAudit('+cls.source.split('bool Hal::gaugeChargeAudit(',1)[1].split('\nbool Hal::gaugeProfileAudit(',1)[0]

    def compile(self,code):
        if not COMPILERS:self.skipTest('configured cross compiler unavailable')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'charge.cpp';p.write_text(code)
            r=subprocess.run([str(COMPILERS[0]),'-std=c++17','-Wall','-Wextra','-Werror','-fsyntax-only',str(p)],capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)

    def test_actual_closed_helper_all_register_failures(self):
        self.compile(r'''
#include <cstdint>
''' +self.helper+r'''
constexpr bool cases(){
 constexpr uint8_t expected[]={0x06,0x08,0x0A,0x0C,0x10,0x12,0x14,0x18,0x2C,0x30,0x32,0x3A,0x3C};
 constexpr uint16_t sample[]={2982,4194,0x5028,0xfff4,50,65,0xfff9,0xffff,77,4200,32,0x00a6,65};
 for(int fail=-1;fail<13;++fail){
  uint16_t v[13]{};for(auto& word:v)word=0xeeee;
  int calls=0;bool wrong=false;
  const bool ok=gauge_charge_read([&](uint8_t reg,uint16_t& value) constexpr {
   int i=calls++;if(i>=13||reg!=expected[i]){wrong=true;return false;}
   value=sample[i];return i!=fail;
  },v);
  if(wrong||calls!=13||ok!=(fail==-1))return false;
  for(unsigned i=0;i<13;++i)if(v[i]!=(ok?sample[i]:0xeeee))return false;
  if(ok&&(static_cast<int16_t>(v[3])!=-12||static_cast<int16_t>(v[6])!=-7||v[7]!=0xffff))return false;
 }
 return true;
}
static_assert(cases(),"exact closed standard registers; any failure leaves no partial published snapshot");
''')

    def test_actual_hal_method_compile(self):
        self.compile(r'''
#include <cstdint>
#include <cstddef>
#include <cstdio>
namespace std{struct mutex{};struct recursive_mutex{};template<class T>struct lock_guard{lock_guard(T&){}};}
std::mutex battery_mutex;std::recursive_mutex gauge_transaction_mutex;
bool gauge;bool gauge_word(uint8_t,uint16_t&);
struct Hal{bool gaugeChargeAudit(char*,size_t);};
'''+self.helper+self.method)

    def test_readonly_command_and_flag_definitions(self):
        registers=self.helper.split('constexpr uint8_t registers[]={',1)[1].split('};',1)[0]
        self.assertEqual([int(x,16) for x in re.findall(r'0x[0-9a-fA-F]+',registers)],
                         [0x06,0x08,0x0A,0x0C,0x10,0x12,0x14,0x18,0x2C,0x30,0x32,0x3A,0x3C])
        for forbidden in ('gauge_mac','gauge_control','gauge_access','gauge_write','nvs_',
                          'sample_battery','battery_telemetry','gauge_profile'):
            self.assertNotIn(forbidden,self.helper+self.method)
        self.assertLess(self.method.index('transaction(gauge_transaction_mutex)'),self.method.index('battery(battery_mutex)'))
        self.assertIn('gauge&&gauge_charge_read(gauge_word,v)',self.method)
        self.assertIn('valid=0 readonly=1 reason=standard_read_failed_or_unavailable',self.method)
        for name in ('charge_voltage_mv','charge_current_ma','time_to_full_min','temperature_dK','fc','tca','ocvcomp','ocvfail','ocvgd'):
            self.assertIn(name+'=',self.method)
        # TI Table 2-6: FC9/TCA6, not OCVCOMP14; retain raw BatteryStatus too.
        self.assertIn('(v[2]>>9)&1U,(v[2]>>6)&1U,(v[2]>>14)&1U,(v[2]>>13)&1U,(v[2]>>5)&1U',self.method)
        self.assertEqual([(0x5028>>bit)&1 for bit in (9,6,14,13,5)],[0,0,1,0,1])
        serial=(ROOT/'main/debug/serial_debug.cpp').read_text()
        command=serial.split('if (std::strcmp(command, "gauge-charge") == 0)',1)[1].split('if (std::strcmp(command, "gauge-profile") == 0)',1)[0]
        self.assertIn('::strtok_r(nullptr, " \\t", &save)',command)
        self.assertIn('expected=no_arguments readonly=1',command)
        self.assertIn('GetHAL().gaugeChargeAudit',command)
        self.assertIn('"gauge-charge"',(ROOT/'main/debug/serial_debug_transport.h').read_text())
        self.assertIn('DBG HELP gauge-charge no_arguments=1 readonly=1',serial)
        header=(ROOT/'main/hal/hal.h').read_text()
        self.assertEqual(header.count('bool gaugeChargeAudit('),2)

if __name__=='__main__':unittest.main()
