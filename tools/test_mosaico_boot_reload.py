"""Source integration guards; hardware and HAL policy acceptance are separate."""
import unittest
import shutil
import subprocess
import tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
class BootIntegrationTests(unittest.TestCase):
 def boot_source(self):
  source=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text(encoding='utf8')
  return source.split('Hal::GaugeBootReloadStatus Hal::gaugeBootReload(',1)[1].split('Hal::MosaicoClockDiagnostics',1)[0]
 def test_factory_sec2_policy_compiled(self):
  compiler=shutil.which('g++') or shutil.which('clang++')
  if not compiler:
   found=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
   compiler=str(found[-1]) if found else None
  if not compiler: self.skipTest('C++ compiler unavailable')
  policy=r'''
using namespace mosaico_gauge;
constexpr Journal nominal() {
 Journal n{}; n.action=1; n.state=static_cast<uint8_t>(State::VerifiedSealedPrior3);
 n.targetDesign=n.targetFcc=n.lastDesign=n.lastFcc=65;
 n.originalDesign=n.originalFcc=3000; n.changedFcc=1; return n;
}
constexpr AccessJournal access() {
 AccessJournal a{}; a.state=static_cast<uint8_t>(AccessState::Restored);
 a.priorSecurity=a.lastSecurity=3; a.unsealAttempted=a.unsealVerified=a.fullAttempted=a.fullVerified=1; return a;
}
constexpr bool cases() {
 auto n=nominal(); auto a=access();
 if(!bootFactorySecurityEligible(n,a,3000,3000,0xa4) || !bootFactorySecurityEligible(n,a,3000,3000,0xa6))return false;
 for(auto op:{0x4a4,0xa5,0x84,0xa0,0xa2})if(bootFactorySecurityEligible(n,a,3000,3000,op))return false;
 if(bootFactorySecurityEligible(n,a,65,62,0xa4) || !keepLearnedNominal(65,62))return false;
 n.originalFcc=65; if(bootFactorySecurityEligible(n,a,3000,3000,0xa4))return false; n=nominal();
 a.priorSecurity=2; if(bootFactorySecurityEligible(n,a,3000,3000,0xa4))return false; a=access();
 a.state=static_cast<uint8_t>(AccessState::Failed); if(bootFactorySecurityEligible(n,a,3000,3000,0xa4))return false;
 if(!bootReloadPhysical(0xa4,4188,2982,32,33))return false;
 if(bootReloadPhysical(0xa4,3499,2982,32,33) || bootReloadPhysical(0x4a4,4188,2982,32,33) ||
    bootReloadPhysical(0xa5,4188,2982,32,33) || bootReloadPhysical(0x84,4188,2982,32,33) ||
    bootReloadPhysical(0xa4,4188,2982,131,33))return false;
 return true;
}
static_assert(cases(), "factory SEC2 guards");
'''
  with tempfile.TemporaryDirectory() as directory:
   source=Path(directory)/'gauge_policy.cpp'
   source.write_text('#include <initializer_list>\n#include "'+(ROOT/'main/hal/mosaico/mosaico_gauge_model.h').as_posix()+'"\n'+policy,encoding='utf8')
   result=subprocess.run([compiler,'-std=c++17','-fsyntax-only',str(source)],capture_output=True,text=True)
   self.assertEqual(result.returncode,0,result.stdout+result.stderr)
 def test_actual_flow_latch_normalize_open_finally_order(self):
  source=self.boot_source()
  tokens=['gauge_store_reload(latch, mac.data())','gauge_boot_info.attempted = true',
   'if (normalizePriorSealed)', 'gaugeAccess(GaugeAccessAction::Restore, normalizeReason',
   '} reloadScope;', 'gaugeAccess(GaugeAccessAction::Open, phaseReason',
   'gaugeSetNominalCapacity(', 'gaugeAccess(GaugeAccessAction::Restore, closeReason']
  offsets=[source.index(token) for token in tokens]
  self.assertEqual(offsets,sorted(offsets))
  normalize=source.split('if (normalizePriorSealed)',1)[1].split('struct BootReloadScope',1)[0]
  self.assertEqual(normalize.count('gaugeAccess('),1)
  self.assertIn('ReloadState::Failed',normalize)
  self.assertIn('boot_critical_normalize_failed_no_retry',normalize)
  self.assertIn('return finish(Status::Critical, details)',normalize)
  for token in ['configExitAccepted(observedOperation)', 'gauge_identity()',
   'gauge_access_preflight(observedOperation, recheck, true)', 'recheck.design == FactoryMah && recheck.fcc == FactoryMah']:
   self.assertIn(token,normalize)
 def test_actual_readonly_guards_precede_all_access(self):
  source=self.boot_source()
  admission=source.split('gauge_boot_info.attempted = true',1)[0]
  self.assertNotIn('gaugeAccess(',admission)
  self.assertLess(admission.index('keepLearnedNominal('),admission.index('bootFactorySecurityEligible('))
  self.assertLess(admission.index('!sample.valid || !sample.capacityValid'),admission.index('keepLearnedNominal('))
  for token in ['latch.state != static_cast<uint8_t>(ReloadState::Completed)',
   'gauge_load_journal(nominal, mac.data())', 'gauge_load_access(access, mac.data())',
   '!bootHistoryEligible(', '!bootFactorySecurityEligible(', '!gauge_access_preflight(', '!gauge_identity()']:
   self.assertIn(token,admission)
  model=(ROOT/'main/hal/mosaico/mosaico_gauge_model.h').read_text(encoding='utf8')
  exit_policy=model.split('bool configExitAccepted(',1)[1].split('inline bool capacityPolicy',1)[0]
  self.assertIn('security == 1 || security == 3',exit_policy)
  self.assertNotIn('security == 2',exit_policy)
 def test_extracted_boot_faults_constant_evaluated(self):
  # Evaluate the actual coordinator body with constexpr HAL/NVS/I2C mocks.
  # Only inert mutex/formatting and privilege RAII are substituted; admission,
  # branches, calls and failure-latch writes are the production source.
  compiler=shutil.which('g++') or shutil.which('clang++')
  if not compiler:
   found=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
   compiler=str(found[-1]) if found else None
  if not compiler: self.skipTest('C++ compiler unavailable')
  body=self.boot_source().split('\nHal::',1)[0]
  body=body[body.index('{'):].strip()
  scope='''struct BootReloadScope {
        BootReloadScope() { boot_factory_reload_scope = true; }
        ~BootReloadScope() { boot_factory_reload_scope = false; }
    } reloadScope;'''
  self.assertIn(scope,body)
  body=body.replace(scope,'struct BootReloadScope {} reloadScope;').replace('std::','mock::')
  harness=r'''
#include <array>
namespace mock {
struct mutex {}; struct recursive_mutex {};
template<class T> struct lock_guard { constexpr lock_guard(T&) {} };
template<class... T> constexpr int snprintf(char*, size_t, const char*, T...) {return 0;}
constexpr void memcpy(uint8_t* to, const uint8_t* from, size_t size) {for(size_t i=0;i<size;++i)to[i]=from[i];}
}
constexpr int ESP_OK=0, ESP_ERR_NVS_NOT_FOUND=1;
using esp_err_t=int;
using namespace mosaico_gauge;
struct Hal {
 enum class GaugeBootReloadStatus {Deferred,Skipped,Critical,Applied};
 enum class GaugeAccessAction {Open,Restore};
 struct GaugeBootReloadInfo {GaugeBootReloadStatus status=GaugeBootReloadStatus::Deferred; bool attempted=false; char reason[128]{};};
};
struct GaugePair {uint16_t design=3000,fcc=3000;};
struct Fixture {
 using GaugeBootReloadStatus=Hal::GaugeBootReloadStatus;
 using GaugeAccessAction=Hal::GaugeAccessAction;
 enum Fault {None,NormalizeFail,PrepareFail,OpenAbortFail,OpenAbortSuccess,FinalStoreFail,ReadFail,UnknownHistory,LowVoltage,Config,Cal,Learned};
 Fault fault; bool gauge=true, gauge_access_cleanup_attempted=false,gauge_access_cleanup_succeeded=false;
 bool durable=false; unsigned stores=0,opens=0,restores=0,seals=0,applies=0;
 ReloadJournal persisted{}; Journal nominal{}; AccessJournal access{};
 mock::mutex battery_mutex; mock::recursive_mutex gauge_transaction_mutex;
 Hal::GaugeBootReloadInfo gauge_boot_info;
 struct Sample {bool valid=true,capacityValid=true;uint16_t operationStatus=0xa6,designMah=3000,fullMah=3000,remainingMah=197;} battery_telemetry;
 constexpr Fixture(Fault f,bool sec2=false):fault(f) {
  nominal.action=1;nominal.state=static_cast<uint8_t>(State::VerifiedSealedPrior3);
  nominal.targetDesign=nominal.targetFcc=nominal.lastDesign=nominal.lastFcc=65;
  nominal.originalDesign=nominal.originalFcc=3000;nominal.changedFcc=1;
  access.state=static_cast<uint8_t>(AccessState::Restored);access.priorSecurity=access.lastSecurity=3;
  access.unsealAttempted=access.unsealVerified=access.fullAttempted=access.fullVerified=1;
  if(sec2)battery_telemetry.operationStatus=0xa4;
  if(f==ReadFail)battery_telemetry.valid=false;
  if(f==Config)battery_telemetry.operationStatus|=0x400;
  if(f==Cal)battery_telemetry.operationStatus|=1;
  if(f==Learned){battery_telemetry.designMah=65;battery_telemetry.fullMah=62;battery_telemetry.remainingMah=20;}
 }
 constexpr std::array<uint8_t,6> getFactoryMac(){return {};}
 constexpr bool selftest(){return true;}
 constexpr int gauge_load_reload(ReloadJournal& j,const uint8_t*){j=persisted;return durable?ESP_OK:ESP_ERR_NVS_NOT_FOUND;}
 constexpr bool gauge_store_reload(ReloadJournal& j,const uint8_t*) {
  ++stores;if(fault==PrepareFail || (fault==FinalStoreFail && stores>1))return false;
  durable=true;persisted=j;return true;
 }
 constexpr int gauge_load_journal(Journal& j,const uint8_t*){j=nominal;return fault==UnknownHistory?2:ESP_OK;}
 constexpr int gauge_load_access(AccessJournal& j,const uint8_t*){j=access;return ESP_OK;}
 constexpr bool gauge_store_journal(Journal& j,const uint8_t*){nominal=j;return true;}
 constexpr void sample_battery_locked(bool){}
 constexpr bool gauge_word(int,uint16_t& op){op=battery_telemetry.operationStatus;return fault!=ReadFail;}
 constexpr bool gauge_identity(){return true;}
 constexpr bool gauge_access_preflight(uint16_t& op,GaugePair& p,bool){op=battery_telemetry.operationStatus;p.design=battery_telemetry.designMah;p.fcc=battery_telemetry.fullMah;return fault!=LowVoltage;}
 constexpr bool gaugeAccess(GaugeAccessAction action,char*,size_t) {
  gauge_access_cleanup_attempted=gauge_access_cleanup_succeeded=false;
  if(action==GaugeAccessAction::Open){
   ++opens;
   if(fault==OpenAbortFail || fault==OpenAbortSuccess){
    gauge_access_cleanup_attempted=true;++seals;
    gauge_access_cleanup_succeeded=fault==OpenAbortSuccess;
    battery_telemetry.operationStatus=fault==OpenAbortSuccess?0xa6:0xa4;return false;
   }
   battery_telemetry.operationStatus=0xa2;return true;
  }
  ++restores;gauge_access_cleanup_attempted=true;
  if(((battery_telemetry.operationStatus>>1)&3)!=3)++seals;
  if(fault==NormalizeFail)return false;
  gauge_access_cleanup_succeeded=true;battery_telemetry.operationStatus=0xa6;return true;
 }
 constexpr bool gaugeSetNominalCapacity(uint16_t,uint16_t,bool,char*,size_t){
  ++applies;battery_telemetry.designMah=battery_telemetry.fullMah=battery_telemetry.remainingMah=65;return true;
 }
 constexpr GaugeBootReloadStatus run(char* reason=nullptr,size_t reasonSize=0) BODY
};
constexpr bool faults(){
 using F=Fixture;using S=Hal::GaugeBootReloadStatus;
 F normalize(F::NormalizeFail,true);if(normalize.run()!=S::Critical || normalize.seals!=1 || normalize.opens || normalize.restores!=1 || normalize.applies || normalize.persisted.state!=static_cast<uint8_t>(ReloadState::Failed))return false;
 F prepare(F::PrepareFail,true);if(prepare.run()!=S::Critical || prepare.seals || prepare.opens || prepare.restores || prepare.applies)return false;
 F abort(F::OpenAbortFail);if(abort.run()!=S::Critical || abort.seals!=1 || abort.restores || abort.opens!=1 || abort.applies || abort.persisted.state!=static_cast<uint8_t>(ReloadState::Failed))return false;
 F closed(F::OpenAbortSuccess);if(closed.run()!=S::Critical || closed.seals!=1 || closed.restores || closed.applies)return false;
 F coldAbort(F::OpenAbortFail,true);if(coldAbort.run()!=S::Critical || coldAbort.seals!=2 || coldAbort.restores!=1 || coldAbort.applies)return false;
 F final(F::FinalStoreFail);if(final.run()!=S::Critical || final.persisted.state!=static_cast<uint8_t>(ReloadState::Pending))return false;
 final.gauge_boot_info={};unsigned openBefore=final.opens;if(final.run()!=S::Critical || final.opens!=openBefore)return false;
 for(auto f:{F::ReadFail,F::UnknownHistory,F::LowVoltage,F::Config,F::Cal,F::Learned}){
  F readonly(f,true);readonly.run();if(readonly.seals || readonly.opens || readonly.restores || readonly.applies || readonly.stores)return false;
 }
 F good(F::None,true);return good.run()==S::Applied && good.opens==1 && good.applies==1 && good.seals==2 && good.restores==2;
}
static_assert(faults(), "actual boot coordinator fault injection");
'''
  with tempfile.TemporaryDirectory() as directory:
   source=Path(directory)/'boot_faults.cpp'
   source.write_text('#include "'+(ROOT/'main/hal/mosaico/mosaico_gauge_model.h').as_posix()+'"\n'+harness.replace('BODY',body),encoding='utf8')
   result=subprocess.run([compiler,'-std=c++17','-fsyntax-only',str(source)],capture_output=True,text=True)
   self.assertEqual(result.returncode,0,result.stdout+result.stderr)
 def test_access_abort_reports_cleanup_ownership(self):
  source=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text(encoding='utf8')
  access=source.split('bool Hal::gaugeAccess(',1)[1].split('void Hal::i2c_init()',1)[0]
  self.assertLess(access.index('gauge_access_cleanup_attempted = false'),access.index('const auto report'))
  self.assertLess(access.index('gauge_access_cleanup_succeeded = false'),access.index('const auto report'))
  abort=access.split('const auto abort =',1)[1].split('if (prior == 3)',1)[0]
  self.assertLess(abort.index('gauge_access_cleanup_attempted = true'),abort.index('gauge_return_access(journal)'))
  self.assertIn('gauge_access_cleanup_succeeded = restored',abort)
 def test_startup_before_display_and_network(self):
  source=(ROOT/'main/hal/hal.cpp').read_text(encoding='utf8')
  init=source.split('void Hal::init()',1)[1].split('Hal::Diagnostics',1)[0]
  self.assertLess(init.index('pmic_init();'),init.index('gaugeBootReload('))
  self.assertLess(init.index('gaugeBootReload('),init.index('display_init();'))
  self.assertIn('#ifdef MOSAICO_BOARD',init.split('gaugeBootReload(',1)[0])
 def test_idle_deferred_only_and_rate_limited(self):
  source=(ROOT/'main/host/network_quota.cpp').read_text(encoding='utf8')
  # TWT retained-association also reports phase2, but must not perform the
  # radio-off-only gauge reload. Select the original deferred-gauge branch.
  prefix,tail=source.split('// Deferred preconditions',1)
  idle=prefix.rsplit('setPhase(2);',1)[1]+tail.split('continue;',1)[0]
  self.assertIn('GaugeBootReloadStatus::Deferred',idle)
  self.assertIn('esp_timer_get_time() >= nextGaugeCheckUs',idle)
  self.assertIn('60LL * 1000000',idle)
  self.assertIn('std::min<uint32_t>(waitMs, 60000)',idle)
 def test_usb_status_does_not_trigger_reload(self):
  source=(ROOT/'main/debug/serial_debug.cpp').read_text(encoding='utf8')
  cmd=source.split('if (std::strcmp(command, "gauge-boot") == 0)',1)[1].split('if (std::strcmp(command, "runtime-restart")',1)[0]
  self.assertIn('gaugeBootReloadInfo()',cmd)
  for mutator in ('gaugeBootReload(', 'gaugeAccess(', 'gaugeSetNominalCapacity(', 'gaugeReconcileNominal('):
   self.assertNotIn(mutator,cmd)
 def test_normal_restart_is_explicit_and_not_download(self):
  source=(ROOT/'main/debug/serial_debug.cpp').read_text(encoding='utf8')
  cmd=source.split('if (std::strcmp(command, "runtime-restart") == 0)',1)[1].split('if (std::strcmp(command, "gauge-selftest")',1)[0]
  self.assertIn('"CONFIRM"',cmd); self.assertIn('power.locked || power.phase != 0',cmd)
  self.assertIn('GaugeBootReloadStatus::Critical',cmd); self.assertIn('GetHAL().reboot()',cmd)
  for forbidden in ('FORCE_DOWNLOAD_BOOT','gaugeAccess(', 'gaugeSetNominalCapacity(', 'nvs_flash_erase'):
   self.assertNotIn(forbidden,cmd)
if __name__=='__main__': unittest.main()
