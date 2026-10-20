"""Fixed selectors, exact lengths, failure isolation and Type13 packing; no device."""
from pathlib import Path
from test_mosaico_gauge_status import GaugeStatusTests
from test_mosaico_system_logs_export import page
import mosaico_system_logs as logs
import struct
ROOT=Path(__file__).resolve().parents[1]

class EngineTests(GaugeStatusTests):
    # Inherited old tests provide regressions for the untouched 48B snapshot.
    def test_engine_faults(self):
        self.compile(r'''
#include "mosaico_gauge_engine_model.h"
using namespace mosaico_gauge_engine;
struct Bus {
 int fault=0,target=0,block=0,reads=0,delays=0;bool wrong=false;uint8_t selector=0;
 constexpr bool transmit(const uint8_t* p,size_t n){
  ++block;selector=block==1?2:block==2?0x73:0x76;
  if(n!=3||p[0]!=0||p[1]!=selector||p[2]!=0)wrong=true;
  return !(block==target&&fault==1);
 }
 constexpr void delayMs(uint32_t n){if(n!=20)wrong=true;++delays;}
 constexpr bool read(uint8_t reg,uint8_t* p,size_t n){
  ++reads;const bool fail=block==target;
  if(reg==0x3e){
   if(n!=2)wrong=true;
   p[0]=(fail&&fault==4)?uint8_t(selector+1):selector;p[1]=(fail&&fault==5)?1:0;
   return !(fail&&fault==2);
  }
  if(reg!=0x40||n!=size_t(block==1?11:block==2?23:17))wrong=true;
  for(size_t i=0;i<n;++i)p[i]=uint8_t(i+0x80);
  if(block==1){p[0]=2;p[1]=0x20;p[2]=0;p[3]=(fail&&fault==6)?4:3;p[4]=0;p[5]=4;}
  return !(fail&&fault==3); // modifies all bytes, reports partial read failure
 }
};
constexpr bool cases(){
 for(int target=1;target<=3;++target)for(int f=0;f<=6;++f){
  Bus bus;bus.target=target;bus.fault=f;Snapshot s{};
  const bool ok=read(bus,s);const bool fault=f>0&&(f!=6||target==1);
  if(ok==fault||bus.wrong||bus.block!=3||bus.delays!=(f==1?2:3)||bus.reads!=(f==1?4:6))return false;
  const unsigned valid=target==1?Identity:target==2?Cedv:Smooth;
  if(bool(s.validMask&valid)==(f>=1&&f<=5))return false;
  if(bool(s.validMask&DefinitionMatch)==(target==1&&f>0))return false;
  if(f==3){const uint8_t* p=target==1?s.rawIdentity:target==2?s.rawCedv:s.rawSmooth;size_t n=target==1?11:target==2?23:17;
   for(size_t i=0;i<n;++i)if(p[i])return false;
  }
  if(target!=2||f!=3){if(f!=1||target!=2)if(leWord(s.rawCedv,0)!=0x8180||leWord(s.rawCedv,16)!=0x9190)return false;}
 }
 Snapshot s{};Bus bus;if(!read(bus,s)||s.echo02!=2||s.echo73!=0x73||s.echo76!=0x76)return false;
 return beWord(s.rawIdentity,0)==0x220&&beWord(s.rawIdentity,2)==3&&beWord(s.rawIdentity,4)==4&&leWord(s.rawSmooth,14)==0x8f8e;
}
static_assert(cases());
''')

    def test_actual_type13_pack_and_boot_ack(self):
        source=(ROOT/'main/host/mosaico_system_log.cpp').read_text()
        pack='constexpr bool engine('+source.split('bool engine(',1)[1].split('void changed(',1)[0]
        self.compile(r'''
#include "mosaico_gauge_engine_model.h"
#include "../../host/mosaico_system_log_model.h"
#include "mosaico_gauge_status_model.h"
struct Hal{using GaugeEngineSnapshot=mosaico_gauge_engine::Snapshot;};
using namespace MosaicoSystemLog::Model;
struct Harness {
 Record captured{};bool critical=false;
 constexpr Record record(Type type,uint64_t ms){Record r;r.type=type;r.monotonicMs=ms;r.epoch=123;r.flags=1;return r;}
 constexpr bool enqueue(const Record& r,bool urgent){captured=r;critical=urgent;return true;}
'''+pack+r'''
};
constexpr bool cases(){
 for(uint8_t phase=1;phase<=3;++phase){Harness h;Hal::GaugeEngineSnapshot s{};s.phase=phase;s.outcome=2;s.sampledUs=12345;s.validMask=8191;
 for(size_t i=0;i<23;++i){s.rawCedv[i]=uint8_t(i+1);}
 for(size_t i=0;i<17;++i){s.rawSmooth[i]=uint8_t(i+24);}
 if(!h.engine(s)){return false;}
 const auto& r=h.captured;const auto b=encode(r);
 if(r.type!=Type::GaugeEngine||r.args[0]!=(8191U|(uint32_t(phase)<<16)|(2U<<24))||r.monotonicMs!=12)return false;
 for(size_t i=0;i<40;++i)if(b[24+i]!=i+1)return false;
 if(phase==3){if(r.flags!=1||r.epoch!=123||h.critical)return false;}else if(r.flags||r.epoch||!h.critical)return false;
 }
 BootCapture capture;Queue q;Hal::GaugeEngineSnapshot a{},b{};a.phase=1;a.sampledUs=1;b.phase=2;b.sampledUs=2;b.outcome=2;
 unsigned accepted=0;auto emit=[&](const Hal::GaugeEngineSnapshot&){Record r;if(!q.push(r))return false;++accepted;return true;};
 auto noOutcome=[](uint8_t,bool){return true;};Record filler;for(size_t i=0;i<128;++i)q.push(filler);
 if(capture.update(a,b,false,emit,noOutcome)||capture.beforeSent)return false;
 q.consume(1);if(capture.update(a,b,false,emit,noOutcome)||!capture.beforeSent||capture.afterSent||accepted!=1)return false;
 q.consume(1);if(!capture.update(a,b,false,emit,noOutcome)||!capture.complete||accepted!=2)return false;
 return capture.update(a,b,false,emit,noOutcome)&&accepted==2;
}
static_assert(cases());
''')

    def test_type13_export(self):
        for mask in (0,1,2,4,8,9,10,15,8191):
            p=bytearray(page());raw=bytes(range(40))
            struct.pack_into('<HHQQ11I',p,60,13,0,123,0,mask|(1<<16)|(2<<24),*struct.unpack('<10I',raw))
            struct.pack_into('<I',p,2044,logs.crc(p[:2044]))
            e=logs.decode_page(p,5)[0]['gauge_engine']
            self.assertEqual(e['raw73'],raw[:23].hex());self.assertEqual(e['raw76'],raw[23:].hex())
            self.assertEqual(e['phase'],1);self.assertEqual(e['outcome'],2)
            self.assertEqual('CEDVRemCap' in e,bool(mask&8 and mask&1))
            self.assertEqual('SmoothRemCap' in e,bool(mask&8 and mask&2))
            if mask&8 and mask&1:self.assertEqual(e['EDVThreshold'],0x1110)
            if mask&8 and mask&2:self.assertEqual(e['SmoothRemCap'],0x1817);self.assertEqual(e['MaxDeltaSeconds'],39)

    def test_reply_bound(self):
        # Execute the actual production format literals through the native CRT.
        import ctypes,re
        source=(ROOT/'main/debug/serial_debug.cpp').read_text()
        command=source.split('if (std::strcmp(command, "gauge-engine")',1)[1].split('if (std::strcmp(command, "gauge-mode")',1)[0]
        formats=re.findall(r'std::snprintf\(details(?:\+n)?,[^;]*?"([^"\n]*)"',command)
        self.assertEqual(len(formats),3)
        crt=ctypes.CDLL('msvcrt');sprintf=crt._snprintf;sprintf.restype=ctypes.c_int
        U=ctypes.c_uint;P=ctypes.c_char_p
        for ok in (True,False):
            # Even FAIL can carry complete raw blocks; exercise maximal numeric
            # formatting separately from the semantic match gate.
            out=ctypes.create_string_buffer(640)
            args=[U(65535),U(255),U(65535),U(65535),U(65535),U(1),P(b'f'*46),P(b'f'*34),P(b'f'*22),U(65535),U(65535),U(65535)]
            n=sprintf(out,ctypes.c_size_t(640),formats[0].encode(),*args)
            self.assertGreater(n,0);self.assertLess(n,640)
            n+=sprintf(ctypes.byref(out,n),ctypes.c_size_t(640-n),formats[1].encode(),U(65535),U(65535))
            self.assertLess(n,640)
            n+=sprintf(ctypes.byref(out,n),ctypes.c_size_t(640-n),formats[2].encode(),*[U(65535)]*8,U(255))
            self.assertLess(n,640);self.assertEqual(out.raw[n],0)
            self.assertEqual(len(out.value),n);self.assertTrue(out.value.endswith(b'MaxDeltaSeconds=255'))
            line=ctypes.create_string_buffer(1536)
            length=sprintf(line,ctypes.c_size_t(1536),b'DBG RESULT command=%s status=%s%s%s\r\n',P(b'gauge-engine'),P(b'PASS' if ok else b'FAIL'),P(b' '),P(out.value))
            self.assertLess(length,1536);self.assertEqual(line.raw[length],0)

    def test_hardware_word_adapter(self):
        source=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()
        adapter=source.split('struct EngineTransport {',1)[1].split('} transport;',1)[0]
        method='constexpr bool read('+adapter.split('bool read(',1)[1]
        self.compile(r''' 
#include "mosaico_gauge_engine_model.h"
struct Fixture {
 size_t total=0,offset=0,calls=0,fail=99;bool wrong=false;
 constexpr bool gauge_read(uint8_t reg,uint8_t* p,size_t n){
  if(total==2){if(reg!=0x3e||n!=2)wrong=true;}
  else if(reg!=0x40+offset||n!=(total-offset>=2?2:1))wrong=true;
  for(size_t i=0;i<n;++i)p[i]=uint8_t(offset+i);
  offset+=n;return calls++!=fail;
 }
'''+method+r'''
};
constexpr bool cases(){
 constexpr size_t lengths[]={2,11,17,23};
 for(size_t n:lengths)for(size_t fail=0;fail<=12;++fail){
  Fixture f;f.total=n;f.fail=fail;uint8_t p[24]{};
  const bool ok=f.read(n==2?0x3e:0x40,p,n);
  if(f.wrong||ok!=(fail>=(n+1)/2)||f.calls!=(ok?(n+1)/2:fail+1))return false;
  if(ok)for(size_t i=0;i<n;++i)if(p[i]!=i)return false;
 }
 for(unsigned reg=0;reg<256;++reg)for(size_t n=0;n<25;++n){
  if((reg==0x3e&&n==2)||(reg==0x40&&(n==11||n==17||n==23)))continue;
  Fixture f;uint8_t p[24]{};if(f.read(uint8_t(reg),p,n)||f.calls)return false;
 }
 return true;
}
static_assert(cases());
''')

    def test_closed_hal_and_command(self):
        s=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()
        helper=s.split('void gauge_engine_locked(',1)[1].split('// Requires gauge_transaction_mutex',1)[0]
        for forbidden in ('gauge_control(', 'gauge_mac_read(', 'nvs_', 'gauge_access_word(', 'sample_battery_locked('):self.assertNotIn(forbidden,helper)
        self.assertIn('gauge_read(reg,p,n)',helper)
        self.assertIn('count=n-i>=2?2:1',helper)
        api=s.split('bool Hal::gaugeEngineSample(',1)[1].split('bool Hal::gaugeDiagnosticSample(',1)[0]
        self.assertLess(api.index('transaction(gauge_transaction_mutex)'),api.index('battery(battery_mutex)'))
        command=(ROOT/'main/debug/serial_debug.cpp').read_text().split('if (std::strcmp(command, "gauge-engine")',1)[1].split('if (std::strcmp(command, "gauge-mode")',1)[0]
        for needed in ('tryStorageLease()', 'releaseStorageLease()', 'healthPending()', 'expected=no_arguments', 'gaugeEngineSample(s)'):self.assertIn(needed,command)
        self.assertNotIn('gaugeAccess(',command)
        self.assertIn('heap_caps_calloc(1,sizeof(Output),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT)',command)
        self.assertIn('~Buffer(){heap_caps_free(p);}',command)
        self.assertNotIn('char details[900]',command)
        log=(ROOT/'main/host/mosaico_system_log.cpp').read_text()
        self.assertIn('BootCapture bootCapture,engineBootCapture',log)
        self.assertIn('gauge(s);Hal::GaugeEngineSnapshot e{};GetHAL().gaugeEngineSample(e);engine(e);sampleInterval=',log)

if __name__=='__main__':
    import unittest
    unittest.main()
