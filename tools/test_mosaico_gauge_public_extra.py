"""Compile-time tests of production public complement and actual reader; no SDK/device."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()
COMPILERS = list(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))

class PublicExtraTests(unittest.TestCase):
    def compile(self, code):
        self.assertTrue(COMPILERS)
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/'public_extra.cpp'
            source.write_text(code)
            result=subprocess.run([str(COMPILERS[0]),'-std=c++20','-Wall','-Wextra','-Werror','-fconstexpr-ops-limit=100000000','-fsyntax-only','-I',str(ROOT/'main/hal/mosaico'),str(source)],capture_output=True,text=True)
            log=ROOT/'artifacts/gauge-public-extra-tests'/f'{self._testMethodName}.log'
            log.parent.mkdir(parents=True,exist_ok=True)
            log.write_text(result.stdout+result.stderr)
            self.assertEqual(result.returncode,0,str(log))

    def test_exact_metadata_complement_widths_and_failure_atomicity(self):
        self.compile(r'''
#include "mosaico_gauge_public_extra_read_model.h"
#include "mosaico_gauge_calibration_read_model.h"
#include "mosaico_threshold_audit_model.h"
using namespace mosaico_public_extra;
constexpr bool coverage(){
 constexpr uint16_t expected[]={0x9182, 0x91b5, 0x91b6, 0x91b7, 0x91b8, 0x91ba, 0x91bc, 0x91be, 0x91c0, 0x91c2, 0x91c4, 0x91c6, 0x91c8, 0x91ca, 0x91cc, 0x91ce, 0x91d0, 0x91d2, 0x91d4, 0x91d6, 0x91d8, 0x91da, 0x91db, 0x91f5, 0x91f7, 0x91f9, 0x9206, 0x9208, 0x920b, 0x920c, 0x920d, 0x920e, 0x9210, 0x9212, 0x9219, 0x921a, 0x921c, 0x921e, 0x921f, 0x9232, 0x9234, 0x9235, 0x9237, 0x9239, 0x923a, 0x923c, 0x925b, 0x9264, 0x927d, 0x9282, 0x9284, 0x9286, 0x9287, 0x9288, 0x928a, 0x928c, 0x928d, 0x928e, 0x9290, 0x9292, 0x9293, 0x9294, 0x9296, 0x9298, 0x9299};
 constexpr uint8_t widths[]={2, 1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 1, 1, 1, 2, 2, 2, 1, 2, 2, 1, 1, 2, 1, 2, 2, 1, 2, 1, 1, 2, 1, 2, 2, 1, 1, 2, 2, 1, 1, 2, 2, 1, 1, 2, 2, 1, 1};
 constexpr Type types[]={Type::I2, Type::I1, Type::I1, Type::I1, Type::I2, Type::I2, Type::I2, Type::I2, Type::I2, Type::I2, Type::I2, Type::I2, Type::I2, Type::I2, Type::I2, Type::I2, Type::I2, Type::I2, Type::I2, Type::I2, Type::I2, Type::U1, Type::I2, Type::I2, Type::I2, Type::I2, Type::H2, Type::H2, Type::U1, Type::U1, Type::H1, Type::I2, Type::I2, Type::U2, Type::U1, Type::I2, Type::I2, Type::U1, Type::U1, Type::I2, Type::U1, Type::I2, Type::I2, Type::U1, Type::I2, Type::I1, Type::U1, Type::I2, Type::U1, Type::I2, Type::I2, Type::U1, Type::U1, Type::I2, Type::I2, Type::U1, Type::U1, Type::I2, Type::I2, Type::U1, Type::U1, Type::I2, Type::I2, Type::U1, Type::U1};
 constexpr uint16_t public131[]={0x9180, 0x9182, 0x9184, 0x9188, 0x91b4, 0x91b5, 0x91b6, 0x91b7, 0x91b8, 0x91ba, 0x91bc, 0x91be, 0x91c0, 0x91c2, 0x91c4, 0x91c6, 0x91c8, 0x91ca, 0x91cc, 0x91ce, 0x91d0, 0x91d2, 0x91d4, 0x91d6, 0x91d8, 0x91da, 0x91db, 0x91dd, 0x91de, 0x91df, 0x91f5, 0x91f7, 0x91f9, 0x91fb, 0x91fd, 0x9201, 0x9206, 0x9208, 0x920b, 0x920c, 0x920d, 0x920e, 0x9210, 0x9212, 0x9217, 0x9219, 0x921a, 0x921c, 0x921e, 0x921f, 0x9228, 0x922a, 0x922c, 0x922e, 0x9230, 0x9231, 0x9232, 0x9234, 0x9235, 0x9237, 0x9239, 0x923a, 0x923c, 0x9240, 0x9242, 0x9243, 0x9251, 0x925b, 0x9264, 0x9268, 0x9269, 0x926b, 0x926d, 0x926f, 0x9270, 0x9271, 0x9272, 0x9274, 0x9276, 0x927b, 0x927c, 0x927d, 0x927f, 0x9281, 0x9282, 0x9284, 0x9286, 0x9287, 0x9288, 0x928a, 0x928c, 0x928d, 0x928e, 0x9290, 0x9292, 0x9293, 0x9294, 0x9296, 0x9298, 0x9299, 0x929a, 0x929b, 0x929d, 0x929f, 0x92a3, 0x92a5, 0x92a7, 0x92a9, 0x92ab, 0x92ad, 0x92af, 0x92b1, 0x92b2, 0x92b3, 0x92b4, 0x92b6, 0x92b7, 0x92b9, 0x92ba, 0x92bc, 0x92bd, 0x92bf, 0x92c1, 0x92c3, 0x92c5, 0x92c7, 0x92c9, 0x92cb, 0x92cd, 0x92cf, 0x92d1};
 if(fieldCount!=65||mosaico_threshold::fieldCount!=59||mosaico_threshold::legacyFieldCount!=26||mosaico_calibration::fieldCount!=7||sizeof(public131)/2!=131)return false;
 for(size_t i=0;i<fieldCount;++i){
  if(uint16_t(fields[i].selector)!=expected[i]||fields[i].width!=widths[i]||fields[i].type!=types[i])return false;
 }
 for(unsigned address=0x9000;address<0x9300;++address){
  bool expectedAllowed=false;for(auto v:expected)expectedAllowed|=address==v;
  if(allowed(Selector(address))!=expectedAllowed)return false;
 }
 for(auto address:public131){unsigned count=0;
  for(auto f:fields)count+=uint16_t(f.selector)==address;
  for(auto f:mosaico_threshold::fields)count+=uint16_t(f.selector)==address;
  for(auto f:mosaico_calibration::fields)count+=uint16_t(f.selector)==address;
  if(count!=1)return false;
 }
 return sizeof(Values)<160;
}
static_assert(coverage(),"exact metadata003 131 disjoint union with unchanged 59+7 indexes, widths and types");
constexpr bool stable(){
 for(int fail=-1;fail<130;++fail){int calls=0;Values out{};
  bool ok=readStable([&](Selector s,uint8_t* b)constexpr{
   int n=calls++;if(s!=fields[n/2].selector||n==fail)return false;
   b[2]=0x80;b[3]=0xff;return true;
  },out);
  if(ok!=(fail==-1)||out.complete!=ok)return false;
  if(ok){if(out.readable!=65||out.error!=Error::None||calls!=130)return false;
   for(size_t i=0;i<65;++i)if(out.raw[i]!=(fields[i].width==1?0x80:0x80ff))return false;
  }else {if(out.readable!=fail/2||out.error!=Error::Transport)return false;for(auto raw:out.raw)if(raw)return false;}
  Values cache{};cache.complete=true;
  publish(false,true,out,cache);if(cache.complete||cache.readable)return false;
  publish(true,false,out,cache);if(cache.complete||cache.readable)return false;
  publish(true,true,out,cache);if(cache.complete!=out.complete||cache.error!=out.error)return false;
 }
 for(size_t field=0;field<65;++field)for(unsigned byte=0;byte<fields[field].width;++byte){int calls=0;Values out{};
  if(readStable([&](Selector,uint8_t* b)constexpr{int n=calls++;b[2]=b[3]=0x5a;
   if(size_t(n/2)==field&&n%2)b[2+byte]^=1;
   return true;
  },out)||out.complete||out.readable!=field||out.error!=Error::Torn)return false;
  for(auto raw:out.raw)if(raw)return false;
 }
 // Adjacent byte is NOT part of a one-byte field.
 int calls=0;Values out{};
 return readStable([&](Selector,uint8_t* b)constexpr{int n=calls++;b[2]=0x81;b[3]=fields[n/2].width==1?uint8_t(n):0x72;return true;},out);
}
static_assert(stable(),"every transport failure and byte tear clears raw, incomplete never publishes as complete");
''')

    def test_actual_exact_echo_length_checksum_closed_reader(self):
        reader='constexpr bool gauge_public_extra_read('+SOURCE.split('bool gauge_public_extra_read(',1)[1].split('// Calibration-only typed MAC',1)[0]
        self.assertEqual(reader.count('i2c_master_transmit('),1)
        self.assertNotIn('0x60',reader)
        self.assertNotIn('0x61',reader)
        self.compile('#include "mosaico_gauge_public_extra_read_model.h"\n'+r'''
namespace mosaico_gauge {constexpr uint8_t macChecksum(const uint8_t* b,size_t n){unsigned sum=0;for(size_t i=0;i<n;++i)sum+=b[i];return uint8_t(255-sum);}}
constexpr int ESP_OK=0;
struct Fake {
 bool gauge=true,wrong=false;int mode=0,writes=0,reads=0;uint8_t response[36]{};
 constexpr void gauge_feed(){}
 constexpr void gauge_delay_ms(uint32_t ms){if(ms!=20)wrong=true;}
 constexpr int i2c_master_transmit(bool,const uint8_t* p,size_t n,int timeout){
  ++writes;if(n!=3||p[0]!=0x3e||timeout!=20)wrong=true;
  response[0]=p[1];response[1]=p[2];for(unsigned i=2;i<34;++i)response[i]=uint8_t(i);
  response[35]=36;
  if(mode==1)response[0]=response[1]=0xff;
  if(mode==2)++response[0];
  if(mode==3)response[35]=6;
  response[34]=mosaico_gauge::macChecksum(response,34);
  if(mode==4)++response[34];
  return mode==5?2:0;
 }
 constexpr bool gauge_read(uint8_t reg,uint8_t* out,size_t n){
  ++reads;if(n!=2)wrong=true;if(mode==6&&reads==10)return false;
  for(unsigned i=0;i<n;++i){out[i]=response[reg - 0x3e + i];}
  return true;
 }
'''+reader+r'''
};
constexpr bool cases(){
 for(auto field:mosaico_public_extra::fields)for(int mode=0;mode<7;++mode){Fake f;f.mode=mode;uint8_t b[36]{};
  if(f.gauge_public_extra_read(field.selector,b)!=(mode==0)||f.wrong||f.writes!=1)return false;
  if(mode==0&&(f.reads!=18||b[2]!=2||b[3]!=3))return false;
 }
 constexpr uint16_t denied[]={0,0x35,0x9180,0x9184,0x929b,0x929d,0x929f,0x92fa,0xffff};
 for(auto address:denied){Fake f;uint8_t b[36]{};if(f.gauge_public_extra_read(mosaico_public_extra::Selector(address),b)||f.writes||f.reads)return false;}
 return true;
}
static_assert(cases(),"actual reader exact echo including FF rejection, length/checksum transport and closed selectors");
''')

    def test_production_integration_publication_stack_and_commands(self):
        backend=SOURCE.split('struct GaugeThresholdBackend {',1)[1].split('} // namespace',1)[0]
        read=backend.split('bool readThresholds()',1)[1].split('uint32_t snapshotCrc()',1)[0]
        self.assertLess(read.index('mosaico_threshold::readStable'),read.index('mosaico_calibration::readStable'))
        self.assertLess(read.index('mosaico_calibration::readStable'),read.index('mosaico_public_extra::readStable'))
        self.assertIn('if(readPublicExtra)(void)',read)
        self.assertIn('uint32_t snapshotCrc()const{return mosaico_threshold::valuesCrc(values);}',backend)
        audit=SOURCE.split('bool Hal::gaugeThresholdAudit(',1)[1].split('bool Hal::gaugeThresholdAuditField(',1)[0]
        self.assertIn('backend.readPublicExtra=privileged',audit)
        self.assertIn('gauge_public_extra_cached={};gauge_public_extra_generation=0;',audit)
        self.assertIn('mosaico_public_extra::publish(result.success,result.closed,backend.publicExtra,gauge_public_extra_cached)',audit)
        self.assertLess(audit.index('coordinator.run()'),audit.index('mosaico_public_extra::publish'))
        self.assertIn('extra_readable=%u/65 extra_complete=%d extra_error=%s',audit)
        getter=SOURCE.split('bool Hal::gaugePublicExtraField(',1)[1].split('// Closed standard-command snapshot',1)[0]
        for forbidden in ('gauge_read(', 'gauge_public_extra_read(', 'gauge_word(', 'nvs_', 'gauge_access_word('):self.assertNotIn(forbidden,getter)
        for key in ('address=%04x','type=%s','width=%u','rawBE=%0*x','generation=%lu','readable=%u/65','complete=%d'):self.assertIn(key,getter)
        self.assertLess(len('cached=1 index=64 address=9299 type=H2 width=2 rawBE=ffff generation=4294967295 readable=65/65 complete=1 error=transport_auth ram_only=1'),192)
        serial=(ROOT/'main/debug/serial_debug.cpp').read_text()
        command=serial.split('if (std::strcmp(command,"gauge-public-extra-field")==0)',1)[1].split('if (std::strcmp(command,"gauge-profile-field")',1)[0]
        self.assertIn('parseUnsignedStrict(value,0,64,index)',command)
        self.assertIn('char details[192]',command)
        self.assertNotIn('_reply_uart',command)
        self.assertIn('(privileged&&_writer&&_writer->_reply_uart)',serial)
        self.assertIn('"gauge-public-extra-field"',(ROOT/'main/debug/serial_debug_transport.h').read_text())
        model=(ROOT/'main/hal/mosaico/mosaico_gauge_public_extra_read_model.h').read_text()
        self.assertNotIn('Values fresh',model)
        self.assertNotIn('[65][36]',model)
        coordinator=(ROOT/'main/hal/mosaico/mosaico_threshold_audit_model.h').read_text()
        self.assertIn('result.closed=b.close()',coordinator)

    def test_actual_cache_getter_generation_and_bounds(self):
        getter='constexpr bool field('+SOURCE.split('bool Hal::gaugePublicExtraField(',1)[1].split('// Closed standard-command snapshot',1)[0]
        getter='\n'.join(line for line in getter.splitlines() if 'std::lock_guard<' not in line).replace('std::snprintf','print')
        self.compile('#include "mosaico_gauge_public_extra_read_model.h"\n'+r'''
struct Fake {
 mosaico_public_extra::Values gauge_public_extra_cached{};uint32_t gauge_public_extra_generation=0;int prints=0,printed=100;
 template<class... Args>constexpr int print(char*,size_t,const char*,Args...){++prints;return printed;}
'''+getter+r'''
};
constexpr bool cases(){Fake f;char out[192]{};
 if(f.field(0,nullptr,192)||f.field(0,out,159)||f.prints)return false;
 if(f.field(0,out,192)||f.prints!=1)return false;
 f.gauge_public_extra_cached.complete=true;f.gauge_public_extra_cached.readable=65;
 if(f.field(0,out,192))return false;
 f.gauge_public_extra_generation=1;
 for(size_t i=0;i<65;++i)if(!f.field(i,out,192))return false;
 if(f.field(65,out,192)||f.field(size_t(-1),out,192))return false;
 f.gauge_public_extra_cached.readable=64;if(f.field(0,out,192))return false;
 f.gauge_public_extra_cached.readable=65;f.gauge_public_extra_cached.complete=false;if(f.field(0,out,192))return false;
 f.gauge_public_extra_cached.complete=true;f.printed=192;if(f.field(0,out,192))return false;
 return true;
}
static_assert(cases(),"actual getter all indexes bounds generation complete partial truncation, no device access");
''')

    def test_existing_1420_byte_summary_budget(self):
        import re
        fmt=re.search(r'"(cache_count=.*?)",',SOURCE).group(1)
        args=[59,4294967295,2,1,1,1,59,59,1,1,1,65535,7,'calibration_denied_auth_or_unstable',1,'not_loaded',255,255,4294967295,1,1,65535,65535,65535,65535,3,3,7,65,1,'transport_auth']
        line=fmt.replace('%lu','%u')%tuple(args)
        model=(ROOT/'main/hal/mosaico/mosaico_threshold_audit_model.h').read_text()
        names=re.findall(r'\{Selector::\w+,Type::\w+,"([^"]+)"\}',model)[:26]
        self.assertEqual(len(names),26)
        line+=''.join(f'{name}=-32768/ffff ' for name in names)
        self.assertLess(len(line)+1,1420)

if __name__=='__main__':unittest.main()
