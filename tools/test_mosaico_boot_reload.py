"""Source integration guards; hardware and HAL policy acceptance are separate."""
import unittest
import shutil
import subprocess
import tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
class BootIntegrationTests(unittest.TestCase):
 def test_normal_boot_before_refresh_is_observational_and_immediately_pre_cfg(self):
  source=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text(encoding='utf8')
  writer=source.split('bool Hal::gaugeSetNominalCapacity(',1)[1].split('bool Hal::gaugeReconcileNominal(',1)[0]
  refresh=writer.split('if (boot_factory_reload_scope && !restore) {',1)[1]
  capture,tail=refresh.split('\n    }',1)
  statements='\n'.join(line.strip() for line in capture.splitlines() if not line.strip().startswith('//')).strip()
  self.assertEqual(statements,'''gauge_diagnostic_locked(gauge_boot_before,mosaico_gauge_status::BootBefore,
static_cast<uint8_t>(GaugeBootReloadStatus::Applied));
gauge_engine_locked(gauge_engine_before,mosaico_gauge_status::BootBefore,
static_cast<uint8_t>(GaugeBootReloadStatus::Applied));''')
  self.assertTrue(tail.lstrip().startswith('const bool enterSent = gauge_control(GaugeCommand::EnterConfig);'))
  prefix=writer.split('if (boot_factory_reload_scope && !restore) {',1)[0]
  self.assertTrue(prefix.rstrip().endswith('return report(false, "blocked_preflight_changed_pending");'))
  self.assertIn('!gauge_pair(recheck) || !same_pair(recheck, before)',prefix)
  self.assertIn('!gauge_standard_matches(before)',prefix)
  # Admission/refusal and final-outcome capture keep their existing locations.
  boot=self.boot_source()
  self.assertEqual(boot.count('gauge_diagnostic_locked(gauge_boot_before'),1)
  self.assertEqual(boot.count('gauge_engine_locked(gauge_engine_before'),1)
  self.assertLess(boot.index('gauge_diagnostic_locked(gauge_boot_before'),boot.index('struct DiagnosticFinish'))
  self.assertIn('gauge_engine_locked(gauge_engine_after',boot)
  self.assertIn('gauge_diagnostic_locked(gauge_boot_after',boot)
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
  tokens=['gauge_load_normal_target(normalTarget)','gauge_boot_info.attempted = true',
   'if (normalizePriorSealed)', 'gaugeAccess(GaugeAccessAction::Restore, normalizeReason',
   '} reloadScope;', 'gaugeAccess(GaugeAccessAction::Open, phaseReason',
   'gaugeSetNominalCapacity(', 'gaugeAccess(GaugeAccessAction::Restore, closeReason']
  offsets=[source.index(token) for token in tokens]
  self.assertEqual(offsets,sorted(offsets))
  normalize=source.split('if (normalizePriorSealed)',1)[1].split('struct BootReloadScope',1)[0]
  self.assertEqual(normalize.count('gaugeAccess('),1)
  self.assertNotIn('gauge_store_reload',normalize)
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
  for token in ['gauge_load_normal_target(normalTarget)', 'normalTarget!=NominalMah',
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
  # Diagnostics are observational only and independently covered by gauge
  # status/engine transport and boot-capture tests. Their global-cache RAII
  # cannot be member-injected into this coordinator-only constexpr fixture.
  start=body.index('    // Capture only initialized pre-write evidence.')
  end=body.index('    bool normalizePriorSealed = false;',start)
  body=body[:start]+body[end:]
  scope='''struct BootReloadScope {
        BootReloadScope() { boot_factory_reload_scope = true; }
        ~BootReloadScope() { boot_exit_seal_only = false; boot_factory_reload_scope = false; }
    } reloadScope;'''
  self.assertIn(scope,body)
  body=body.replace(scope,'struct BootReloadScope {} reloadScope;').replace('std::','mock::')
  body=body.replace('NormalGaugeStorageLease lease;', 'MockLease lease{*this};')
  # Dependency injection only: route the old profile reader/coordinator into
  # this fixture. Its real backend/model are exercised by their own tests.
  body=body.replace('GaugeFccBackend reader;', 'GaugeFccBackend reader(*this);')
  body=body.replace('GaugeFccBackend backend(permission);', 'GaugeFccBackend backend(*this,permission);')
  body=body.replace('mosaico_fcc_limit::Coordinator<GaugeFccBackend>', 'MockFccCoordinator<GaugeFccBackend>')
  harness=r'''
#include <array>
namespace mock {
struct mutex {}; struct recursive_mutex {};
template<class T> struct lock_guard { constexpr lock_guard(T&) {} };
template<class... T> constexpr int snprintf(char*, size_t, const char*, T...) {return 0;}
constexpr int strncmp(const char* a,const char* b,size_t n){for(size_t i=0;i<n;++i){if(a[i]!=b[i])return a[i]<b[i]?-1:1;if(!a[i])return 0;}return 0;}
constexpr int memcmp(const mosaico_gauge::AccessJournal* a,const mosaico_gauge::AccessJournal* b,size_t){return a->state==b->state&&a->unsealAttempted==b->unsealAttempted&&a->fullAttempted==b->fullAttempted?0:1;}
constexpr void memcpy(uint8_t* to, const uint8_t* from, size_t size) {for(size_t i=0;i<size;++i)to[i]=from[i];}
}
constexpr int ESP_OK=0, ESP_ERR_NVS_NOT_FOUND=1;
using esp_err_t=int;
using namespace mosaico_gauge;
template<class B> struct MockFccCoordinator {
 B& backend;
 constexpr MockFccCoordinator(B& b,mosaico_fcc_limit::Mode mode):backend(b) {
  if(mode!=mosaico_fcc_limit::Mode::ColdFactory)backend.fixture.badColdMode=true;
 }
 constexpr mosaico_fcc_limit::Result run(){return backend.execute();}
};
struct Hal {
 enum class GaugeBootReloadStatus {Deferred,Skipped,Critical,Applied};
 enum class GaugeAccessAction {Open,Restore};
 struct GaugeBootReloadInfo {GaugeBootReloadStatus status=GaugeBootReloadStatus::Deferred; bool attempted=false; char reason[128]{};};
};
struct GaugePair {uint16_t design=3000,fcc=3000;};
struct Fixture {
 using GaugeBootReloadStatus=Hal::GaugeBootReloadStatus;
 using GaugeAccessAction=Hal::GaugeAccessAction;
 enum Fault {None,NormalizeFail,PrepareFail,OpenAbortFail,OpenAbortSuccess,FinalStoreFail,ReadFail,UnknownHistory,LowVoltage,Config,Cal,Learned,ColdSuccess,ColdFail,RuntimeInvalid,RuntimeUnverified,ColdFinalStoreFail,Historical,HistoricalBad,PendingLatch,BusyLease,FenceDirty,TargetUnset,TargetCorrupt,ApplyFail,PreflightBlocked,PreflightChanged,AccessUnknown,TxUnresolved};
 Fault fault; bool gauge=true, gauge_access_cleanup_attempted=false,gauge_access_cleanup_succeeded=false;
 bool durable=false,badColdMode=false,seenAttemptedPendingSec3=false; uint16_t normalTarget=65; unsigned targetStores=0,stores=0,opens=0,restores=0,seals=0,applies=0,coldRuns=0,profileReads=0;
 ReloadJournal persisted{}; Journal nominal{}; AccessJournal access{};
 mock::mutex battery_mutex; mock::recursive_mutex gauge_transaction_mutex;
 Hal::GaugeBootReloadInfo gauge_boot_info;
 struct Sample {bool valid=true,capacityValid=true;uint16_t operationStatus=0xa6,designMah=3000,fullMah=3000,remainingMah=197;} battery_telemetry;
 struct GaugeFccBackend {
  Fixture& fixture; const char* why="mock_cold_failure";
  constexpr explicit GaugeFccBackend(Fixture& f):fixture(f){}
  constexpr GaugeFccBackend(Fixture& f,const mosaico_fcc_limit::Journal&):fixture(f){}
  constexpr mosaico_fcc_limit::Load loadRaw(const char*,mosaico_fcc_limit::Journal& p){
   using namespace mosaico_fcc_limit;
   ++fixture.profileReads;
   if(fixture.fault==RuntimeInvalid)return Load::Invalid;
   if(fixture.fault!=ColdSuccess&&fixture.fault!=ColdFail&&fixture.fault!=RuntimeUnverified&&fixture.fault!=ColdFinalStoreFail)return Load::Absent;
   p.state=fixture.fault==RuntimeUnverified?mosaico_fcc_limit::State::Pending:mosaico_fcc_limit::State::Verified;
   p.backupValid=p.fullAccessVerified=true;p.before={0x102a,65,100};p.target={0x112a,65,65};
   p.intents=Unseal|Full|Enter|Config|Fcc|TargetVerified|Exit|Seal;
   return Load::Present;
  }
  constexpr mosaico_fcc_limit::Result execute(){
   ++fixture.coldRuns;
   fixture.seenAttemptedPendingSec3=fixture.gauge_boot_info.attempted&&fixture.durable&&
    fixture.persisted.state==static_cast<uint8_t>(ReloadState::Pending)&&
    ((fixture.battery_telemetry.operationStatus>>1)&3)==3;
   if(!fixture.seenAttemptedPendingSec3||fixture.fault==ColdFail)return mosaico_fcc_limit::Result::Failed;
   fixture.battery_telemetry.designMah=fixture.battery_telemetry.fullMah=65;
   fixture.battery_telemetry.operationStatus=0xa6;
   return mosaico_fcc_limit::Result::Verified;
  }
 };
 struct MockLease { bool held; constexpr MockLease(Fixture& f):held(f.fault!=BusyLease){} };
 constexpr bool normal_gauge_fence_locked(){return fault!=FenceDirty;}
 constexpr int gauge_load_normal_target(uint16_t& t){t=normalTarget;return fault==TargetCorrupt?2:ESP_OK;}
 constexpr bool gauge_store_normal_target(uint16_t t){++targetStores;if(fault==PrepareFail||(fault==FinalStoreFail&&targetStores>1))return false;normalTarget=t;return true;}
 constexpr Fixture(Fault f,bool sec2=false):fault(f) {
  if(f==Historical||f==HistoricalBad||f==PendingLatch){
   durable=true;persisted.state=static_cast<uint8_t>(f==PendingLatch?ReloadState::Pending:ReloadState::Failed);
  }
  nominal.action=1;nominal.state=static_cast<uint8_t>(State::VerifiedSealedPrior3);
  nominal.targetDesign=nominal.targetFcc=nominal.lastDesign=nominal.lastFcc=65;
  nominal.originalDesign=nominal.originalFcc=3000;nominal.changedFcc=1;
  access.state=static_cast<uint8_t>(AccessState::Restored);access.priorSecurity=access.lastSecurity=3;
  access.unsealAttempted=access.unsealVerified=access.fullAttempted=access.fullVerified=1;
  if(f==AccessUnknown)access.state=static_cast<uint8_t>(AccessState::Failed);
  if(f==TxUnresolved)nominal.state=static_cast<uint8_t>(State::Pending);
  if(f==TargetUnset)normalTarget=0;
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
  ++stores;if(fault==PrepareFail || ((fault==FinalStoreFail||fault==ColdFinalStoreFail) && stores>1))return false;
  durable=true;persisted=j;return true;
 }
 constexpr int gauge_load_journal(Journal& j,const uint8_t*){j=nominal;return fault==UnknownHistory?2:ESP_OK;}
 constexpr int gauge_load_access(AccessJournal& j,const uint8_t*){j=access;return ESP_OK;}
 constexpr bool gauge_store_journal(Journal& j,const uint8_t*){nominal=j;return true;}
 constexpr void sample_battery_locked(bool){}
 constexpr bool gauge_word(int reg,uint16_t& op){op=reg==0x3c?battery_telemetry.designMah:reg==0x12?battery_telemetry.fullMah:battery_telemetry.operationStatus;return fault!=ReadFail;}
 constexpr bool gauge_identity(){return true;}
 constexpr bool gauge_access_preflight(uint16_t& op,GaugePair& p,bool){op=battery_telemetry.operationStatus;p.design=battery_telemetry.designMah;p.fcc=battery_telemetry.fullMah;return fault!=LowVoltage;}
 constexpr bool gaugeAccess(GaugeAccessAction action,char* reason,size_t) {
  gauge_access_cleanup_attempted=gauge_access_cleanup_succeeded=false;
  if(action==GaugeAccessAction::Open){
   ++opens;
   if(fault==PreflightBlocked||fault==PreflightChanged){
    constexpr char why[]="blocked_preflight";for(unsigned i=0;i<sizeof(why);++i)reason[i]=why[i];
    if(fault==PreflightChanged)access.state=static_cast<uint8_t>(AccessState::Pending);
    return false;
   }
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
  ++applies;if(fault==ApplyFail)return false;battery_telemetry.designMah=battery_telemetry.fullMah=battery_telemetry.remainingMah=65;return true;
 }
 constexpr GaugeBootReloadStatus run(char* reason=nullptr,size_t reasonSize=0) BODY
};
constexpr bool faults(){
 using F=Fixture;using S=Hal::GaugeBootReloadStatus;
 F normalize(F::NormalizeFail,true);if(normalize.run()!=S::Critical || normalize.seals!=1 || normalize.opens || normalize.restores!=1 || normalize.applies || normalize.normalTarget!=65)return false;
 F abort(F::OpenAbortFail);if(abort.run()!=S::Critical || abort.seals!=1 || abort.restores || abort.opens!=1 || abort.applies || abort.normalTarget!=65)return false;
 F closed(F::OpenAbortSuccess);if(closed.run()!=S::Critical || closed.seals!=1 || closed.restores || closed.applies||closed.normalTarget!=65)return false;
 F failed(F::ApplyFail);if(failed.run()!=S::Critical||failed.applies!=1||failed.restores!=1||failed.normalTarget!=65)return false;
 const auto prior=failed.opens;if(failed.run()!=S::Critical||failed.opens!=prior)return false;
 for(auto f:{F::ReadFail,F::UnknownHistory,F::LowVoltage,F::Config,F::Cal,F::Learned,F::TargetUnset,F::TargetCorrupt,F::BusyLease,F::FenceDirty,F::AccessUnknown,F::TxUnresolved}){
  F readonly(f,true);readonly.run();if(readonly.seals || readonly.opens || readonly.restores || readonly.applies || readonly.stores||readonly.targetStores)return false;
 }
 F untouched(F::PreflightBlocked);if(untouched.run()!=S::Deferred||untouched.restores||untouched.applies||untouched.normalTarget!=65||untouched.gauge_boot_info.attempted)return false;
 F changed(F::PreflightChanged);if(changed.run()!=S::Critical||changed.restores!=1||changed.applies||changed.normalTarget!=65)return false;
 F good(F::None,true);return good.run()==S::Applied && good.opens==1 && good.applies==1 && good.seals==2 && good.restores==2&&good.normalTarget==65&&!good.targetStores;
}
static_assert(faults(), "actual normal boot fault injection: target survives deferrals, sameboot failure no replay");
constexpr bool normalRoutes(){
 using F=Fixture;using S=Hal::GaugeBootReloadStatus;
 for(auto f:{F::ColdSuccess,F::ColdFail,F::RuntimeInvalid,F::RuntimeUnverified,F::ColdFinalStoreFail,F::Historical,F::HistoricalBad,F::PendingLatch}){
  F normal(f);if(normal.run()!=S::Applied||normal.coldRuns||normal.profileReads||normal.stores||normal.opens!=1||normal.applies!=1||normal.normalTarget!=65)return false;
  const auto opens=normal.opens;
  if(normal.run()!=S::Applied||normal.opens!=opens)return false;
 }
 return true;
}
static_assert(normalRoutes(), "old failure/profile records not normal authority and never overwritten");
'''
  with tempfile.TemporaryDirectory() as directory:
   source=Path(directory)/'boot_faults.cpp'
   source.write_text('#include "'+(ROOT/'main/hal/mosaico/mosaico_gauge_model.h').as_posix()+'"\n#include "'+(ROOT/'main/hal/mosaico/mosaico_fcc_limit_journal_codec.h').as_posix()+'"\n'+harness.replace('BODY',body),encoding='utf8')
   result=subprocess.run([compiler,'-std=c++17','-fsyntax-only',str(source)],capture_output=True,text=True)
   self.assertEqual(result.returncode,0,result.stdout+result.stderr)
 def test_actual_sealed_apply_single_owner_finally(self):
  compiler=shutil.which('g++') or shutil.which('clang++')
  if not compiler:
   found=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
   compiler=str(found[-1]) if found else None
  if not compiler:self.skipTest('C++ compiler unavailable')
  source=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text(encoding='utf8')
  actual=source.split('bool Hal::gaugeSetNominalCapacity(',1)[1].split('    std::lock_guard<std::mutex> lock(battery_mutex);',1)[0]
  actual=actual[actual.index('{')+1:].replace('std::','mock::').replace('NormalGaugeStorageLease lease;', 'MockLease lease{*this};').replace('MosaicoOta::busy()', 'otaBusy')
  harness=r"""
namespace mock {struct mutex{};struct recursive_mutex{};template<class T>struct lock_guard{constexpr lock_guard(T&){} };template<class...T>constexpr int snprintf(char*,size_t,const char*,T...){return 0;} }
struct Fixture {
 enum class GaugeAccessAction {Open,Restore};enum Fault{None,NoKeys,UnknownKeys,WriteFail,CloseFail,ReconcileFail,BusyAfterOpen};
 Fault fault=None;bool otaBusy=false,stable=true,gauge=true,gauge_access_cleanup_attempted=false,gauge_access_cleanup_succeeded=false;
 uint16_t op=0xa6,dc=3000,fcc=3000,target=0;unsigned depth=0,opens=0,writes=0,closes=0,seals=0,reconciles=0;bool ownerViolation=false;
 mock::mutex battery_mutex;mock::recursive_mutex gauge_transaction_mutex;
 struct MockLease {Fixture& f;bool held;constexpr MockLease(Fixture& v):f(v),held(v.depth||!v.otaBusy){if(held)++f.depth;}constexpr ~MockLease(){if(held)--f.depth;}};
 constexpr bool normal_gauge_fence_locked(){return stable;}
 constexpr bool gauge_word(int r,uint16_t& v){v=r==0x3a?op:r==0x3c?dc:fcc;return true;}
 constexpr bool gaugeAccess(GaugeAccessAction action,char*,size_t){
  if(!depth)ownerViolation=true;
  gauge_access_cleanup_attempted=gauge_access_cleanup_succeeded=false;
  if(action==GaugeAccessAction::Open){++opens;if(fault==NoKeys)return false;
   if(fault==UnknownKeys){gauge_access_cleanup_attempted=true;++seals;return false;}
   op=0xa2;if(fault==BusyAfterOpen)otaBusy=true;return true;
  }
  ++closes;++seals;if(fault==CloseFail)return false;op=0xa6;return true;
 }
 constexpr bool gaugeReconcileNominal(char*,size_t){if(!depth)ownerViolation=true;++reconciles;if(fault==ReconcileFail)return false;target=65;return true;}
 constexpr bool gaugeSetNominalCapacity(uint16_t expectedOld=3000,uint16_t target65=65,bool restore=false,char* reason=nullptr,size_t reasonSize=0){
 ACTUAL
  if(!depth)ownerViolation=true;++writes;if(fault==WriteFail)return false;dc=fcc=65;return true;
 }
};
constexpr bool check(){
 Fixture good;if(!good.gaugeSetNominalCapacity()||good.opens!=1||good.writes!=1||good.closes!=1||good.seals!=1||good.reconciles!=1||good.target!=65||good.depth||good.ownerViolation)return false;
 if(!good.gaugeSetNominalCapacity()||good.opens!=1||good.writes!=1||good.closes!=1)return false;
 Fixture fa;fa.op=0xa2;if(!fa.gaugeSetNominalCapacity()||fa.opens||fa.closes||fa.writes!=1)return false;
 for(auto fault:{Fixture::NoKeys,Fixture::UnknownKeys,Fixture::WriteFail,Fixture::CloseFail,Fixture::ReconcileFail,Fixture::BusyAfterOpen}){
  Fixture f;f.fault=fault;if(f.gaugeSetNominalCapacity()||f.depth||f.ownerViolation||f.target)return false;
  if(fault==Fixture::NoKeys&&(f.writes||f.closes||f.seals))return false;
  if(fault==Fixture::UnknownKeys&&(f.writes||f.closes||f.seals!=1))return false;
  if(fault==Fixture::BusyAfterOpen&&(f.writes||f.closes!=1||f.seals!=1))return false;
  if(fault==Fixture::WriteFail&&(f.writes!=1||f.closes!=1||f.reconciles))return false;
 }
 return true;
}
static_assert(check(),"actual SEALED wrapper one owner, finally once, no reopen/retry, OTA after Open still seals");
""".replace('ACTUAL',actual)
  with tempfile.TemporaryDirectory() as directory:
   path=Path(directory)/'sealed.cpp';path.write_text('#include <initializer_list>\n#include "'+(ROOT/'main/hal/mosaico/mosaico_gauge_model.h').as_posix()+'"\n'+harness)
   result=subprocess.run([compiler,'-std=c++20','-fsyntax-only',str(path)],capture_output=True,text=True)
   self.assertEqual(result.returncode,0,result.stdout+result.stderr)
 def test_normal_reload_uses_existing_physical_not_soc_gate(self):
  source=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text(encoding='utf8')
  full=source.split('bool gauge_quiet_full(',1)[1].split('bool gauge_write_field(',1)[0]
  boot=full.split('if (ok && boot_factory_reload_scope',1)[1].split('return ok &&',1)[0]
  self.assertIn('bootReloadPhysical(operation, mv, temperature',boot)
  self.assertNotIn('soc >=',boot)
  access=source.split('bool gauge_access_preflight(',1)[1].split('uint8_t gauge_observed_security()',1)[0]
  boot=access.split('if (boot_factory_reload_scope || bootReadonlyProbe)',1)[1].split('return mosaico_gauge::quietAccess',1)[0]
  self.assertIn('bootReloadPhysical(operation, mv, temperature',boot)
  self.assertNotIn('soc >=',boot)
  self.assertLess(self.boot_source().index('keepLearnedNominal('),self.boot_source().index('gaugeAccess('))
 def test_actual_normal_policy_native65(self):
  compiler=shutil.which('g++') or shutil.which('clang++')
  if not compiler:
   found=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
   compiler=str(found[-1]) if found else None
  if not compiler:self.skipTest('C++ compiler unavailable')
  source=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text(encoding='utf8')
  actual=source.split('Hal::BatteryTelemetry Hal::batteryPolicyTelemetry(bool refresh)',1)[1].strip()
  self.assertTrue(actual.endswith('}'))
  actual=actual.replace('std::','mock::')
  harness=r"""
namespace mock {struct recursive_mutex{};constexpr int try_to_lock=0;template<class T>struct unique_lock{constexpr unique_lock(T&,int){} constexpr bool owns_lock(){return true;}};}
struct Fixture {
 struct BatteryTelemetry {bool valid=true,capacityValid=true,nominalConfigured=true,knownSafetyCalibrationMismatch=false;
  int16_t currentMa=-25,averageCurrentMa=-25;uint16_t remainingMah=20,fullMah=65,designMah=65,operationStatus=0xa6;};
 mosaico_gauge_physical::Snapshot<BatteryTelemetry> snap{};
 bool stable=true,missing=false;uint16_t normal_gauge_target=65;mock::recursive_mutex gauge_transaction_mutex;
 constexpr auto physicalBatteryTelemetry(bool){return snap;}
 constexpr bool normal_gauge_fence_locked(){return stable;}
 constexpr bool scale_journal_missing_locked(){return missing;}
 constexpr BatteryTelemetry run(bool refresh=false) ACTUAL
};
constexpr bool check(){
 Fixture good;good.snap.context.phase=mosaico_gauge_physical::Phase::Original;
 auto result=good.run();if(!result.valid||!result.capacityValid||!result.nominalConfigured||result.currentMa!=-25||result.remainingMah!=20)return false;
 Fixture unset;unset.normal_gauge_target=0;if(unset.run().valid)return false;
 Fixture old;old.normal_gauge_target=0;old.missing=true;if(!old.run().valid)return false;
 Fixture active;active.stable=false;if(active.run().valid)return false;
 Fixture uncertain;uncertain.snap.context.verified=true;if(uncertain.run().valid)return false;
 Fixture unsafe;unsafe.snap.native.knownSafetyCalibrationMismatch=true;if(unsafe.run().valid)return false;
 Fixture factory;factory.snap.native.nominalConfigured=false;factory.snap.native.designMah=3000;if(factory.run().valid)return false;
 return true;
}
static_assert(check(),"actual native65 policy supports normal target without publishing unit factor; dirty/unknown/factory rejected");
""".replace('ACTUAL',actual)
  with tempfile.TemporaryDirectory() as directory:
   path=Path(directory)/'policy.cpp';path.write_text('#include "'+(ROOT/'main/hal/mosaico/mosaico_gauge_model.h').as_posix()+'"\n#include "'+(ROOT/'main/hal/mosaico/mosaico_gauge_physical_model.h').as_posix()+'"\n'+harness)
   result=subprocess.run([compiler,'-std=c++17','-fsyntax-only',str(path)],capture_output=True,text=True)
   self.assertEqual(result.returncode,0,result.stdout+result.stderr)
 def test_actual_normal_setting_failures(self):
  compiler=shutil.which('g++') or shutil.which('clang++')
  if not compiler:
   found=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
   compiler=str(found[-1]) if found else None
  if not compiler:self.skipTest('C++ compiler unavailable')
  source=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text(encoding='utf8')
  actual=source[source.index('esp_err_t gauge_load_normal_target('):source.index('bool gauge_rollback(')]
  actual=actual.replace('esp_err_t gauge_load_normal_target(', 'constexpr esp_err_t gauge_load_normal_target(').replace('bool gauge_store_normal_target(', 'constexpr bool gauge_store_normal_target(')
  harness=r"""
using esp_err_t=int;using nvs_handle_t=int;
constexpr int ESP_OK=0,ESP_FAIL=2,ESP_ERR_NVS_NOT_FOUND=1,NVS_READONLY=0,NVS_READWRITE=1;
struct Fixture {
 enum Fault {None,OpenRead,OpenWrite,Get,Set,Commit,WrongReadback};Fault fault=None;
 uint16_t value=0,normal_gauge_target=0;unsigned sets=0,commits=0;bool wrote=false;
 constexpr int nvs_open(const char*,int mode,int* h){*h=1;return (fault==OpenRead&&mode==0)||(fault==OpenWrite&&mode==1)?2:0;}
 constexpr int nvs_get_u16(int,const char*,uint16_t* v){if(fault==Get)return 2;*v=fault==WrongReadback&&wrote?3000:value;return 0;}
 constexpr int nvs_set_u16(int,const char*,uint16_t v){++sets;if(fault==Set)return 2;value=v;wrote=true;return 0;}
 constexpr int nvs_commit(int){++commits;return fault==Commit?2:0;}
 constexpr void nvs_close(int){}
 ACTUAL
};
constexpr bool check(){
 Fixture f;uint16_t v=999;if(f.gauge_load_normal_target(v)||v)return false;
 if(!f.gauge_store_normal_target(65)||f.value!=65||f.commits!=1)return false;
 if(!f.gauge_store_normal_target(0)||f.value)return false;
 const auto sets=f.sets;if(f.gauge_store_normal_target(3000)||f.sets!=sets)return false;
 f.value=3000;if(f.gauge_load_normal_target(v)!=ESP_FAIL)return false;
 for(auto fault:{Fixture::OpenRead,Fixture::OpenWrite,Fixture::Get,Fixture::Set,Fixture::Commit,Fixture::WrongReadback}){
  Fixture b;b.fault=fault;if(b.gauge_store_normal_target(65))return false;
 }
 return true;
}
static_assert(check(),"actual normal target strict values, commit/readback and NVS failure rejection");
""".replace('ACTUAL',actual)
  with tempfile.TemporaryDirectory() as directory:
   path=Path(directory)/'target.cpp';path.write_text('#include <initializer_list>\n#include "'+(ROOT/'main/hal/mosaico/mosaico_gauge_model.h').as_posix()+'"\n'+harness)
   result=subprocess.run([compiler,'-std=c++17','-fsyntax-only',str(path)],capture_output=True,text=True)
   self.assertEqual(result.returncode,0,result.stdout+result.stderr)
 def test_actual_storage_lease_scoped(self):
  compiler=shutil.which('g++') or shutil.which('clang++')
  if not compiler:
   found=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
   compiler=str(found[-1]) if found else None
  if not compiler:self.skipTest('C++ compiler unavailable')
  source=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text(encoding='utf8')
  actual='struct NormalGaugeStorageLease {'+source.split('struct NormalGaugeStorageLease {',1)[1].split('\n};',1)[0]+'\n};'
  actual=actual.replace('NormalGaugeStorageLease() {','Api& api; constexpr NormalGaugeStorageLease(Api& a):api(a) {').replace('~NormalGaugeStorageLease()', 'constexpr ~NormalGaugeStorageLease()').replace('MosaicoOta::','api.')
  harness=r"""
struct Api {bool owner=false,ota=false,health=false,admit=true;unsigned acquired=0,released=0;
 constexpr bool currentTaskOwnsStorageLease(){return owner;}
 constexpr bool busy(){return ota;} constexpr bool healthPending(){return health;}
 constexpr bool tryStorageLease(){++acquired;return admit;}
 constexpr void releaseStorageLease(){++released;}
};
ACTUAL
constexpr bool check(){
 Api a;{NormalGaugeStorageLease l(a);if(!l.held||a.acquired!=1||a.released)return false;}if(a.released!=1)return false;
 for(int i=0;i<3;++i){Api b;b.ota=i==0;b.health=i==1;b.admit=false;{NormalGaugeStorageLease l(b);if(l.held)return false;}
 if(b.released||b.acquired!=(i==2?1:0))return false;}
 Api nested;nested.owner=nested.ota=true;{NormalGaugeStorageLease l(nested);if(!l.held||l.acquired)return false;}if(nested.released||nested.acquired)return false;
 return true;
}
static_assert(check(),"production lease is nonblocking, OTA/health gated, exactly released");
""".replace('ACTUAL',actual)
  with tempfile.TemporaryDirectory() as directory:
   path=Path(directory)/'lease.cpp';path.write_text(harness)
   result=subprocess.run([compiler,'-std=c++20','-fsyntax-only',str(path)],capture_output=True,text=True)
   self.assertEqual(result.returncode,0,result.stdout+result.stderr)
 def test_normal_storage_and_no_profile_writer(self):
  source=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text(encoding='utf8')
  for name in ['bool Hal::gaugeAccess(', 'bool Hal::gaugeSetNominalCapacity(', 'Hal::GaugeBootReloadStatus Hal::gaugeBootReload(']:
   block=source.split(name,1)[1].split('\n}',1)[0]
   self.assertLess(block.index('NormalGaugeStorageLease lease'),block.index('transaction(gauge_transaction_mutex)'))
   self.assertIn('lease.held',block)
   self.assertIn('normal_gauge_fence_locked()',block)
  boot=self.boot_source()
  for forbidden in ['GaugeFccBackend','mosaico_fcc_limit::Coordinator','coldRuns','0x000C','0x0041','0x929B']:
   self.assertNotIn(forbidden,boot)
  self.assertNotIn('gauge_store_reload',boot)
  self.assertNotIn('gauge_load_reload',boot)
  self.assertNotIn('gauge_store_normal_target(',boot)
  helper=source.split('bool normal_gauge_fence_locked()\n{',1)[1].split('bool cold_poll_supervisor()',1)[0]
  for token in ['fallbackAllowed()', 'protectedState()', 'factory_scale_maintenance_cached_safe_locked()', 'contrast_maintenance_cached_safe_locked()', 'Phase::Original']:
   self.assertIn(token,helper)
  self.assertNotIn('scale_boot_fence_locked()',helper)
 def test_access_abort_reports_cleanup_ownership(self):
  source=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text(encoding='utf8')
  access=source.split('bool Hal::gaugeAccess(',1)[1].split('void Hal::i2c_init()',1)[0]
  self.assertLess(access.index('gauge_access_cleanup_attempted = false'),access.index('const auto report'))
  self.assertLess(access.index('gauge_access_cleanup_succeeded = false'),access.index('const auto report'))
  abort=access.split('const auto abort =',1)[1].split('if (prior == 3)',1)[0]
  self.assertLess(abort.index('gauge_access_cleanup_attempted = true'),abort.index('gauge_return_access(journal)'))
  self.assertIn('gauge_access_cleanup_succeeded = restored',abort)
 def test_startup_splash_before_protected_reload_and_network(self):
  source=(ROOT/'main/hal/hal.cpp').read_text(encoding='utf8')
  init=source.split('void Hal::init()',1)[1].split('Hal::Diagnostics',1)[0]
  self.assertLess(init.index('pmic_init();'),init.index('gaugeBootReload('))
  self.assertLess(init.index('display_init();'),init.index('display_boot_splash();'))
  self.assertLess(init.index('display_boot_splash();'),init.index('gaugeBootReload('))
  self.assertLess(init.index('gaugeBootReload('),init.index('lvgl_init();'))
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
