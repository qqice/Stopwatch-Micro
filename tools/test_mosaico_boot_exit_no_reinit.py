"""Compile actual boot exit and cleanup blocks with deterministic transport faults; no device."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPILERS = list(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))

class BootExitTests(unittest.TestCase):
    def test_actual_exit_and_finally_faults(self):
        if not COMPILERS:
            self.skipTest('configured compiler unavailable')
        source = (ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text(encoding='utf8')
        block = source.split('    const bool noReinit =',1)[1].split('    if (!exited) {\n        journal.state',1)[0]
        cleanup = source.split('bool gauge_return_access(',1)[1].split('\nesp_err_t gauge_load_reload(',1)[0]
        enum = source.split('enum class GaugeCommand',1)[1].split(';',1)[0]
        code = r'''
#include <cstdint>
#include <initializer_list>
#include "mosaico_gauge_engine_model.h"
namespace mosaico_gauge { constexpr uint16_t NominalMah=65;
struct AccessJournal {uint8_t priorSecurity=3;}; }
namespace mosaico_gauge_status {constexpr uint8_t BootAfter=2;}
enum class GaugeBootReloadStatus {Applied};
enum class GaugeCommand''' + enum + r''';
enum class AccessWord {Seal};
struct Simulation {
 bool boot_factory_reload_scope=true,restore=false,forward=true,boot_exit_seal_only=false;
 bool ack=true,wait=true,known=true,io=true,engineValid=true,discharging=true,seal=true;
 bool fallbackAck=true,fallbackWait=true,legacyExit=true;
 uint16_t security=1,cfg=0,nativeCapacity=65,engineCapacity=65,threshold=65,changeTo=2;
 unsigned noReinit=0,enter=0,reinit=0,seals=0,opReads=0,changeAt=0;
 mosaico_gauge_engine::Snapshot gauge_engine_after{};
 constexpr bool gauge_word(uint8_t reg,uint16_t& out) {
  if(reg==0x3a){++opReads;if(opReads==changeAt)security=changeTo;out=(security<<1)|cfg;
   if(opReads==1&&!boot_exit_seal_only)out|=0x400;
   return known;}
  out=reg==0x0a?discharging:nativeCapacity;return io;
 }
 constexpr bool gauge_control(GaugeCommand command){
  if(command==GaugeCommand::ExitNoReinit)++noReinit;
  if(command==GaugeCommand::EnterConfig){++enter;cfg=0x400;return fallbackAck;}
  return ack;
 }
 constexpr bool gauge_wait_config(bool entering){return entering?fallbackWait:wait;}
 constexpr bool gauge_exit_config(){++reinit;cfg=legacyExit?0:0x400;return legacyExit;}
 constexpr void gauge_engine_locked(mosaico_gauge_engine::Snapshot& out,uint8_t,uint8_t){
  out.validMask=engineValid?11:0;
  out.rawCedv[0]=out.rawSmooth[0]=uint8_t(engineCapacity);
  out.rawCedv[1]=out.rawSmooth[1]=uint8_t(engineCapacity>>8);
  out.rawSmooth[14]=uint8_t(threshold);out.rawSmooth[15]=uint8_t(threshold>>8);
 }
 constexpr bool exit() {
  using namespace mosaico_gauge;
  // First status is the CFG1 pre-command probe; subsequent reads use cfg.
  opReads=0;
  const bool noReinit =''' + block.replace('gauge_word(0x3A, beforeExit)', 'preExit(beforeExit)') + r'''
  return exited;
 }
 constexpr bool preExit(uint16_t& out){out=0x400|(security<<1);return known;}
 constexpr void gauge_delay_ms(unsigned){}
 constexpr bool gauge_access_word(AccessWord){++seals;return seal;}
 constexpr bool gauge_wait_security(uint8_t){return seal;}
 constexpr bool cleanup(const mosaico_gauge::AccessJournal& journal)
''' + cleanup.split(')',1)[1] + r'''
};
constexpr bool cases(){
 Simulation good{};if(!good.exit()||good.noReinit!=1||good.reinit||good.enter)return false;
 for(int fault=0;fault<7;++fault){
  Simulation s{};
  if(fault==0)s.ack=false;if(fault==1)s.wait=false;if(fault==2)s.known=false;
  if(fault==3)s.security=2;if(fault==4)s.cfg=0x400;if(fault==5)s.io=false;if(fault==6)s.engineValid=false;
  if(s.exit()||s.enter||s.reinit)return false;
  s.opReads=2;
  if(fault==2||fault==3||fault==4){if(s.cleanup({})||s.seals||s.reinit)return false;}
 }
 for(int fault=0;fault<3;++fault){Simulation s{};
  if(fault==0)s.nativeCapacity=3000;if(fault==1)s.engineCapacity=3000;if(fault==2)s.threshold=32767;
  if(!s.exit()||s.noReinit!=1||s.enter!=1||s.reinit!=1)return false;
 }
 for(int fault=0;fault<3;++fault){Simulation failedFallback{};failedFallback.nativeCapacity=3000;
  if(fault==0)failedFallback.fallbackAck=false;if(fault==1)failedFallback.fallbackWait=false;
  if(fault==2)failedFallback.legacyExit=false;
  if(failedFallback.exit()||failedFallback.enter!=1||failedFallback.reinit!=(fault==2?1U:0U))return false;
  unsigned prior=failedFallback.reinit;
  if(failedFallback.cleanup({})||failedFallback.seals||failedFallback.reinit!=prior)return false;
 }
 Simulation charge{};charge.discharging=false;charge.threshold=32767;if(!charge.exit()||charge.reinit)return false;
 Simulation ackLost{};ackLost.ack=false;if(ackLost.exit())return false;
 ackLost.opReads=2;if(!ackLost.cleanup({})||ackLost.seals!=1||ackLost.reinit)return false;
 for(unsigned read:{1U,2U,3U}){Simulation changed{};changed.boot_exit_seal_only=true;
  changed.changeAt=read;if(changed.cleanup({})||changed.seals||changed.reinit)return false;}
 for(unsigned read:{1U,2U,3U}){Simulation protectedUnit{};protectedUnit.boot_exit_seal_only=true;
  protectedUnit.changeAt=read;protectedUnit.changeTo=3;
  if(!protectedUnit.cleanup({})||protectedUnit.seals||protectedUnit.reinit)return false;}
 Simulation unknownAckSealed{};unknownAckSealed.ack=false;
 if(unknownAckSealed.exit())return false;
 unknownAckSealed.security=3;unknownAckSealed.opReads=2;
 if(!unknownAckSealed.cleanup({})||unknownAckSealed.seals||unknownAckSealed.reinit)return false;
 Simulation wrongPrior{};wrongPrior.boot_exit_seal_only=true;wrongPrior.security=3;wrongPrior.opReads=2;
 if(wrongPrior.cleanup({2})||wrongPrior.seals||wrongPrior.reinit)return false;
 Simulation old{};old.boot_factory_reload_scope=false;if(!old.exit()||old.noReinit||old.reinit!=1)return false;
 Simulation restored{};restored.restore=true;if(!restored.exit()||restored.noReinit||restored.reinit!=1)return false;
 return true;
}
static_assert(cases(),"boot 0092 and finally cleanup safety");
'''
        # Cleanup acceptance helper is the unchanged existing predicate contract.
        code=code.replace('namespace mosaico_gauge { constexpr', 'namespace mosaico_gauge { constexpr bool configExitAccepted(uint16_t v){return !(v&0x401)&&((v>>1)&3)>=1;} constexpr')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'exit.cpp';path.write_text(code,encoding='utf8')
            result=subprocess.run([str(COMPILERS[0]),'-std=c++17','-fsyntax-only','-I',str(ROOT/'main/hal/mosaico'),str(path)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('if (noReinit) {\n            // No implicit rollback', source)
        self.assertIn('usedExitReinitFallback = true;', source)
        self.assertIn('"nominal_set_not_learned_exit0091_fallback"', source)
        self.assertIn('"nominal_set_not_learned_exit0092"', source)
        self.assertIn('"boot_applied65_normal_sealed_verified_once: %.72s", phaseReason', source)
        self.assertIn('return finish(Status::Applied, appliedReason);', source)
        self.assertIn('boot_exit_seal_only = false; boot_factory_reload_scope = false;', source)

if __name__ == '__main__':
    unittest.main()

