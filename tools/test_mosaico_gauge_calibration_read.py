"""Read-only calibration and actual HAL boundary tests; no device or SDK."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPILERS = list(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
SOURCE = (ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()

class CalibrationTests(unittest.TestCase):
    def compile(self, code):
        self.assertTrue(COMPILERS, 'configured compiler required')
        with tempfile.TemporaryDirectory() as td:
            source = Path(td)/'calibration.cpp'
            source.write_text(code)
            result = subprocess.run([str(COMPILERS[0]), '-std=c++20', '-Wall', '-Wextra', '-Werror', '-fsyntax-only', '-I', str(ROOT/'main/hal/mosaico'), str(source)], capture_output=True, text=True)
            log = ROOT/'.artifacts/mosaico'/f'{self._testMethodName}.log'
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(result.stdout+result.stderr)
            self.assertEqual(result.returncode, 0, str(log))

    def test_closed_width_endian_stability_and_publish(self):
        self.compile(r'''
#include "mosaico_gauge_calibration_read_model.h"
using namespace mosaico_calibration;
constexpr bool cases(){
 constexpr uint16_t addresses[]={0x9180,0x9184,0x9188,0x91b4,0x91dd,0x91de,0x91df};
 constexpr uint8_t widths[]={2,4,4,1,1,1,1};
 if(fieldCount!=7)return false;
 for(size_t i=0;i<7;++i)if(uint16_t(fields[i].selector)!=addresses[i]||fields[i].width!=widths[i]||!allowed(fields[i].selector))return false;
 for(unsigned selector=0;selector<65536;++selector){bool expected=false;
  for(auto address:addresses)expected|=selector==address;
  if(allowed(Selector(selector))!=expected)return false;
 }
 for(int fail=-1;fail<14;++fail){int calls=0;Values out;out.readable=7;
  bool ok=readStable([&](Selector selector,uint8_t* b)constexpr{
   int call=calls++;if(selector!=fields[call/2].selector||call==fail)return false;
   b[2]=0xff;b[3]=0x80;b[4]=0x12;b[5]=0x34;return true;
  },out);
  if(ok!=(fail==-1)||out.readable!=(ok?7:0))return false;
  if(ok&&(calls!=14||rawWord(out,0)!=0xff80||decoded(out,0)!=-128||decoded(out,3)!=-1||decoded(out,4)!=255||rawWord(out,1)!=0xff801234||rawWord(out,2)!=0xff801234))return false;
  Values cache{};publish(true,out,cache);if(cache.readable!=out.readable)return false;
  publish(false,out,cache);if(cache.readable||rawWord(cache,0))return false;
 }
 // Every byte, including byte 3/4 of opaque F4, participates in fresh comparison.
 for(size_t field=0;field<7;++field)for(unsigned byte=0;byte<widths[field];++byte){
  int calls=0;Values out;out.readable=7;
  if(readStable([&](Selector,uint8_t* b)constexpr{
   int call=calls++;for(unsigned j=0;j<4;++j)b[2+j]=0x5a;
   if(size_t(call/2)==field&&call%2)b[2+byte]^=1;
   return true;
  },out)||out.readable||rawWord(out,0))return false;
 }
 return true;
}
static_assert(cases(),"exact seven selectors widths BE signed integers opaque F4 all-byte fresh checks failure clears publication");
''')

    def test_actual_authenticated_reader_closed_no_parameter_writes(self):
        reader = 'constexpr bool gauge_calibration_read('+SOURCE.split('bool gauge_calibration_read(',1)[1].split('// Threshold-only typed MAC',1)[0]
        model = (ROOT/'main/hal/mosaico/mosaico_gauge_model.h').read_text()
        auth = ('inline uint8_t macChecksum('+model.split('inline uint8_t macChecksum(',1)[1].split('inline bool validJournal(',1)[0]).replace('inline ', 'constexpr ')
        self.assertEqual(reader.count('i2c_master_transmit('), 1)
        self.assertNotIn('0x60', reader)
        self.assertNotIn('0x61', reader)
        self.compile('#include "mosaico_gauge_calibration_read_model.h"\nnamespace mosaico_gauge{'+auth+'}\n'+r'''
constexpr int ESP_OK=0;
struct Fake{
 bool gauge=true,wrong=false;int mode=0,writes=0,reads=0;uint8_t response[36]{};
 constexpr void gauge_feed(){}
 constexpr void gauge_delay_ms(uint32_t ms){if(ms!=20)wrong=true;}
 constexpr int i2c_master_transmit(bool,const uint8_t* p,size_t n,int timeout){
  ++writes;if(n!=3||p[0]!=0x3e||timeout!=20)wrong=true;
  response[0]=p[1];response[1]=p[2];for(unsigned i=2;i<34;++i)response[i]=i;
  response[35]=36;response[34]=mosaico_gauge::macChecksum(response,34);
  if(mode==0)response[0]=response[1]=0xff;
  if(mode==1)++response[34];
  if(mode==2)response[35]=6;
  if(mode==3)++response[0];
  return mode==4?2:0;
 }
 constexpr bool gauge_read(uint8_t reg,uint8_t* out,size_t n){
  ++reads;if(n!=2)wrong=true;if(mode==6&&reads==10)return false;
  for(unsigned i=0;i<n;++i)out[i]=response[reg - 0x3e + i];
  return true;
 }
'''+reader+r'''
};
constexpr bool cases(){
 for(auto field:mosaico_calibration::fields)for(int mode=0;mode<=6;++mode){
  Fake f;f.mode=mode;uint8_t block[36]{};bool ok=f.gauge_calibration_read(field.selector,block);
  if(ok!=(mode==0||mode==5)||f.wrong||f.writes!=1)return false;
  if(ok&&(f.reads!=18||block[2]!=2||block[5]!=5))return false;
 }
 constexpr uint16_t deniedSelectors[]={0,0x929f,0x9181,0x91e0};
 Fake denied;uint8_t block[36]{};
 for(uint16_t selector:deniedSelectors)
  if(denied.gauge_calibration_read(mosaico_calibration::Selector(selector),block)||denied.writes||denied.reads)return false;
 return true;
}
static_assert(cases(),"actual full36 ACK length checksum echo FF quirk transport failure closed reader no writes");
''')

    def test_actual_success_only_publication_and_unchanged_journal_crc(self):
        backend = SOURCE.split('struct GaugeThresholdBackend {',1)[1].split('\n} // namespace',1)[0]
        read = backend.split('bool readThresholds()',1)[1].split('uint32_t snapshotCrc()',1)[0]
        self.assertLess(read.index('mosaico_threshold::readStable'), read.index('mosaico_calibration::readStable'))
        self.assertIn('uint32_t snapshotCrc()const{return mosaico_threshold::valuesCrc(values);}', backend)
        audit = SOURCE.split('bool Hal::gaugeThresholdAudit(',1)[1].split('bool Hal::gaugeThresholdAuditField(',1)[0]
        self.assertIn('gauge_calibration_cached={};', audit)
        self.assertIn('mosaico_calibration::publish(result.success,backend.calibration,gauge_calibration_cached);', audit)
        self.assertLess(audit.index('result=coordinator.run()'), audit.index('mosaico_calibration::publish'))
        getter = SOURCE.split('bool Hal::gaugeThresholdAuditField(',1)[1].split('// Closed standard-command snapshot',1)[0]
        for forbidden in ('gauge_read(', 'gauge_calibration_read(', 'gauge_word(', 'nvs_', 'gauge_access_word('):
            self.assertNotIn(forbidden, getter)
        for required in ('raw=%0*lx', 'width=%u', 'ram_only=1', 'journal_crc_fields=59', '"opaque"'):
            self.assertIn(required, getter)
        # Longest published line must fit the parent's 192-byte reply buffer.
        line = 'cached=1 generation=4294967295 index=60 count=66 address=9184 name=CCGain type=F4 width=4 raw=ffffffff decoded=opaque ram_only=1 journal_crc_fields=59'
        self.assertLess(len(line), 192)

if __name__ == '__main__':
    unittest.main()
