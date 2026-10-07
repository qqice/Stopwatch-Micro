"""BLE monitor policy/actual native status validator; no hardware or full build."""
from pathlib import Path
import json
import re
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
COMPILER=Path('C:/Espressif/tools/riscv32-esp-elf/esp-15.2.0_20251204/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe')
BLE=(ROOT/'main/hal/ble/codex_micro_ble.cpp').read_text()
MODULE=(ROOT/'main/host/mosaico_session_monitor.cpp').read_text()

class SessionMonitorTests(unittest.TestCase):
    def test_actual_cpp_validator_metadata_and_lease(self):
        validator=BLE.split('bool validThreadStatus(',1)[1].split('\n#endif',1)[0]
        effect=BLE.split('CodexMicroLightEffect parseLightEffect(',1)[1].split('\nbool jsonFlag(',1)[0]
        effect='constexpr CodexMicroLightEffect parseLightEffect('+effect.replace('std::strcmp','compareText')
        code=r'''
#include "main/host/mosaico_session_model.h"
#include "main/hal/ble/codex_micro_protocol.h"
#include <cmath>
#include <string_view>
#include <limits>
enum {Obj=1,Num=2,Str=3,Bool=4};
struct cJSON { int type=0; double valuedouble=0; int valueint=0; const char* valuestring=nullptr;
 const cJSON* id=nullptr; const cJSON* c=nullptr; const cJSON* b=nullptr; const cJSON* e=nullptr;
 const cJSON* m=nullptr; const cJSON* s=nullptr; const cJSON* sk=nullptr; const cJSON* sa=nullptr; };
constexpr bool cJSON_IsObject(const cJSON* n) { return n && n->type==Obj; }
constexpr bool cJSON_IsNumber(const cJSON* n) { return n && n->type==Num; }
constexpr bool cJSON_IsString(const cJSON* n) { return n && n->type==Str; }
constexpr bool cJSON_IsBool(const cJSON* n) { return n && n->type==Bool; }
constexpr int compareText(const char* a,const char* b) { return std::string_view(a)==std::string_view(b) ? 0 : 1; }
constexpr const cJSON* objectItem(const cJSON* n,const char* key) {
 if(!n) return nullptr;
 const std::string_view k(key);
 return k=="id"?n->id:k=="c"?n->c:k=="b"?n->b:k=="e"?n->e:k=="m"?n->m:k=="s"?n->s:k=="sk"?n->sk:n->sa;
}
EFFECT_FUNCTION
constexpr bool validThreadStatus(VALIDATOR_BODY
constexpr cJSON number(double value) { cJSON n; n.type=Num; n.valuedouble=value; n.valueint=value>=0 && value<=2147483647 ? static_cast<int>(value):0; return n; }
struct State { uint8_t knownMask=0; std::array<uint32_t,6> lastThreadStatusMs{}; uint32_t connectionGeneration=0; };
constexpr bool test() {
 cJSON id=number(0),c=number(0xffffff),b=number(0),e=number(6),m=number(0),s=number(1),flag=number(1);
 cJSON node; node.type=Obj; node.id=&id; node.c=&c; node.b=&b; node.e=&e;
 bool complete=false;
 if(!validThreadStatus(&node,complete)||!complete) return false;
 for(double slot : {0.,1.,2.,3.,4.,5.}) { id=number(slot); if(!validThreadStatus(&node,complete)) return false; }
 for(double slot : {-1.,.5,5.5,6.}) { id=number(slot); if(validThreadStatus(&node,complete)) return false; }
 id=number(0); id.type=Bool; if(validThreadStatus(&node,complete)) return false; id=number(0);
 b=number(1); if(!validThreadStatus(&node,complete)) return false;
 b=number(1.001); if(validThreadStatus(&node,complete)) return false;
 b=number(-.001); if(validThreadStatus(&node,complete)) return false;
 b=number(0); c=number(16777216); if(validThreadStatus(&node,complete)) return false;
 c=number(1.5); if(validThreadStatus(&node,complete)) return false; c=number(0xffffff);
 e=number(7); if(validThreadStatus(&node,complete)) return false;
 e=number(1.5); if(validThreadStatus(&node,complete)) return false;
 e.type=Str; e.valuestring="snake"; if(!validThreadStatus(&node,complete)||!complete) return false;
 e.valuestring="unrecognized"; if(validThreadStatus(&node,complete)) return false; e=number(1);
 b.valuedouble=std::numeric_limits<double>::quiet_NaN(); if(validThreadStatus(&node,complete)) return false;
 b.valuedouble=std::numeric_limits<double>::infinity(); if(validThreadStatus(&node,complete)) return false;
 b=number(0);
 node.c=nullptr; node.b=nullptr; node.e=nullptr; node.m=&m; node.sk=&flag; node.sa=&flag;
 if(!validThreadStatus(&node,complete)||complete) return false; // Partial fields are not receipt evidence.
 flag=number(2); if(validThreadStatus(&node,complete)) return false; flag=number(1);
 State state; MosaicoSessions::clearKnown(state,7);
 if(state.knownMask || state.connectionGeneration!=7) return false;
 MosaicoSessions::markKnown(state,2,100);
 if(state.knownMask!=4 || state.lastThreadStatusMs[2]!=100) return false;
 MosaicoSessions::markKnown(state,2,200);
 if(state.knownMask!=4 || state.lastThreadStatusMs[2]!=100) return false; // Same-value receipt is not change.
 MosaicoSessions::markKnown(state,4,250);
 MosaicoSessions::markKnown(state,2,300,true);
 if(state.lastThreadStatusMs[2]!=300 || state.lastThreadStatusMs[4]!=250) return false;
 MosaicoSessions::markKnown(state,2,400); // m/sk/sa/s-only is not semantic change.
 if(state.lastThreadStatusMs[2]!=300) return false;
 MosaicoSessions::clearKnown(state,8);
 if(state.knownMask || state.lastThreadStatusMs[2] || state.connectionGeneration!=8) return false;
 using MosaicoSessions::radioAllowed;
 return radioAllowed(true,true,true,false,false) && !radioAllowed(false,true,true,false,false) &&
  !radioAllowed(true,false,true,false,false) && !radioAllowed(true,true,false,false,false) &&
  !radioAllowed(true,true,true,true,false) && !radioAllowed(true,true,true,false,true);
}
static_assert(test());
'''.replace('EFFECT_FUNCTION',effect).replace('VALIDATOR_BODY',validator)
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'session.cpp'; source.write_text(code)
            result=subprocess.run([str(COMPILER),'-std=c++17','-fsyntax-only','-I'+str(ROOT),str(source)],capture_output=True,text=True)
            log=ROOT/'.artifacts/mosaico/session-monitor-constexpr.log'; log.parent.mkdir(parents=True,exist_ok=True)
            log.write_text(result.stdout+result.stderr)
            self.assertEqual(result.returncode,0,str(log))

    def test_startup_failure_and_radio_owners(self):
        main=(ROOT/'main/main.cpp').read_text()
        self.assertLess(main.index('MosaicoSessions::init();'),main.index('GetNetworkQuota().begin();'))
        self.assertLess(main.index('GetMooncake().update();'),main.index('MosaicoSessions::service('))
        init=MODULE.split('void init()',1)[1].split('void setEnabled(',1)[0]
        self.assertNotIn('esp_restart',init); self.assertIn('quota remains available',init)
        service=MODULE.split('void service(',1)[1].split('bool snapshot(',1)[0]
        self.assertIn('if (beginSucceeded && hidReady)',service)
        setter=MODULE.split('void setEnabled(',1)[1].split('bool enabled()',1)[0]
        for forbidden in ('GetCodexMicroBle','nvs_','GetHAL','esp_ble_'): self.assertNotIn(forbidden,setter)
        network=(ROOT/'main/host/network_quota.cpp').read_text()
        self.assertIn('locked || !MosaicoSessions::enabled() || MosaicoOta::busy()',network)
        self.assertIn('GetCodexMicroBle().requestRadioIdle(true);',network)

    def test_invalid_batch_and_generation_are_all_or_nothing(self):
        update=BLE.split('bool CodexMicroBle::updateThreadLighting(',1)[1].split('void CodexMicroBle::updateLightingSide(',1)[0]
        self.assertLess(update.index('!validThreadStatus(candidate, complete)'),update.index('xSemaphoreTake('))
        self.assertLess(update.index('generation != _connection_generation.load'),update.index('updateLightingSide(light, value)'))
        self.assertIn('if (completeMask || statusMask)',update)
        self.assertIn('if (completeMask && !changed) ++_state.revision;',update)
        self.assertIn('((_state.knownMask & bit) && (statusMask & bit))',update)
        self.assertIn('before.color != after.color',update)
        self.assertIn('before.brightness != after.brightness || before.effect != after.effect',update)
        self.assertIn('MosaicoSessions::markKnown(_state, slot, now, semanticChanged)',update)
        connect=BLE.split('void CodexMicroBle::onConnected(',1)[1].split('bool CodexMicroBle::markProtocolReady(',1)[0]
        self.assertIn('MosaicoSessions::clearKnown(_state, _connection_generation.load());',connect)
        handshake=BLE.split('bool CodexMicroBle::markProtocolReady(',1)[1].split('void CodexMicroBle::recoverHalfOpenConnection(',1)[0]
        self.assertNotIn('markKnown',handshake)
        begin=BLE.split('bool CodexMicroBle::begin()',1)[1].split('void CodexMicroBle::poll()',1)[0]
        self.assertIn('return _begin_succeeded.load();',begin)
        self.assertLess(begin.index('unable to allocate required BLE'),begin.index('_begin_succeeded.store(true)'))

    def test_terminal_failure_preserves_actual_connection_and_disables_lease(self):
        fail=BLE.split('void CodexMicroBle::failMonitorLink()',1)[1].split('\n#endif',1)[0]
        self.assertIn('_link_failed.exchange(true)',fail)
        self.assertIn('MosaicoSessions::clearKnown(_state, generation)',fail)
        self.assertIn('_state.protocolReady = false;',fail)
        for forbidden in ('esp_restart','_connected.store','_state.connected','esp_ble_remove_bond','initializeController'):
            self.assertNotIn(forbidden,fail)
        self.assertIn('if (_link_failed.load()) requested = true;',BLE)
        self.assertIn('if (failed) effective.store(false);',MODULE)
        self.assertIn('next.radioRequestedEnabled = allow && !failed;',MODULE)

    def test_configured_target_syntax(self):
        db=ROOT/'.artifacts/mosaico/ota-build/compile_commands.json'
        if not db.exists(): self.skipTest('target configuration unavailable')
        entries=json.loads(db.read_text()); logs=[]
        for filename in ('main.cpp','network_quota.cpp','codex_micro_ble.cpp','mosaico_session_monitor.cpp','bt_config.c'):
            lookup='network_quota.cpp' if filename=='mosaico_session_monitor.cpp' else filename
            entry=next(e for e in entries if e['file'].replace('\\','/').endswith('/'+lookup))
            command=re.sub(r' -o \S+ -c ', ' -fsyntax-only ',entry['command'])
            if filename=='mosaico_session_monitor.cpp': command=command.replace(entry['file'],str(ROOT/'main/host/mosaico_session_monitor.cpp'))
            result=subprocess.run(command,cwd=entry['directory'],capture_output=True,text=True)
            logs.append(filename+'\n'+result.stdout+result.stderr)
            log=ROOT/'.artifacts/mosaico/session-monitor-syntax.log'; log.write_text('\n'.join(logs))
            self.assertEqual(result.returncode,0,str(log))

    def test_actual_mosaico_preprocessed_code_has_no_restart_or_bond_reset(self):
        db=ROOT/'.artifacts/mosaico/ota-build/compile_commands.json'
        if not db.exists(): self.skipTest('target configuration unavailable')
        entry=next(e for e in json.loads(db.read_text()) if e['file'].replace('\\','/').endswith('/codex_micro_ble.cpp'))
        command=re.sub(r' -o \S+ -c ', ' -E -P ',entry['command'])
        result=subprocess.run(command,cwd=entry['directory'],capture_output=True,text=True)
        calls=re.findall(r'\besp_restart\s*\(\s*\)\s*;',result.stdout)
        log=ROOT/'.artifacts/mosaico/session-monitor-no-reboot.log'
        log.write_text(f'preprocess_exit={result.returncode}\nrestart_calls={len(calls)}\n'+result.stderr)
        self.assertEqual(result.returncode,0,str(log)); self.assertFalse(calls,str(log))
        pairing=result.stdout.split('bool CodexMicroBle::resetPairing()',1)[1].split('bool CodexMicroBle::schedulePairingRestart()',1)[0]
        self.assertIn('return false;',pairing); self.assertNotIn('esp_ble_remove_bond_device',pairing)
        for method,next_method in [('sendKey','sendJoystick'),('sendJoystick','sendJoystickButton'),('sendJoystickButton','sendEncoderSteps'),('sendEncoderSteps','inputTaskEntry')]:
            body=result.stdout.split(f'bool CodexMicroBle::{method}(',1)[1].split(f'CodexMicroBle::{next_method}(',1)[0]
            self.assertIn('return false;',body)
            self.assertNotIn('queueInput(',body)

if __name__=='__main__': unittest.main()
