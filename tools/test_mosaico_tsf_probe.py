"""Offline production-method shim. No Wi-Fi driver linking or device access."""
import subprocess
import tempfile
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
CPP=(ROOT/"main/host/network_quota.cpp").read_text()
COMPILERS=sorted(Path("C:/Espressif/tools/riscv32-esp-elf").glob("*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe"))

class TsfProbeTests(unittest.TestCase):
    def test_production_owner_and_callback_faults(self):
        if not COMPILERS: self.skipTest("cross compiler unavailable")
        start=CPP.index("bool NetworkQuota::requestTsfProbe(")
        end=CPP.index("bool NetworkQuota::requestHwRxSnapshot(",start)
        cleanup=CPP[CPP.index("void NetworkQuota::cleanupTwtTrial()"):CPP.index("void NetworkQuota::serviceTwtTrial(")]
        methods=CPP[start:end]+cleanup
        for kind in ("bool", "void", "MosaicoTsfProbe::Snapshot"):
            methods=methods.replace(kind+" NetworkQuota::", "constexpr "+kind+" NetworkQuota::")
        methods=methods.replace("MosaicoOta::blocksTwt()", "otaBlocked()")
        harness=r'''
#include "mosaico_tsf_probe.h"
#include "mosaico_twt_model.h"
#define CONFIG_IDF_TARGET_ESP32S31 1
#define portENTER_CRITICAL(x) ((void)0)
#define portEXIT_CRITICAL(x) ((void)0)
constexpr int ESP_OK=0, ESP_ERR_INVALID_STATE=259, WIFI_IF_STA=0, FLOW_ID_ALL=255;
using esp_err_t=int; using wifi_ps_type_t=int;
template<class T> struct Atomic {
 T value{};
 constexpr T load() const { return value; }
 constexpr void store(T v) { value=v; }
 constexpr bool compare_exchange_strong(T& expected,T desired) {
  if(value==expected){value=desired;return true;} expected=value;return false;
 }
};
struct wifi_ap_record_t {};
using esp_timer_handle_t=void*;
constexpr int ESP_TIMER_TASK=0;
struct esp_timer_create_args_t {
 void (*callback)(void*)=nullptr; void* arg=nullptr; int dispatch_method=0; const char* name=nullptr;
};
struct NetworkQuota {
 Atomic<bool> _tsf_probe_pending{}, _hw_rx_owner_ready{true}, _wifi_running{true}, _connected{true};
 struct LanProbe { bool closed=true; constexpr bool quiesce(){return closed;} } _lan_tcp;
 MosaicoTwt::Model _twt;
 MosaicoTwt::Snapshot _twt_cache;
 MosaicoTsfProbe::Snapshot _tsf_probe_cache;
 Atomic<bool> _tsf_probe_timer_seen{};
 esp_timer_handle_t _tsf_probe_timer=nullptr;
 bool timerImmediate=true;
 int createCalls=0,startCalls=0,deleteCalls=0,createError=0,startError=0,deleteError=0,secondError=0;
 static constexpr void tsfProbeTimerSentinel(void*);
 struct TsfProbeEvent { bool seen=false; int status=0; uint8_t reason=0; int64_t localUs=0; };
 TsfProbeEvent _tsf_probe_event;
 bool _tsf_probe_consumed=false, _tsf_probe_accept_event=false;
 Atomic<int> _twt_request{-1};
 bool _twt_cleanup_failed=false, _twt_submitted=true, _twt_ps_saved=true, _twt_cleanup_barrier_needed=false;
 bool _twt_negotiation_pending=false, _twt_teardown_sent=false, _twt_teardown_ack=false;
 uint8_t _twt_event_read=0, _twt_event_write=0, _twt_cleanup_attempts=0;
 bool _twt_event_overflow=false, locked=true;
 int _twt_saved_ps=1, barrierCalls=0, drainError=0, teardownCalls=0, psCalls=0;
 bool blocked=false, blockAfterAp=false, immediate=false;
 int calls=0, tsfCalls=0, apCalls=0, apiError=0, apError=0;
 int64_t clock=1, tsf=500;
 constexpr NetworkQuota() {
  _twt.state.stage=MosaicoTwt::Stage::Active;
  _twt.state.requested=MosaicoTwt::Mode::On;
  _twt.state.associated=true; _twt.state.id=3; _twt.state.expiryUs=1000000000; _twt_cache=_twt.state;
 }
 constexpr bool idleLocked() const { return locked; }
 constexpr bool otaBlocked() { return blocked; }
 constexpr int esp_wifi_sta_itwt_get_flow_id_status(int* bitmap) { ++barrierCalls; *bitmap=0; return barrierCalls==2?secondError:drainError; }
 constexpr int esp_timer_create(const esp_timer_create_args_t* args,esp_timer_handle_t* handle) {
  ++createCalls;
  if(barrierCalls!=1 || args->arg!=this || args->callback!=&NetworkQuota::tsfProbeTimerSentinel || args->dispatch_method!=ESP_TIMER_TASK)return 99;
  if(!createError)*handle=this;
  return createError;
 }
 constexpr int esp_timer_start_once(esp_timer_handle_t handle,uint64_t us) {
  ++startCalls;
  if(handle!=this || us!=1 || barrierCalls!=1)return 99;
  if(!startError && timerImmediate)_tsf_probe_timer_seen.store(true);
  return startError;
 }
 constexpr int esp_timer_delete(esp_timer_handle_t handle) {
  ++deleteCalls; if(handle!=this)return 99; return deleteError;
 }
 constexpr int esp_wifi_sta_itwt_teardown(int) { ++teardownCalls; return 0; }
 constexpr int esp_wifi_set_ps(int) { ++psCalls; return 0; }
 constexpr void cleanupTwtTrial();
 constexpr int64_t esp_timer_get_time() { return clock++; }
 constexpr int esp_wifi_sta_get_ap_info(wifi_ap_record_t*) { ++apCalls; if(blockAfterAp)blocked=true; return apError; }
 constexpr int64_t esp_wifi_get_tsf_time(int) { ++tsfCalls; return tsf; }
 constexpr int esp_wifi_sta_itwt_send_probe_req(int timeout) {
  ++calls;
  if(timeout<250 || timeout>2000)return 99;
  if(immediate)captureTsfProbeEvent(1,7,clock++);
  return apiError;
 }
 constexpr bool requestTsfProbe(uint32_t,uint64_t&);
 constexpr MosaicoTsfProbe::Snapshot tsfProbeSnapshot() const;
 constexpr void captureTsfProbeEvent(int,uint8_t,int64_t);
 constexpr void serviceTsfProbe();
};
'''+methods+r'''
constexpr bool immediateAndOnce() {
 NetworkQuota n; uint64_t id=0;
 n.serviceTsfProbe(); n.captureTsfProbeEvent(3,4,55);
 if(n.calls || n.tsfCalls || n._tsf_probe_event.seen || n.createCalls)return false;
 n.immediate=true;
 if(!n.requestTsfProbe(1000,id) || id!=1 || !n.tsfProbeSnapshot().queued)return false;
 n.serviceTsfProbe(); auto s=n.tsfProbeSnapshot();
 if(!s.driverDrained || !s.drainAttempted || s.drainResult || s.flowBitmap || n.barrierCalls!=2 || !s.resourceReclaimed || !s.timerSeen || n.calls!=1 || n.tsfCalls!=2 || !s.apiCalled || !s.callbackSeen || s.apiResult || s.probeStatus!=1 || s.reason!=7 || !s.callbackLocalUs || !s.sameTrial || !s.beforeValid || !s.afterValid || s.inflight || s.queued || s.processedId!=id || !s.consumed || s.deadlineExpired)return false;
 n.captureTsfProbeEvent(0,99,88); n.serviceTsfProbe();
 if(n.requestTsfProbe(1000,id) || id || n.calls!=1 || n.tsfProbeSnapshot().reason!=7)return false;
 return true;
}
constexpr bool lateCallback() {
 NetworkQuota n; uint64_t id=0;
 if(!n.requestTsfProbe(250,id))return false;
 n.serviceTsfProbe(); auto s=n.tsfProbeSnapshot();
 if(n.calls!=1 || n.tsfCalls!=1 || !s.inflight || s.callbackSeen)return false;
 n.clock=s.deadlineUs+1; n.serviceTsfProbe(); s=n.tsfProbeSnapshot();
 if(!s.deadlineExpired || !s.inflight || s.callbackSeen || n.tsfCalls!=1)return false;
 if(n.requestTsfProbe(250,id))return false;
 n.captureTsfProbeEvent(2,9,n.clock);
 if(n.tsfCalls!=1)return false;
 n._twt.state.id=4;
 n.serviceTsfProbe(); s=n.tsfProbeSnapshot();
 if(!s.callbackSeen || s.inflight || !s.deadlineExpired || s.sameTrial || s.afterValid || s.reason!=9 || s.probeStatus!=2 || n.tsfCalls!=1 || n.calls!=1)return false;
 return true;
}
constexpr bool apiFailureAndInvalidTsf() {
 NetworkQuota n; uint64_t id=0; n.apiError=42; n.immediate=true;
 n.requestTsfProbe(2000,id); n.serviceTsfProbe(); auto s=n.tsfProbeSnapshot();
 if(!s.apiCalled || !s.consumed || s.apiResult!=42 || s.callbackSeen || s.inflight || n.calls!=1)return false;
 n.captureTsfProbeEvent(1,0,123);
 if(n.requestTsfProbe(1000,id) || n._tsf_probe_event.seen || n.calls!=1)return false;
 NetworkQuota zero; zero.tsf=0; zero.immediate=true;
 zero.requestTsfProbe(1000,id); zero.serviceTsfProbe(); s=zero.tsfProbeSnapshot();
 return s.callbackSeen && !s.beforeValid && !s.afterValid && zero.calls==1;
}
constexpr bool preCallRejections() {
 NetworkQuota n; uint64_t id=0;
 if(n.requestTsfProbe(249,id) || n.requestTsfProbe(2001,id))return false;
 n._hw_rx_owner_ready.store(false);
 if(n.requestTsfProbe(1000,id) || n.tsfProbeSnapshot().apiCalled)return false;
 n._hw_rx_owner_ready.store(true); n.blocked=true;
 if(n.requestTsfProbe(1000,id))return false;
 n.blocked=false; n._twt_cache.stage=MosaicoTwt::Stage::Off;
 if(n.requestTsfProbe(1000,id))return false;
 n._twt_cache=n._twt.state;
 if(!n.requestTsfProbe(1000,id))return false;
 n.blocked=true; n.serviceTsfProbe();
 if(n.calls || n.tsfCalls || n.tsfProbeSnapshot().apiCalled || n.tsfProbeSnapshot().processedId!=id)return false;
 n.blocked=false; n._wifi_running.store(false);
 n.requestTsfProbe(1000,id); n.serviceTsfProbe();
 if(n.calls || n.apCalls || n.tsfCalls)return false;
 n._wifi_running.store(true); n._connected.store(false);
 n.requestTsfProbe(1000,id); n.serviceTsfProbe(); if(n.calls || n.apCalls)return false;
 n._connected.store(true); n._twt.state.id=4;
 n.requestTsfProbe(1000,id); n.serviceTsfProbe();
 if(n.calls || n.tsfProbeSnapshot().sameTrial)return false;
 n._twt.state.id=3; n.blockAfterAp=true;
 n.requestTsfProbe(1000,id); n.serviceTsfProbe(); if(n.calls || n.tsfCalls)return false;
 n.blockAfterAp=false; n.blocked=false; n.immediate=true;
 if(!n.requestTsfProbe(1000,id))return false;
 n.serviceTsfProbe();
 return n.calls==1 && n.tsfProbeSnapshot().callbackSeen;
}
constexpr bool queuedCancellationGates() {
 for(int reason=0;reason<5;++reason) {
  NetworkQuota n; uint64_t id=0;
  if(!n.requestTsfProbe(1000,id))return false;
  if(reason==0)n._twt_request.store(0); // queued explicit Off wins
  if(reason==1)n.locked=false;
  if(reason==2)n._twt.state.expiryUs=n.clock;
  if(reason==3)n._twt_event_write=1; // AP teardown/disconnect event not consumed yet
  if(reason==4)n._twt_event_overflow=true;
  n.serviceTsfProbe(); auto s=n.tsfProbeSnapshot();
  if(s.apiCalled || s.consumed || n.calls || n.tsfCalls || s.processedId!=id || s.queued)return false;
 }
 return true;
}
constexpr bool cleanupHold() {
 for(int reason=0;reason<4;++reason) {
  NetworkQuota n; uint64_t id=0;
  n.requestTsfProbe(1000,id); n.serviceTsfProbe();
  if(reason==0)n._twt.end(MosaicoTwt::Stop::Explicit);
  if(reason==1)n._twt.end(MosaicoTwt::Stop::Expired);
  if(reason==2){n.locked=false;n._twt.end(MosaicoTwt::Stop::Awake);}
  if(reason==3){n.blocked=true;n._twt.end(MosaicoTwt::Stop::Ota);}
  n.cleanupTwtTrial();
  if(!n._twt.state.cleanupPending || n._twt.state.cleanupStage!=6 || n._twt.state.cleanupDeadlineUs || n.teardownCalls || n.psCalls || n.barrierCalls)return false;
  n.captureTsfProbeEvent(1,5,n.clock++);
  n.cleanupTwtTrial(); // Callback ALONE does not authorize cleanup.
  if(n.teardownCalls || n.psCalls || n.barrierCalls)return false;
  n.serviceTsfProbe(); auto s=n.tsfProbeSnapshot();
  if(!s.driverDrained || s.inflight || !s.callbackSeen || n.barrierCalls!=2 || n.tsfCalls!=1)return false;
  n.cleanupTwtTrial();
  if(n.teardownCalls!=1 || n.psCalls || n._twt.state.cleanupStage==6 || !n._twt.state.cleanupDeadlineUs)return false;
  n._twt_teardown_ack=true; n.cleanupTwtTrial();
  if(n.psCalls!=1 || n._twt.state.cleanupPending)return false;
 }
 NetworkQuota fail; uint64_t id=0;
 fail.requestTsfProbe(1000,id); fail.serviceTsfProbe();
 fail._twt.end(MosaicoTwt::Stop::Explicit); fail.cleanupTwtTrial();
 fail.drainError=42; fail.captureTsfProbeEvent(2,8,fail.clock++); fail.serviceTsfProbe();
 auto s=fail.tsfProbeSnapshot();
 if(!s.callbackSeen || !s.drainAttempted || s.driverDrained || !s.inflight || s.drainResult!=42 || s.afterValid)return false;
 fail.cleanupTwtTrial(); fail.serviceTsfProbe(); fail.cleanupTwtTrial();
 if(fail.barrierCalls!=1 || fail.teardownCalls || fail.psCalls || fail._twt.state.cleanupStage!=6 || fail._twt.state.cleanupFailed || fail._twt.state.cleanupDeadlineUs)return false;
 return true;
}
constexpr bool timerProducerBarrier() {
 NetworkQuota n; uint64_t id=0; n.immediate=true; n.timerImmediate=false;
 n.requestTsfProbe(1000,id); n.serviceTsfProbe(); auto s=n.tsfProbeSnapshot();
 if(!s.callbackSeen || !s.timerCreated || !s.timerStarted || s.timerSeen || s.driverDrained || n.barrierCalls!=1 || n.createCalls!=1 || n.startCalls!=1 || n.deleteCalls)return false;
 n.cleanupTwtTrial(); n.serviceTsfProbe();
 if(n.barrierCalls!=1 || n.createCalls!=1 || n.startCalls!=1 || n.teardownCalls || n.psCalls)return false;
 n._tsf_probe_timer_seen.store(true); // Timer-task sentinel has run, no arbitrary time delay.
 n.serviceTsfProbe(); s=n.tsfProbeSnapshot();
 if(!s.driverDrained || !s.resourceReclaimed || n.barrierCalls!=2 || n.deleteCalls!=1 || n._tsf_probe_timer)return false;
 n.serviceTsfProbe(); if(n.barrierCalls!=2 || n.deleteCalls!=1)return false;
 for(int fault=0;fault<5;++fault) {
  NetworkQuota f; f.immediate=true;
  if(fault==0)f.drainError=42;
  if(fault==1)f.createError=42;
  if(fault==2)f.startError=42;
  if(fault==3)f.secondError=42;
  if(fault==4)f.deleteError=42;
  f.requestTsfProbe(1000,id); f.serviceTsfProbe(); auto a=f.tsfProbeSnapshot();
  if(a.driverDrained || !a.inflight || !a.callbackSeen)return false;
  auto pp=f.barrierCalls,create=f.createCalls,start=f.startCalls,del=f.deleteCalls;
  f.cleanupTwtTrial(); f.serviceTsfProbe(); f.cleanupTwtTrial();
  if(f.barrierCalls!=pp || f.createCalls!=create || f.startCalls!=start || f.deleteCalls!=del || f.teardownCalls || f.psCalls || f._twt.state.cleanupStage!=6)return false;
  if(fault==0 && (a.firstPpResult!=42 || f.createCalls))return false;
  if(fault==1 && (a.timerCreated || a.timerError!=42 || f.startCalls || f.deleteCalls))return false;
  if(fault==2 && (!a.timerCreated || a.timerStarted || a.timerError!=42 || !a.resourceReclaimed || f.deleteCalls!=1 || f.barrierCalls!=1))return false;
  if(fault==3 && (!a.secondPpAttempted || a.secondPpResult!=42 || !a.resourceReclaimed))return false;
  if(fault==4 && (a.resourceReclaimed || a.deleteResult!=42 || !f._tsf_probe_timer))return false;
 }
 return true;
}
constexpr bool socketCloseHold() {
 NetworkQuota n; n._lan_tcp.closed=false;
 n._twt.end(MosaicoTwt::Stop::Explicit); n.cleanupTwtTrial();
 if (!n._twt.state.cleanupPending || n._twt.state.cleanupStage!=7 ||
     n._twt.state.cleanupDeadlineUs || n.teardownCalls || n.psCalls || n.barrierCalls) return false;
 n._lan_tcp.closed=true; n.cleanupTwtTrial();
 return n._twt.state.cleanupStage!=7;
}
static_assert(socketCloseHold(), "socket close must precede driver teardown and PS restore");
static_assert(timerProducerBarrier(), "timer producer sentinel ordering/resource/fault regression");
static_assert(queuedCancellationGates(), "queued cancel/unlock/expiry/AP event gate regression");
static_assert(cleanupHold(), "probe callback+PP barrier required before ANY cleanup");
static_assert(immediateAndOnce(), "immediate callback or once-per-boot regression");
static_assert(lateCallback(), "deadline/late callback/trial attribution regression");
static_assert(apiFailureAndInvalidTsf(), "API failure or TSF validity regression");
static_assert(preCallRejections(), "pre-call gate rejection must not consume latch");
'''
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"tsf.cpp"; path.write_text(harness)
            run=subprocess.run([str(COMPILERS[-1]),"-std=c++17","-fsyntax-only","-I"+str(ROOT/"main/host"),str(path)],capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)

    def test_source_guards_parser_and_event_boundary(self):
        region=CPP[CPP.index("bool NetworkQuota::requestTsfProbe("):CPP.index("bool NetworkQuota::requestHwRxSnapshot(")]
        self.assertNotIn("xTaskNotifyGive",region)
        self.assertEqual(region.count("esp_wifi_sta_itwt_get_flow_id_status("),2)
        callback=region[region.index("void NetworkQuota::tsfProbeTimerSentinel("):region.index("void NetworkQuota::serviceTsfProbe()")]
        self.assertIn("static_cast<NetworkQuota*>(arg)->_tsf_probe_timer_seen.store(true)",callback)
        for forbidden in ("esp_timer_", "esp_wifi_", "xTaskNotify", "malloc", "ESP_LOG", "nvs_"):
            self.assertNotIn(forbidden,callback)
        self.assertIn("args.arg = this",region)
        self.assertIn("args.dispatch_method = ESP_TIMER_TASK",region)
        run=CPP[CPP.index("void NetworkQuota::run("):]
        probe=run.index("serviceTsfProbe();")
        self.assertLess(run.index("serviceTwtTrial(esp_timer_get_time(), idleLocked())"),probe)
        self.assertLess(run.index("reconcileTwtCycleIdentity();"),probe)
        self.assertLess(probe,run.index("if (_twt.state.cleanupPending || _twt_cleanup_failed)"))
        self.assertIn("wait((_twt_cleanup_failed || (_twt.state.cleanupStage == 6 || _twt.state.cleanupStage == 7)) ? 1000 : 50)",run)
        wait=CPP[CPP.index("void NetworkQuota::wait("):CPP.index("void NetworkQuota::setCpu(")]
        self.assertIn("cleanupPending && (_twt.state.cleanupStage == 6 || _twt.state.cleanupStage == 7)",wait)
        self.assertIn("milliseconds = std::min<uint32_t>(milliseconds, 1000)",wait)
        cleanup=CPP[CPP.index("void NetworkQuota::cleanupTwtTrial()"):CPP.index("void NetworkQuota::serviceTwtTrial(")]
        self.assertLess(cleanup.index("!probe.driverDrained"),cleanup.index("esp_wifi_sta_itwt_teardown"))
        self.assertLess(cleanup.index("!probe.driverDrained"),cleanup.index("esp_wifi_set_ps"))
        self.assertEqual(region.count("esp_wifi_sta_itwt_send_probe_req("),1)
        event=CPP[CPP.index("if (event == WIFI_EVENT_ITWT_PROBE)"):CPP.index("    TwtEvent copied{};")]
        self.assertNotIn("esp_wifi_get_tsf_time",event)
        self.assertNotIn("esp_wifi_sta_itwt_send_probe_req",event)
        self.assertNotIn("xTaskNotifyGive",event)
        self.assertIn("wifi_event_sta_itwt_probe_t",event)
        serial=(ROOT/"main/debug/serial_debug.cpp").read_text()
        block=serial[serial.index('std::strcmp(command, "tsf-probe")'):serial.index('std::strcmp(command, "hw-rx")')]
        for token in ("!action || extra",'"status") && timeout',"timeoutMs < 250", "settingsInteger(timeout, 2000", "one_per_boot=1", "callback_request_id=0"):
            self.assertIn(token,block)
        self.assertIn('"tsf-probe"',(ROOT/"main/debug/serial_debug_transport.h").read_text())
        before=CPP[:CPP.index("bool NetworkQuota::requestTsfProbe(")]
        self.assertIn("#if defined(MOSAICO_BOARD) && CONFIG_IDF_TARGET_ESP32S31\nbool NetworkQuota::requestLanTcp(", before)
        self.assertNotIn("esp_wifi_he.h",(ROOT/"main/host/network_quota.h").read_text())

if __name__ == "__main__": unittest.main()
