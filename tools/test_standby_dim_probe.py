"""Offline actual-source lease checks; not hardware/power acceptance."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

R = Path(__file__).resolve().parents[1]
V = (R / 'main/apps/app_codex_micro/view/view_mosaico.cpp').read_text(encoding='utf8')
A = (R / 'main/apps/app_codex_micro/app_codex_micro.cpp').read_text(encoding='utf8')
S = (R / 'main/debug/serial_debug.cpp').read_text(encoding='utf8')


def function(source, signature):
    start = source.index(signature)
    opening = source.index('{', start)
    level = 1
    end = opening + 1
    while level:
        level += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


class StandbyDimTests(unittest.TestCase):
    def test_actual_lease_cpp(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        if not compiler:
            candidates = sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
            compiler = str(candidates[-1]) if candidates else None
        if not compiler:
            self.skipTest('C++ compiler unavailable')
        functions = '\n'.join('constexpr ' + function(V, sig) for sig in [
            'bool CodexMicroView::standbyDimEligible()',
            'void CodexMicroView::clearStandbyDim()',
            'bool CodexMicroView::standbyDimForDebug(',
            'void CodexMicroView::refreshDisplaySettings()'])
        functions = functions.replace('MosaicoDisplay::snapshot(next)', '_snapshot(next)')
        fixture = r'''
#include <cstdint>
namespace MosaicoDisplay {
struct Config { bool burnIn=false; int chargeBrightness=70, batteryBrightness=40, lockBrightness=30; };
struct Snapshot { Config config{}, effectiveConfig{}; unsigned revision=1, savedRevision=1, runtimeRevision=1; bool pending=false,error=false,temporary=false; };
constexpr bool snapshot(Snapshot&) { return false; }
constexpr Config sanitize(Config c) { return c; }
}
struct Hal { int brightness=30; unsigned writes=0; constexpr void setBackLightBrightness(int b,bool) {brightness=b;++writes;} };
struct CodexMicroView {
 enum class RotationPhase { Idle, Other };
 RotationPhase _rotationPhase=RotationPhase::Idle;
 bool _locked=true,_suppressed=false,_rotationFault=false,root=true,ota=false;
 bool _shiftPending=false,_profileSeen=true,_chargeProfile=false;
 struct { bool external=false; } _chargeSupply;
 unsigned tick=0,_standbyDimDeadline=0,_activity=0;
 int _standbyDimBrightness=-1,_brightness=40,_appliedBrightness=30;
 MosaicoDisplay::Snapshot _displaySettings, bank;
 bool snapshotReady=false; unsigned renders=0;
 constexpr bool _snapshot(MosaicoDisplay::Snapshot& out) { if(!snapshotReady)return false;out=bank;return true; }
 bool _settingsPage=false; Hal hal;
 constexpr Hal& GetHAL(){return hal;}
 constexpr bool ready()const{return root;}
 constexpr bool otaKeepAwake()const{return ota;}
 constexpr unsigned lv_tick_get()const{return tick;}
 constexpr void renderSettings(){++renders;}
 constexpr bool standbyDimEligible()const;
 constexpr void clearStandbyDim();
 constexpr bool standbyDimForDebug(int,uint32_t);
 constexpr void refreshDisplaySettings();
};
'''
        checks = r'''
constexpr bool check() {
 CodexMicroView v;
 if(v.standbyDimForDebug(0,29)||v.standbyDimForDebug(0,301)||v.standbyDimForDebug(-2,180)||v.standbyDimForDebug(101,180))return false;
 if(!v.standbyDimForDebug(0,30)||v.hal.brightness!=0||v._displaySettings.config.lockBrightness!=30||v._displaySettings.effectiveConfig.lockBrightness!=30)return false;
 v.tick=29999;v.refreshDisplaySettings();if(v.hal.brightness!=0)return false;
 v.tick=30000;v.refreshDisplaySettings();if(v.hal.brightness!=30||v._standbyDimBrightness!=-1)return false;
 if(!v.standbyDimForDebug(100,300))return false;
 v.ota=true;v.refreshDisplaySettings();if(v.hal.brightness!=30||v.standbyDimForDebug(0,180))return false;
 v.ota=false;v.standbyDimForDebug(0,180);v._suppressed=true;v.refreshDisplaySettings();if(v.hal.brightness!=30)return false;
 v._suppressed=false;v.standbyDimForDebug(0,180);v._rotationFault=true;v.refreshDisplaySettings();if(v.hal.brightness!=30)return false;
 v._rotationFault=false;v.standbyDimForDebug(0,180);v._locked=false;v.refreshDisplaySettings();if(v.hal.brightness!=40||v.standbyDimForDebug(0,180))return false;
 v._locked=true;v.tick=0xfffffff0U;v.standbyDimForDebug(0,30);v.tick+=30000;v.refreshDisplaySettings();if(v.hal.brightness!=30)return false;
 v.standbyDimForDebug(0,180);if(!v.standbyDimForDebug(-1,180)||v.hal.brightness!=30)return false;
 v.root=false;if(v.standbyDimForDebug(0,180))return false;
 v.root=true;v._rotationPhase=CodexMicroView::RotationPhase::Other;if(v.standbyDimForDebug(0,180))return false;
 // Generic temporary settings take precedence over the legacy dim lease.
 v._rotationPhase=CodexMicroView::RotationPhase::Idle;v._settingsPage=true;v.snapshotReady=true;
 v.bank.temporary=true;v.bank.effectiveConfig.lockBrightness=12;++v.bank.runtimeRevision;
 if(!v.standbyDimForDebug(0,180)||v.hal.brightness!=12||v.renders!=1||v._displaySettings.config.lockBrightness!=30)return false;
 // Runtime-only changes redraw and apply despite unchanged persistent revision.
 v.bank.effectiveConfig.lockBrightness=16;v.bank.effectiveConfig.burnIn=true;++v.bank.runtimeRevision;
 v.refreshDisplaySettings();if(v.hal.brightness!=16||v.renders!=2||!v._shiftPending||v._displaySettings.config.burnIn)return false;
 // Generic restore changes effective values only; legacy override remains bounded independently.
 v.bank.temporary=false;v.bank.effectiveConfig=v.bank.config;++v.bank.runtimeRevision;
 v.refreshDisplaySettings();if(v.hal.brightness!=0||v.renders!=3||v._displaySettings.effectiveConfig.lockBrightness!=30)return false;
 v.clearStandbyDim();if(v.hal.brightness!=30)return false;
 return v._displaySettings.config.lockBrightness==30&&v._displaySettings.config.chargeBrightness==70&&v._displaySettings.config.batteryBrightness==40;
}
static_assert(check(), "actual-source lease safety");
'''
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'probe.cpp'
            p.write_text(fixture + functions + checks, encoding='utf8')
            q = subprocess.run([compiler, '-std=c++17', '-fsyntax-only', str(p)], capture_output=True, text=True)
            self.assertEqual(q.returncode, 0, q.stdout + q.stderr)

    def test_transition_hooks_and_no_persistence(self):
        for sig in ['CodexMicroView::~CodexMicroView()', 'void CodexMicroView::wakeDisplay()', 'void CodexMicroView::setInputSuppressed(']:
            self.assertIn('clearStandbyDim()', function(V, sig))
        wake = function(V, 'void CodexMicroView::wakeDisplay()')
        self.assertLess(wake.index('clearStandbyDim()'), wake.index('if (_rotationFault)'))
        lease = V[V.index('bool CodexMicroView::standbyDimEligible()'):V.index('void CodexMicroView::applyBurnInShift(')]
        for forbidden in ['nvs_', 'requestSave', 'setConfig', 'lv_timer_create', 'esp_timer_create', 'setTouchIdle', 'setCpu', 'sleep(']:
            self.assertNotIn(forbidden, lease)
        status = function(V, 'void CodexMicroView::standbyDimDetails(')
        self.assertNotIn('refreshDisplaySettings()', status)
        self.assertIn('static_cast<int32_t>(now - _standbyDimDeadline) >= 0', status)

    def test_strict_parser_and_uart_scope(self):
        command = S.split('if (std::strcmp(command, "standby-dim") == 0)', 1)[1].split('if (std::strcmp(command, "display-lock")', 1)[0]
        for token in ['parseUnsignedStrict(value, 0, 100, brightness)', 'parseUnsignedStrict(lease, 30, 300, seconds)', 'seconds = 180', '((statusOnly || off) && lease)', 'char details[160]']:
            self.assertIn(token, command)
        self.assertIn('"standby-dim"', (R / 'main/debug/serial_debug_transport.h').read_text())

    def test_s3_fallback_and_mutex(self):
        setter = function(A, 'bool AppCodexMicro::debugStandbyDim(')
        details = function(A, 'void AppCodexMicro::debugStandbyDimDetails(')
        for code in [setter, details]:
            self.assertIn('LvglLockGuard lock;', code)
            self.assertIn('#ifdef MOSAICO_BOARD', code)
        self.assertIn('(void)brightness; (void)leaseSeconds;\n    return false;', setter)
        self.assertIn('override=-1 ready=0 supported=0', details)


if __name__ == '__main__':
    unittest.main()

