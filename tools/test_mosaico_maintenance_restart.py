"""Extracted production parser/consume + synchronous admission on RV32.

Mocks SDK, locking and output, not an electrical/scheduler proof. Actual HAL raw
read/evidence provider is separately executed by test_mosaico_maintenance_power.
"""
from pathlib import Path
import re, unittest, zlib
import test_serial_debug_ota_health as health
import test_microlink_peer_probe as rv32

ROOT=health.ROOT
RUN=ROOT/'.artifacts/private/mosaico/maintenance-restart-tests-001'

def fixture(deny=False):
    code=health.fixture(True,deny)
    code=code[:code.index('static int run_case(')]
    code=code.replace('bool handleMaintenanceRestart(const char*){return false;}',
                      'bool handleMaintenanceRestart(const char*);')
    code=code.replace(' void consume(char);', ' enum class AsyncTest{None,Other}; AsyncTest _async_test=AsyncTest::None;\n static SerialDebug* _writer;\n void consume(char);')
    # Cached status formatting is not exercised in these cases.
    decl=rv32.function((ROOT/'main/hal/hal.h').read_text(),'    struct MaintenancePowerSnapshot {').rstrip()
    decl=decl[:-1]+'};' if not decl.endswith(';') else decl
    # function() returns the closing brace, not a struct semicolon.
    if decl.endswith('}};'):raise AssertionError(decl)
    code+=r'''
SerialDebug* SerialDebug::_writer=nullptr;
namespace std {size_t strlen(const char* p){size_t n=0;while(p[n])++n;return n;}
struct FILE{};int fflush(FILE*){return 0;}
struct mutex{};struct try_to_lock_t{};constexpr try_to_lock_t try_to_lock{};
template<class T>struct unique_lock{unique_lock(T&,try_to_lock_t){}bool owns_lock(){return true;}};
}
std::FILE* stdout=nullptr;
static int id,leases,touches,restarts,replies,samples,delays,yields,badOrder;
static bool held,trial,otaBusy,sdkValid,bootSame,storageBusy,touchBusy,readFail;
static int64_t nowUs=1000000;
int64_t esp_timer_get_time(){return nowUs;}
void vTaskDelay(int){++yields;nowUs+=100000;}
struct Partition{unsigned address;};static Partition running{1},boot{1};
const Partition* esp_ota_get_running_partition(){return &running;}
const Partition* esp_ota_get_boot_partition(){boot.address=bootSame?1:2;return &boot;}
struct Hal{
'''+decl+r'''
 enum class MaintenancePowerResult{Rejected,Busy,Safe};
 bool gaugeScaleTrialActive(){return trial;}
 bool tryMaintenancePowerSnapshot(MaintenancePowerSnapshot& s,MaintenancePowerResult* result){
  ++samples;if(!held||!touches)++badOrder;
  *result=MaintenancePowerResult::Rejected;
  if(readFail)return false;
  if(id==17 || (id==18&&samples==1)){*result=MaintenancePowerResult::Busy;return false;}
  s={};s.valid=true;s.sampledUs=nowUs;s.historicalCritical=id!=3 && !(id==38&&delays);s.historicalFailedCrc=id==2 || (id==37&&delays)?9:0x430c5ed4;
  s.operationStatus=0x26;s.designMah=s.fullMah=3000;s.voltageMv=id==4?3800:4200;s.temperatureDeciK=id==5?3200:3000;
  *result=MaintenancePowerResult::Safe;return true;
 }
 void delay(unsigned ms){++delays;if(!held||!touches||ms!=250)++badOrder;nowUs+=250000;
  if(id==19)otaBusy=true;if(id==20)sdkValid=false;if(id==21)bootSame=false;
  if(id==22)trial=true;if(id==23)nandBusy=true;if(id==24)readFail=true;
 }
 void reboot(){++restarts;if(!held||!touches||otaBusy||trial||nandBusy||!sdkValid||!bootSame)++badOrder;}
};static Hal hal;Hal& GetHAL(){return hal;}
namespace TouchSleep{struct OtaAdmission{bool ok;OtaAdmission():ok(!touchBusy){if(ok)++touches;}~OtaAdmission(){if(ok)--touches;}explicit operator bool(){return ok;}};}
namespace MosaicoOta{
std::mutex lock;
struct Flag{bool value=false;bool load(){return value;}};
Flag imageReady,selected,maintenanceMode;
bool busy(){return otaBusy;}bool currentReady(){return sdkValid;}
struct StorageAdmission{bool ok;StorageAdmission():ok(!storageBusy){if(ok){held=true;++leases;}}~StorageAdmission(){if(ok){held=false;--leases;}}explicit operator bool(){return ok;}};
}
'''
    model=(ROOT/'main/ota/mosaico_ota_maintenance_model.h').read_text()
    code+='namespace MosaicoOtaMaintenance{'+rv32.function(model,'template<class Snapshot> bool powerSafe(')+'}\n'
    ota=(ROOT/'main/ota/mosaico_ota.cpp').read_text()
    code+='namespace MosaicoOta{'+rv32.function(ota,'bool maintenanceRuntimeRestart(')+'}\n'
    parser=rv32.function((ROOT/'main/debug/serial_debug.cpp').read_text(),'bool __attribute__((noinline)) SerialDebug::handleMaintenanceRestart(')
    code+=parser
    commands=['debug runtime-restart CONFIRM_EXTERNAL_POWER 430c5ed4\n']*39
    commands[1]='debug runtime-restart CONFIRM\n'
    for n,text in {25:'debug runtime-restart CONFIRM_EXTERNAL_POWER',26:'debug runtime-restart CONFIRM_EXTERNAL_POWER 430c5ed',27:'debug runtime-restart CONFIRM_EXTERNAL_POWER 430c5ed44',28:'debug runtime-restart CONFIRM_EXTERNAL_POWER 430c5edg',29:'debug runtime-restart CONFIRM_EXTERNAL_POWER 430c5ed4 extra',30:'debug runtime-restart CONFIRM_USB_POWER 430c5ed4',31:'debugx runtime-restart CONFIRM_EXTERNAL_POWER 430c5ed4',32:'dbg runtime-restart CONFIRM_EXTERNAL_POWER 430C5ED4'}.items():commands[n]=text+'\n'
    for n in (33,34,35):
        payload=commands[n].strip();commands[n]=f'uart {zlib.crc32(payload.encode()):08x} {payload}\n'
    commands[34]=commands[34].replace('uart ','uart f',1)
    entries=[]
    for n,text in enumerate(commands):
        literal=''.join('\\x%02x'%b for b in text.encode())
        entries.append(f'case {n}:text="{literal}";break;')
    code+=r'''
static int run_case(int n){id=n;leases=touches=restarts=replies=samples=delays=yields=badOrder=0;
 passes=failures=fullCalls=0;held=trial=otaBusy=storageBusy=touchBusy=readFail=nandBusy=false;sdkValid=bootSame=true;nowUs=1000000;
 MosaicoOta::imageReady.value=MosaicoOta::selected.value=MosaicoOta::maintenanceMode.value=false;
 if(n==6)readFail=true;if(n==7)sdkValid=false;if(n==8)bootSame=false;if(n==9)otaBusy=true;
 if(n==10)storageBusy=true;if(n==11)trial=true;if(n==12)touchBusy=true;if(n==13||n==35)nandBusy=true;
 if(n==14)MosaicoOta::imageReady.value=true;if(n==15)MosaicoOta::selected.value=true;
 if(n==16)MosaicoOta::maintenanceMode.value=true;
 SerialDebug d;d._reply_uart=n>=33&&n<=35;d._async_test=n==36?SerialDebug::AsyncTest::Other:SerialDebug::AsyncTest::None;
 const char* text="";switch(n){
'''+''.join(entries)+r'''
 }SerialDebug::_writer=&d;for(unsigned i=0;text[i];++i)d.consume(text[i]);SerialDebug::_writer=nullptr;
 if(leases||touches||held||badOrder)return 1;
 bool accepted=n==0||n==18||n==32||n==33;
 if(AUTH_DENY&&n==33)accepted=false;
 if(accepted)return restarts==1&&passes==1&&samples>=2&&delays==1&&!failures?0:2;
 if(n==1||n==31)return fullCalls==1&&!restarts&&!samples?0:3;
 if((n>=19&&n<=24)||n==37||n==38)return passes==1&&failures==1&&!restarts&&delays==1?0:4;
 return !restarts&&failures==1?0:5;
}
extern "C" {volatile int test_results[39]={};}
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
extern "C" void _start(){for(int n=0;n<39;++n)test_results[n]=run_case(n);test_done();}
'''.replace('AUTH_DENY',str(int(deny)))
    return code

class RestartTests(unittest.TestCase):
    def test_small_parser_stack(self):
        import os, subprocess
        RUN.mkdir(parents=True,exist_ok=True)
        cpp=RUN/'stack.cpp';cpp.write_text(fixture())
        args=[str(rv32.CLANG),'--target=riscv32-unknown-elf','-march=rv32im','-mabi=ilp32','-std=c++20','-Os','-Wno-missing-multilib','-fno-builtin','-fno-exceptions','-fno-rtti','-nostdinc++','-fstack-usage','-c',str(cpp),'-o',str(RUN/'stack.o')]
        result=subprocess.run(args,capture_output=True,text=True,env={**os.environ,'TEMP':str(RUN),'TMP':str(RUN)})
        (RUN/'stack-compile.log').write_text(result.stdout+result.stderr)
        self.assertEqual(result.returncode,0,result.stderr)
        stack=(RUN/'stack.su').read_text()
        entry=next(line for line in stack.splitlines() if 'handleMaintenanceRestart' in line and 'lambda' not in line)
        self.assertLessEqual(int(entry.split('\t')[1]),128,entry)

    def test_actual_parser_admission(self):
        for deny in (False,True):
            old,run=rv32.fixture,rv32.RUN
            try:
                rv32.fixture=lambda _:fixture(deny);rv32.RUN=RUN
                values=rv32.execute('','restart-deny' if deny else 'restart',39)
                self.assertEqual(values,(0,)*39)
            finally:rv32.fixture,rv32.RUN=old,run

    def test_legacy_heavy_and_guard_unchanged(self):
        import subprocess
        current=(ROOT/'main/debug/serial_debug.cpp').read_text()
        baseline=subprocess.check_output(['git','show','HEAD:main/debug/serial_debug.cpp'],cwd=ROOT,text=True)
        for start,end in [('void SerialDebug::handleLine(', 'void SerialDebug::printHelp('),('void SerialDebug::printStatus(', 'void SerialDebug::runSelfTest(')]:
            self.assertEqual(current.split(start,1)[1].split(end,1)[0],baseline.split(start,1)[1].split(end,1)[0])
        self.assertLess(current.index('!handleMaintenanceRestart(command)'),current.index('void SerialDebug::handleLine('))

if __name__=='__main__':unittest.main(verbosity=2)
