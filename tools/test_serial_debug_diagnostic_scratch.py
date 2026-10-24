"""Real three gauge predispatch handlers + instance PSRAM scratch on RV32 mocks."""
import json,os,struct,subprocess,sys,tempfile,unittest,zlib
from pathlib import Path
import test_mosaico_gauge_cold_console as base
ROOT=base.ROOT;RUN=ROOT/'artifacts/serial-diagnostic-scratch-tests';SOURCE=ROOT/'main/debug/serial_debug.cpp'
PREFIX=base.SUPPORT.split('static bool forceUartDeny',1)[0]+r'''
static int taskA,taskB;using TaskHandle_t=void*;static TaskHandle_t task=&taskA;
TaskHandle_t xTaskGetCurrentTaskHandle(){return task;}
static bool oom=false,nandBusy=false,deny=false;static unsigned allocations=0,frees=0,halCalls=0,faults=0,results=0;static char lastStatus[8]{};
constexpr int MALLOC_CAP_SPIRAM=0x400,MALLOC_CAP_8BIT=4;
static char pool[4][1024];
void* heap_caps_malloc(size_t n,int caps){++allocations;if(n!=1024||caps!=(MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT))++faults;return oom?nullptr:pool[(allocations-1)%4];}
void heap_caps_free(void*){++frees;}
static void copy(char* out,const char* in,size_t n){size_t i=0;for(;i+1<n&&in&&in[i];++i)out[i]=in[i];out[i]=0;}
static bool invoked(char* out,size_t n){++halCalls;if(n!=1024)++faults;for(size_t i=0;i<n;++i)if(out[i])++faults;for(size_t i=0;i<n;++i)out[i]='X';out[1]=0;return true;}
namespace GaugeDischargeTrace {namespace NandBackend{bool busy(){return nandBusy;}}}
namespace MosaicoGaugeCold {
bool status(char* p,size_t n){return invoked(p,n);}bool bindFragment(const char*,char* p,size_t n){return invoked(p,n);}
bool verifyFragment(const char*,char* p,size_t n){return invoked(p,n);}
bool apply(char* p,size_t n){return invoked(p,n);}bool handoff(char* p,size_t n){return invoked(p,n);}
bool recover(char* p,size_t n){return invoked(p,n);}bool cancel(char* p,size_t n){return invoked(p,n);}
bool evidencePage(uint32_t,char* p,size_t n){return invoked(p,n);}
}
struct Hal {
bool gaugeContrastStatus(char* p,size_t n){return invoked(p,n);}bool gaugeContrastImages(const char*,char* p,size_t n){return invoked(p,n);}
bool gaugeContrastPrepare(const char*,char* p,size_t n){return invoked(p,n);}bool gaugeContrastApply(char* p,size_t n){return invoked(p,n);}
bool gaugeContrastRestore(char* p,size_t n){return invoked(p,n);}bool gaugeContrastObservedPage(uint32_t,char* p,size_t n){return invoked(p,n);}
bool gaugeFactoryScaleStatus(char* p,size_t n){return invoked(p,n);}bool gaugeFactoryScalePrepare(char* p,size_t n){return invoked(p,n);}
bool gaugeFactoryScaleImages(const char*,char* p,size_t n){return invoked(p,n);}bool gaugeFactoryScaleApply(char* p,size_t n){return invoked(p,n);}
bool gaugeFactoryScaleRestore(char* p,size_t n){return invoked(p,n);}bool gaugeFactoryScaleObservedPage(uint32_t,char* p,size_t n){return invoked(p,n);}
bool gaugeFactoryScaleObservedField(uint32_t,char* p,size_t n){return invoked(p,n);}
};Hal& GetHAL(){static Hal hal;return hal;}
struct SerialDebug {static constexpr size_t DiagnosticCapacity=1024;char* _diagnostic_scratch=nullptr;void* _diagnostic_owner=nullptr;bool _reply_uart=false;
char* diagnosticScratch();void releaseDiagnosticScratch();bool handleCold(char*);bool handleContrast(char*);bool handleFactoryScale(char*);
void result(const char*,const char* status,const char*){++results;copy(lastStatus,status,sizeof(lastStatus));}
};
'''
CHECK=r'''
static bool dispatch(SerialDebug& console,const char* text,bool framed=false){char line[512]{};copy(line,text,sizeof(line));char* command=framed?serial_debug_transport::decodeUartLine(line,std::strlen(line)):line;return command&&(console.handleCold(command)||console.handleContrast(command)||console.handleFactoryScale(command));}
extern "C" int run_case(int id){task=&taskA;oom=nandBusy=deny=false;allocations=frees=halCalls=faults=results=0;SerialDebug console;
 const char* names[]={"debug gauge-cold status","debug gauge-contrast status","debug gauge-factory-scale status"};
 const char* mutators[]={"debug gauge-cold apply CONFIRM_EXTERNAL_POWER","debug gauge-contrast apply CONFIRM_EXTERNAL_POWER","debug gauge-factory-scale apply CONFIRM_EXTERNAL_POWER"};
 if(id<3){oom=true;if(!dispatch(console,mutators[id])||halCalls||allocations!=1||console._diagnostic_scratch||std::strcmp(lastStatus,"FAIL"))return 1;oom=false;if(!dispatch(console,mutators[id])||halCalls!=1||allocations!=2||faults||std::strcmp(lastStatus,"PASS"))return 2;return 0;}
 if(id==3){for(auto name:names)if(!dispatch(console,name))return 3;return halCalls!=3||allocations!=1||faults?4:0;}
 if(id==4){dispatch(console,names[0]);const auto* original=console._diagnostic_scratch;task=&taskB;for(auto name:names)if(!dispatch(console,name)||std::strcmp(lastStatus,"FAIL"))return 5;console.releaseDiagnosticScratch();if(frees||halCalls!=1||console._diagnostic_scratch!=original)return 6;task=&taskA;console.releaseDiagnosticScratch();if(frees!=1||console._diagnostic_scratch||console._diagnostic_owner)return 7;dispatch(console,names[1]);return allocations!=2||halCalls!=2||faults?8:0;}
 if(id==5){SerialDebug second;dispatch(console,names[0]);dispatch(second,names[1]);return allocations!=2||console._diagnostic_scratch==second._diagnostic_scratch||faults?9:0;}
 if(id==6){for(auto name:names){console._reply_uart=true;deny=true;dispatch(console,name);deny=false;console._reply_uart=false;nandBusy=true;dispatch(console,name);nandBusy=false;}dispatch(console,"debug gauge-cold status extra");dispatch(console,"debug gauge-contrast status extra");dispatch(console,"debug gauge-factory-scale status extra");return halCalls||allocations||faults?10:0;}
'''

class ScratchTests(unittest.TestCase):
    def test_real_three_handlers_owner_oom_and_reuse(self):
        RUN.mkdir(parents=True,exist_ok=True);source=SOURCE.read_text()
        actual='\n'.join(base.extract(source,prefix+name+'(') for prefix,name in (
            ('char* __attribute__((noinline)) SerialDebug::','diagnosticScratch'),('void __attribute__((noinline)) SerialDebug::','releaseDiagnosticScratch'),
            ('bool __attribute__((noinline)) SerialDebug::','handleCold'),('bool __attribute__((noinline)) SerialDebug::','handleContrast'),('bool __attribute__((noinline)) SerialDebug::','handleFactoryScale')))
        parser=base.extract(source,'bool parseUnsignedStrict(')
        transport=base.TRANSPORT.read_text().replace('inline bool uartAllowed(', 'inline bool productionUartAllowed(')
        frames=[]
        for name in ('gauge-cold','gauge-contrast','gauge-factory-scale'):
            line='debug '+name+' status';frames.append('uart '+f'{zlib.crc32(line.encode()):08x}'+' '+line)
        check=CHECK+' if(id==7){console._reply_uart=true;'
        for frame in frames:check+='if(!dispatch(console,'+json.dumps(frame)+',true))return 11;'
        check+='return allocations!=1||halCalls!=3||faults?12:0;}if(id==8){oom=true;dispatch(console,"debug gauge-cold verify cap");if(halCalls||allocations!=1)return 13;oom=false;dispatch(console,"debug gauge-cold verify cap");return halCalls!=1||allocations!=2||faults?14:0;}return 99;}\n'
        check+='extern "C" {volatile int test_results[9];void __attribute__((noinline,used)) test_done(){for(;;){asm volatile("");}}void _start(){for(int i=0;i<9;++i)test_results[i]=run_case(i);test_done();}}'
        code='#define MOSAICO_BOARD 1\n#define MOSAICO_NAND_STORE 1\n'+PREFIX+transport+'\nnamespace serial_debug_transport {bool uartAllowed(const char* command){return !deny&&productionUartAllowed(command);}}\n'+parser+actual+check
        with tempfile.TemporaryDirectory(prefix='diagnostic-',dir=RUN) as td:
            temp=Path(td)
            for name in ('cstdint','cstddef','cstring','array'):(temp/name).write_text(base.HEADERS[name])
            src=temp/'diagnostic.cpp';src.write_text(code);elf=RUN/'diagnostic.elf'
            args=[str(base.CLANG),'--target=riscv32-unknown-elf','-march=rv32im','-mabi=ilp32','-std=c++20','-O1','-fstack-usage','-ffunction-sections','-fdata-sections','-fno-builtin','-fno-exceptions','-fno-rtti','-nostdinc++','-nostdlib','-I'+str(temp),str(src),'--ld-path='+str(base.CLANG.parent/'ld.lld.exe'),'-Wl,--gc-sections','-Wl,-e,_start','-Wl,-Ttext=0x10000','-o',str(elf)]
            result=subprocess.run(args,capture_output=True,text=True,env={**os.environ,'TMP':str(temp),'TEMP':str(temp)})
            (RUN/'compile.log').write_text(result.stdout+result.stderr);self.assertEqual(result.returncode,0,str(RUN/'compile.log'))
        sys.path.insert(0,str(ROOT/'.artifacts/tools/unicorn-2.1.4-private-001/package'))
        from unicorn import Uc,UC_ARCH_RISCV,UC_MODE_RISCV32
        from unicorn.riscv_const import UC_RISCV_REG_SP,UC_RISCV_REG_PC
        data=elf.read_bytes();symbols=base.elf_symbols(data);emulator=Uc(UC_ARCH_RISCV,UC_MODE_RISCV32);emulator.mem_map(0,32*1024*1024)
        offset=struct.unpack_from('<I',data,28)[0];size,count=struct.unpack_from('<HH',data,42)
        for i in range(count):
            kind,position,address,_,length,_,_,_=struct.unpack_from('<8I',data,offset+i*size)
            if kind==1:emulator.mem_write(address,data[position:position+length])
        emulator.reg_write(UC_RISCV_REG_SP,0x1fffff0);emulator.emu_start(struct.unpack_from('<I',data,24)[0],symbols['test_done'],timeout=20000000,count=100000000)
        values=struct.unpack('<9i',bytes(emulator.mem_read(symbols['test_results'],36)))
        (RUN/'execution.json').write_text(json.dumps(dict(results=values,limits='actual three handlers and shared scratch; HAL/allocator/task/libc mocked, no hardware'),indent=2));self.assertEqual(values,(0,)*9)
    def test_scope(self):
        source=SOURCE.read_text();helper=base.extract(source,'char* __attribute__((noinline)) SerialDebug::diagnosticScratch(')
        self.assertIn('MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT',helper);self.assertNotIn('MALLOC_CAP_INTERNAL',helper)
        self.assertIn('releaseDiagnosticScratch();',base.extract(source,'void SerialDebug::end('))
        for name in ('handleCold','handleContrast','handleFactoryScale'):
            body=base.extract(source,'bool __attribute__((noinline)) SerialDebug::'+name+'(')
            self.assertNotIn('char details[1024]',body);self.assertNotIn('sizeof(details)',body);self.assertIn('std::memset(details,0,DiagnosticCapacity)',body)
if __name__=='__main__':unittest.main()
