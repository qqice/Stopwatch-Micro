"""Gauge-only UI supply inference; not physical 5V or OTA safety acceptance."""
import re, subprocess, tempfile, unittest
from pathlib import Path

R=Path(__file__).resolve().parents[1]
V=R/'main/apps/app_codex_micro/view'
CPP=(V/'view_mosaico.cpp').read_text(encoding='utf8')

class ChargeSupplyUiTests(unittest.TestCase):
    def test_source_integration_and_scope(self):
        profile=CPP.split('void CodexMicroView::refreshDisplaySettings()',1)[1].split('void CodexMicroView::applyBurnInShift',1)[0]
        self.assertIn('const bool chargeProfile = _chargeSupply.external;',profile)
        self.assertNotIn('tud_',profile)
        battery=CPP.split('void CodexMicroView::refreshBattery(',1)[1].split('void CodexMicroView::refreshClock',1)[0]
        self.assertEqual(battery.count('batteryTelemetry(false)'),1)
        self.assertNotIn('isBatteryCharging()',battery)
        self.assertIn('_chargeSupply.update(telemetry.valid, telemetry.currentMa)',battery)
        self.assertIn('_batteryCharging = telemetry.valid && telemetry.currentMa > 3;',battery)
        self.assertIn('if (_chargeSupply.external) lv_obj_remove_flag(_boltIcon',battery)
        # Deliberately unchanged: the inference does not widen OTA safety.
        self.assertIn('MosaicoOta::manualInstallPowerSafe(telemetry, tud_mounted(), critical)',battery)
        self.assertIn('setMotion(_batteryIcon, phase, _batteryValid && _batteryCharging)',CPP)
        self.assertIn('GetHAL().millis() - _batteryReadTick >= 5000',CPP)
        self.assertIn('refreshElapsed >= 60000U && refreshElapsed < 0x80000000U',CPP)
        self.assertNotIn('lv_timer_create',CPP)

    def test_actual_model_and_refresh_prefix_boundaries(self):
        compilers=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
        if not compilers:self.skipTest('embedded compiler unavailable')
        start=CPP.index('void CodexMicroView::refreshBattery(')
        prefix=CPP[start:CPP.index('    const bool critical',start)]
        prefix=prefix.replace('void CodexMicroView::refreshBattery','constexpr void CodexMicroView::refreshBattery').replace('GetHAL()', '_hal')+'}\n'
        code='#include "'+(V/'charge_supply_state.h').as_posix()+'"\n'+r'''
using namespace mosaico_charge;
constexpr bool boundaries() {
 ChargeSupplyState s;
 s.update(true,0);if(s.external)return false; // Cold full/zero is not evidence.
 for(int i=-3;i<=3;++i){s.update(true,i);if(s.external)return false;}
 s.update(true,4);if(!s.external)return false;
 for(int pass=0;pass<10;++pass)for(int i=-3;i<=3;++i){s.update(true,i);if(!s.external)return false;}
 s.update(true,0);if(!s.external)return false; // Full charge after positive anchor.
 s.update(true,-4);if(s.external)return false;
 s.update(true,0);if(s.external)return false; // No sticky charge state after unplug.
 s.update(true,100);s.update(false,100);if(s.external)return false;
 s.update(true,0);if(s.external)return false;
 s.update(true,4);s.update(false,0);if(s.external)return false;
 s.update(true,4);s.update(true,-1000);return !s.external;
}
struct Telemetry { bool valid=true;int currentMa=0; };
struct Hal { Telemetry sample;int reads=0;bool forced=false;
 constexpr Telemetry batteryTelemetry(bool refresh){++reads;forced=refresh;return sample;}
};
struct CodexMicroView {
 Hal _hal;ChargeSupplyState _chargeSupply;
 unsigned _batteryReadTick=0;bool _batterySeen=false,_batteryValid=false,_batteryCharging=false;
 constexpr void refreshBattery(uint32_t now);
};
'''+prefix+r'''
constexpr bool integration() {
 CodexMicroView v;
 v.refreshBattery(1);if(v._chargeSupply.external || v._batteryCharging)return false;
 v._hal.sample.currentMa=4;v.refreshBattery(2);
 if(!v._chargeSupply.external || !v._batteryCharging)return false;
 v._hal.sample.currentMa=0;v.refreshBattery(3);
 if(!v._chargeSupply.external || v._batteryCharging)return false; // Static bolt, no charging animation.
 v._hal.sample.currentMa=-4;v.refreshBattery(4);
 if(v._chargeSupply.external || v._batteryCharging)return false;
 v._hal.sample.currentMa=0;v.refreshBattery(5);if(v._chargeSupply.external)return false;
 v._hal.sample.currentMa=4;v.refreshBattery(6);
 v._hal.sample.valid=false;v.refreshBattery(7);
 return !v._chargeSupply.external && !v._batteryCharging && v._hal.reads==7 && !v._hal.forced;
}
static_assert(boundaries(),"deadband requires a positive anchor, negative or invalid samples clear it");
static_assert(integration(),"single cached gauge sample drives static supply bolt and actual charging animation separately");
'''
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'supply.cpp';source.write_text(code,encoding='utf8')
            result=subprocess.run([str(compilers[-1]),'-std=c++17','-fsyntax-only',str(source)],capture_output=True,text=True)
            log=R/'.artifacts/mosaico/charge-supply-ui-harness.log';log.write_text(result.stdout+result.stderr,encoding='utf8')
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

if __name__=='__main__':unittest.main()
