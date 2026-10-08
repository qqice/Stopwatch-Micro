"""Pure C++ trial traces and ownership guards; no AP/power acceptance claim."""
from pathlib import Path
import subprocess
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[1]
COMPILER = Path('C:/Espressif/tools/riscv32-esp-elf/esp-15.2.0_20251204/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe')


class TwtTests(unittest.TestCase):
    def test_actual_cleanup_source_fault_traces(self):
        source = (ROOT/'main/host/network_quota.cpp').read_text()
        body = 'void NetworkQuota::cancelTwtTrial' + source.split('void NetworkQuota::cancelTwtTrial', 1)[1].split('void NetworkQuota::serviceTwtTrial', 1)[0]
        body = body.replace('void NetworkQuota::', 'constexpr void Owner::')
        code = r'''
#include "main/host/mosaico_twt_model.h"
using esp_err_t=int; using wifi_ps_type_t=int;
constexpr int ESP_OK=0,FLOW_ID_ALL=8,ESP_ERR_INVALID_STATE=-2;
struct Owner {
 MosaicoTwt::Model _twt;
 bool _twt_submitted=false,_twt_ps_saved=false,_twt_negotiation_pending=false;
 bool _twt_cleanup_failed=false,_wifi_running=true,_connected=true;
 bool _twt_teardown_sent=false,_twt_teardown_ack=false,_twt_cleanup_barrier_needed=false;
 int64_t now=1;
 constexpr int64_t esp_timer_get_time(){return now;}
 uint8_t _twt_cleanup_attempts=0;
 int _twt_saved_ps=2;
 int bitmap=0,queryError=0,teardownError=0,stopError=0,restoreError=0;
 int stops=0,disconnects=0,teardowns=0,restores=0,publishes=0;
 constexpr int esp_wifi_sta_itwt_teardown(int){++teardowns;return teardownError;}
 constexpr int esp_wifi_sta_itwt_get_flow_id_status(int* out){*out=bitmap;return queryError;}
 constexpr int esp_wifi_disconnect(){++disconnects;return 0;}
 constexpr int esp_wifi_stop(){++stops;return stopError;}
 constexpr int esp_wifi_set_ps(int){++restores;return restoreError;}
 constexpr void recordWifiRunning(bool value){_wifi_running=value;}
 constexpr void publishTwt(){++publishes;}
 constexpr void cancelTwtTrial(MosaicoTwt::Stop);
 constexpr void cleanupTwtTrial();
};
''' + body + r'''
constexpr bool cleanupTraces(){
 Owner accepted;accepted._twt_submitted=true;accepted.bitmap=0;
 accepted.cancelTwtTrial(MosaicoTwt::Stop::Explicit);
 if(!accepted._twt_submitted||accepted.teardowns!=1||accepted.restores||!accepted._twt.state.cleanupPending)return false;
 accepted.cleanupTwtTrial();if(accepted.teardowns!=1||accepted.stops)return false;
 accepted._twt_teardown_ack=true;accepted.cleanupTwtTrial();
 if(accepted._twt_submitted||accepted.stops||accepted._twt.state.cleanupPending)return false;
 Owner pending;pending._twt_submitted=pending._twt_negotiation_pending=true;pending.bitmap=0;
 pending.cancelTwtTrial(MosaicoTwt::Stop::Ota);
 if(pending.stops||pending.teardowns||!pending._twt.state.cleanupPending||pending._twt.state.cleanupStage!=1)return false;
 pending._twt_negotiation_pending=false;pending.cleanupTwtTrial();
 if(pending.teardowns!=1||pending.stops||!pending._twt_submitted)return false;
 pending._twt_teardown_ack=true;pending.cleanupTwtTrial();
 if(pending._twt_submitted||pending.stops||pending._twt.state.cleanupPending)return false;
 Owner timeout;timeout._twt_submitted=true;
 timeout.cancelTwtTrial(MosaicoTwt::Stop::Timeout);
 timeout.now=timeout._twt.state.cleanupDeadlineUs;timeout.cleanupTwtTrial();
 if(!timeout._twt_cleanup_failed||!timeout._twt_submitted||timeout.stops||timeout.teardowns!=1)return false;
 timeout.cleanupTwtTrial();if(timeout.teardowns!=1)return false;
 Owner barrier;barrier._twt_submitted=true;barrier.bitmap=1;
 barrier.cancelTwtTrial(MosaicoTwt::Stop::Explicit);barrier._twt_teardown_ack=true;barrier.cleanupTwtTrial();
 if(!barrier._twt_cleanup_failed||barrier.stops||barrier.disconnects||!barrier._twt_submitted)return false;
 Owner ps;ps._twt_ps_saved=true;ps.restoreError=-1;
 ps.cancelTwtTrial(MosaicoTwt::Stop::Explicit);
 if(!ps._twt_ps_saved||ps.stops||!ps._twt.state.cleanupPending)return false;
 ps.cleanupTwtTrial();
 if(!ps._twt_ps_saved||!ps._twt_cleanup_failed||ps.restores!=2||ps.stops)return false;
 ps.cleanupTwtTrial();if(ps.restores!=2)return false;
 Owner rejected;rejected._twt_cleanup_barrier_needed=rejected._twt_ps_saved=true;
 rejected.cancelTwtTrial(MosaicoTwt::Stop::Rejected);
 if(rejected.teardowns||rejected.stops||rejected._twt.state.cleanupPending||rejected.restores!=1)return false;
 Owner recovered;recovered._twt_ps_saved=true;recovered.restoreError=-1;
 recovered.cancelTwtTrial(MosaicoTwt::Stop::Explicit);recovered.restoreError=0;
 recovered.cleanupTwtTrial();
 if(recovered._twt_ps_saved||recovered._twt_cleanup_failed||recovered._twt.state.cleanupPending)return false;
 return true;
}
static_assert(cleanupTraces(), "actual cleanup fault traces");
'''
        self.compile_cpp(code)

    def compile_cpp(self, code):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d)/'twt.cpp'; src.write_text(code)
            result = subprocess.run([str(COMPILER), '-std=c++17', '-fsyntax-only', '-I', str(ROOT), str(src)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)

    def test_cpp_trial_fault_traces(self):
        code = r'''
#include "main/host/mosaico_twt_model.h"
#include <initializer_list>
using namespace MosaicoTwt;
constexpr Result good(uint16_t id) { Result r; r.id=id; r.flow=5; r.status=1; r.mantissa=512; r.exponent=11; r.duration=64; return r; }
constexpr bool traces() {
 Model m;
 if(m.live() || m.state.requested!=Mode::Off)return false;
 if(!m.start(Mode::On,0)||m.state.id!=1)return false;
 m.end(Stop::Unsupported);
 if(m.live()||m.state.stop!=Stop::Unsupported)return false;
 if(!m.start(Mode::On,1))return false;
 m.state.stage=Stage::Negotiating;
 auto r=good(m.state.id);r.status=0; // ESP_OK is not negotiation success
 if(m.accept(r)||m.live()||m.state.stop!=Stop::Rejected)return false;
 m.start(Mode::On,2);m.state.stage=Stage::Negotiating;
 if(!m.accept(good(m.state.id))||m.state.intervalUs!=1048576||m.state.durationUs!=16384)return false;
 if(!m.check(3,false,true))return false;
 if(m.check(m.state.expiryUs,false,true)||m.state.stop!=Stop::Expired)return false;
 m.start(Mode::On,4);m.state.stage=Stage::Negotiating;
 if(m.check(m.state.setupDeadlineUs,false,true)||m.state.stop!=Stop::Timeout)return false;
 auto late=good(m.state.id);
 m.start(Mode::Off,5);
 if(m.accept(late)||m.live()||m.state.lateEvents!=1)return false;
 m.start(Mode::On,6);m.state.stage=Stage::Negotiating;
 if(m.accept(late)||m.state.stage!=Stage::Negotiating)return false;
 if(!m.accept(good(m.state.id)))return false;
 if(m.check(7,true,true)||m.state.stop!=Stop::Ota)return false;
 m.start(Mode::On,8);m.state.stage=Stage::Negotiating;m.accept(good(m.state.id));
 if(m.check(9,false,false)||m.state.stop!=Stop::Awake)return false;
 m.start(Mode::Baseline,10);m.state.stage=Stage::Baseline;m.state.setupDeadlineUs=0;
 if(!m.check(20000000,false,true)||m.state.stage!=Stage::Baseline)return false;
 m.end(Stop::Lost);if(m.live())return false;
 m.start(Mode::Baseline,10);m.state.stage=Stage::Baseline;m.state.setupDeadlineUs=0;
 if(m.check(11,false,false)||m.state.stop!=Stop::Awake)return false;
 m.start(Mode::On,10);
 if(m.check(11,false,false)||m.state.stop!=Stop::Awake)return false;
 m.start(Mode::On,11);m.state.stage=Stage::Negotiating;r=good(m.state.id);r.exponent=12;
 if(m.accept(r)||m.state.stop!=Stop::Invalid)return false; // >2 seconds
 m.start(Mode::On,12);m.state.stage=Stage::Negotiating;r=good(m.state.id);r.exponent=255;
 if(m.accept(r)||m.state.stop!=Stop::Invalid)return false; // guard shift
 m.start(Mode::On,13);m.state.stage=Stage::Negotiating;r=good(m.state.id);r.mantissa=1;r.exponent=0;
 if(m.accept(r)||m.state.stop!=Stop::Invalid)return false;
 m.start(Mode::Off,14);
 if(m.state.lastFailure!=Stop::Invalid||m.state.stop!=Stop::Explicit)return false;
 m.nextId=32767;if(m.start(Mode::On,14))return false;
 return true;
}
static_assert(traces(), "trial safety traces");
'''
        self.compile_cpp(code)

    def test_long_lease_and_cycle_ring(self):
        self.compile_cpp(r'''
#include "main/host/mosaico_twt_model.h"
#include <initializer_list>
using namespace MosaicoTwt;
constexpr bool longLease() {
 Model m; if(!m.start(Mode::Baseline, 7, 1800)) return false;
 if(m.state.expiryUs!=1800000007LL || m.state.setupDeadlineUs!=10000007LL) return false;
 if(m.start(Mode::On,8,1200)||m.state.id!=1) return false;
 m.state.stage=Stage::Baseline; m.state.setupDeadlineUs=0;
 if(!m.check(1799999999LL,false,true)||m.check(1800000007LL,false,true)) return false;
 if(m.state.stop!=Stop::Expired || encodeRequest(Mode::On,1800)!=6 || encodeRequest(Mode::Baseline,600)!=1) return false;
 if(encodeRequest(Mode::On,1200)!=-1 || !m.start(Mode::On,9) || m.state.expiryUs!=600000009LL) return false;
 CycleRing r; Cycle c; for(int i=0;i<31;++i){c.cycleSeq=i;r.push(c);}
 r.push(c); if(r.dropped!=1) return false;
 for(int i=0;i<31;++i){if(!r.pop(c)||c.cycleSeq!=unsigned(i))return false;}
 if(r.pop(c))return false; r.push(c);return r.pop(c)&&c.dropped==1;
}
static_assert(longLease(), "lease atomic encoding and bounded ring");
''')

    def test_actual_cycle_owner(self):
        source = (ROOT/'main/host/network_quota.cpp').read_text()
        body = 'void NetworkQuota::reconcileTwtCycleIdentity' + source.split('void NetworkQuota::reconcileTwtCycleIdentity', 1)[1].split('MosaicoTwt::Snapshot NetworkQuota::twtSnapshot', 1)[0]
        body = body.replace('void NetworkQuota::', 'constexpr void Owner::')
        self.compile_cpp(r'''
#include "main/host/mosaico_twt_model.h"
#include <initializer_list>
using namespace MosaicoTwt;
constexpr int ESP_OK=0;
struct wifi_ap_record_t {};
struct Flag { bool value=false; constexpr bool load() const{return value;} };
struct Owner {
 Model _twt; CycleRing _twt_cycles; Flag _twt_observe;
 bool _twt_cycle_open=false,_wifi_running=true,associated=true;
 uint32_t _twt_cycle_seq=0;uint16_t _twt_cycle_trial_id=0;
 int _twt_mux=0;int64_t now=1;
 constexpr int64_t esp_timer_get_time(){return now;}
 constexpr int esp_wifi_sta_get_ap_info(wifi_ap_record_t*){return associated?0:1;}
 constexpr void portENTER_CRITICAL(int*){} constexpr void portEXIT_CRITICAL(int*){}
 constexpr void reconcileTwtCycleIdentity();
 constexpr void recordTwtCycle(CyclePhase, CycleReason=CycleReason::None,uint8_t=0);
};
''' + body + r'''
constexpr bool actualOwner() {
 Owner o;Cycle c;
 o.recordTwtCycle(CyclePhase::Start);if(o._twt_cycles.pop(c))return false;
 o._twt_observe.value=true;o.recordTwtCycle(CyclePhase::Start);
 if(!o._twt_cycles.pop(c)||c.trialId||c.cycleSeq!=1||!c.associated)return false;
 o.associated=false;o.now=2;o.recordTwtCycle(CyclePhase::Fetch,CycleReason::None,5);
 if(!o._twt_cycles.pop(c)||c.fetchFlags!=5||c.associated)return false;
 o.recordTwtCycle(CyclePhase::End,CycleReason::Deadline);
 if(!o._twt_cycles.pop(c)||c.reason!=CycleReason::Deadline||o._twt_cycle_open)return false;
 o.recordTwtCycle(CyclePhase::Fetch);if(o._twt_cycles.pop(c))return false;
 o._twt.start(Mode::On,3,1800);o.recordTwtCycle(CyclePhase::Start);o._twt_cycles.pop(c);
 if(c.trialId!=1||c.cycleSeq!=2)return false;
 o._twt.end(Stop::Expired);o.recordTwtCycle(CyclePhase::End,CycleReason::Cancel);
 if(!o._twt_cycles.pop(c)||c.trialId!=1||c.reason!=CycleReason::Cancel)return false;
 return true;
}
constexpr bool identityTransitions() {
 for (Mode next : {Mode::Baseline, Mode::On}) {
  Owner o;Cycle c;o._twt.start(Mode::Baseline,0,1800);o._twt.state.stage=Stage::Baseline;
  o.recordTwtCycle(CyclePhase::Start);o._twt_cycles.pop(c);
  o.reconcileTwtCycleIdentity();if(o._twt_cycles.pop(c))return false;
  o._twt.start(next,1,1800);o.reconcileTwtCycleIdentity();
  if(!o._twt_cycles.pop(c)||c.trialId!=1||c.reason!=CycleReason::Cancel||o._twt_cycle_open)return false;
  o.recordTwtCycle(CyclePhase::Fetch,CycleReason::None,15);if(o._twt_cycles.pop(c))return false;
  o.recordTwtCycle(CyclePhase::Start);if(!o._twt_cycles.pop(c)||c.trialId!=2)return false;
 }
 Owner observe;Cycle c;observe._twt_observe.value=true;observe.recordTwtCycle(CyclePhase::Start);observe._twt_cycles.pop(c);
 observe.reconcileTwtCycleIdentity();if(observe._twt_cycles.pop(c))return false;
 observe._twt.start(Mode::Baseline,1);observe.reconcileTwtCycleIdentity();
 if(!observe._twt_cycles.pop(c)||c.trialId!=0||c.reason!=CycleReason::Cancel)return false;
 return !observe._twt_cycle_open;
}
static_assert(actualOwner(), "actual network owner anchors");
static_assert(identityTransitions(), "actual owner identity changes never mix evidence");
''')

    def test_owner_and_serial_boundaries(self):
        source = (ROOT/'main/host/network_quota.cpp').read_text()
        serial = (ROOT/'main/debug/serial_debug.cpp').read_text()
        event = source.split('void NetworkQuota::twtEvent', 1)[1].split('void NetworkQuota::cancelTwtTrial', 1)[0]
        for forbidden in ('esp_wifi_', 'MosaicoOta::', 'GetHAL(', 'nvs_', 'ESP_LOG', 'new '):
            self.assertNotIn(forbidden, event)
        command = serial.split('if (std::strcmp(command, "twt")', 1)[1].split('#endif', 1)[0]
        self.assertNotIn('esp_wifi_', command)
        self.assertIn('requestTwtTrial', command); self.assertIn('twtSnapshot', command)
        self.assertIn('requestTwtTrial(requested, leaseSeconds)', command)
        self.assertIn('MosaicoTwt::validLease(leaseSeconds)', command)
        self.assertIn('requestTwtObserve', command)
        self.assertIn('recordTwtCycle(MosaicoTwt::CyclePhase::Start)', source)
        self.assertIn('recordTwtCycle(MosaicoTwt::CyclePhase::Fetch', source)
        owner = source.split('void NetworkQuota::recordTwtCycle', 1)[1].split('MosaicoTwt::Snapshot', 1)[0]
        self.assertNotIn('printf', owner)
        poll = serial.split('void SerialDebug::poll()', 1)[1].split('void SerialDebug::drainUart()', 1)[0]
        self.assertIn('DBG TWT_CYCLE', poll)
        self.assertIn('_reply_uart = twtCycleUart', poll)
        self.assertIn('"twt"', (ROOT/'main/debug/serial_debug_transport.h').read_text())
        service = source.split('void NetworkQuota::serviceTwtTrial', 1)[1].split('void NetworkQuota::setLowClockDiagnostic', 1)[0]
        self.assertGreaterEqual(service.count('reconcileTwtCycleIdentity();'), 2)
        self.assertLess(service.index('_twt.check('), service.index('_twt.accept('))
        self.assertIn('e.setup.id == _twt.state.id', service)
        self.assertIn('phy != WIFI_PHY_MODE_HE20', service)
        self.assertIn('esp_wifi_get_ps', service)
        self.assertNotIn('esp_wifi_set_protocol', source)
        self.assertNotIn('esp_wifi_set_twt_config', source)
        cancel = source.split('void NetworkQuota::cancelTwtTrial', 1)[1].split('void NetworkQuota::serviceTwtTrial', 1)[0]
        self.assertIn('esp_wifi_sta_itwt_teardown(FLOW_ID_ALL)', cancel)
        self.assertNotIn('esp_wifi_disconnect(', cancel)
        self.assertNotIn('esp_wifi_stop(', cancel)
        self.assertLess(cancel.index('if (_twt_submitted && !_twt_teardown_ack)'), cancel.index('esp_wifi_sta_itwt_get_flow_id_status'))
        self.assertIn('_twt_teardown_sent = true', cancel)
        self.assertIn('esp_wifi_set_ps(static_cast<wifi_ps_type_t>(_twt_saved_ps))', cancel)
        ota = source.split('void NetworkQuota::updateFirmware()', 1)[1]
        self.assertLess(ota.index('cancelTwtTrial'), ota.index('requestJson'))
        idle = source.split('const bool trialHold', 1)[1].split('if (!trialHold)', 1)[0]
        self.assertNotIn('fetch(', idle)
        self.assertNotIn('GetTailnetQuota().pause', idle)
        self.assertIn('GetTailnetQuota().ready()', idle)
        self.assertIn('SOC_WIFI_HE_SUPPORT', source)
        self.assertIn('_twt.state.cleanupPending && !_twt_cleanup_failed', source)
        self.assertIn('copied.setup.status = e.status', event)
        self.assertIn('_twt_teardown_sent && e.flow == FLOW_ID_ALL', service)
        self.assertIn('if (e.setup.status == ITWT_TEARDOWN_SUCCESS) _twt_teardown_ack = true', service)
        overflow = service.split('if (overflow)', 1)[1].split('while (true)', 1)[0]
        self.assertNotIn('esp_wifi_', overflow)
        self.assertIn('_twt_cleanup_failed', overflow)
        run = source.split('while (true) {\n#ifdef MOSAICO_BOARD', 1)[1]
        self.assertLess(run.index('serviceTwtTrial('), run.index('MosaicoOta::processLocalRequests()'))
        cleanup_gate = run.split('if (_twt.state.cleanupPending || _twt_cleanup_failed)', 1)[1].split('if (ownsDisplaySettings)', 1)[0]
        self.assertIn('continue;', cleanup_gate)
        # OTA can change after the entry gate. Every same-iteration cleanup
        # gate must exit before the radio-off path, not only skip installation.
        gates = run.split('if (_twt.state.cleanupPending || _twt_cleanup_failed)')[1:]
        self.assertGreaterEqual(len(gates), 2)
        for gate in gates:
            self.assertIn('continue;', gate.split('}', 1)[0])
        self.assertIn('wait(_twt_cleanup_failed ? 1000 : 50)', cleanup_gate)
        self.assertIn('if (_twt.state.cleanupPending || _twt_cleanup_failed)', run)
        self.assertLess(run.index('wait(50); continue;'), run.index('GetTailnetQuota().start()'))
        transfers = run.split('const bool quotaOk = fetch();', 1)[1]
        self.assertLess(transfers.index('serviceTwtTrial('), transfers.index('fetchHistory()'))
        self.assertIn('&& twtHistoryAllowed', transfers)
        self.assertIn('_wifi_running && !_twt.state.cleanupPending && !_twt_cleanup_failed', transfers)
        after_history = transfers.split('historyOk = fetchHistory();', 1)[1]
        self.assertLess(after_history.index('serviceTwtTrial('), after_history.index('wait(locked ?'))
        self.assertIn('twtMeasure && _twt.state.id == twtFetchId', after_history)


if __name__ == '__main__': unittest.main()
