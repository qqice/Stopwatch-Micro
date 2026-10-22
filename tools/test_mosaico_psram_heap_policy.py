"""Actual quarantine wrapper on RV32A; mocked linker address/SDK delegate only.
No firmware build, target device, SDK modification or target-memory reads.
"""
from pathlib import Path
import inspect,re,unittest,ast
import test_microlink_peer_probe as rv32
import test_mosaico_ota_health_startup as health
import test_serial_debug_ota_health as parser
ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/'.artifacts/private/mosaico/psram-heap-policy-amo-tests-001'

def strip(s):return re.sub(r'^\s*#(?:include|pragma)[^\n]*','',s,flags=re.M)
def fixture():
    h=strip((ROOT/'main/hal/mosaico/mosaico_psram_heap_policy.h').read_text())
    cpp=strip((ROOT/'main/hal/mosaico/mosaico_psram_heap_policy.cpp').read_text())
    # Geometry under test is exact production arithmetic; only linker symbol
    # address is injectable to exercise overflow/empty boundaries.
    cpp=cpp.replace('static_cast<uint32_t>(reinterpret_cast<uintptr_t>(&_rodata_reserved_end))','0x4c9ac000u')
    cpp=cpp.replace('static_cast<uint32_t>(reinterpret_cast<uintptr_t>(&_instruction_reserved_end))','testAddress')
    return r'''
#define MOSAICO_BOARD 1
#define CONFIG_IDF_TARGET_ESP32S31 1
#define CONFIG_IDF_TARGET_ARCH_RISCV 1
#define SOC_MMU_DI_VADDR_SHARED 1
#define CONFIG_SPIRAM_FETCH_INSTRUCTIONS 1
#define CONFIG_SPIRAM_PRE_CONFIGURE_MEMORY_PROTECTION 1
#define CONFIG_SPIRAM_RODATA 1
#define CONFIG_SPIRAM_XIP_FROM_PSRAM 1
#define CONFIG_SPIRAM_SPEED 200
#define CONFIG_MMU_PAGE_SIZE 65536
#define SOC_MEMORY_TYPE_NO_PRIOS 3
#define DRAM_ATTR
#define IRAM_ATTR
#define UINT32_MAX 0xffffffffu
#define MALLOC_CAP_SPIRAM 1024
#define MALLOC_CAP_DEFAULT 4096
#define MALLOC_CAP_8BIT 4
#define MALLOC_CAP_32BIT 2
#define MALLOC_CAP_SIMD 40960
#define ESP_OK 0
#define ESP_ERR_INVALID_ARG 258
#define ESP_ERR_INVALID_STATE 259
typedef unsigned uint32_t;typedef unsigned char uint8_t;typedef int int32_t;typedef int intptr_t;typedef unsigned uintptr_t;typedef int esp_err_t;
static uint32_t testAddress;static int calls,returnCode;static const uint32_t* actualCaps;static intptr_t actualStart,actualEnd;
extern "C" esp_err_t __real_heap_caps_add_region_with_caps(const uint32_t* c,intptr_t s,intptr_t e){++calls;actualCaps=c;actualStart=s;actualEnd=e;return returnCode;}
'''+h+cpp+r'''
static uint32_t caps[3]={MALLOC_CAP_SPIRAM|MALLOC_CAP_DEFAULT,0,MALLOC_CAP_8BIT|MALLOC_CAP_32BIT|MALLOC_CAP_SIMD};
static void reset(){skipped=forwarded=failed=faults=recordCount=completed=0;for(auto& r:records)r.ready=0;testAddress=0x4c8ac12b;calls=returnCode=0;}
static int add(const uint32_t* c=caps,intptr_t s=0x4c8ac12c,intptr_t e=0x4c8b0000){return __wrap_heap_caps_add_region_with_caps(c,s,e);}
static int run(int id){reset();MosaicoPsramHeapPolicyDiagnostic d;MosaicoPsramHeapPolicyRecord r;
 if(id==0){if(add()||calls||!MosaicoPsramHeapPolicyHealthy())return 1;MosaicoPsramHeapPolicySnapshot(d);if(d.bytes!=16084||d.skipped!=1)return 2;return !MosaicoPsramHeapPolicyReadRecord(0,r)||r.action!=1||r.result||r.start!=0x4c8ac12c?3:0;}
 if(id>=1&&id<=4){uint32_t wrong[3]={caps[0],caps[1],caps[2]};if(id<=3)wrong[id-1]^=1;int ret=add(id==4?nullptr:wrong);return ret==258&&!calls&&!MosaicoPsramHeapPolicyHealthy()&&faults==2?0:4;}
 if(id==5){if(add()||add()!=259||calls||MosaicoPsramHeapPolicyHealthy()||faults!=4)return 5;return 0;}
 if(id==6||id==7){int s=id==6?0x4c8ac128:0x4c8ac130;returnCode=id==7?277:0;int ret=add(caps,s);if(ret!=returnCode||calls!=1||actualCaps!=caps||actualStart!=s||actualEnd!=0x4c8b0000)return 6;return id==7?(faults==8&&failed==1?0:7):(!faults&&forwarded==1?0:8);}
 if(id==8||id==9||id==10){testAddress=id==8?0xfffffff0u:id==9?0x4c8b0000:0x4c8affff;return add()==259&&!calls&&!MosaicoPsramHeapPolicyHealthy()?0:9;}
 if(id==11){return !MosaicoPsramHeapPolicyHealthy()&&!calls?0:10;}
 if(id==12){add();for(int i=0;i<15;++i)if(add(caps,0x4d000000,0x4d010000))return 11;if(add(caps,0x4d000000,0x4d010000)!=0||calls!=16||!(faults&16)||MosaicoPsramHeapPolicyHealthy())return 12;return MosaicoPsramHeapPolicyReadRecord(0,r)&&r.action==1?0:13;}
 if(id==13){add();add(caps,0x4d000000,0x4d010000);add(caps,0x4c9ac000,0x4c9b0000);if(!MosaicoPsramHeapPolicyHealthy()||forwarded!=2)return 14;for(unsigned i=1;i<3;++i)if(!MosaicoPsramHeapPolicyReadRecord(i,r)||r.action||r.result||r.caps[2]!=caps[2])return 15;return 0;}
 if(id==14){returnCode=278;if(add(nullptr,1,3)!=278||actualCaps||calls!=1||!faults)return 16;return 0;}
 if(id==16){skipped=1;recordCount=1;return !MosaicoPsramHeapPolicyHealthy()?0:18;}
 if(id==17){add();for(int i=0;i<15;++i)add(caps,1,3);return add()==259&&calls==15&&!MosaicoPsramHeapPolicyHealthy()?0:19;}
 if(id==15){forwarded=UINT32_MAX;add(caps,1,3);return faults&16?0:17;}
 return 99;}
extern "C" {volatile int test_results[18]={};}
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
extern "C" void _start(){for(int i=0;i<18;++i)test_results[i]=run(i);test_done();}
'''

def execute(text,label,count):
    old_fixture,old_run=rv32.fixture,rv32.RUN
    old_execute=rv32.execute
    # Actual atomic intrinsics compile to RV32A, not mocked synchronization.
    namespace=dict(rv32.__dict__)
    code=inspect.getsource(rv32.execute).replace("'-march=rv32im'","'-march=rv32ima'")
    if 'testAtomicAddress' in text:
        # LLVM currently lowers weak CAS to an internal strong LR/SC loop.
        # Match the installed IDF native compiler for the atomic regression.
        old_args=next(line for line in code.splitlines() if line.strip().startswith('args='))
        gcc='C:/Espressif/tools/riscv32-esp-elf/esp-15.2.0_20251204/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'
        code=code.replace(old_args,"    args=["+repr(gcc)+",'-march=rv32ima','-mabi=ilp32','-O1','-msmall-data-limit=0','-fno-builtin','-fno-exceptions','-fno-rtti','-nostdinc++','-nostdlib',str(cpp),'-Wl,--no-relax','-Wl,-e,_start','-Wl,-Ttext=0x10000','-o',str(elf)]")
    # Fail every actual SC.W if any remains. At an AMOADD record reservation,
    # optionally inject a competing final-slot owner after the precheck.
    hook=r"""
    if 'testAtomicAddress' in symbols:
        from unicorn import UC_HOOK_CODE
        from unicorn.riscv_const import UC_RISCV_REG_X0
        injection={'failed_sc':0,'swap':0,'races':0}
        def inject_atomic_race(machine,pc,size,_):
            if size!=4:return
            word=struct.unpack('<I',bytes(machine.mem_read(pc,4)))[0]
            operation=word&0xf800707f
            if operation==0x0800202f:injection['swap']+=1
            if operation==0x1800202f:
                injection['failed_sc']+=1
                rd=(word>>7)&31
                if rd:machine.reg_write(UC_RISCV_REG_X0+rd,1)
                machine.reg_write(UC_RISCV_REG_PC,pc+4)
            if operation!=0x0000202f:return
            race=struct.unpack('<I',bytes(machine.mem_read(symbols['testInjectRace'],4)))[0]
            target=struct.unpack('<I',bytes(machine.mem_read(symbols['testAtomicAddress'],4)))[0]
            rs1=(word>>15)&31
            if race and target and machine.reg_read(UC_RISCV_REG_X0+rs1)==target:
                machine.mem_write(target,struct.pack('<I',16))
                machine.mem_write(symbols['testInjectRace'],struct.pack('<I',0))
                injection['races']+=1
        uc.hook_add(UC_HOOK_CODE,inject_atomic_race)
"""
    code=code.replace('    uc.reg_write(UC_RISCV_REG_SP',hook+'    uc.reg_write(UC_RISCV_REG_SP',1)
    code=code.replace("    values=struct.unpack(","    if 'testAtomicAddress' in symbols:\n        (RUN/(label+'-atomic-injection.log')).write_text(str(injection)+'\\n')\n        assert injection=={'failed_sc':0,'swap':3,'races':2},injection\n    values=struct.unpack(",1)
    exec(code,namespace)
    try:
        rv32.fixture=lambda _:text;rv32.RUN=RUN
        namespace.update(fixture=rv32.fixture,RUN=RUN)
        return namespace['execute']('',label,count)
    finally:rv32.fixture,rv32.RUN=old_fixture,old_run

class PolicyTests(unittest.TestCase):
    def test_actual_wrapper_18_scenarios(self):self.assertEqual(execute(fixture(),'wrapper',18),(0,)*18)
    def test_actual_amo_operations_and_racing_final_slot(self):
        text=fixture().split('extern "C" {volatile int test_results[18]',1)[0]
        text+=r"""
extern "C" {volatile unsigned testAtomicAddress=0,testInjectRace=0;volatile int test_results[4]={};}
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
extern "C" void _start(){
 reset();int ret=add();test_results[0]=ret==0&&MosaicoPsramHeapPolicyHealthy()&&!calls?0:1;
 reset();recordCount=15;testAtomicAddress=reinterpret_cast<uintptr_t>(&recordCount);testInjectRace=1;
 ret=add(caps,1,3);test_results[1]=ret==0&&calls==1&&recordCount==17&&!forwarded&&!completed&&faults==16?0:2;
 for(auto& r:records)if(r.ready)test_results[1]=3;
 reset();recordCount=15;testAtomicAddress=reinterpret_cast<uintptr_t>(&recordCount);testInjectRace=1;
 ret=add();test_results[2]=ret==259&&!calls&&recordCount==17&&!skipped&&!completed&&faults==16?0:4;
 for(auto& r:records)if(r.ready)test_results[2]=5;
 reset();testAtomicAddress=0;ret=add();int duplicate=add();
 test_results[3]=ret==0&&duplicate==259&&skipped==1&&faults==4&&!calls?0:6;
 test_done();}
"""
        self.assertEqual(execute(text,'amo-final-slot-race',4),(0,0,0,0))
    def test_actual_pending_health_refuses_mark_valid(self):
        source=health.health_fixture().split('static void reset(){',1)[0]
        reset=rv32.function(health.CASES,'static void reset(')
        cases=r'''
extern "C" {volatile int test_results[2]={};}
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
extern "C" void _start(){for(int mode=0;mode<2;++mode){reset();policyHealthy=mode;MosaicoOta::healthPoll(false);for(int i=0;i<25;++i){probeOrder=0;poll(2000000+int64_t(i)*1000000,true);}test_results[mode]=mode?(markCalls==1?0:1):(!markCalls&&MosaicoOta::bootPending.load()&&timerStarts==1&&integrityCalls?0:2);}test_done();}
'''
        self.assertEqual(execute(source+reset+rv32.function(health.CASES,'static void poll(')+cases,'health-gate',2),(0,0))
    def test_actual_heap_policy_fast_readonly_parser(self):
        prefix=parser.fixture().split('static int run_case(',1)[0]
        original=rv32.function(prefix,' static __attribute__((noinline)) int debugPrintf(')
        prefix=prefix.replace(original,'static __attribute__((noinline)) int debugPrintf(const char*,...){++formats;return 1;}')
        prefix=prefix.replace('out={};','++copies;out={};')
        cases=r"""
extern "C" {volatile int test_results[6]={};}
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
extern "C" void _start(){for(int i=0;i<6;++i){copies=formats=passes=failures=fullCalls=0;SerialDebug d;d._reply_uart=i==1||i==4;
 char good[]="debug heap-policy";char spaced[]="  dbg\theap-policy  ";char extra[]="debug heap-policy extra";char unknown[]="debug heap-policy-more";
 bool handled=d.handleCachedRead(i==2?spaced:i==3||i==4?extra:i==5?unknown:good);
 test_results[i]=i<=2?(handled&&copies==1&&formats==1&&passes==1&&!failures?0:1):i<=4?(handled&&!copies&&!formats&&!passes&&failures==1?0:2):(!handled&&!copies&&!formats&&!passes&&!failures?0:3);
 }test_done();}
"""
        self.assertEqual(execute(prefix+cases,'heap-parser',6),(0,)*6)
    def test_query_wire_output_upper_bound(self):
        src=(ROOT/'main/debug/serial_debug.cpp').read_text()
        formats=re.findall(r'debugPrintf\(("DBG HEAP_POLICY[^"\n]*")',src)
        self.assertEqual(len(formats),2)
        sizes=[]
        for literal in formats:
            fmt=ast.literal_eval(literal).replace('%lu','%u').replace('%lx','%x').replace('%ld','%d')
            values=tuple(0xffffffff for _ in re.findall(r'%[udx]',fmt))
            size=len(fmt%values)
            self.assertLess(size,1536);sizes.append(size)
        # Conservative result footer reservation; UART ring is fixed 8192B.
        self.assertLess(sizes[0]+16*sizes[1]+256,8192)
    def test_source_boundaries(self):
        source=(ROOT/'main/hal/mosaico/mosaico_psram_heap_policy.cpp').read_text()
        wrapper=rv32.function(source,'extern "C" esp_err_t IRAM_ATTR __wrap_heap_caps_add_region_with_caps(')
        for bad in ('malloc(', 'printf(', 'memcpy(', 'memset(', 'esp_psram_','mmu_'):self.assertNotIn(bad,source)
        self.assertIn('__atomic_always_lock_free',source)
        self.assertNotIn('__atomic_compare_exchange',source)
        self.assertIn('DRAM_ATTR Record records',source)
        self.assertIn('if(reserved)count(&forwarded)',wrapper)
        ota=(ROOT/'main/ota/mosaico_ota.cpp').read_text()
        self.assertEqual(ota.count('MosaicoPsramHeapPolicyHealthy()'),1)
        self.assertIn('integrityHealthy() && MosaicoPsramHeapPolicyHealthy()',ota)
        self.assertNotIn('MosaicoPsramHeapPolicy',rv32.function(ota,'bool currentReady('))

if __name__=='__main__':unittest.main()
