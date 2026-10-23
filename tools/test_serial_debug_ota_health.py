"""Actual small cached command/CRC routing on RV32; no firmware build or device."""
from pathlib import Path
import hashlib, os, re, subprocess, unittest, zlib
import test_microlink_peer_probe as rv32
import test_mosaico_gauge_scale_backend as base
ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/'.artifacts/private/mosaico/ota-health-fast-command-tests-001'


def source_method(source,signature):return rv32.function(source,signature)


def fixture(mosaico=True,auth_deny=False):
    source=(ROOT/'main/debug/serial_debug.cpp').read_text()
    header=(ROOT/'main/debug/serial_debug_transport.h').read_text()
    ota=(ROOT/'main/ota/mosaico_ota.h').read_text()
    decl='struct HealthDiagnostic {'+ota.split('struct HealthDiagnostic {',1)[1].split('\n};',1)[0]+'\n};'
    strip=lambda text:re.sub(r'^\s*#(?:pragma|include)[^\n]*','',text,flags=re.M)
    prefix='\n'.join(strip(base.HEADERS[k]) for k in ('cstdint','cstddef','array'))
    prefix+=r'''
namespace std {
int strcmp(const char* a,const char* b){for(;*a&&*a==*b;++a,++b){}return *a-*b;}
int strncmp(const char* a,const char* b,size_t n){for(size_t i=0;i<n;++i){if(a[i]!=b[i]||!a[i])return a[i]-b[i];}return 0;}
template<class P>void fill_n(P p,size_t n,char value){for(size_t i=0;i<n;++i)p[i]=value;}
}
extern "C" void* memset(void* d,int v,size_t n){for(unsigned i=0;i<n;++i)((uint8_t*)d)[i]=v;return d;}
extern "C" void* memcpy(void* d,const void* s,size_t n){for(unsigned i=0;i<n;++i)((uint8_t*)d)[i]=((const uint8_t*)s)[i];return d;}
static unsigned copies,formats,passes,failures,fullCalls,bad;static bool nandBusy;static const char* lastReason="";
namespace system_config{constexpr const char* FirmwareVersion="test-frozen-version";}
namespace GaugeDischargeTrace{namespace NandBackend{bool busy(){return nandBusy;}}}
'''
    if mosaico:prefix+='#define MOSAICO_BOARD 1\n#define MOSAICO_NAND_STORE 1\n'
    if auth_deny:
        header=header.replace('inline bool uartAllowed(', 'inline bool originalUartAllowed(')
        header+='\nnamespace serial_debug_transport {inline bool uartAllowed(const char*){return false;}}\n'
    policy=(ROOT/'main/hal/mosaico/mosaico_psram_heap_policy.h').read_text()
    prefix+=strip(policy)+r'''
void MosaicoPsramHeapPolicySnapshot(MosaicoPsramHeapPolicyDiagnostic& out){out={};}
bool MosaicoPsramHeapPolicyReadRecord(uint32_t,MosaicoPsramHeapPolicyRecord&){return false;}
'''
    prefix+=strip(header)
    prefix+='namespace MosaicoOta{'+decl+r'''
static HealthDiagnostic cached{};
__attribute__((noinline)) void copyHealthDiagnostic(HealthDiagnostic& out){++copies;out=cached;}
}
struct SerialDebug{
 static constexpr size_t LineCapacity=1536;
 std::array<char,1536> _line{},_uart_line{};size_t _line_length=0,_uart_line_length=0;
 bool _line_overflow=false,_uart_line_overflow=false,_reply_uart=false;
 void consume(char);bool __attribute__((noinline)) handleCachedRead(const char*);
 bool handleMaintenanceRestart(const char*){return false;}
 bool handleContrast(char*){return false;}
 bool handleFactoryScale(char*){return false;} // New isolated mutating parser tested separately.
 __attribute__((noinline)) void handleLine(char*){++fullCalls;}
 static void result(const char* command,const char* status,const char* details=nullptr){
  if(!std::strcmp(status,"PASS"))++passes;else ++failures;
  if(!command||!status)++bad;lastReason=details?details:"";
 }
 static __attribute__((noinline)) int debugPrintf(const char* format,...){
  ++formats;__builtin_va_list args;__builtin_va_start(args,format);
  unsigned long version=__builtin_va_arg(args,unsigned long);const char* firmware=__builtin_va_arg(args,const char*);
  if(version!=1||std::strcmp(firmware,system_config::FirmwareVersion)||std::strncmp(format,"DBG OTA_HEALTH version=",22))++bad;
  __builtin_va_end(args);return 1;
 }
};
'''
    prefix+=source_method(source,'bool __attribute__((noinline)) SerialDebug::handleCachedRead(')
    prefix+=source_method(source,'void SerialDebug::consume(')
    cases=[('debug ota-health\n',False),('dbg ota-health\n',False),('  debug ota-health  \n',False),
           ('debug ota-health extra\n',False),('debug ota-health-more\n',False),('debugx ota-health\n',False),
           ('ota-health\n',False),('debug status\n',False),('debug ota-health\n',True),('debug ota-health extra\n',True)]
    declarations=[]
    for i,(text,uart) in enumerate(cases):
        if uart:
            payload=text.rstrip('\n');text=f'\x75art {zlib.crc32(payload.encode()):08x} {payload}\n'
        encoded=''.join('\\x%02x'%b for b in text.encode())
        declarations.append(f'case {i}:text="{encoded}";uart={str(uart).lower()};break;')
    payload='debug ota-health';valid=f'\x75art {zlib.crc32(payload.encode()):08x} {payload}\n'
    broken=valid[:5]+('0' if valid[5]!='0' else '1')+valid[6:]
    for idx,text in [(10,broken),(11,'debug ota-health\n'),(12,valid)]:
        declarations.append(f'case {idx}:text="'+''.join('\\x%02x'%b for b in text.encode())+'";uart=true;break;')
    declarations.append('case 13:text="debug ota-health\\n";uart=false;break;')
    return prefix+r'''
static int run_case(int id){copies=formats=passes=failures=fullCalls=bad=0;lastReason="";nandBusy=id==12||id==13;
 MosaicoOta::cached={};MosaicoOta::cached.version=1;const char* text="";bool uart=false;switch(id){
'''+''.join(declarations)+r'''
}SerialDebug d;d._reply_uart=uart;
 if(id==14){char input[]="  dbg\tota-health \t";const char expected[]="  dbg\tota-health \t";d.handleCachedRead(input);if(std::strcmp(input,expected))return 12;}
 else for(unsigned i=0;text[i];++i)d.consume(text[i]);
#ifndef MOSAICO_BOARD
 if(id==14)return !copies&&!formats&&!fullCalls?0:13;
 if(id==10||id==11)return failures==1&&!copies&&!formats&&!fullCalls?0:10;
 return fullCalls==1&&!copies&&!formats?0:11;
#else
 if(id==10||id==11)return failures==1&&!copies&&!formats&&!fullCalls&&!std::strcmp(lastReason,"reason=uart_crc_frame_required no_changes=1")?0:1;
 if(id>=4&&id<=7)return fullCalls==1&&!copies&&!formats&&!passes&&!failures?0:2;
 if(id==3||id==9||id==12||id==13){const char* expected=AUTH_DENY&&uart?"reason=uart_requires_extended_authorization no_changes=1":id==12||id==13?"reason=nand_format_maintenance no_changes=1":"reason=expected_no_arguments no_changes=1";return failures==1&&!copies&&!formats&&!fullCalls&&!std::strcmp(lastReason,expected)?0:3;}
 if(AUTH_DENY&&uart)return failures==1&&!copies&&!formats&&!fullCalls?0:4;
 if(copies!=1||formats!=1||passes!=1||failures||fullCalls||bad)return 5;
 for(char c:d._line)if(c)return 6;for(char c:d._uart_line)if(c)return 7;return 0;
#endif
}
extern "C" {volatile int test_results[15]={};}
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
extern "C" void _start(){for(int i=0;i<15;++i)test_results[i]=run_case(i);test_done();}
'''.replace('AUTH_DENY',str(int(auth_deny)))


def execute(mosaico=True,deny=False):
    old_fixture,old_run=rv32.fixture,rv32.RUN
    label='fastpath'+('' if mosaico else '-legacy')+('-deny' if deny else '')
    try:
        rv32.fixture,rv32.RUN=lambda _:fixture(mosaico,deny),RUN
        values=rv32.execute('',label,15)
        (RUN/(label+'-execution.log')).write_text('Actual noinline cached helper + consume + CRC/allowlist; mocked cache/output only.\n'+f'results={values}\n')
        return values
    finally:rv32.fixture,rv32.RUN=old_fixture,old_run


class CachedCommandTests(unittest.TestCase):
    def test_actual_crc_parse_and_cache_route(self):self.assertEqual(execute(),(0,)*15)
    def test_actual_uart_auth_denial_before_cache(self):self.assertEqual(execute(True,True),(0,)*15)
    def test_legacy_route_unchanged(self):self.assertEqual(execute(False),(0,)*15)

    def test_source_stack_and_schema(self):
        source=(ROOT/'main/debug/serial_debug.cpp').read_text()
        baseline=subprocess.check_output(['git','show','6258a3891d5356f308500e8946d480a461a6703a:main/debug/serial_debug.cpp'],cwd=ROOT,text=True)
        self.assertEqual(source_method(source,'void SerialDebug::printStatus('),source_method(baseline,'void SerialDebug::printStatus('))
        helper=source_method(source,'bool __attribute__((noinline)) SerialDebug::handleCachedRead(')
        for forbidden in ('heap_','esp_','GetHAL','GetDisplay','nvs_','GetNetwork','new ','malloc','handleLine('):self.assertNotIn(forbidden,helper)
        self.assertIn('firmware=%s',helper);self.assertIn('system_config::FirmwareVersion',helper)
        for field in ('version','firmware','cached_only','polls','us','probe_us','app','evaluated','passed','hal','frames','internal','psram',
                      'gap_us','max_gap_us','since_us','loops','sdk_read','sdk_state','sdk_errno','slot','pending','mark_attempts','mark_errno','mark_ok','timeout'):
            self.assertIn(field+'=',helper)
        def dispatcher(text):
            body=text.split('void SerialDebug::handleLine(',1)[1].split('\nvoid SerialDebug::printHelp(',1)[0]
            # The independently tested public131 revalidation command expands
            # only this existing parser block. Preserve the complete remaining
            # dispatcher comparison; do not relax the health fast path, status,
            # formatter or native target ELF frame checks.
            start='    if (std::strcmp(command, "gauge-factory-audit") == 0) {'
            end='    if (std::strcmp(command, "gauge-native-recovery") == 0)'
            before, block=body.split(start,1)
            audit, after=block.split(end,1)
            self.assertIn('char details[1024]{};',audit)
            return before+start+'/* separately tested fixed-capacity factory parser */'+end+after
        self.assertEqual(dispatcher(source),dispatcher(baseline))
        self.assertIn('char bytes[1536]',source_method(source,'int SerialDebug::debugPrintf('))
        self.assertEqual(source_method(source,'int SerialDebug::debugPrintf('),source_method(baseline,'int SerialDebug::debugPrintf('))
        consume=source_method(source,'void SerialDebug::consume(')
        self.assertLess(consume.index('decodeUartLine('),consume.index('handleCachedRead('))
        self.assertLess(consume.index('handleCachedRead('),consume.index('handleLine('))
        RUN.mkdir(parents=True,exist_ok=True)
        cpp=RUN/'stack-check.cpp';cpp.write_text(fixture())
        args=[str(rv32.CLANG),'--target=riscv32-unknown-elf','-march=rv32im','-mabi=ilp32','-std=c++20','-Os','-Wno-missing-multilib','-fno-builtin','-fno-exceptions','-fno-rtti','-nostdinc++','-fstack-usage','-c',str(cpp),'-o',str(RUN/'stack-check.o')]
        result=subprocess.run(args,capture_output=True,text=True,env={**os.environ,'TEMP':str(RUN),'TMP':str(RUN)})
        (RUN/'stack-compile.log').write_text(result.stdout+result.stderr);self.assertEqual(result.returncode,0,result.stderr)
        stack=(RUN/'stack-check.su').read_text();entry=next(line for line in stack.splitlines() if 'handleCachedRead' in line)
        size=int(entry.split('\t')[1]);self.assertLessEqual(size,320,entry)
        # Frozen actual r8 ordinary chain costs 1712B before handleLine. Removing
        # its 3696B frame and printStatus640B leaves room for the unchanged
        # 1600B formatter and libc80+128B. Target build must verify its own ELF.
        budget=1712+size+1600+80+128+32
        self.assertLess(budget,4096)
        (RUN/'stack-budget.log').write_text(stack+'\nConservative known-frame fast cached chain='+str(budget)+' bytes; firmware ELF verification still required.\n')

if __name__=='__main__':unittest.main(verbosity=2)
