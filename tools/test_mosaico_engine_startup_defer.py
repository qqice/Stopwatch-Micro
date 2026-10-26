"""R47 optional diagnostic timing only: actual HAL helper + mocked I2C RV32."""
from pathlib import Path
import struct, unittest
import test_mosaico_maintenance_power as maintenance
import test_microlink_peer_probe as rv32
import test_mosaico_gauge_status as status
from test_mosaico_system_logs_export import page
import mosaico_system_logs as logs

ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/'.artifacts/private/mosaico/engine-startup-defer-tests'

class StartupDeferTests(unittest.TestCase):
    compile=status.GaugeStatusTests.compile
    def test_actual_hal_rv32(self):
        # Reuse existing actual transport fixture rather than maintaining a
        # second copy of the exact word-read/I2C mocks. Replace only its runner.
        prefix=maintenance.transport_fixture((ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text())
        prefix=prefix.replace('#define MOSAICO_GAUGE_DEFER_ENGINE_DIAGNOSTICS 0', '#define MOSAICO_GAUGE_DEFER_ENGINE_DIAGNOSTICS ENABLED')
        prefix=prefix.replace('namespace MosaicoSystemLog {static void engineDiagnosticEvent(bool,uint8_t,uint64_t){}}', r'''
static uint64_t nowUs=0,beginMs=0,endMs=0;
static unsigned begins=0,ends=0,endResult=0,markerBad=0;
namespace MosaicoSystemLog {static void engineDiagnosticEvent(bool begin,uint8_t result,uint64_t ms){
 if(begin){++begins;beginMs=ms;if(txCallsAtMarker()!=0||result)++markerBad;}
 else {++ends;endMs=ms;endResult=result;}
}}
''')
        # Marker callback observes transmit count independently of the helper.
        prefix=prefix.replace('static uint64_t nowUs=', 'static int txCallsAtMarker();\nstatic uint64_t nowUs=')
        prefix=prefix.replace('static int64_t esp_timer_get_time(){return 100;}', 'static int txCallsAtMarker(){return txCalls;}\nstatic int64_t esp_timer_get_time(){return nowUs;}')
        # Check the end event occurs only after all three attempted commands.
        prefix=prefix.replace('endResult=result;', 'endResult=result;if(txCallsAtMarker()!=3)++markerBad;')
        runner=r'''
extern "C" uint64_t __udivdi3(uint64_t a,uint64_t b){uint64_t r=0,q=0;for(int i=63;i>=0;--i){r=(r<<1)|((a>>i)&1);if(r>=b){r-=b;q|=uint64_t(1)<<i;}}return q;}
static int run_case(int id){
 Hal::GaugeEngineSnapshot s{};nowUs=uint64_t(id==0?0:id==1?299999:id==2?300000:360000)*1000;
 txCalls=rxCalls=feedCalls=delayMsCalls=bad=0;
 if(id==2)failTx=FIRST_FAIL;else failTx=0;
 gauge_engine_locked(s,1,2);
 if(s.phase!=1||s.outcome!=2||s.sampledUs!=nowUs||s.units.factor!=1||s.units.epoch!=2)return 1;
 if(ENABLED&&id<2){if(s.validMask!=32768||txCalls||rxCalls||feedCalls||delayMsCalls||begins||ends)return 2;return 0;}
 if(s.validMask&32768||txCalls!=3||bad)return 3;
 if(ENABLED){
  if(begins!=1||ends!=1||markerBad||beginMs!=300000||endMs!=300000||endResult!=(FIRST_FAIL?2U:1U))return 4;
  if(id==3&&(s.validMask&15)!=15)return 5;
 }else if(begins||ends)return 6;
 return 0;
}
extern "C" {volatile int test_results[4]={};}
extern "C" void* memset(void* d,int v,size_t n){for(unsigned i=0;i<n;++i)((uint8_t*)d)[i]=v;return d;}
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
extern "C" void _start(){for(int i=0;i<4;++i)test_results[i]=run_case(i);test_done();}
'''
        old_fixture,old_run=rv32.fixture,rv32.RUN
        try:
            rv32.RUN=RUN
            for enabled,first_fail in ((0,0),(1,0),(1,1)):
                code=f'#define ENABLED {enabled}\n#define FIRST_FAIL {first_fail}\n'+prefix+runner
                rv32.fixture=lambda _,code=code:code
                self.assertEqual(rv32.execute(code,f'enabled-{enabled}-first-fail-{first_fail}',4),(0,)*4)
        finally:
            rv32.fixture,rv32.RUN=old_fixture,old_run

    def test_deferred_boot_capture_terminal_once(self):
        self.compile(r'''
#include "mosaico_gauge_engine_model.h"
#include "../../host/mosaico_system_log_model.h"
using namespace MosaicoSystemLog::Model;
constexpr bool cases(){
 BootCapture c;mosaico_gauge_engine::Snapshot a{},b{};
 a.phase=1;a.validMask=32768;a.sampledUs=0;
 b.phase=2;b.validMask=32768;b.sampledUs=8000000;b.outcome=2;
 unsigned emitted=0;auto emit=[&](const auto& s){++emitted;return s.validMask==32768;};
 auto outcome=[](uint8_t,bool){return true;};
 if(!c.update(a,b,false,emit,outcome)||!c.complete||emitted!=2)return false;
 b.sampledUs=400000000;
 return c.update(a,b,false,emit,outcome)&&emitted==2;
}
static_assert(cases());
''')

    def test_actual_marker_record_pack(self):
        source=(ROOT/'main/host/mosaico_system_log.cpp').read_text()
        pack=rv32.function(source,'void engineDiagnosticEvent(').replace('void engineDiagnosticEvent(', 'constexpr void engineDiagnosticEvent(',1)
        self.compile(r'''
#include "../../host/mosaico_system_log_model.h"
using namespace MosaicoSystemLog::Model;
struct Harness {
 Record captured{};bool urgent=false;
 constexpr Record record(Type type,uint64_t ms){Record r;r.type=type;r.monotonicMs=ms;return r;}
 constexpr bool enqueue(const Record& r,bool critical=false){captured=r;urgent=critical;return true;}
'''+pack+r'''
};
constexpr bool cases(){
 for(unsigned result=0;result<=2;++result){
  Harness h;h.engineDiagnosticEvent(result==0,result,0x123456789ULL);
  const auto r=decode(encode(h.captured));
  if(r.type!=Type::EngineDiagnosticEvent||r.monotonicMs!=0x123456789ULL||h.urgent||
     r.args[0]!=(result==0?1U:2U)||r.args[1]!=result||
     r.args[2]!=0x23456789||r.args[3]!=1)return false;
  for(unsigned i=4;i<11;++i)if(r.args[i])return false;
 }
 return true;
}
static_assert(cases());
''')

    def test_decode_optional_and_markers(self):
        for mask in (0,8191,32768):
            p=bytearray(page())
            struct.pack_into('<HHQQ11I',p,60,13,0,0,0,mask|(1<<16)|(2<<24),*([0]*10))
            struct.pack_into('<I',p,2044,logs.crc(p[:2044]))
            e=logs.decode_page(p,5)[0]['gauge_engine']
            self.assertEqual('startup_deferred' in e,mask==32768)
            self.assertEqual('defer_until_ms' in e,mask==32768)
            if mask==32768:self.assertEqual(e['defer_until_ms'],300000)
        for stage,result in ((1,0),(2,1),(2,2)):
            p=bytearray(page())
            struct.pack_into('<HHQQ11I',p,60,15,0,300000,0,stage,result,300000,*([0]*8))
            struct.pack_into('<I',p,2044,logs.crc(p[:2044]))
            e=logs.decode_page(p,5)[0]['engine_diagnostic_event']
            self.assertEqual(e['stage'],'begin' if stage==1 else 'end')
            self.assertEqual(e['timestamp_ms'],300000)

    def test_no_bypass_or_gate_change(self):
        hal=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()
        self.assertEqual(hal.count('mosaico_gauge_engine::read('),1)
        helper=rv32.function(hal,'void gauge_engine_locked(')
        self.assertLess(helper.index('startupDeferred('),helper.index('struct EngineTransport'))
        self.assertNotIn('sleep',helper.split('return; //',1)[0])
        standard=rv32.function(hal,'void gauge_diagnostic_locked(')
        self.assertNotIn('StartupDeferred',standard)
        self.assertNotIn('startupDeferred',standard)
        command=(ROOT/'main/debug/serial_debug.cpp').read_text().split('if (std::strcmp(command, "gauge-engine")',1)[1].split('if (std::strcmp(command, "gauge-mode")',1)[0]
        for token in ('tryStorageLease()', 'healthPending()', '"SKIP"', 'defer_until_ms=300000'):
            self.assertIn(token,command)
        self.assertIn('DIAGNOSTICS "Defer optional engine diagnostics until boot uptime 5 minutes" OFF)',(ROOT/'boards/mosaico/CMakeLists.txt').read_text())

if __name__=='__main__':unittest.main()
