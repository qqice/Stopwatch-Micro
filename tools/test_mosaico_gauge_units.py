"""Compile pure constexpr unit-model tests, with no SDK build or device access.

Set CXX to a native C++ compiler or use the installed Espressif cross compiler.
The latter validates static assertions without executing target binaries.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HEADER = ROOT / 'main/hal/mosaico/mosaico_gauge_units_model.h'


def compiler():
    if os.environ.get('CXX'):
        return os.environ['CXX']
    if shutil.which('g++'):
        return shutil.which('g++')
    installed = sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob(
        '*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
    return str(installed[-1]) if installed else None


class UnitsTests(unittest.TestCase):
    def compile(self, code):
        cxx = compiler()
        self.assertIsNotNone(cxx, 'C++ compiler required (set CXX)')
        with tempfile.TemporaryDirectory(prefix='.gauge-units-', dir=ROOT) as td:
            source = Path(td) / 'units.cpp'
            source.write_text('#include "mosaico_gauge_units_model.h"\n'
                              'using namespace mosaico_gauge_units;\n' + code)
            result = subprocess.run([cxx, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                                     '-fconstexpr-ops-limit=100000000', '-fsyntax-only',
                                     '-I', str(HEADER.parent), str(source)],
                                    capture_output=True, text=True)
        log = ROOT / 'artifacts/gauge-units-tests' / (self._testMethodName + '.log')
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(result.stdout + result.stderr)
        self.assertEqual(result.returncode, 0, str(log))

    def test_legacy_identity_and_exact_scaled_micro_units(self):
        self.compile(r'''
constexpr bool legacy() {
 for(int64_t n=-32768;n<=32767;++n){int32_t out=0;
  if(!currentMicroamp(n,1,out)||out!=n*1000||out/1000!=n)return false;}
 for(int64_t n=0;n<=65535;++n){uint32_t out=0;
  if(!capacityMicroampHour(n,1,out)||out!=n*1000||out/1000!=n)return false;}
 return true;
}
static_assert(legacy(),"factor1 exact identity for every register value");
constexpr bool scaled() {
 constexpr int values[]={-32768,-32767,-13,-1,0,1,13,32766,32767};
 for(int factor: {1,2,4})for(int n:values){int32_t out=0;
  if(!currentMicroamp(n,factor,out)||int64_t(out)*factor!=int64_t(n)*1000)return false;
  if(n<0&&out>=0)return false;}
 for(int factor: {1,2,4})for(int64_t n:{0,1,65,65534,65535}){uint32_t out=0;
  if(!capacityMicroampHour(n,factor,out)||int64_t(out)*factor!=n*1000)return false;}
 int32_t out=0;return currentMicroamp(-1,4,out)&&out==-250;
}
static_assert(scaled(),"signed sub-mA precision and extreme values");
'''.replace('constexpr bool legacy()', '#include <initializer_list>\nconstexpr bool legacy()'))

    def test_threshold_cross_multiplication_and_capacity_ratio(self):
        self.compile(r'''
#include <initializer_list>
constexpr bool comparisons(){
 for(int factor:{1,2,4}){
  for(int n:{-32768,-13,-12,-7,-1,0,1,3,6,7,12,13,32767})
   for(int threshold:{-2147483647-1,-3,0,3,2147483647}){
    bool out=false;int32_t ua=0;
    if(!currentAboveMilliamp(n,factor,threshold,out)||!currentMicroamp(n,factor,ua))return false;
    if(out!=(int64_t(ua)>int64_t(threshold)*1000))return false;
   }
  bool out=true;
  if(!currentAboveMilliamp(3*factor,factor,3,out)||out)return false;
  if(!currentAboveMilliamp(3*factor+1,factor,3,out)||!out)return false;
  if(!currentAboveMilliamp(-3*factor-1,factor,-3,out)||out)return false;
  for(int rm:{0,1,64,65,65535}){uint32_t r=0,c=0;
   if(!capacityMicroampHour(rm,factor,r)||!capacityMicroampHour(65535,factor,c))return false;
   if(uint64_t(r)*65535!=uint64_t(c)*rm)return false;
  }
 }
 return true;
}
static_assert(comparisons(),"strict signed physical thresholds and capacity ratios");
''')

    def test_invalid_factor_ranges_and_failure_atomicity(self):
        self.compile(r'''
#include <initializer_list>
constexpr bool invalid(){
 for(int64_t factor:{-1LL,0LL,3LL,8LL,4294967297LL,9223372036854775807LL}){
  int32_t i=71;uint32_t c=72;bool b=true;
  if(currentMicroamp(1,factor,i)||i!=71||capacityMicroampHour(1,factor,c)||c!=72||
     currentAboveMilliamp(1,factor,3,b)||!b)return false;
 }
 for(int64_t raw:{-9223372036854775807LL-1, -32769LL,32768LL,9223372036854775807LL}){
  int32_t i=71;bool b=true;
  if(currentMicroamp(raw,4,i)||i!=71||currentAboveMilliamp(raw,4,0,b)||!b)return false;
 }
 for(int64_t raw:{-1LL,65536LL,9223372036854775807LL}){uint32_t c=72;
  if(capacityMicroampHour(raw,4,c)||c!=72)return false;}
 for(int64_t t:{-9223372036854775807LL-1,-2147483649LL,2147483648LL,9223372036854775807LL}){
  bool b=true;if(currentAboveMilliamp(1,4,t,b)||!b)return false;}
 return !readyForHardware;
}
static_assert(invalid(),"unknown factor or overflow rejected without modifying output");
''')

    def test_relative_native_counter_wrap_and_context(self):
        self.compile(r'''
#include <initializer_list>
constexpr bool counters(){
 for(int factor:{1,2,4}){
  constexpr uint16_t starts[]={65535,0,65000,500,0,32767,32769};
  constexpr uint16_t ends[]={0,65535,500,65000,0,0,0};
  constexpr int deltas[]={1,-1,1036,-1036,0,-32767,32767};
  for(unsigned i=0;i<7;++i){CounterSample a{starts[i],1,factor,1},b{ends[i],1,factor,2};
   RelativeNativeCounterDelta out{};
   if(!relativeNativeCounterDelta(a,b,true,true,out)||out.previousRaw!=starts[i]||
      out.currentRaw!=ends[i]||out.nativeDelta!=deltas[i]||
      int64_t(out.scaledMilliunits)*factor!=int64_t(deltas[i])*1000)return false;
  }
 }
 CounterSample a{0,1,4,1},b{1,1,4,2};
 for(int mode=0;mode<9;++mode){auto bad=b;auto prior=a;
  if(mode==0)bad.boot=2;
  if(mode==1)bad.factor=2;
  if(mode==2)bad.factor=prior.factor=3;
  if(mode==3)bad.monotonicTime=1;
  if(mode==4)bad.monotonicTime=0;
  if(mode==5)bad.raw=32768;
  if(mode==8){prior.raw=32768;bad.raw=0;}
  RelativeNativeCounterDelta out{2,3,4,5,6};
  if(relativeNativeCounterDelta(prior,bad,mode!=6,mode!=7,out)||
     out.previousRaw!=2||out.currentRaw!=3||out.moduloDelta!=4||
     out.nativeDelta!=5||out.scaledMilliunits!=6)return false;
 }
 return a.raw==0&&b.raw==1;
}
static_assert(counters(),"modulo before signed interpretation and scaling; context fail closed");
''')

    def test_not_integrated_into_production(self):
        for source in (ROOT / 'main').rglob('*'):
            if source.suffix in ('.h', '.hpp', '.cpp', '.c') and source != HEADER:
                self.assertNotIn('mosaico_gauge_units_model.h', source.read_text(encoding='utf-8'), str(source))


if __name__ == '__main__':
    unittest.main()
