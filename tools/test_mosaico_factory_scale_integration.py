"""Factory-scale root integration. Production parser/consume/provider on RV32; no hardware."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,os,re,struct,subprocess,sys,tempfile,unittest
import test_mosaico_gauge_scale_backend as base
import test_mosaico_maintenance_power as maintenance
import test_microlink_peer_probe as rv32
ROOT=base.ROOT
RUN=ROOT/'.artifacts/private/mosaico/factory-scale-integration-tests'/datetime.now(timezone.utc).strftime('run-%Y%m%dT%H%M%S%fZ')

def execute(test,code,label,count):
 RUN.mkdir(parents=True,exist_ok=True)
 with tempfile.TemporaryDirectory(prefix='.factory-integration-',dir=ROOT) as td:
  td=Path(td)
  for name,text in base.HEADERS.items():(td/name).write_text(text)
  src=td/(label+'.cpp');src.write_text(code);elf=RUN/(label+'.elf')
  args=[str(base.CLANG),'--target=riscv32-unknown-elf','-march=rv32im','-mabi=ilp32','-std=c++20','-O1','-Wall','-Wextra','-Werror','-Wno-missing-multilib','-Wno-unused-function','-fstack-usage','-ffunction-sections','-fdata-sections','-fno-builtin','-fno-exceptions','-fno-rtti','-nostdinc++','-nostdlib','-I'+str(td),str(src),'--ld-path='+str(base.CLANG.parent/'ld.lld.exe'),'-Wl,--gc-sections','-Wl,-e,_start','-Wl,-Ttext=0x10000','-o',str(elf)]
  result=subprocess.run(args,capture_output=True,text=True,env={**os.environ,'TEMP':str(td),'TMP':str(td)});(RUN/(label+'-compile.log')).write_text(result.stdout+result.stderr);test.assertEqual(result.returncode,0,str(RUN/(label+'-compile.log')))
  stack=elf.with_suffix('.su').read_text();(RUN/(label+'-stack.log')).write_text(stack)
  helper=[v for v in stack.splitlines() if 'handleFactoryScale' in v];test.assertTrue(helper)
  for v in helper:test.assertLessEqual(int(v.split('\t')[1]),1200,v)
 raw=elf.read_bytes();sys.path.insert(0,str(ROOT/'.artifacts/tools/unicorn-2.1.4-private-001/package'))
 from unicorn import Uc,UC_ARCH_RISCV,UC_MODE_RISCV32
 from unicorn.riscv_const import UC_RISCV_REG_SP,UC_RISCV_REG_PC
 uc=Uc(UC_ARCH_RISCV,UC_MODE_RISCV32);uc.mem_map(0,32*1024*1024);entry=struct.unpack_from('<I',raw,24)[0];phoff=struct.unpack_from('<I',raw,28)[0];phsize,phnum=struct.unpack_from('<HH',raw,42)
 for i in range(phnum):
  kind,off,addr,_,n,_,_,_=struct.unpack_from('<8I',raw,phoff+i*phsize)
  if kind==1:uc.mem_write(addr,raw[off:off+n])
 shoff=struct.unpack_from('<I',raw,32)[0];shsize,shnum=struct.unpack_from('<HH',raw,46);symbols={}
 for i in range(shnum):
  sh=struct.unpack_from('<10I',raw,shoff+i*shsize)
  if sh[1]!=2:continue
  strings=struct.unpack_from('<10I',raw,shoff+sh[6]*shsize);text=raw[strings[4]:strings[4]+strings[5]]
  for pos in range(sh[4],sh[4]+sh[5],sh[9]):
   name,value,_,_,_,_=struct.unpack_from('<IIIBBH',raw,pos);symbols[text[name:].split(b'\0',1)[0].decode()]=value
 uc.reg_write(UC_RISCV_REG_SP,0x1fffff0);uc.emu_start(entry,symbols['test_done'],timeout=20000000,count=150000000)
 values=struct.unpack('<'+'i'*count,bytes(uc.mem_read(symbols['test_results'],4*count)))
 (RUN/(label+'-execution.log')).write_text('Production extracted parser+consume+CRC; HAL/authorization/NAND mocks.\n'+f'ELF_SHA256={hashlib.sha256(raw).hexdigest()}\n'+''.join(f'case={i} result={v}\n' for i,v in enumerate(values)))
 test.assertEqual(uc.reg_read(UC_RISCV_REG_PC),symbols['test_done']);test.assertEqual(values,(0,)*count,str(RUN/(label+'-execution.log')))

PARSER_PREFIX=r'''
#define MOSAICO_BOARD
#define MOSAICO_NAND_STORE
#include <array>
#include <cstdint>
#include <cstddef>
#include <cstring>
#include <cstdio>
namespace std {template<class T>void fill_n(T* p,size_t n,int v){for(size_t i=0;i<n;++i)p[i]=v;}}
static int errno;static int halCalls=0,actionSeen=-1,pass=0,results=0,cachedCalls=0,legacyCalls=0,authCalls=0,nandCalls=0;static unsigned indexSeen=0;static bool allowUart=true,nandBusy=false;
namespace std {unsigned long strtoul(const char* s,char** end,int){unsigned long v=0;const char* p=s;for(;*p>='0'&&*p<='9';++p){unsigned d=unsigned(*p-'0');if(v>(0xffffffffU-d)/10)errno=1;v=v*10+d;}*end=const_cast<char*>(p);return v;}}
char* strtok_r(char* value,const char*,char** save){char* p=value?value:*save;if(!p)return nullptr;while(*p==' '||*p=='\t')++p;if(!*p){*save=p;return nullptr;}char* begin=p;while(*p&&*p!=' '&&*p!='\t')++p;if(*p)*p++=0;*save=p;return begin;}
struct Hal {__attribute__((noinline)) bool call(int action,char* out,size_t n){asm volatile(""::"r"(out),"r"(n):"memory");++halCalls;actionSeen=action;return true;}bool gaugeFactoryScaleStatus(char* p,size_t n){return call(0,p,n);}bool gaugeFactoryScalePrepare(char* p,size_t n){return call(1,p,n);}bool gaugeFactoryScaleImages(const char* proof,char* p,size_t n){return std::strlen(proof)<=1400&&call(2,p,n);}bool gaugeFactoryScaleApply(char* p,size_t n){return call(3,p,n);}bool gaugeFactoryScaleRestore(char* p,size_t n){return call(4,p,n);}bool gaugeFactoryScaleObservedField(size_t i,char* p,size_t n){indexSeen=i;return call(5,p,n);}bool gaugeFactoryScaleObservedPage(size_t i,char* p,size_t n){indexSeen=i;return call(6,p,n);}};
static Hal hal;Hal& GetHAL(){return hal;}
namespace GaugeDischargeTrace{struct NandBackend{static bool busy(){++nandCalls;return nandBusy;}};}
enum class ConsoleStackTag{Outside};ConsoleStackTag consoleStackCommand(const char*){return ConsoleStackTag::Outside;}
void consoleStackObserve(ConsoleStackTag,unsigned,bool){}
struct SerialDebug{
 bool _reply_uart=false;std::array<char,1536> _line{},_uart_line{};size_t _line_length=0,_uart_line_length=0;bool _line_overflow=false,_uart_line_overflow=false;
 bool handleContrast(char*){return false;} // separately exercised by contrast integration
 bool handleCold(char*){return false;} // separately exercised by cold console integration
 bool handleFactoryScale(char*);void consume(char);
 static constexpr size_t DiagnosticCapacity=1024;
 char* diagnosticScratch(){static char buffer[DiagnosticCapacity];return buffer;}
 bool handleStackBudget(const char*){return false;}bool handleSleepIo(const char*){return false;}bool handleGaugeTrace(const char*){return false;}
 bool handleMaintenanceRestart(const char*){return false;}
 bool handleCachedRead(const char* line){++cachedCalls;return !std::strcmp(line,"debug ota-health");}
 void handleLine(char*){++legacyCalls;}
 void result(const char*,const char* status,const char* =nullptr){++results;pass=!std::strcmp(status,"PASS");}
};
'''
PARSER_RUNNER=r'''
static SerialDebug serial;
static void reset(){halCalls=pass=results=cachedCalls=legacyCalls=authCalls=nandCalls=0;actionSeen=-1;indexSeen=999;allowUart=true;nandBusy=false;serial={};}
static int direct(const char* text,int action,bool uart=false){reset();serial._reply_uart=uart;char buf[1536]{},old[1536]{};std::memcpy(buf,text,std::strlen(text)+1);std::memcpy(old,buf,sizeof(buf));bool used=serial.handleFactoryScale(buf);
 if(action==-2)return used||std::memcmp(old,buf,sizeof(buf))||halCalls||results?1:0;
 if(!used)return 2;if(action<0)return halCalls||pass?3:0;
 return halCalls!=1||actionSeen!=action||!pass?4:0;}
static void feed(const char* text,bool uart,bool validCrc=true){serial._reply_uart=uart;if(uart){char frame[1536]{};std::snprintf(frame,sizeof(frame),"uart %08lx %s",static_cast<unsigned long>(serial_debug_transport::crc32(text,std::strlen(text))^(validCrc?0:1)),text);for(const char* p=frame;*p;++p)serial.consume(*p);}else for(const char* p=text;*p;++p)serial.consume(*p);serial.consume('\n');}
static int run_case(int id){
 const char* text[]={"debug gauge-factory-scale","debug gauge-factory-scale status","dbg gauge-factory-scale prepare CONFIRM","debug gauge-factory-scale apply CONFIRM_EXTERNAL_POWER","debug gauge-factory-scale restore CONFIRM","debug gauge-factory-scale observed 0","debug gauge-factory-scale observed 130","debug gauge-factory-scale images eA==","debug gauge-factory-scale status extra","debug gauge-factory-scale prepare","debug gauge-factory-scale prepare confirm","debug gauge-factory-scale prepare CONFIRM extra","debug gauge-factory-scale apply CONFIRM","debug gauge-factory-scale apply CONFIRM_EXTERNAL_POWER extra","debug gauge-factory-scale restore","debug gauge-factory-scale restore CONFIRM extra","debug gauge-factory-scale observed 131","debug gauge-factory-scale observed -1","debug gauge-factory-scale observed 1x","debug gauge-factory-scale observed +1","debug gauge-factory-scale observed 999999999999999","debug gauge-factory-scale observed 1 extra","debug gauge-factory-scale images","debug gauge-factory-scale images eA== extra","debug gauge-factory-scale bogus","debug gauge-factory-scale-prefix status","debug gauge-scale status","else gauge-factory-scale status","debugger gauge-factory-scale status","debug gauge-factory-scale observed"};
 const int expected[]={0,0,1,3,4,5,5,2,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-2,-2,-2,-2,-1};
 if(id<60){int index=id%30;int result=direct(text[index],expected[index],id>=30);if(!result&&(index==5||index==6)&&indexSeen!=(index==5?0U:130U))return 5;return result;}
 reset();
 if(id==60||id==61){char line[1536]{};const char* prefix="debug gauge-factory-scale images ";unsigned n=std::strlen(prefix);std::memcpy(line,prefix,n);unsigned size=id==60?1400:1401;for(unsigned i=0;i<size;++i)line[n+i]='A';line[n+size]=0;return direct(line,id==60?2:-1);}
 if(id==62){serial._reply_uart=true;allowUart=false;char line[]="debug gauge-factory-scale apply CONFIRM_EXTERNAL_POWER";if(!serial.handleFactoryScale(line)||halCalls||nandCalls||!authCalls||pass)return 6;return 0;}
 if(id==63||id==64){serial._reply_uart=id==64;nandBusy=true;char line[]="debug gauge-factory-scale prepare CONFIRM";if(!serial.handleFactoryScale(line)||halCalls||nandCalls!=1||pass)return 7;return 0;}
 if(id==65||id==66){feed("debug gauge-factory-scale prepare CONFIRM",id==66);if(halCalls!=1||actionSeen!=1||cachedCalls!=1||legacyCalls||!pass)return 8;return 0;}
 if(id==67){feed("debug gauge-factory-scale apply CONFIRM_EXTERNAL_POWER",true,false);if(halCalls||cachedCalls||legacyCalls||authCalls||nandCalls||pass||results!=1)return 9;return 0;}
 if(id==68){feed("debug ota-health",true);if(cachedCalls!=1||legacyCalls||halCalls||authCalls||nandCalls)return 10;return 0;}
 if(id==69){feed("debug unrelated command",false);if(cachedCalls!=1||legacyCalls!=1||halCalls||authCalls||nandCalls)return 11;return 0;}
 if(id==70){feed("debug gauge-factory-scale observed 130",true);if(halCalls!=1||indexSeen!=130||legacyCalls||serial._uart_line_length||serial._uart_line_overflow||serial._uart_line[0])return 12;return 0;}
 return 999;}
extern "C" {volatile int test_results[71]={};}
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
extern "C" void _start(){for(int i=0;i<71;++i)test_results[i]=run_case(i);test_done();}
'''
class FactoryIntegrationTests(unittest.TestCase):
 def test_actual_root_parser_and_consume(self):
  source=(ROOT/'main/debug/serial_debug.cpp').read_text()
  unsigned='bool parseUnsignedStrict'+source.split('bool parseUnsignedStrict',1)[1].split('#ifdef MOSAICO_BOARD',1)[0]
  header=(ROOT/'main/debug/serial_debug_transport.h').read_text();transport='namespace serial_debug_transport {'+header.split('namespace serial_debug_transport {',1)[1].split('// No driver waits',1)[0]+'bool uartAllowed(const char*){++authCalls;return allowUart;}\n}\n'
  support=base.MOCKS.split('static Hal hal;',1)[0]+base.RV_SUPPORT.split('static esp_partition_t imageParts',1)[0]
  code=PARSER_PREFIX+transport+unsigned+rv32.function(source,'bool __attribute__((noinline)) SerialDebug::handleFactoryScale(')+rv32.function(source,'void SerialDebug::consume(')+support+PARSER_RUNNER
  execute(self,code,'root-parser',71)
 def test_actual_maintenance_provider_with_factory_macro(self):
  old_fixture,old_cases,old_run,old_reset=maintenance.FIXTURE,maintenance.CASES,maintenance.RUN,maintenance.RESET
  hooks='''\n#define MOSAICO_FACTORY_SCALE_RUNTIME\nstatic bool factoryCachedSafe=true,factoryOriginal=false;\nbool factory_scale_maintenance_cached_safe_locked(){return factoryCachedSafe;}\nbool factory_scale_maintenance_original_cached_locked(){return factoryOriginal;}\n'''
  cases=list(old_cases[:5])+[
   ('factory_missing_historical','factoryCachedSafe=true;factoryOriginal=false;scale_load_status=scale_journal::Status::Missing;',True,False),
   ('factory_original_new_exact','factoryOriginal=true;std::snprintf(gauge_boot_info.reason,128,"boot_critical_scale_recovery_required");',True,False),
   ('factory_pending_old_reason','factoryCachedSafe=false;',False,True),
   ('new_reason_not_original','factoryOriginal=false;std::snprintf(gauge_boot_info.reason,128,"boot_critical_scale_recovery_required");',False,True),
   ('new_reason_suffix','factoryOriginal=true;std::snprintf(gauge_boot_info.reason,128,"boot_critical_scale_recovery_required suffix");',False,True),
   ('new_reason_prefix','factoryOriginal=true;std::snprintf(gauge_boot_info.reason,128,"xboot_critical_scale_recovery_required");',False,True),
   ('new_reason_attempted','factoryOriginal=true;gauge_boot_info.attempted=true;std::snprintf(gauge_boot_info.reason,128,"boot_critical_scale_recovery_required");',False,True),
   ('new_reason_bad_failed','factoryOriginal=true;alterCold(2);std::snprintf(gauge_boot_info.reason,128,"boot_critical_scale_recovery_required");',False,True),
   ('new_reason_raw130','factoryOriginal=true;dc=fcc=finalDc=finalFcc=130;std::snprintf(gauge_boot_info.reason,128,"boot_critical_scale_recovery_required");',False,False),
  ]
  for reason in ['null','unloaded','io','corrupt','active','third','safety','selectionbad']:
   cases.append(('factory_cache_'+reason,'factoryCachedSafe=false;factoryOriginal=false;',False,True))
  try:
   maintenance.FIXTURE=old_fixture.replace('#include "mosaico_maintenance_power.inc"',hooks+'\n#include "mosaico_maintenance_power.inc"')
   maintenance.CASES=cases;maintenance.RUN=RUN/'maintenance';maintenance.RESET=maintenance.RESET.replace('cacheLocks=0;','cacheLocks=0;factoryCachedSafe=true;factoryOriginal=false;')
   maintenance.MaintenancePowerTests('test_actual_provider_rv32').test_actual_provider_rv32()
  finally:
   maintenance.FIXTURE,maintenance.CASES,maintenance.RUN=old_fixture,old_cases,old_run
   maintenance.RESET=old_reset
 def test_static_legacy_exclusion_and_order(self):
  serial=(ROOT/'main/debug/serial_debug.cpp').read_text();consume=rv32.function(serial,'void SerialDebug::consume(')
  self.assertLess(consume.index('decodeUartLine'),consume.index('handleCachedRead'));self.assertLess(consume.index('handleCachedRead'),consume.index('handleFactoryScale'));self.assertLess(consume.index('handleFactoryScale'),consume.index('handleLine'))
  audit=(ROOT/'main/hal/mosaico/mosaico_factory_audit.inc').read_text()
  for name in ['gaugeFactoryAuditCapture','gaugeFactoryAuditRevalidate']:
   body=rv32.function(audit,'bool Hal::'+name+'(');self.assertLess(body.index('factory_scale_audit_allowed_locked'),body.index('battery_mutex'))
  runtime=(ROOT/'main/hal/mosaico/mosaico_gauge_scale_runtime.inc').read_text();trial=(ROOT/'main/hal/mosaico/mosaico_gauge_scale_trial.inc').read_text();native=(ROOT/'main/hal/mosaico/mosaico_native_recovery.inc').read_text()
  for name,text in [('gaugeScalePrepare',runtime),('gaugeScaleImages',trial),('gaugeScaleApply',trial),('gaugeScaleRestore',trial),('gaugeNativeRecoveryResolve',native)]:
   body=rv32.function(text,'bool Hal::'+name+'(');self.assertIn('factory_scale_present_locked()',body);self.assertLess(body.index('factory_scale_present_locked()'),body.index('battery_mutex') if 'battery_mutex' in body else len(body))
  units=rv32.function(runtime,'mosaico_gauge_physical::UnitContext scale_unit_context_locked(');self.assertLess(units.index('factory_scale_present_locked'),units.index('scale_load_locked'))
  poll=rv32.function(trial,'void Hal::gaugeScalePoll(');self.assertLess(poll.index('factory_scale_poll_locked'),poll.index('scale_load_locked'))
  check=rv32.function(native,'void native_recovery_check_sample_locked(');self.assertLess(check.index('factory_scale_present_locked'),check.index('scale_mutation_in_progress'))
  hal=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text();self.assertLess(hal.index('#include "mosaico_factory_audit.inc"'),hal.index('#include "mosaico_factory_scale_runtime.inc"'))
  for name in ['gaugeAccess','gaugeSetNominalCapacity','gaugeThresholdAudit','gaugeOcvMeasure','gaugeFccLimitRepair']:
   body=rv32.function(hal,'bool Hal::'+name+'(');fence='normal_gauge_fence_locked()' if name in ['gaugeAccess','gaugeSetNominalCapacity'] else 'scale_boot_fence_locked()';self.assertIn(fence,body)
   if 'gauge_access_word(' in body:self.assertLess(body.index(fence),body.index('gauge_access_word('))
  reconcile=rv32.function(hal,'bool Hal::gaugeReconcileNominal(')
  self.assertLess(reconcile.index('normal_gauge_fence_locked()'),reconcile.index('gauge_load_journal('))
  fence=rv32.function(runtime,'bool scale_boot_fence_locked(');self.assertLess(fence.index('scale_load_locked()'),fence.index('factory_scale_boot_fence_locked()'))
  missing=rv32.function(runtime,'bool scale_journal_missing_locked(');self.assertLess(missing.index('factory_scale_present_locked()'),missing.index('scale_load_locked()'))
  protection=rv32.function(trial,'bool Hal::gaugeScaleTrialActive(');self.assertIn('factory_scale_protected()',protection)
if __name__=='__main__':unittest.main()
