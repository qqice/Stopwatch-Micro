"""Small fault-injected constexpr tests of the actual capacity one-shot code.
No firmware build, hardware access, device files, or vendor modifications.
"""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
import test_mosaico_official_gauge as official

ROOT=Path(__file__).resolve().parents[1]
HAL=ROOT/'main/hal/mosaico/hal_mosaico.cpp'

def function(source, signature):
    start=source.index(signature)
    brace=source.index('{',start)
    depth=1;end=brace+1
    while depth:
        depth += (source[end]=='{')-(source[end]=='}');end+=1
    return source[start:end]

def compiler():
    found=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
    return str(found[-1]) if found else None

class Capacity130Tests(unittest.TestCase):
    def test_only_two_selected_values_and_no_vendor_drift(self):
        source=official.text(official.BASE/'mosaico_official_gauge.cpp')
        previous,profile=official.profiles()
        self.assertEqual(len(profile),26)
        self.assertIn('if (capacity != 65 && capacity != 130)',source)
        self.assertIn('if (capacity != 65) return {false, false, false};',source)
        assignments=re.findall(r'selected\.(\w+) = (\w+);',source)
        self.assertEqual(assignments,[('full_charge_cap','capacity'),('design_cap','capacity')])
        changed=profile|{name:130 for name,_ in assignments}
        self.assertEqual({k for k in profile if changed[k]!=profile[k]}, {'full_charge_cap','design_cap'})
        # Existing independent manifest assertion checks all six immutable files.
        official.OfficialGaugeTests('test_vendor_and_board_provenance').test_vendor_and_board_provenance()

    def test_hook_transport_independence_and_policy(self):
        source=HAL.read_text(encoding='utf8')
        poll=function((HAL.parent/'mosaico_gauge_scale_trial.inc').read_text(), 'void Hal::gaugeScalePoll()')
        self.assertTrue(poll.split('{',1)[1].lstrip().startswith('capacity_test_poll();'))
        active=function((HAL.parent/'mosaico_gauge_scale_trial.inc').read_text(), 'bool Hal::gaugeScaleTrialActive()')
        self.assertNotIn('capacity_test',active)
        command=function(source,'bool Hal::gaugeCapacityTest(')
        self.assertNotIn('gauge_official_apply_locked(',command.split('if(std::strcmp(action,"arm130"))',1)[1])
        boot=function(source,'Hal::GaugeBootReloadStatus Hal::gaugeBootReload(')
        for a,b in [('gauge_boot_info.attempted = true','if (gaugeScaleTrialActive())'),
                    ('capacity_consume_once()','if (gaugeScaleTrialActive())'),
                    ('capacity_consume_once()','if (!gauge || !selftest())'),
                    ('capacity_test.restorePending=true','gauge_official_apply_locked('),
                    ('capacity_test.deadlineUs=','gauge_official_apply_locked(')]:
            self.assertLess(boot.index(a),boot.index(b))
        helper=function(source,'bool gauge_official_apply_locked(')
        self.assertIn('if (capacity == 65) normal_gauge_target = 65;',helper)
        self.assertNotIn('normal_gauge_target = 130',source)
        self.assertNotIn('capacity_test', (ROOT/'main/ota/mosaico_ota.cpp').read_text())
        self.assertIn('"gauge-capacity-test"',(ROOT/'main/debug/serial_debug_transport.h').read_text())
        serial=(ROOT/'main/debug/serial_debug.cpp').read_text()
        command=serial.split('if (std::strcmp(command,"gauge-capacity-test")==0)',1)[1].split('if (std::strcmp(command, "gauge-profile")',1)[0]
        self.assertIn('"CONFIRM"',command)
        self.assertNotIn('_reply_uart',command)

    def test_actual_fault_injection(self):
        cxx=compiler()
        if not cxx:self.skipTest('target compiler unavailable')
        source=HAL.read_text(encoding='utf8')
        names=['bool gauge_official_apply_locked(', 'esp_err_t capacity_arm_read(', 'bool capacity_arm_write(',
               'bool capacity_consume_once(', 'bool gauge_official_sealed_locked(', 'bool capacity_safe_locked(', 'bool capacity_restore_locked(',
               'void capacity_test_poll(', 'bool Hal::gaugeCapacityTest(',
               'Hal::GaugeBootReloadStatus Hal::gaugeBootReload(']
        actual=[]
        for name in names:
            body=function(source,name)
            if name.startswith('Hal::GaugeBoot'):
                begin=body.index('    // Capture only initialized pre-write evidence.')
                end=body.index('    } diagnosticFinish;')+len('    } diagnosticFinish;')
                body=body[:begin]+body[end:]
                body=body.replace('Hal::GaugeBootReloadStatus Hal::gaugeBootReload','GaugeBootReloadStatus boot')
            body=body.replace('bool Hal::gaugeCapacityTest','bool command')
            body=re.sub(r'std::lock_guard<[^;]+;','',body)
            body=re.sub(r'std::unique_lock<[^;]+;','',body).replace('!transaction.owns_lock()||','')
            body=body.replace('NormalGaugeStorageLease lease;','Lease lease{leaseAvailable};')
            body=body.replace('GetHAL().gaugeScaleTrialActive()','gaugeScaleTrialActive()')
            body=body.replace('capacity_test.restorePending.load(std::memory_order_acquire)','capacity_test.restorePending')
            body=body.replace('capacity_test.restorePending.load()','capacity_test.restorePending')
            body=body.replace('std::snprintf','mock::snprintf').replace('std::strcmp','mock::strcmp')
            actual.append('constexpr '+body)
        harness=r'''
#include <cstdint>
#include <cstddef>
#include <initializer_list>
#include "MODEL"
#define MOSAICO_GAUGE_USE_PREVIOUS_PROFILE 0
namespace mock {
 template<class...T> constexpr int snprintf(char*,size_t,const char*,T...){return 0;}
 constexpr int strcmp(const char* a,const char* b){while(*a&&*a==*b){++a;++b;}return *a-*b;}
}
using esp_err_t=int;using nvs_handle_t=int;
constexpr int ESP_OK=0,ESP_FAIL=-1,ESP_ERR_NVS_NOT_FOUND=-2,NVS_READONLY=0,NVS_READWRITE=1;
constexpr int ESP_RST_POWERON=1;
struct Lease {bool held;};
struct Fixture {
 enum class GaugeBootReloadStatus {Deferred,Skipped,Applied,Critical};
 struct {GaugeBootReloadStatus status=GaugeBootReloadStatus::Deferred;bool attempted=false;char reason[128]{};} gauge_boot_info;
 struct State {bool possiblyChanged=false,restorePending=false,restoreAttempted=false;int64_t deadlineUs=0;const char* outcome="idle";} capacity_test;
 struct Result {bool success=true,exitAttempted=false,sealAttempted=false;};
 enum Fault {None,Partial,UnknownExit,UnknownSeal};
 Fault fault=None;
 bool gauge=true,model=true,oldTrial=false,leaseAvailable=true,readable=true,preserveFcc=false;
 bool readFail=false,setFail=false,commitFail=false,readbackFail=false;
 uint8_t armed=0,pending=0;
 uint16_t op=0x26,dc=65,fcc=65,mv=4100,temp=2982,normal_gauge_target=0,lastCapacity=0;
 unsigned applies=0,exits=0,seals=0,sets=0,commits=0;
 int reset=ESP_RST_POWERON;int64_t now=0;
 constexpr int nvs_open(const char*,int,nvs_handle_t* h){*h=1;return ESP_OK;}
 constexpr int nvs_get_u8(int,const char*,uint8_t* value){*value=armed;return readFail||(readbackFail&&commits)?ESP_FAIL:ESP_OK;}
 constexpr int nvs_set_u8(int,const char*,uint8_t value){++sets;pending=value;return setFail?ESP_FAIL:ESP_OK;}
 constexpr int nvs_commit(int){++commits;if(commitFail)return ESP_FAIL;armed=pending;return ESP_OK;}
 constexpr void nvs_close(int){}
 constexpr int esp_reset_reason(){return reset;}
 constexpr int64_t esp_timer_get_time(){return now;}
 constexpr bool selftest(){return model;}
 constexpr bool gaugeScaleTrialActive(){return oldTrial;}
 constexpr void gauge_legacy_cache_init_locked(){}
 constexpr bool gauge_word(int reg,uint16_t& value){
  value=reg==0x3a?op:reg==0x3c?dc:reg==0x12?fcc:reg==0x08?mv:reg==0x06?temp:0;
  return readable;
 }
 constexpr bool gauge_exit_config(bool){++exits;return false;}
 constexpr void gauge_delay_ms(int){}
 constexpr int i2c_master_transmit(bool,const uint8_t*,size_t,int){++seals;return ESP_FAIL;}
 constexpr Result mosaico_official_gauge_apply(bool,uint16_t capacity){
  ++applies;lastCapacity=capacity;
  if(fault==None){dc=capacity;if(!preserveFcc)fcc=capacity;op=0x26;return {};}
  dc=capacity; // represent a possibly submitted/partial write, never pretend unchanged
  if(fault==UnknownExit){op=0x426;return {false,true,false};}
  if(fault==UnknownSeal){op=0x22;return {false,false,true};}
  return {false,false,false};
 }
ACTUAL
};
constexpr bool cases(){
 using S=Fixture::GaugeBootReloadStatus;
 // Every reset cause consumes; only POWERON dispatches130.
 for(int reset:{0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15}){
  Fixture f;f.armed=1;f.reset=reset;
  if(f.boot(nullptr,0)!=S::Applied||f.armed||f.commits!=1||f.applies!=1||f.lastCapacity!=(reset==1?130:65))return false;
  if(f.boot(nullptr,0)!=S::Applied||f.applies!=1||f.commits!=1)return false;
 }
 // Ordinary disarmed/missing-equivalent boots do not wear flash.
 {Fixture f;if(f.boot(nullptr,0)!=S::Applied||f.sets||f.commits||f.lastCapacity!=65)return false;}
 // Real cold start restores factory3000 RAM before selecting armed130.
 {Fixture f;f.armed=1;f.dc=f.fcc=3000;
  if(f.boot(nullptr,0)!=S::Applied||f.lastCapacity!=130||f.dc!=130||f.fcc!=130||f.armed)return false;
 }
 // Unknown/mixed pairs cannot select130, even with a valid one-shot arm.
 for(auto pair:{0u,1u,2u}){Fixture f;f.armed=1;f.dc=pair==0?3000:pair==1?130:65;f.fcc=pair==0?65:pair==1?130:3000;
  if(f.boot(nullptr,0)!=S::Applied||f.lastCapacity!=65||f.armed)return false;
 }
 // None of read/set/commit/readback failures dispatch130, even if arm was true.
 for(unsigned fail:{0u,1u,2u,3u}){
  Fixture f;f.armed=1;f.readFail=fail==0;f.setFail=fail==1;f.commitFail=fail==2;f.readbackFail=fail==3;
  if(f.boot(nullptr,0)!=S::Applied||f.lastCapacity!=65||mock::strcmp(f.capacity_test.outcome,"consume_failed_default65"))return false;
 }
 {Fixture f;f.armed=1;f.oldTrial=true;if(f.boot(nullptr,0)!=S::Skipped||f.sets!=1||f.commits!=1||f.applies||f.armed)return false;}
 {Fixture f;f.armed=1;f.gauge=false;if(f.boot(nullptr,0)!=S::Skipped||f.armed||f.applies)return false;}
 {Fixture f;f.armed=1;f.leaseAvailable=false;if(f.boot(nullptr,0)!=S::Deferred||f.sets||f.gauge_boot_info.attempted)return false;}
 // No profile/key dispatch from CFG, CAL, FULL_ACCESS, UNSEALED or unreadable state.
 for(unsigned state:{0x426u,0x27u,0x22u,0x24u,0u})for(unsigned armed:{0u,1u}){
  Fixture f;f.armed=armed;f.op=state;
  if(f.boot(nullptr,0)!=S::Skipped||f.applies||f.exits||f.seals||f.armed)return false;
 }
 for(unsigned armed:{0u,1u}){Fixture f;f.armed=armed;f.readable=false;
  if(f.boot(nullptr,0)!=S::Skipped||f.applies||f.exits||f.seals||f.armed)return false;
 }
 // Arm has no profile writer; cancel is durable even when restore admission fails.
 {Fixture f;char d[384]{};if(!f.command("arm130",d,sizeof d)||f.armed!=1||f.applies)return false;
  f.op=0x426;if(f.command("restore65",d,sizeof d)||f.armed||f.applies)return false;}
 {Fixture f;f.armed=1;f.commitFail=true;char d[384]{};if(f.command("restore65",d,sizeof d)||f.applies)return false;}
 {Fixture f;f.armed=1;char d[384]{};if(!f.command("restore65",d,sizeof d)||f.armed||f.applies)return false;}
 // Ordinary65 keeps learned FCC, but130/restore65 require exact FCC targets.
 {Fixture f;f.fcc=64;f.preserveFcc=true;
  if(f.boot(nullptr,0)!=S::Applied||f.fcc!=64||f.normal_gauge_target!=65)return false;
 }
 {Fixture f;f.armed=1;f.preserveFcc=true;
  if(f.boot(nullptr,0)!=S::Skipped||f.dc!=130||f.fcc!=65||!f.capacity_test.restorePending||f.normal_gauge_target)return false;
 }
 {Fixture f;f.dc=65;f.fcc=64;f.preserveFcc=true;char d[384]{};
  if(f.command("restore65",d,sizeof d)||f.applies!=1||f.fcc!=64||!f.capacity_test.restorePending||!f.capacity_test.restoreAttempted||f.normal_gauge_target)return false;
 }
 // Deadline starts BEFORE possibly failed apply. Failure keeps pending; no auto immediate retry.
 for(auto fault:{Fixture::Partial,Fixture::UnknownExit,Fixture::UnknownSeal}){
  Fixture f;f.armed=1;f.fault=fault;f.now=123;
  if(f.boot(nullptr,0)!=S::Skipped||!f.capacity_test.possiblyChanged||!f.capacity_test.restorePending||f.capacity_test.deadlineUs!=600000123||f.applies!=1)return false;
  f.capacity_test_poll();if(f.applies!=1)return false;
  f.now=f.capacity_test.deadlineUs;f.capacity_test_poll();
  if(fault==Fixture::UnknownExit&&(f.applies!=1||f.exits||f.seals||f.capacity_test.restoreAttempted))return false;
  if(fault==Fixture::UnknownSeal&&(f.applies!=1||f.seals||f.capacity_test.restoreAttempted))return false;
 }
 // Busy/lease postpones without spending budget; once admitted, restore at most once.
 {Fixture f;f.armed=1;f.boot(nullptr,0);if(f.normal_gauge_target||f.dc!=130)return false;
  f.now=600000000;f.leaseAvailable=false;f.capacity_test_poll();if(f.applies!=1||f.capacity_test.restoreAttempted)return false;
  f.leaseAvailable=true;f.capacity_test_poll();if(f.applies!=2||f.lastCapacity!=65||f.capacity_test.restorePending||f.normal_gauge_target!=65)return false;
  f.capacity_test_poll();if(f.applies!=2)return false;
 }
 {Fixture f;f.armed=1;f.boot(nullptr,0);f.fault=Fixture::Partial;f.now=600000000;f.capacity_test_poll();
  if(f.applies!=2||!f.capacity_test.restorePending||!f.capacity_test.restoreAttempted)return false;
  f.capacity_test_poll();char d[384]{};f.command("restore65",d,sizeof d);if(f.applies!=2)return false;
 }
 return true;
}
static_assert(cases(),"actual one-shot/cancel/reset failures, unknown cleanup, defer and restore once");
'''.replace('MODEL',(HAL.parent/'mosaico_gauge_model.h').as_posix()).replace('ACTUAL','\n'.join(actual))
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path=Path(directory)/'capacity.cpp';path.write_text(harness,encoding='utf8')
            result=subprocess.run([cxx,'-std=c++20','-fsyntax-only',str(path)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

if __name__=='__main__':unittest.main()
