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
  exit_policy=model.split('inline bool configExitAccepted(',1)[1].split('inline bool capacityPolicy',1)[0]
  self.assertIn('security == 1 || security == 3',exit_policy)
  self.assertNotIn('security == 2',exit_policy)
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
