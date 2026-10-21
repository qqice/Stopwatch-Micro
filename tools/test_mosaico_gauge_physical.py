"""Compile constexpr physical-snapshot tests; no device access or shared build.

Defaults to the installed Mosaico ESP32-S31 RISC-V target compiler. Set CXX
to also exercise a native compiler. Logs include the exact compiler command.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HEADER = ROOT / 'main/hal/mosaico/mosaico_gauge_physical_model.h'


class PhysicalTests(unittest.TestCase):
    def test_constexpr_snapshot_contract(self):
        installed = sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob(
            '*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
        cxx = os.environ.get('CXX') or (str(installed[-1]) if installed else None)
        self.assertIsNotNone(cxx, 'Mosaico target compiler required (or set CXX)')
        code = r'''
#include "mosaico_gauge_physical_model.h"
#include <initializer_list>
using namespace mosaico_gauge_physical;
struct Native { int soc; int voltage; int temperature; unsigned flags; int rawCC; };
constexpr Native native{63, 3791, -21, 0x9123, 65535};
constexpr UnitContext context(int factor) {
 return {factor, 9, 13, factor==1?Phase::Original:Phase::Applied, true};
}
constexpr SampleMeta metadata(int factor) { return {factor,9,13,true}; }
constexpr bool good() {
 for(int factor:{1,2,4}) {
  auto c=context(factor); auto m=metadata(factor);
  for(int current:{-32768,-21,-1,0,1,6,7,10,11,12,13,20,21,32767}) {
   NativeQuantities q{current,-1,1,65535,65};
   auto s=makeSnapshot(native,q,m,c);
   if(!s.valid || int64_t(s.currentMicroamp)*factor!=int64_t(current)*1000 ||
      s.averageCurrentMicroamp!=-1000/factor ||
      s.remainingCapacityMicroampHour!=1000U/factor ||
      s.fullChargeCapacityMicroampHour!=65535000U/factor ||
      s.designCapacityMicroampHour!=65000U/factor ||
      chargingAbove3Milliamp(s)!=(current>3*factor) ||
      traceAbove5Milliamp(s)!=(current>5*factor) ||
      s.native.soc!=native.soc || s.native.voltage!=native.voltage ||
      s.native.temperature!=native.temperature || s.native.flags!=native.flags ||
      s.native.rawCC!=native.rawCC) return false;
  }
 }
 auto half=makeSnapshot(native,NativeQuantities{7,-1,1,130,130},metadata(2),context(2));
 return half.currentMicroamp==3500 && half.averageCurrentMicroamp==-500 &&
        chargingAbove3Milliamp(half) && !traceAbove5Milliamp(half);
}
static_assert(good(), "exact signed micro units, strict thresholds, native identity");

constexpr bool rejected() {
 for(int mode=0;mode<20;++mode) {
  auto c=context(2);auto m=metadata(2);NativeQuantities q{7,-1,65,130,130};
  if(mode==0)c.phase=Phase::Unknown;
  if(mode==1)c.phase=Phase::Pending;
  if(mode==2)c.phase=Phase::Restoring;
  if(mode==3)c.phase=Phase::Original;
  if(mode==4)c.verified=false;
  if(mode==5)m.valid=false;
  if(mode==6)m.factor=4;
  if(mode==7)++m.unitEpoch;
  if(mode==8)++m.generation;
  if(mode==9)c.factor=m.factor=0;
  if(mode==10)c.factor=m.factor=3;
  if(mode==11)c.factor=m.factor=9223372036854775807LL;
  if(mode==12)c.phase=static_cast<Phase>(255);
  if(mode==13){c=context(1);m=metadata(1);c.phase=Phase::Applied;}
  if(mode==14)q.currentMilliamp=32768;
  if(mode==15)q.averageCurrentMilliamp=-32769;
  if(mode==16)q.remainingCapacityMilliampHour=-1;
  if(mode==17)q.fullChargeCapacityMilliampHour=65536;
  if(mode==18)q.designCapacityMilliampHour=9223372036854775807LL;
  if(mode==19)q.currentMilliamp=-9223372036854775807LL-1;
  auto s=makeSnapshot(native,q,m,c);
  if(s.valid || s.currentMicroamp || s.averageCurrentMicroamp ||
     s.remainingCapacityMicroampHour || s.fullChargeCapacityMicroampHour ||
     s.designCapacityMicroampHour || chargingAbove3Milliamp(s) ||
     traceAbove5Milliamp(s) || s.native.rawCC!=65535) return false;
 }
 // No missing context defaults. A historical scale-1 sample does NOT become
 // physical again after 1 -> 2 -> 1 (epoch ABA), nor across a new generation.
 auto old=metadata(1);auto now=context(1);now.unitEpoch+=2;
 NativeQuantities q{7,-1,65,65,65};
 if(makeSnapshot(native,q,old,now).valid) return false;
 now=context(1);++now.generation;
 return !makeSnapshot(native,q,old,now).valid &&
        !makeSnapshot(native,q,SampleMeta{},UnitContext{}).valid;
}
static_assert(rejected(), "fail closed on transitions, stale context, overflow and missing metadata");
'''
        with tempfile.TemporaryDirectory(prefix='.gauge-physical-', dir=ROOT) as td:
            source = Path(td) / 'physical.cpp'
            obj = Path(td) / 'physical.o'
            source.write_text(code)
            command = [cxx, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                       '-I', str(HEADER.parent), '-c', str(source), '-o', str(obj)]
            result = subprocess.run(command, capture_output=True, text=True)
            log = ROOT / 'artifacts/gauge-physical-tests/compile.log'
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(subprocess.list2cmdline(command) + '\n' +
                           result.stdout + result.stderr +
                           f'\nexit_code={result.returncode}\n')
            self.assertEqual(result.returncode, 0, str(log))
            self.assertTrue(obj.is_file())


if __name__ == '__main__':
    unittest.main()
