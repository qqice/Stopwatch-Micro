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
  self.assertNotIn('near_boot_finish(',boot)
  self.assertEqual(boot.count('mosaico_official_gauge_apply(gauge)'),1)
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
 def test_actual_flow_latch_official_apply_cleanup_order(self):
  source=self.boot_source()
  tokens=['gauge_boot_info.attempted = true','mosaico_official_gauge_apply(gauge)',
          'const bool verified =','if (verified)', 'bool cleanup = observed;']
  offsets=[source.index(token) for token in tokens]
  self.assertEqual(offsets,sorted(offsets))
  for retired in ['gauge_load_normal_target(', 'gaugeAccess(', 'gaugeSetNominalCapacity(', 'near_boot_apply(', 'near_boot_finish(']:
   self.assertNotIn(retired,source)
  self.assertIn('!official.exitAttempted && gauge_exit_config(true)',source)
  self.assertIn('!official.sealAttempted',source)
  self.assertIn('return finish(Status::Skipped, details)',source)
 def test_actual_readonly_guards_precede_all_access(self):
  source=self.boot_source()
  admission=source.split('gauge_boot_info.attempted = true',1)[0]
  self.assertIn('if (gauge_boot_info.attempted ||',admission)
  self.assertIn('if(!lease.held)',admission)
  self.assertNotIn('mosaico_official_gauge_apply(',admission)
  self.assertIn('gauge_diagnostic_locked(gauge_boot_before',admission)
  self.assertLess(source.index('if (!gauge || !selftest())'),source.index('mosaico_official_gauge_apply(gauge)'))
  model=(ROOT/'main/hal/mosaico/mosaico_gauge_model.h').read_text(encoding='utf8')
  exit_policy=model.split('bool configExitAccepted(',1)[1].split('inline bool capacityPolicy',1)[0]
  self.assertIn('security == 1 || security == 3',exit_policy)
  self.assertNotIn('security == 2',exit_policy)
 def test_extracted_boot_faults_constant_evaluated(self):
  import re
  compiler=shutil.which('g++') or shutil.which('clang++')
  if not compiler:
   found=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
   compiler=str(found[-1]) if found else None
  if not compiler:self.skipTest('C++ compiler unavailable')
  body=self.boot_source();body=body[body.index('{'):].strip()
  start=body.index('    // Capture only initialized pre-write evidence.')
  end=body.index('    std::lock_guard<std::mutex> battery(battery_mutex);',body.index('    } diagnosticFinish;'))
  body=body[:start]+body[end:]
  body=re.sub(r'std::lock_guard<[^;]+;', '', body)
  body=body.replace('std::snprintf','mock::snprintf').replace('NormalGaugeStorageLease lease;','NormalGaugeStorageLease lease{leaseAvailable};')
  harness=r"""
#include <cstdint>
#include <cstddef>
#include <initializer_list>
namespace mock {template<class...T>constexpr int snprintf(char*,size_t,const char*,T...){return 0;}}
struct NormalGaugeStorageLease {bool held;};
constexpr int ESP_OK=0;
struct Sim {
 enum class GaugeBootReloadStatus {Deferred,Skipped,Applied,Critical};
 struct {GaugeBootReloadStatus status=GaugeBootReloadStatus::Deferred;bool attempted=false;char reason[128]{};} gauge_boot_info;
 struct Result {bool success=true,exitAttempted=false,sealAttempted=false;} result;
 bool leaseAvailable=true,gauge=true,model=true,readable=true;unsigned applies=0,exits=0,seals=0;
 uint16_t dc=65,fcc=65,op=0x26,normal_gauge_target=0;
 constexpr bool selftest(){return model;}
 constexpr Result mosaico_official_gauge_apply(bool){++applies;return result;}
 constexpr bool gauge_word(uint8_t reg,uint16_t& out){out=reg==0x3a?op:reg==0x3c?dc:fcc;return readable;}
 constexpr bool gauge_exit_config(bool){++exits;return false;}
 constexpr int i2c_master_transmit(bool,const uint8_t*,size_t,int){++seals;return 1;}
 constexpr void gauge_delay_ms(unsigned){}
 constexpr GaugeBootReloadStatus run(char* reason=nullptr,size_t reasonSize=0) BODY
};
constexpr bool cases(){using S=Sim::GaugeBootReloadStatus;
 {Sim s;if(s.run()!=S::Applied||s.applies!=1||s.normal_gauge_target!=65)return false;if(s.run()!=S::Applied||s.applies!=1)return false;}
 {Sim s;s.fcc=62;if(s.run()!=S::Applied||s.fcc!=62||s.applies!=1)return false;}
 {Sim s;s.leaseAvailable=false;if(s.run()!=S::Deferred||s.applies||s.gauge_boot_info.attempted)return false;s.leaseAvailable=true;if(s.run()!=S::Applied)return false;}
 for(unsigned fcc:{0U,66U}){Sim s;s.fcc=fcc;if(s.run()!=S::Skipped||s.normal_gauge_target||s.applies!=1)return false;if(s.run()!=S::Skipped||s.applies!=1)return false;}
 {Sim s;s.result={false,true,false};s.op=0x402;if(s.run()!=S::Skipped||s.exits||s.seals||s.normal_gauge_target)return false;}
 {Sim s;s.result={false,false,true};s.op=0x22;if(s.run()!=S::Skipped||s.seals||s.normal_gauge_target)return false;}
 {Sim s;s.result.success=false;s.op=0x402;if(s.run()!=S::Skipped||s.exits!=1||s.seals)return false;}
 {Sim s;s.readable=false;if(s.run()!=S::Skipped||s.exits||s.seals)return false;}
 {Sim s;s.gauge=false;if(s.run()!=S::Skipped||s.applies)return false;}
 return true;
}
static_assert(cases(),"official boot once, preserve learned FCC, failure visible without OTA gate, no ambiguous cleanup retry");
""".replace('BODY',body)
  with tempfile.TemporaryDirectory() as directory:
   path=Path(directory)/'official_boot.cpp';path.write_text('#include "'+(ROOT/'main/hal/mosaico/mosaico_gauge_model.h').as_posix()+'"\n'+harness)
   result=subprocess.run([compiler,'-std=c++20','-fsyntax-only',str(path)],capture_output=True,text=True)
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
  self.assertNotIn('gaugeAccess(',self.boot_source())
  self.assertIn('fcc > 0 && fcc <= 65',self.boot_source())
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
 constexpr void officialBootVerified(){BOOTCACHE}
 constexpr BatteryTelemetry run(bool refresh=false) ACTUAL
};
constexpr bool check(){
 Fixture good;good.normal_gauge_target=0;good.snap.context.phase=mosaico_gauge_physical::Phase::Original;
 good.snap.context.verified=false;good.officialBootVerified();
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
  success=self.boot_source().split('if (verified) {',1)[1].split('return finish',1)[0]
  cache=success[success.index('normal_gauge_target ='):].strip()
  self.assertEqual(cache,'normal_gauge_target = 65;')
  harness=harness.replace('BOOTCACHE',cache)
  with tempfile.TemporaryDirectory() as directory:
   path=Path(directory)/'policy.cpp' ;path.write_text('#include "'+(ROOT/'main/hal/mosaico/mosaico_gauge_model.h').as_posix()+'"\n#include "'+(ROOT/'main/hal/mosaico/mosaico_gauge_physical_model.h').as_posix()+'"\n'+harness)
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
   if not name.startswith('Hal::GaugeBootReloadStatus'):self.assertIn('normal_gauge_fence_locked()',block)
   else:self.assertNotIn('normal_gauge_fence_locked()',block)
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
