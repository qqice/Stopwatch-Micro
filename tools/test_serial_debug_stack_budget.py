"""Execute real bounded predispatch handlers on RV32; no SDK/device access."""
import hashlib,json,os,struct,subprocess,sys,tempfile,unittest,zlib
from pathlib import Path
import test_mosaico_gauge_cold_console as base
ROOT=base.ROOT
RUN=ROOT/'artifacts/serial-stack-budget-tests'
SOURCE=ROOT/'main/debug/serial_debug.cpp'

SUPPORT=base.SUPPORT.split('static bool forceUartDeny',1)[0]+r'''
static bool forceUartDeny=false,backendOk=true,emitOk=true,otaBusy=false,nandBusy=false;
static unsigned sleepReads=0,formatCalls=0,formatAt=0;static bool formatOversize=false;
namespace MosaicoOta {bool busy(){return otaBusy;}}
static unsigned calls[8]{},resultCount=0,budgetCount=0,hwmCalls=0;
static char resultStatus[8]{},resultDetails[1024]{};
static void copyString(char* out,const char* text,size_t cap){size_t i=0;for(;i+1<cap&&text&&text[i];++i)out[i]=text[i];out[i]=0;}
namespace std {int snprintf(char* out,size_t n,const char*,...){++formatCalls;if(formatAt==formatCalls)return formatOversize?int(n):-1;copyString(out,"mock_export_hwm",n);return 15;}}
unsigned uxTaskGetStackHighWaterMark(void*){++hwmCalls;return 321;}
namespace GaugeDischargeTrace {
namespace NandBackend {bool busy(){return nandBusy;}}
using Emit=void(*)(const char*,void*);
void status(Emit f,void* p){++calls[0];f("mock_status",p);}
void exportRows(Emit f,void* p){++calls[1];f("mock_export",p);}
bool armConfirm(){++calls[2];return backendOk;}bool resumeConfirm(){++calls[3];return backendOk;}
bool stop(){++calls[4];return backendOk;}bool clearConfirm(){++calls[5];return backendOk;}
bool checkpointConfirm(){++calls[6];return backendOk;}
}
#include "mosaico_sleep_io_model.h"
struct Hal {mosaico_sleep_io::Snapshot sleepIoRetentionInfo(){++sleepReads;++calls[7];mosaico_sleep_io::Snapshot s{};s.error=backendOk?0:1;s.pins[0].pin=42;return s;}};Hal& GetHAL(){static Hal h;return h;}
struct SerialDebug {
 void dumpStackEvents(){}
 bool handleSleepIo(char*);
 bool _reply_uart=false;static SerialDebug* _writer;
 bool handleGaugeTrace(char*);bool handleStackBudget(const char*);
 static bool debugTraceLine(const char*){return emitOk;}
 void debugPrintf(const char*,...){++budgetCount;}
 void result(const char*,const char* status,const char* details){++resultCount;copyString(resultStatus,status,sizeof(resultStatus));copyString(resultDetails,details,sizeof(resultDetails));}
};
SerialDebug* SerialDebug::_writer=nullptr;
'''
CHECK=r'''
static int check(const char* input,bool handled,int action,bool uart,bool deny,bool backend,bool emission,bool framed,bool budget,bool fail,bool busy,bool nand,unsigned formatFault,bool oversize){
 forceUartDeny=deny;backendOk=backend;emitOk=emission;otaBusy=busy;nandBusy=nand;sleepReads=formatCalls=0;formatAt=formatFault;formatOversize=oversize;resultCount=budgetCount=hwmCalls=0;for(auto& c:calls)c=0;resultStatus[0]=resultDetails[0]=0;
 char line[512]{},original[512]{};if(input){copyString(line,input,sizeof(line));copyString(original,input,sizeof(original));}
 SerialDebug console;console._reply_uart=uart;SerialDebug::_writer=&console;char* command=input?line:nullptr;
 if(framed&&command)command=serial_debug_transport::decodeUartLine(command,std::strlen(command));
 bool actual=command&&(console.handleStackBudget(command)||console.handleSleepIo(command)||console.handleGaugeTrace(command));
 if(actual!=handled)return 1;
 if(!handled&&std::strcmp(line,original))return 2;
 unsigned total=0;for(unsigned i=0;i<8;++i){total+=calls[i];if(calls[i]!=unsigned(action==int(i)))return 3;}
 if(total!=unsigned(action>=0))return 4;
 if(budgetCount!=unsigned(budget)||hwmCalls!=unsigned(budget||action==1))return 5;
 if(resultCount!=unsigned(handled&&!budget))return 6;
 if(handled&&!budget&&std::strcmp(resultStatus,fail?"FAIL":"PASS"))return 7;
 if(console._reply_uart!=uart||SerialDebug::_writer!=&console)return 8;
 if(busy&&handled&&!budget&&action!=7&&std::strcmp(resultDetails,"reason=ota_busy no_changes=1"))return 9;
 return 0;
}
'''

def vectors():
    cases=[]
    def add(line,action=-1,**k):cases.append(dict(line=line,action=action,**k))
    for line in (None,'','debug','gauge-trace status','debugx gauge-trace status','debug gauge-tracex','DEBUG gauge-trace status','debug stack-budgetx','debug gauge-cold status'):
        add(line,handled=False)
    for prefix in ('debug','dbg',' \tdebug\t'):
        for command,action in (('gauge-trace',0),('gauge-trace status',0),('gauge-trace export',1),('gauge-trace arm CONFIRM',2),('gauge-trace resume CONFIRM',3),('gauge-trace stop',4),('gauge-trace clear CONFIRM',5),('gauge-trace checkpoint CONFIRM',6)):
            add(prefix+' '+command,action)
        add(prefix+' stack-budget',budget=True)
    for command in ('arm','arm confirm','arm CONFIRM extra','resume','resume CONFIRM x','clear','checkpoint','checkpoint CONFIRM x','stop x','status x','export x','unknown','STATUS'):
        add('debug gauge-trace '+command,fail=True)
    for command in ('stack-budget extra','stack-budget status','stack-budget 0'):
        add('debug '+command,fail=True)
    for command,action in (('status',0),('arm CONFIRM',2),('resume CONFIRM',3),('stop',4),('clear CONFIRM',5),('checkpoint CONFIRM',6)):
        add('debug gauge-trace '+command,action,uart=True,framed=True)
        add('debug gauge-trace '+command,uart=True,deny=True,fail=True)
    add('debug gauge-trace export',uart=True,framed=True,fail=True)
    add('debug gauge-trace status',0,emission=False,fail=True)
    add('debug gauge-trace export',1,emission=False,fail=True)
    add('debug gauge-trace stop',4,backend=False,fail=True)
    add('debug stack-budget',uart=True,framed=True,budget=True)
    add('debug stack-budget',uart=True,deny=True,fail=True)
    for command in ('status','export','clear CONFIRM','arm CONFIRM'):
        for uart in (False,True):
            add('debug gauge-trace '+command,uart=uart,framed=uart,busy=True,fail=True)
    add('debug stack-budget',busy=True,budget=True)
    # Invalid CRC and unframed UART must not reach either handler.
    for text in ('uart 00000000 debug stack-budget','debug stack-budget','uart 00000000 debug gauge-trace status'):
        add(text,uart=True,framed=True,handled=False,raw=True)
    for prefix in ('debug','dbg',' \tdebug\t'):
        add(prefix+' sleep-io',7)
    add('debug sleep-io',7,uart=True,framed=True)
    add('debug sleep-io',7,busy=True) # Original readonly branch precedes OTA gate.
    add('debug sleep-io',uart=True,deny=True,fail=True)
    add('debug sleep-io',nand=True,fail=True)
    add('debug sleep-io',7,backend=False,fail=True)
    for command in ('sleep-io status','sleep-io CONFIRM','sleep-io x y'):
        add('debug '+command,fail=True)
    for command in ('debug sleep-iox','debugx sleep-io','sleep-io'):
        add(command,handled=False)
    for at in (1,2,3):
        add('debug sleep-io',7,formatFault=at,fail=True)
        add('debug sleep-io',7,formatFault=at,oversize=True,fail=True)
    for c in cases:
        if c.get('framed') and not c.get('raw'):
            line=c['line'];c['line']=f'uart {zlib.crc32(line.encode()):08x} '+line
    return cases

class StackBudgetTests(unittest.TestCase):
    def test_predispatch_rv32(self):
        RUN.mkdir(parents=True,exist_ok=True);source=SOURCE.read_text()
        handlers='\n'.join(base.extract(source,'bool __attribute__((noinline)) SerialDebug::'+name+'(') for name in ('handleStackBudget','handleSleepIo','handleGaugeTrace'))
        transport=base.TRANSPORT.read_text().replace('inline bool uartAllowed(', 'inline bool productionUartAllowed(')
        cases=vectors();branches=[]
        for i,c in enumerate(cases):
            args=[json.dumps(c['line']) if c['line'] is not None else 'nullptr',str(c.get('handled',True)).lower(),str(c['action'])]
            args += [str(c.get(k,d)).lower() for k,d in (('uart',False),('deny',False),('backend',True),('emission',True),('framed',False),('budget',False),('fail',False),('busy',False),('nand',False),('formatFault',0),('oversize',False))]
            branches.append(f'if(id=={i})return check('+','.join(args)+');')
        driver='extern "C" int run_case(int id){'+''.join(branches)+'return 999;}\n'
        driver+=f'extern "C" {{volatile int test_results[{len(cases)}];void __attribute__((noinline,used)) test_done(){{for(;;){{asm volatile("");}}}}void _start(){{for(int i=0;i<{len(cases)};++i)test_results[i]=run_case(i);test_done();}}}}'
        code='#define MOSAICO_BOARD 1\n#define MOSAICO_NAND_STORE 1\n#define CONFIG_ESP_MAIN_TASK_STACK_SIZE 8192\n'+SUPPORT+transport+'\nnamespace serial_debug_transport {inline bool uartAllowed(const char* c){return !forceUartDeny&&productionUartAllowed(c);}}\n'+handlers+CHECK+driver
        with tempfile.TemporaryDirectory(prefix='trace-stack-',dir=RUN) as td:
            temp=Path(td)
            for name in ('cstdint','cstddef','array','cstring'):(temp/name).write_text(base.HEADERS[name])
            src=temp/'console.cpp';src.write_text(code);elf=RUN/'console.elf'
            cmd=[str(base.CLANG),'--target=riscv32-unknown-elf','-march=rv32im','-mabi=ilp32','-std=c++20','-O1','-fstack-usage','-ffunction-sections','-fdata-sections','-fno-builtin','-fno-exceptions','-fno-rtti','-nostdinc++','-nostdlib','-I'+str(temp),'-I'+str(ROOT/'main/hal/mosaico'),str(src),'--ld-path='+str(base.CLANG.parent/'ld.lld.exe'),'-Wl,--gc-sections','-Wl,-e,_start','-Wl,-Ttext=0x10000','-o',str(elf)]
            result=subprocess.run(cmd,capture_output=True,text=True,env={**os.environ,'TMP':str(temp),'TEMP':str(temp)})
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
        self.assertEqual(emulator.reg_read(UC_RISCV_REG_PC),symbols['test_done'])
        values=struct.unpack('<'+str(len(cases))+'i',bytes(emulator.mem_read(symbols['test_results'],4*len(cases))))
        (RUN/'execution.json').write_text(json.dumps(dict(source_sha256=hashlib.sha256(source.encode()).hexdigest(),cases=[dict(**c,result=values[i]) for i,c in enumerate(cases)],limits='actual extracted handlers; libc/trace/FreeRTOS mocks, not SDK/device validation'),indent=2))
        self.assertEqual(values,(0,)*len(cases),str(RUN/'execution.json'))

    def test_printf_nonmosaico_array_unchanged_rv32(self):
        self.test_printf_shared_scratch_transport_and_overflow_rv32(False)

    def test_printf_shared_scratch_transport_and_overflow_rv32(self,mosaico=True):
        RUN.mkdir(parents=True,exist_ok=True)
        body=base.extract(SOURCE.read_text(),'int SerialDebug::debugPrintf(')
        support=base.SUPPORT.split('static bool forceUartDeny',1)[0]+r'''
using va_list=__builtin_va_list;
#define va_start(a,b) __builtin_va_start(a,b)
#define va_end(a) __builtin_va_end(a)
static unsigned held=0,faults=0,queued=0,printed=0,formats=0,allocAttempts=0,allocSuccess=0;
static bool failAllocation=true;
constexpr int MALLOC_CAP_SPIRAM=0x400,MALLOC_CAP_8BIT=0x4;
static char psram[1536];
void* heap_caps_malloc(size_t n,int caps){if(held!=1||n!=1536||caps!=(MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT))++faults;++allocAttempts;if(failAllocation)return nullptr;++allocSuccess;return psram;}
static int wanted=5;static bool queueOk=true;static char pattern='A';static unsigned shortWrite=0;
static char output[1536]{};void* stdout=nullptr;
namespace std {
struct mutex{void lock(){if(held)++faults;++held;}void unlock(){if(held!=1)++faults;--held;}};
template<class T>struct lock_guard{T& lock;explicit lock_guard(T& x):lock(x){lock.lock();}~lock_guard(){lock.unlock();}};
int vsnprintf(char* out,size_t capacity,const char*,va_list){++formats;if(held!=1||capacity!=1536)++faults;for(size_t i=0;i+1<capacity&&i<size_t(wanted<0?0:wanted);++i)out[i]=pattern;return wanted;}
size_t fwrite(const void* in,size_t unit,size_t count,void*){if(held!=1||unit!=1)++faults;++printed;for(size_t i=0;i<count;++i)output[i]=static_cast<const char*>(in)[i];return shortWrite?shortWrite:count;}
}
struct SerialDebug{static SerialDebug* _writer;bool _reply_uart=false;unsigned _format_overflow=0;
struct Tx{bool enqueue(const char* p,size_t n){if(held!=1)++faults;++queued;for(size_t i=0;i<n;++i)output[i]=p[i];return queueOk;}} _uart_tx;
static int debugPrintf(const char*,...);
};SerialDebug* SerialDebug::_writer=nullptr;
'''
        checks=r'''
extern "C" int run_case(int id){SerialDebug console;SerialDebug::_writer=&console;wanted=5;queueOk=true;shortWrite=0;pattern='A';held=faults=printed=queued=0;for(auto& c:output)c=0;
 if(id==1){console._reply_uart=true;wanted=7;}if(id==2){console._reply_uart=true;queueOk=false;}
 if(id==3)wanted=1536;if(id==4)wanted=1535;if(id==5)wanted=-7;if(id==6)shortWrite=3;if(id==7)SerialDebug::_writer=nullptr;
 #ifdef MOSAICO_BOARD
 if(id==0){int failed=SerialDebug::debugPrintf("mock");if(failed!=-1||held||faults||formats||queued||printed||allocAttempts!=1||allocSuccess||console._format_overflow)return 9;failAllocation=false;}
#endif
 const int result=SerialDebug::debugPrintf("mock_%u",123U);
#ifdef MOSAICO_BOARD
 if(allocAttempts!=2||allocSuccess!=1)return 10;
#else
 if(allocAttempts||allocSuccess)return 11;
#endif
 if(held||faults)return 1;
 if(id==3)return result!=-1||console._format_overflow!=1||queued||printed?2:0;
 if(id==5)return result!=-7||console._format_overflow||queued||printed?3:0;
 if(id==2)return result!=-1||queued!=1||printed?4:0;
 if(result!=(id==6?3:wanted)||queued!=unsigned(console._reply_uart)||printed!=unsigned(!console._reply_uart))return 5;
 for(int i=0;i<wanted;++i)if(output[i]!='A')return 6;
 if(id==0){pattern='B';wanted=3;if(SerialDebug::debugPrintf("mock")!=3||held||faults)return 7;for(unsigned i=0;i<3;++i)if(output[i]!='B')return 8;}
 return 0;}
extern "C" {volatile int test_results[8];void __attribute__((noinline,used)) test_done(){for(;;){asm volatile("");}}void _start(){for(int i=0;i<8;++i)test_results[i]=run_case(i);test_done();}}
'''
        with tempfile.TemporaryDirectory(prefix='printf-stack-',dir=RUN) as td:
            temp=Path(td)
            for name in ('cstdint','cstddef','cstring'):(temp/name).write_text(base.HEADERS[name])
            src=temp/'printf.cpp';src.write_text(('#define MOSAICO_BOARD 1\n' if mosaico else '')+support+body+checks);elf=RUN/('printf.elf' if mosaico else 'printf-nonmosaico.elf')
            cmd=[str(base.CLANG),'--target=riscv32-unknown-elf','-march=rv32im','-mabi=ilp32','-std=c++20','-O1','-fstack-usage','-ffunction-sections','-fdata-sections','-fno-builtin','-fno-exceptions','-fno-rtti','-nostdinc++','-nostdlib','-I'+str(temp),'-I'+str(ROOT/'main/hal/mosaico'),str(src),'--ld-path='+str(base.CLANG.parent/'ld.lld.exe'),'-Wl,--gc-sections','-Wl,-e,_start','-Wl,-Ttext=0x10000','-o',str(elf)]
            result=subprocess.run(cmd,capture_output=True,text=True,env={**os.environ,'TMP':str(temp),'TEMP':str(temp)})
            (RUN/('printf-compile.log' if mosaico else 'printf-nonmosaico-compile.log')).write_text(result.stdout+result.stderr);self.assertEqual(result.returncode,0,str(RUN/'printf-compile.log'))
        sys.path.insert(0,str(ROOT/'.artifacts/tools/unicorn-2.1.4-private-001/package'))
        from unicorn import Uc,UC_ARCH_RISCV,UC_MODE_RISCV32
        from unicorn.riscv_const import UC_RISCV_REG_SP,UC_RISCV_REG_PC
        data=elf.read_bytes();symbols=base.elf_symbols(data);emulator=Uc(UC_ARCH_RISCV,UC_MODE_RISCV32);emulator.mem_map(0,32*1024*1024)
        offset=struct.unpack_from('<I',data,28)[0];size,count=struct.unpack_from('<HH',data,42)
        for i in range(count):
            kind,position,address,_,length,_,_,_=struct.unpack_from('<8I',data,offset+i*size)
            if kind==1:emulator.mem_write(address,data[position:position+length])
        emulator.reg_write(UC_RISCV_REG_SP,0x1fffff0);emulator.emu_start(struct.unpack_from('<I',data,24)[0],symbols['test_done'],timeout=20000000,count=100000000)
        self.assertEqual(emulator.reg_read(UC_RISCV_REG_PC),symbols['test_done'])
        values=struct.unpack('<8i',bytes(emulator.mem_read(symbols['test_results'],32)))
        (RUN/('printf-execution.json' if mosaico else 'printf-nonmosaico-execution.json')).write_text(json.dumps(dict(results=values,limits='real debugPrintf with pure formatter/transport/lock ownership mocks; no concurrency or libc/device certification'),indent=2))
        self.assertEqual(values,(0,)*8)

    def test_attribution_ring_actual_helpers_rv32(self):
        source=SOURCE.read_text();helpers=source[source.index('enum class ConsoleStackTag:'):].split('#else\n// Preserve other boards',1)[0]
        dump=base.extract(source,'void __attribute__((noinline)) SerialDebug::dumpStackEvents(')
        code='#define MOSAICO_BOARD 1\n'+base.SUPPORT.split('static bool forceUartDeny',1)[0]+r'''
using TaskHandle_t=const void*;static int ownerTag,foreignTag;static TaskHandle_t currentTask=&ownerTag;
TaskHandle_t xTaskGetCurrentTaskHandle(){return currentTask;}
static uint32_t currentHwm=4000;static unsigned allocations=0,capsFault=0,outputs=0;static bool oom=false;
uint32_t uxTaskGetStackHighWaterMark(void*){return currentHwm;}
constexpr int MALLOC_CAP_SPIRAM=0x400,MALLOC_CAP_8BIT=4;
void* heap_caps_calloc(size_t,size_t,int);
void* operator new(size_t,void* memory){return memory;}
struct SerialDebug{void dumpStackEvents();void debugPrintf(const char*,...){++outputs;}};
'''+helpers+r'''
static_assert(sizeof(ConsoleStackState)<=512);
alignas(ConsoleStackState) static unsigned char storage[sizeof(ConsoleStackState)];
void* heap_caps_calloc(size_t n,size_t size,int caps){++allocations;if(n!=1||size!=sizeof(ConsoleStackState)||caps!=(MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT))++capsFault;return oom?nullptr:storage;}
'''+dump+r'''
extern "C" int run_case(int id){consoleStack=nullptr;consoleStackAttempted=false;allocations=capsFault=outputs=0;oom=id==2;currentTask=&ownerTag;currentHwm=4000;
 consoleStackObserve(ConsoleStackTag::Outside,0,false);
 if(id==2){oom=false;consoleStackObserve(ConsoleStackTag::Cold,1,true);return consoleStack||allocations!=1||capsFault?1:0;}
 if(!consoleStack||allocations!=1||capsFault||consoleStack->count)return 2;
 if(id==0){currentHwm=3900;consoleStackObserve(ConsoleStackTag::Outside,0,false);currentHwm=3800;consoleStackObserve(ConsoleStackTag::Status,1,true);currentHwm=3700;consoleStackObserve(ConsoleStackTag::Status,2,true);return consoleStack->count!=3||consoleStack->events[0].tag!=ConsoleStackTag::Outside||consoleStack->events[1].stage!=1||consoleStack->events[2].stage!=2||consoleStack->events[2].before!=3800||consoleStack->events[2].after!=3700||!consoleStack->events[2].uart?3:0;}
 if(id==1){for(unsigned i=0;i<40;++i){currentHwm-=10;consoleStackObserve(ConsoleStackTag::Other,2,false);}if(consoleStack->count!=16||consoleStack->sequence!=40||consoleStack->events[consoleStack->head].sequence!=25)return 4;auto before=*consoleStack;SerialDebug console;console.dumpStackEvents();return outputs!=17||memcmp(&before,consoleStack,sizeof(before))||allocations!=1?5:0;}
 if(id==3){currentTask=&foreignTag;currentHwm=3500;consoleStackObserve(ConsoleStackTag::Other,1,true);if(consoleStack->count||consoleStack->last!=4000)return 6;currentTask=&ownerTag;consoleStackObserve(ConsoleStackTag::Network,2,false);return consoleStack->count!=1||consoleStack->events[0].tag!=ConsoleStackTag::Network?7:0;}
 if(id==4){currentHwm=3800;consoleStackObserve(ConsoleStackTag::Cold,2,false);const auto seq=consoleStack->sequence;currentHwm=3000;consoleStackObserve(ConsoleStackTag::Budget,2,false);consoleStackObserve(ConsoleStackTag::Outside,0,false);if(consoleStack->count!=2||consoleStack->sequence!=seq+1||consoleStack->events[1].tag!=ConsoleStackTag::Budget||consoleStack->last!=3000||currentHwm!=3000)return 8;outputs=0;SerialDebug console;console.dumpStackEvents();consoleStackObserve(ConsoleStackTag::Budget,2,false);return outputs!=3||consoleStack->count!=2||consoleStack->sequence!=seq+1?12:0;}
 if(id==5){char line[]="debug secretssid password_hidden";char original[sizeof(line)]{};memcpy(original,line,sizeof(line));if(consoleStackCommand(line)!=ConsoleStackTag::Other||memcmp(line,original,sizeof(line)))return 9;if(consoleStackCommand("debugx gauge-cold")!=ConsoleStackTag::Outside||consoleStackCommand("dbg")!=ConsoleStackTag::Outside||consoleStackCommand("debug stack-budgetx")!=ConsoleStackTag::Other||consoleStackCommand(" \tdebug\t sleep-io CONFIRM")!=ConsoleStackTag::SleepIo)return 10;for(unsigned i=0;i<=unsigned(ConsoleStackTag::Settings);++i)if(std::strlen(consoleStackName(static_cast<ConsoleStackTag>(i)))>20)return 11;return 0;}
 return 0;}
extern "C" {volatile int test_results[6];void __attribute__((noinline,used)) test_done(){for(;;){asm volatile("");}}void _start(){for(int i=0;i<6;++i)test_results[i]=run_case(i);test_done();}}
'''
        with tempfile.TemporaryDirectory(prefix='attribute-stack-',dir=RUN) as td:
            temp=Path(td)
            for name in ('cstdint','cstddef','cstring'):(temp/name).write_text(base.HEADERS[name])
            src=temp/'attribute.cpp';src.write_text(code);elf=RUN/'attribute.elf'
            cmd=[str(base.CLANG),'--target=riscv32-unknown-elf','-march=rv32im','-mabi=ilp32','-std=c++20','-O1','-fstack-usage','-ffunction-sections','-fdata-sections','-fno-builtin','-fno-exceptions','-fno-rtti','-nostdinc++','-nostdlib','-I'+str(temp),str(src),'--ld-path='+str(base.CLANG.parent/'ld.lld.exe'),'-Wl,--gc-sections','-Wl,-e,_start','-Wl,-Ttext=0x10000','-o',str(elf)]
            result=subprocess.run(cmd,capture_output=True,text=True,env={**os.environ,'TMP':str(temp),'TEMP':str(temp)})
            (RUN/'attribute-compile.log').write_text(result.stdout+result.stderr);self.assertEqual(result.returncode,0,str(RUN/'attribute-compile.log'))
        sys.path.insert(0,str(ROOT/'.artifacts/tools/unicorn-2.1.4-private-001/package'))
        from unicorn import Uc,UC_ARCH_RISCV,UC_MODE_RISCV32
        from unicorn.riscv_const import UC_RISCV_REG_SP,UC_RISCV_REG_PC
        data=elf.read_bytes();symbols=base.elf_symbols(data);emulator=Uc(UC_ARCH_RISCV,UC_MODE_RISCV32);emulator.mem_map(0,32*1024*1024)
        offset=struct.unpack_from('<I',data,28)[0];size,count=struct.unpack_from('<HH',data,42)
        for i in range(count):
            kind,position,address,_,length,_,_,_=struct.unpack_from('<8I',data,offset+i*size)
            if kind==1:emulator.mem_write(address,data[position:position+length])
        emulator.reg_write(UC_RISCV_REG_SP,0x1fffff0);emulator.emu_start(struct.unpack_from('<I',data,24)[0],symbols['test_done'],timeout=20000000,count=100000000)
        self.assertEqual(emulator.reg_read(UC_RISCV_REG_PC),symbols['test_done'])
        values=struct.unpack('<6i',bytes(emulator.mem_read(symbols['test_results'],24)))
        (RUN/'attribute-execution.json').write_text(json.dumps(dict(results=values,limits='actual PSRAM attribution helper/ring/dump; mocked allocator/task/RTOS HWM, no hardware measurement'),indent=2))
        self.assertEqual(values,(0,)*6)

    def test_source_stack_boundaries_and_order(self):
        source=SOURCE.read_text();poll=base.extract(source,'void SerialDebug::poll(');async_body=base.extract(source,'void __attribute__((noinline)) SerialDebug::pollAsync(')
        self.assertNotIn('details[1000]',poll);self.assertIn('pollAsync();',poll)
        for token in ('_writer = this; _reply_uart = _async_uart','char details[1000]','updateAsyncTest();','popTwtCycle(cycle)','_reply_uart = false; _writer = previous'):
            self.assertIn(token,async_body)
        self.assertLess(async_body.index('TouchSleep::snapshot()'),async_body.index('updateAsyncTest();'));self.assertLess(async_body.index('updateAsyncTest();'),async_body.index('popTwtCycle(cycle)'))
        consume=base.extract(source,'void SerialDebug::consume(')
        self.assertLess(consume.index('decodeUartLine'),consume.index('handleStackBudget(command)'));self.assertLess(consume.index('handleGaugeTrace(command)'),consume.index('handleLine(command)'))
        main=source.split('void SerialDebug::handleLine(',1)[1].split('void SerialDebug::printHelp(',1)[0];self.assertNotIn('GaugeDischargeTrace::exportRows',main);self.assertNotIn('char details[1492]',main)
        self.assertLess(consume.index('decodeUartLine'),consume.index('consoleStackCommand(command)'));self.assertLess(consume.index('handleSleepIo(command)'),consume.index('handleLine(command)'))
        budget=base.extract(source,'bool __attribute__((noinline)) SerialDebug::handleStackBudget(')
        for forbidden in ('GaugeDischargeTrace','Nand','GetHAL','setTemporary','char details') :self.assertNotIn(forbidden,budget)
        printf=base.extract(source,'int SerialDebug::debugPrintf(')
        self.assertIn('static char bytes[Capacity]',printf);self.assertIn('heap_caps_malloc(Capacity,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT)',printf);self.assertNotIn('sizeof(bytes)',printf);self.assertLess(printf.index('std::lock_guard'),printf.index('std::vsnprintf'));self.assertLess(printf.index('std::lock_guard'),printf.index('std::fwrite'))
        self.assertLess(budget.index('dumpStackEvents();'),budget.index('DBG RESULT command=stack-budget'));self.assertIn('CONFIG_ESP_MAIN_TASK_STACK_SIZE',budget);self.assertIn('cumulative_current_task',budget)

if __name__=='__main__':unittest.main()
