"""Actual cached HAL page and early parser on RV32; no device or real I/O."""
import hashlib,inspect,json,os,re,tempfile,unittest,subprocess
from pathlib import Path
from unittest.mock import patch
import test_mosaico_factory_scale_journal as codec
import test_mosaico_factory_scale_integration as integration
import test_microlink_peer_probe as rv32
import test_mosaico_gauge_scale_backend as base
import mosaico_factory_scale_observed_pages as decoder
ROOT=codec.ROOT
RUN=ROOT/'.artifacts/private/mosaico/factory-observed-page-runtime-tests'

class PageRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):codec.FactoryScaleJournalTests.setUpClass();RUN.mkdir(parents=True,exist_ok=True)
    def test_actual_HAL_source_target_pages_decode_and_fail_closed(self):
        source=(ROOT/'main/hal/mosaico/mosaico_factory_scale_runtime.inc').read_text()
        method=rv32.function(source,'bool Hal::gaugeFactoryScaleObservedPage(')
        prefix=r'''
#include <mutex>
#include <cstdio>
#include <cstring>
#include "mosaico_factory_scale_observed_page.h"
namespace scale_recovery=mosaico_gauge_recovery;
struct Hal {bool gaugeFactoryScaleObservedPage(size_t,char*,size_t);};
struct Runtime {mosaico_factory_scale_journal::Record record;scale_recovery::Observation observed;uint32_t observedGeneration=4;uint64_t observedEpoch=4;int64_t observedReadMs=12345;scale_recovery::Phase observedPhase=scale_recovery::Phase::Restoring;const char* observedStage="pre_exit_cfg_fa";};
static Runtime storage;static Runtime* factory_scale_rt=&storage;static std::recursive_mutex gauge_transaction_mutex;
static bool factory_scale_capacity(char*,size_t n,size_t required){return n>=required;}
static bool encoderFail;
int mbedtls_base64_encode(unsigned char* out,size_t cap,size_t* count,const unsigned char* input,size_t size){
 if(encoderFail)return -1;const char* alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";size_t needed=((size+2)/3)*4;
 if(cap<=needed)return -1;*count=0;for(size_t i=0;i<size;i+=3){unsigned v=unsigned(input[i])<<16;if(i+1<size)v|=unsigned(input[i+1])<<8;if(i+2<size)v|=input[i+2];
 out[(*count)++]=alphabet[(v>>18)&63];out[(*count)++]=alphabet[(v>>12)&63];out[(*count)++]=i+1<size?alphabet[(v>>6)&63]:'=';out[(*count)++]=i+2<size?alphabet[v&63]:'=';}out[*count]=0;return 0;}
'''
        setup=r'''
static void setup(bool target){storage={};factory_scale_rt=&storage;storage.record=sample();storage.record.snapshot.generation=5;storage.record.snapshot.epoch=5;
 storage.observed.count=131;storage.observed.authenticated=true;
 for(unsigned i=0;i<131;++i)for(unsigned j=0;j<4;++j)storage.observed.raw[i][j]=j<storage.record.snapshot.fields[i].width?(target?storage.record.snapshot.fields[i].target[j]:storage.record.snapshot.fields[i].original[j]):0xa5;}
extern "C" {char page_outputs[10][1024]={};volatile int test_results[10]={};}
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
extern "C" void _start(){Hal hal;char scratch[1024]{};
 for(unsigned target=0;target<2;++target){setup(target);for(unsigned i=0;i<5;++i)if(!hal.gaugeFactoryScaleObservedPage(i,page_outputs[target*5+i],1024))test_results[0]=1;}
 setup(false);if(hal.gaugeFactoryScaleObservedPage(5,scratch,1024))test_results[1]=1;
 if(hal.gaugeFactoryScaleObservedPage(0,scratch,767))test_results[2]=1;
 setup(false);char longest[81]{};for(unsigned i=0;i<80;++i)longest[i]='A';storage.observedStage=longest;storage.observedGeneration=0xffffffffU;storage.observedEpoch=~uint64_t(0);storage.observedReadMs=0x7fffffffffffffffLL;
 if(!hal.gaugeFactoryScaleObservedPage(0,scratch,768)||std::strlen(scratch)>=768)test_results[2]=2;
 storage.observed.authenticated=false;if(hal.gaugeFactoryScaleObservedPage(0,scratch,1024))test_results[3]=1;
 setup(false);storage.observed.count=130;if(hal.gaugeFactoryScaleObservedPage(0,scratch,1024))test_results[4]=1;
 setup(false);storage.observedReadMs=0;if(hal.gaugeFactoryScaleObservedPage(0,scratch,1024))test_results[5]=1;
 setup(false);storage.observedGeneration=0;if(hal.gaugeFactoryScaleObservedPage(0,scratch,1024))test_results[6]=1;
 setup(false);storage.record.snapshot.fields[130].address^=1;if(hal.gaugeFactoryScaleObservedPage(0,scratch,1024))test_results[7]=1;
 setup(false);encoderFail=true;if(hal.gaugeFactoryScaleObservedPage(0,scratch,1024))test_results[8]=1;
 factory_scale_rt=nullptr;if(hal.gaugeFactoryScaleObservedPage(0,scratch,1024))test_results[9]=1;test_done();}
'''
        support=base.MOCKS.split('static Hal hal;',1)[0]+base.RV_SUPPORT.split('static esp_partition_t imageParts',1)[0]
        fixture=codec.FactoryScaleJournalTests.fixture+prefix+method+setup+support
        namespace=dict(rv32.__dict__);namespace.update(fixture=lambda _:fixture,RUN=RUN,json=json)
        code=inspect.getsource(rv32.execute)
        code=code.replace("    values=struct.unpack(","    outputs=bytes(uc.mem_read(symbols['page_outputs'],10240))\n    (RUN/'HAL-pages.json').write_text(json.dumps([outputs[i*1024:(i+1)*1024].split(b'\\0',1)[0].decode() for i in range(10)]))\n    values=struct.unpack(",1)
        with tempfile.TemporaryDirectory(dir=RUN) as td:
            for name,text in base.HEADERS.items():p=Path(td)/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
            code=code.replace("'-nostdlib',str(cpp)","'-nostdlib','-I"+td.replace('\\','/')+"','-I"+str(ROOT/'main/hal/mosaico').replace('\\','/')+"',str(cpp)")
            exec(code,namespace);self.assertEqual(namespace['execute']('', 'HAL-pages',10),(0,)*10)
        outputs=json.loads((RUN/'HAL-pages.json').read_text())
        raw=json.loads(codec.PRIVATE.read_bytes())['fields'];changes={int(a,0):(int(w),int(after,0)) for a,w,_,after in re.findall(r'\{(0x[0-9a-f]+),(1|2|4),(0x[0-9a-f]+|[0-9]+),(0x[0-9a-f]+|[0-9]+)\}',(ROOT/'main/hal/mosaico/mosaico_factory_scale_model.h').read_text())}
        for target in (False,True):
            expected=[]
            for i,f in enumerate(raw):
                value=f['rawBE'].lower().ljust(8,'0')
                if target and int(f['address'],16) in changes:w,v=changes[int(f['address'],16)];value=v.to_bytes(w,'big').hex().ljust(8,'0')
                expected.append(dict(index=i,address=f['address'],width=f['width'],raw=value))
            pages=[dict(part.split('=',1) for part in out.split()) for out in outputs[int(target)*5:int(target)*5+5]]
            result=decoder.decode(pages,expected);self.assertEqual(result['context']['generation'],'4');self.assertEqual(result['context']['epoch'],'4');self.assertEqual(result['context']['cache_phase'],'3')
            self.assertEqual(result['observed_crc'],'b5b0f0d6' if target else '70a767be')
            self.assertTrue(all(len(p['data'])<=300 for p in pages))
    def test_actual_early_parser_new_page_bounds_CRC_and_old_field(self):
        source=(ROOT/'main/debug/serial_debug.cpp').read_text();unsigned='bool parseUnsignedStrict'+source.split('bool parseUnsignedStrict',1)[1].split('#ifdef MOSAICO_BOARD',1)[0]
        header=(ROOT/'main/debug/serial_debug_transport.h').read_text();transport='namespace serial_debug_transport {'+header.split('namespace serial_debug_transport {',1)[1].split('// No driver waits',1)[0]+'bool uartAllowed(const char*){++authCalls;return allowUart;}\n}\n'
        runner=integration.PARSER_RUNNER.split('static int run_case(',1)[0]+r'''
static int run_case(int i){
 const char* cases[]={"debug gauge-factory-scale observed-page 0","dbg gauge-factory-scale observed-page 4","debug gauge-factory-scale observed-page 5","debug gauge-factory-scale observed-page -1","debug gauge-factory-scale observed-page +1","debug gauge-factory-scale observed-page 1x","debug gauge-factory-scale observed-page","debug gauge-factory-scale observed-page 1 extra"};
 if(i<16){int result=direct(cases[i%8],i%8<2?6:-1,i>=8);return result||((i%8<2)&&indexSeen!=(i%8?4u:0u))?1:0;}
 reset();if(i==16){feed(cases[0],true,false);return halCalls||pass?2:0;}
 if(i==17){serial._reply_uart=true;allowUart=false;char s[]="debug gauge-factory-scale observed-page 0";serial.handleFactoryScale(s);return halCalls||pass?3:0;}
 if(i==18){feed("debug gauge-factory-scale observed 130",true);return halCalls!=1||actionSeen!=5||indexSeen!=130?4:0;}
 feed(cases[1],true);return halCalls!=1||actionSeen!=6||indexSeen!=4||legacyCalls?5:0;}
extern "C" {volatile int test_results[20]={};}extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
extern "C" void _start(){for(int i=0;i<20;++i)test_results[i]=run_case(i);test_done();}
'''
        support=base.MOCKS.split('static Hal hal;',1)[0]+base.RV_SUPPORT.split('static esp_partition_t imageParts',1)[0]
        text=integration.PARSER_PREFIX+transport+unsigned+rv32.function(source,'bool __attribute__((noinline)) SerialDebug::handleFactoryScale(')+rv32.function(source,'void SerialDebug::consume(')+support+runner
        with patch.object(integration,'RUN',RUN/'parser'):integration.execute(self,text,'page-parser',20)
    def test_source_no_IO_alloc_and_native_symbols_to_check(self):
        source=(ROOT/'main/hal/mosaico/mosaico_factory_scale_runtime.inc').read_text();method=rv32.function(source,'bool Hal::gaugeFactoryScaleObservedPage(')
        for forbidden in ('loadNvs(', 'GetHAL(', 'gauge_word(', 'nvs_', 'heap_caps_', 'new(', '917'):self.assertNotIn(forbidden,method)
        self.assertIn('t.observedGeneration',method);self.assertIn('t.observedEpoch',method);self.assertIn('t.observedPhase',method)
        self.assertIn('page.bytes.data()',method)
        debug=(ROOT/'main/debug/serial_debug.cpp').read_text()
        original=subprocess.check_output(['git','show','HEAD:main/debug/serial_debug.cpp'],cwd=ROOT,text=True)
        self.assertEqual(rv32.function(debug,'void SerialDebug::printStatus('),rv32.function(original,'void SerialDebug::printStatus('))
        body=lambda text:text.split('void SerialDebug::handleLine(',1)[1].split('\nvoid SerialDebug::printHelp(',1)[0]
        self.assertEqual(body(debug),body(original))
        maximum='version=1 cached=1 cached_only=1 no_i2c=1 no_nvs=1 page=4 first=128 count=32 total=131 authenticated=1 generation=4294967295 epoch=18446744073709551615 read_ms=9223372036854775807 cache_phase=3 stage='+('A'*80)+' observed_crc=ffffffff page_crc=ffffffff encoding=le_addr2_width1_raw4_be_pad0_v1 data='+('A'*300)
        self.assertLess(len(maximum),768);self.assertLess(len(maximum)+len('DBG RESULT command=gauge-factory-scale status=PASS \r\n'),1536)

        (RUN/'native-symbols-pending.txt').write_text('Hal::gaugeFactoryScaleObservedPage\nSerialDebug::handleFactoryScale\nSerialDebug::handleCachedRead\nSerialDebug::handleLine\nSerialDebug::printStatus\nmosaico_factory_observed_page::encode (may inline)\n')

if __name__=='__main__':unittest.main()
