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
 m.start(Mode::On,4);m.bootstrapReady(4);
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
constexpr bool disconnectRetention() {
 for (Mode mode : {Mode::On, Mode::Baseline}) {
  Model m;
  if (m.state.staDisconnect.valid || m.state.staDisconnect.eventUs) return false;
  if (!m.start(mode, 100)) return false;
  const auto ownerId = m.state.id;
  // Valid zero reason is not missing data; timestamp is the original callback time.
  m.recordStaDisconnect(0, 9000000123LL, true);
  m.end(Stop::Lost);
  if (!m.start(Mode::Off, 200)) return false;
  if (!m.state.staDisconnect.valid || m.state.staDisconnect.reason != 0 ||
      m.state.staDisconnect.eventUs != 9000000123LL || m.state.staDisconnect.ownerTrialId != ownerId) return false;
  for (Mode next : {Mode::On, Mode::Baseline}) {
   if (!m.start(next, 300)) return false;
   if (m.state.id == ownerId || !m.state.staDisconnect.valid || m.state.staDisconnect.reason != 0 ||
       m.state.staDisconnect.eventUs != 9000000123LL || m.state.staDisconnect.ownerTrialId != ownerId) return false;
  }
  // Consumption identity is the current owner id, including after check/cleanup ended it.
  const auto consumingId = m.state.id;
  m.end(Stop::Timeout);
  m.recordStaDisconnect(204, 9000000000LL, true);
  if (m.state.staDisconnect.ownerTrialId != consumingId || m.state.staDisconnect.reason != 204 ||
      m.state.staDisconnect.eventUs != 9000000000LL || !m.state.staDisconnect.valid || m.live()) return false;
  // A later event with absent data replaces, rather than fabricates, reason validity.
  m.recordStaDisconnect(0, 9000000999LL, false);
  m.start(Mode::Off, 400);
  if (!m.start(mode, 500)) return false;
  if (m.state.staDisconnect.valid || m.state.staDisconnect.reason != 0 ||
      m.state.staDisconnect.eventUs != 9000000999LL || m.state.staDisconnect.ownerTrialId != consumingId) return false;
 }
 return true;
}
static_assert(disconnectRetention(), "actual disconnect record/end/off/start retention and owner identity");

'''
        self.compile_cpp(code)

    def test_bootstrap_and_negotiation_deadlines(self):
        self.compile_cpp(r'''
#include "main/host/mosaico_twt_model.h"
#include <initializer_list>
using namespace MosaicoTwt;
constexpr bool deadlines() {
 for(Mode mode : {Mode::Baseline,Mode::On}) {
  Model m;m.start(mode,7,1800);
  if(m.state.bootstrapDeadlineUs!=60000007 || m.state.setupDeadlineUs)return false;
  for(int64_t now=7;now<60000007;now+=5000000)
   if(!m.check(now,false,true)||m.state.bootstrapDeadlineUs!=60000007)return false;
  if(m.check(60000007,false,true)||m.state.stop!=Stop::Timeout)return false;
  m.start(mode,100);const auto expiry=m.state.expiryUs;
  if(!m.bootstrapReady(50000100)||m.state.bootstrapDeadlineUs||m.state.expiryUs!=expiry)return false;
  if(mode==Mode::On) {
   if(m.state.setupDeadlineUs!=60000100 || m.bootstrapReady(51000100))return false;
   if(!m.check(60000099,false,true)||m.state.setupDeadlineUs!=60000100)return false;
   if(m.check(60000100,false,true)||m.state.stop!=Stop::Timeout)return false;
  } else if(m.state.stage!=Stage::Baseline||m.state.setupDeadlineUs)return false;
  m.start(mode,0);if(m.bootstrapReady(60000000)||m.state.stop!=Stop::Timeout)return false;
  m.start(mode,0);if(m.check(m.state.expiryUs,false,true)||m.state.stop!=Stop::Expired)return false;
  m.start(mode,0);if(m.check(60000000,false,false)||m.state.stop!=Stop::Awake)return false;
  m.start(mode,0);if(m.check(60000000,true,true)||m.state.stop!=Stop::Ota)return false;
  m.start(mode,0);m.start(Mode::Off,60000000);if(m.live()||m.state.stop!=Stop::Explicit)return false;
 }
 return true;
}
static_assert(deadlines(), "finite bootstrap then fixed dispatch deadline, manual priority");
''')

    def test_actual_bootstrap_dispatch_gate(self):
        source = (ROOT/'main/host/network_quota.cpp').read_text()
        service = source.split('void NetworkQuota::serviceTwtTrial',1)[1]
        block = service.split('    if (_twt.live() && _wifi_running) {',1)[1].split('    reconcileTwtCycleIdentity();',1)[0]
        block = '    if (_twt.live() && _wifi_running) {' + block
        request_block = service.split('    if (request >= 0) {', 1)[1].split('    if (_twt.live() && _wifi_running) {', 1)[0]
        request_block = '    if (request >= 0) {' + request_block
        bootstrap_run = '#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT\n// Armed must allow DHCP/clock/tailnet bootstrap.' + source.split('// Armed must allow DHCP/clock/tailnet bootstrap.',1)[1].split('        // Reuse an already-online quota window.',1)[0]
        bootstrap_run = bootstrap_run.rsplit('#ifdef MOSAICO_BOARD',1)[0].replace('std::time(nullptr)', 'bootTime()')
        dispatch_run = source.split('        // Dispatch only after normal bootstrap has completed, before HTTP.',1)[1].split('        const int64_t twtFetchStart',1)[0]
        helper = source.split('bool NetworkQuota::serviceTwtBootstrap()',1)[1].split('#endif\nvoid NetworkQuota::setLowClockDiagnostic',1)[0]
        self.compile_cpp(r'''
#define ESP_LOGI(...)
#define MOSAICO_BOARD 1
#define SOC_WIFI_HE_SUPPORT 1
#include "main/host/mosaico_twt_model.h"
#include <initializer_list>
using namespace MosaicoTwt;
using esp_err_t=int;using wifi_phy_mode_t=int;using wifi_ps_type_t=int;
constexpr int ESP_OK=0,ESP_ERR_INVALID_STATE=-1,WIFI_PHY_MODE_HE20=1,WIFI_PS_MIN_MODEM=1,TWT_SUGGEST=1;
struct wifi_ap_record_t { bool phy_11ax=true; };
struct wifi_itwt_setup_config_t { int setup_cmd=0,trigger=0,flow_type=0,flow_id=0,wake_invl_expn=0,wake_invl_mant=0,min_wake_dura=0,wake_duration_unit=0,twt_id=0,timeout_time_ms=0; };
struct Tailnet { bool configured=true,online=false;int starts=0;constexpr bool enabled(){return configured;}constexpr bool ready(){return online;} constexpr void start(){++starts;} constexpr void rebind(){} };
struct Owner {
 Model _twt; Tailnet tail; bool _twt_handler_ready=true;
 bool ota=false;
 constexpr bool twtOtaBlocked(){return ota;}
 uint16_t _twt_bootstrap_id=0;
 bool _twt_bootstrap_attempted=false,_twt_bootstrap_ok=false;
 int probes=0,probeAction=0;bool probeOk=true;
 constexpr bool fetch(){
  ++probes;
  if(probeAction==1)pendingOff=true;
  if(probeAction==2)lateUnlock=true;
  if(probeAction==3)ota=true;
  if(probeAction==4)now=_twt.state.bootstrapDeadlineUs;
  if(probeAction==5)now=_twt.state.expiryUs;
  if(probeAction==6)associated=false;
  if(probeAction==7){_twt.start(Mode::On,now);_twt_bootstrap_attempted=_twt_bootstrap_ok=false;_twt_bootstrap_id=0;}
  return probeOk;
 }
 constexpr bool serviceTwtBootstrap();
 constexpr void closeWindow(CycleReason){}
 bool _wifi_running=true,_connected=false,associated=true,_twt_cleanup_failed=false;
 bool _twt_ps_saved=false,_twt_submitted=false,_twt_negotiation_pending=false,_twt_cleanup_barrier_needed=false;
 wifi_itwt_setup_config_t submitted{};
 int _twt_saved_ps=0,setups=0,psCalls=0;int64_t now=1;
 constexpr Tailnet& GetTailnetQuota(){return tail;}
 constexpr int esp_wifi_sta_get_ap_info(wifi_ap_record_t*){return associated?0:1;}
 constexpr int esp_wifi_sta_get_negotiated_phymode(int* p){*p=1;return 0;}
 constexpr int esp_wifi_get_ps(int* p){*p=2;return 0;}
 constexpr int esp_wifi_set_ps(int){++psCalls;return 0;}
 constexpr int esp_wifi_sta_itwt_setup(wifi_itwt_setup_config_t* config){submitted=*config;++setups;return 0;}
 constexpr int64_t esp_timer_get_time(){return now;}
 constexpr void cancelTwtTrial(Stop stop){_twt.end(stop);}
 constexpr void publishTwt(){}
 int http=0,waits=0;bool pendingOff=false,lateUnlock=false;unsigned tailnetHeapBefore=0,tailnetHeapAfter=0;
 constexpr int64_t bootTime(){return 1800000000;}
 constexpr void sampleNetworkHeap(const char*,unsigned&){}
 constexpr void wait(int){++waits;}
 constexpr bool idleLocked(){return !lateUnlock;}
 constexpr void serviceTwtTrial(int64_t,bool locked){
  _twt.check(now,ota,locked && !lateUnlock);
  if(pendingOff)_twt.start(Mode::Off,now);
  if(_twt.live())dispatch(locked && !lateUnlock);
 }
 constexpr void processRequest(int request,bool locked=true){
''' + request_block + r'''
 }
 constexpr void dispatch(bool locked=true){
''' + block + r'''
 }
 constexpr void runBootstrap(bool updateWindow=true) {
  bool locked=true,hadConnection=true;
  for(int once=0;once<1;++once) {
''' + bootstrap_run + '#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT\n' + dispatch_run + '#endif\n' + r'''
   ++http;
  }
 }
};
constexpr bool Owner::serviceTwtBootstrap()''' + helper + r'''
constexpr bool profiles() {
 if(encodeRequest(Mode::On,600)!=2 || encodeRequest(Mode::On,1800)!=6 ||
    encodeRequest(Mode::Baseline,600)!=1 || encodeRequest(Mode::Baseline,1800)!=5 ||
    encodeRequest(Mode::Off,600,Profile::AnnouncedTrigger)!=0)return false;
 for(unsigned lease : {600u,1800u}) {
  const int request=encodeRequest(Mode::On,lease,Profile::AnnouncedTrigger);
  Owner o;o._connected=o.tail.online=true;
  if(o._twt.state.profile!=Profile::Default)return false;
  o.processRequest(request);
  o.serviceTwtBootstrap();
  if(o.setups!=1 || o.submitted.trigger!=1 || o.submitted.flow_type!=0 ||
     o.submitted.wake_invl_expn!=11 || o.submitted.wake_invl_mant!=512 ||
     o.submitted.min_wake_dura!=64 || o.submitted.wake_duration_unit!=0 ||
     o.submitted.flow_id!=0 || o.submitted.timeout_time_ms!=5000)return false;
  for(Stop reason : {Stop::Explicit,Stop::Expired,Stop::Driver,Stop::Rejected}) {
   o._twt.start(Mode::On,0,lease,Profile::AnnouncedTrigger);
   if(reason==Stop::Explicit)o.processRequest(0);
   else if(reason==Stop::Expired)o._twt.check(int64_t(lease)*1000000,false,true);
   else o.cancelTwtTrial(reason);
   if(o._twt.state.profile!=Profile::Default)return false;
   o.processRequest(encodeRequest(Mode::On,lease));o.serviceTwtBootstrap();
   if(o.submitted.trigger!=0 || o.submitted.flow_type!=1 || o._twt.state.profile!=Profile::Default)return false;
  }
  o._twt.start(Mode::Baseline,0,lease);int setups=o.setups;o.serviceTwtBootstrap();
  if(o.setups!=setups || o._twt.state.profile!=Profile::Default)return false;
 }
 return true;
}
constexpr bool probeFaults() {
 for(Mode mode : {Mode::On,Mode::Baseline}) {
  Owner gate;gate._connected=gate.tail.online=true;gate.processRequest(encodeRequest(mode,600));
  gate.dispatch(); // Even control+association true cannot change PS before real HTTP.
  if(gate.psCalls||gate.setups||gate.probes)return false;
  gate.serviceTwtBootstrap();
  if(gate.probes!=1||gate.psCalls!=1||gate._twt.state.fetchAttempts||gate._twt.state.fetchOk)return false;
  gate.serviceTwtBootstrap();if(gate.probes!=1)return false;
  gate.processRequest(encodeRequest(mode,600));gate.dispatch();
  if(gate.psCalls!=1)return false; // No success inherited by the next trial.
  gate.serviceTwtBootstrap();if(gate.probes!=2||gate.psCalls!=2)return false;
  for(int action=0;action<=7;++action) {
   Owner o;o._connected=o.tail.online=true;o.processRequest(encodeRequest(mode,600));
   auto id=o._twt.state.id;o.probeOk=action!=0;o.probeAction=action;
   o.serviceTwtBootstrap();
   if(o.probes!=1||o.psCalls||o.setups||o.http||o._twt.state.fetchOk||o._twt.state.fetchAttempts)return false;
   if(action==7) {
    if(o._twt.state.id==id||o._twt.state.stage!=Stage::Armed||o._twt_bootstrap_ok)return false;
    o.probeAction=0;o.serviceTwtBootstrap();if(o.probes!=2||o.psCalls!=1)return false;
   } else {
    if(o._twt.live())return false;
    o.serviceTwtBootstrap();if(o.probes!=1)return false;
   }
  }
 }
 return true;
}
static_assert(probeFaults(), "quota-only one-shot bootstrap, both arms, no late or cross-trial success");
static_assert(profiles(), "production setup selects only temporary announced trigger profile and resets on termination");
constexpr bool gates() {
 for(Mode mode : {Mode::Baseline,Mode::On}) {
  Owner o;o._twt.start(mode,0);o.serviceTwtBootstrap();
  if(o.setups||o.psCalls||o._twt.state.stage!=Stage::Armed)return false;
  o._connected=true;o.serviceTwtBootstrap(); // tailnet false still forbids PS/baseline/setup
  if(o.setups||o.psCalls||o._twt.state.stage!=Stage::Armed)return false;
  o.tail.online=true;o.associated=false;o.serviceTwtBootstrap();
  if(o.setups||o.psCalls||o._twt.state.stage!=Stage::Armed)return false;
  o.associated=true;o.now=50000000;o.serviceTwtBootstrap();
  if(o.psCalls!=1||o._twt.state.bootstrapDeadlineUs)return false;
  if(mode==Mode::On && (o.setups!=1||o._twt.state.stage!=Stage::Negotiating||o._twt.state.setupDeadlineUs!=60000000))return false;
  if(mode==Mode::Baseline && (o.setups||o._twt.state.stage!=Stage::Baseline))return false;
  o.now++;o.serviceTwtBootstrap();if(o.psCalls!=1||o.setups!=(mode==Mode::On?1:0))return false;
  o.tail.online=false;o.serviceTwtBootstrap();
  if(!o._twt.live() || o._twt.state.losses || o._twt.state.controlReady)return false;
  o.tail.online=true;o.serviceTwtBootstrap();
  if(!o._twt.live() || o._twt.state.losses || !o._twt.state.controlReady || o.psCalls!=1 || o.setups!=(mode==Mode::On?1:0))return false;
  Owner starting;starting._connected=true;starting._twt.start(mode,0);starting.runBootstrap();
  if(starting.tail.starts!=1||starting.http||starting._twt.state.stage!=Stage::Armed)return false;
  starting.tail.online=true;starting.runBootstrap();
  if(mode==Mode::On && (starting.http||starting._twt.state.stage!=Stage::Negotiating))return false;
  if(mode==Mode::Baseline && starting.http!=0)return false;
  if(mode==Mode::On)starting._twt.state.stage=Stage::Active;
  starting.http=0;starting.runBootstrap(false);if(starting.http)return false;
  // Late service cancellation must return to owner idle/stop, even when
  // no SDK setup/cleanup exists and the trial has become Off or Failed.
  for(int action=0;action<4;++action) {
   Owner ended;ended._connected=ended.tail.online=true;ended._twt.start(mode,0);
   ended.pendingOff=action==0;ended.lateUnlock=action==1;
   ended.now=action==2?60000000:action==3?600000000:1;
   ended.runBootstrap(false);
   if(ended.http||ended._twt.live()||ended.setups||ended._twt.state.cleanupPending)return false;
   const auto expected=action==0?Stop::Explicit:action==1?Stop::Awake:action==2?Stop::Timeout:Stop::Expired;
   if(ended._twt.state.stop!=expected)return false;
  }
  Owner manual;manual._connected=manual.tail.online=true;manual._twt.start(mode,0);
  manual.now=60000000;manual.pendingOff=true;manual.runBootstrap(false);
  if(manual.http||manual._twt.state.stop!=Stop::Explicit)return false;
  Owner expiredManual;expiredManual._connected=expiredManual.tail.online=true;expiredManual._twt.start(mode,0);
  expiredManual.now=600000000;expiredManual.pendingOff=true;expiredManual.runBootstrap(false);
  if(expiredManual.http||expiredManual._twt.state.stop!=Stop::Explicit)return false;
  Owner lan;lan.tail.configured=false;lan._connected=true;lan._twt.start(mode,0);lan.serviceTwtBootstrap();
  if(lan._twt.state.stage!=(mode==Mode::On?Stage::Negotiating:Stage::Baseline))return false;
 }
 return true;
}
static_assert(gates(), "production dispatch block requires DHCP and configured tailnet for both arms");
constexpr bool ongoingReadiness() {
 for (Mode mode : {Mode::On,Mode::Baseline}) {
  Owner o;o._connected=o.tail.online=true;o._twt.start(mode,0);o.serviceTwtBootstrap();
  if(mode==Mode::On)o._twt.state.stage=Stage::Active;
  const auto stage=o._twt.state.stage;
  const auto expiry=o._twt.state.expiryUs;
  o.tail.online=false;o.serviceTwtBootstrap();
  if(!o._twt.live() || o._twt.state.stage!=stage || o._twt.state.losses ||
     o._twt.state.controlReady || o._twt.state.expiryUs!=expiry)return false;
  // Execute actual owner bootstrap/dispatch gate: no readiness means no HTTP.
  o.runBootstrap(false);
  if(o.http || !o._twt.live() || o._twt.state.losses || o.waits!=1)return false;
  o.tail.online=true;o.runBootstrap();
  if(o.http!=1 || !o._twt.live() || !o._twt.state.controlReady || o._twt.state.losses)return false;
  for(bool associationLoss : {false,true}) {
   Owner lost;lost._connected=lost.tail.online=true;lost._twt.start(mode,0);lost.serviceTwtBootstrap();
   if(mode==Mode::On)lost._twt.state.stage=Stage::Active;
   if(associationLoss)lost.associated=false;else lost._connected=false;
   lost.serviceTwtBootstrap();
   if(lost._twt.live() || lost._twt.state.losses!=1 || lost._twt.state.stop!=Stop::Lost)return false;
  }
  for(int action=0;action<4;++action) {
   Owner ended;ended._connected=ended.tail.online=true;ended._twt.start(mode,0);ended.serviceTwtBootstrap();
   if(mode==Mode::On)ended._twt.state.stage=Stage::Active;
   ended.tail.online=false;
   if(action==0)ended._twt.check(ended._twt.state.expiryUs,false,true);
   else if(action==1)ended.processRequest(0);
   else if(action==2)ended._twt.check(1,false,false);
   else ended._twt.check(1,true,true);
   ended.serviceTwtBootstrap();
   if(ended._twt.live() || ended._twt.state.losses)return false;
   const auto expected=action==0?Stop::Expired:action==1?Stop::Explicit:action==2?Stop::Awake:Stop::Ota;
   if(ended._twt.state.stop!=expected)return false;
  }
 }
 return true;
}
static_assert(ongoingReadiness(), "production owner keeps linked trial through control readiness loss but gates HTTP");

''')

    def test_announced_command_and_status_wire_budget(self):
        import re
        serial = (ROOT/'main/debug/serial_debug.cpp').read_text()
        command = serial.split('if (std::strcmp(command, "twt")', 1)[1].split('#endif', 1)[0]
        self.assertIn('const bool announced = !std::strcmp(mode, "on-announced")', command)
        self.assertIn('announced && !option', command)
        self.assertIn('MosaicoTwt::Profile::AnnouncedTrigger : MosaicoTwt::Profile::Default', command)
        fmt = re.search(r'"(lease_s=%lu mode=%u profile=%u[^"\n]+)"', command).group(1)
        widths = {'lu':10, 'u':10, 'hu':5, 'hhu':3, 'lld':20, 'llu':20, 'd':11}
        # Snapshot enums and Result fields are uint8_t, id is uint16_t.
        bounded = {name:3 for name in ('mode', 'profile', 'stage', 'stop', 'reason', 'flow', 'last_failure', 'cleanup_stage')}
        bounded['id'] = 5
        bounded['control_ready'] = 1 # Bool rendered as unsigned 0/1.
        longest = re.sub(r'(\w+)=%(llu|lld|lu|hhu|hu|u|d)',
                         lambda m: m.group(1)+'='+'9'*bounded.get(m.group(1), widths[m.group(2)]), fmt)
        details_size = int(re.search(r'char details\[(\d+)\]', command).group(1))
        self.assertLess(len(longest), details_size)
        self.assertLess(len(longest) + len('DBG RESULT command=twt status=PASS ') + 2, 1536)

    def test_bootstrap_run_ordering(self):
        source = (ROOT/'main/host/network_quota.cpp').read_text()
        run = source.split('// Armed must allow DHCP/clock/tailnet bootstrap.',1)[1]
        pre = run.split('GetTailnetQuota().start();',1)[0]
        self.assertIn('Stage::Negotiating', pre)
        self.assertNotIn('Stage::Armed', pre)
        before_fetch = run.split('const bool quotaOk = fetch();',1)[0]
        self.assertLess(before_fetch.index('GetTailnetQuota().start();'), before_fetch.index('serviceTwtTrial('))
        self.assertIn('!GetTailnetQuota().ready()', before_fetch)
        gate = before_fetch.split('serviceTwtTrial(',1)[1]
        self.assertIn('Stage::Armed', gate); self.assertIn('Stage::Negotiating', gate)
        self.assertIn('wait(50); continue;', gate)
        self.assertIn('if (locked && !updateWindow) continue;', gate)

    def test_bootstrap_owner_scope_and_schedule(self):
        source = (ROOT/'main/host/network_quota.cpp').read_text()
        helper = source.split('bool NetworkQuota::serviceTwtBootstrap()',1)[1].split('#endif',1)[0]
        self.assertEqual(helper.count('const bool ok = fetch();'), 1)
        self.assertNotIn('fetchHistory(', helper)
        self.assertNotIn('recordTwtCycle(', helper)
        self.assertNotIn('fetchAttempts', helper)
        self.assertNotIn('fetchOk', helper)
        self.assertNotIn('esp_wifi_set_ps', helper)
        self.assertNotIn('esp_wifi_sta_itwt_setup', helper)
        self.assertGreaterEqual(helper.count('serviceTwtTrial('), 3)
        self.assertLess(helper.index('_twt_bootstrap_attempted = true;'), helper.index('fetch();'))
        self.assertLess(helper.index('fetch();'), helper.index('_twt.state.id != id'))
        self.assertLess(helper.index('_twt.state.id != id'), helper.index('_twt_bootstrap_ok = true;'))
        run = source.split('void NetworkQuota::run()',1)[1].split('bool NetworkQuota::requestJson',1)[0]
        self.assertIn('trialHold && _twt.state.stage != MosaicoTwt::Stage::Armed', run)
        armed = run.split('// Dispatch only after normal bootstrap has completed, before HTTP.',1)[1].split('if (_twt.state.cleanupPending',1)[0]
        self.assertLess(armed.index('closeWindow('), armed.index('serviceTwtBootstrap()'))
        self.assertIn('if (serviceTwtBootstrap()) continue;', armed)
        self.assertNotIn('nextRefresh', armed)
        self.assertNotIn('esp_wifi_stop', armed)
        self.assertNotIn('esp_wifi_disconnect', armed)

    def test_long_lease_and_cycle_ring(self):
        self.compile_cpp(r'''
#include "main/host/mosaico_twt_model.h"
#include <initializer_list>
using namespace MosaicoTwt;
constexpr bool longLease() {
 Model m; if(!m.start(Mode::Baseline, 7, 1800)) return false;
 if(m.state.expiryUs!=1800000007LL || m.state.bootstrapDeadlineUs!=60000007LL || m.state.setupDeadlineUs!=0) return false;
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
 o._twt.start(Mode::On,3,1800);o.recordTwtCycle(CyclePhase::Start);
 if(o._twt_cycles.pop(c)||o._twt_cycle_open)return false; // Armed bootstrap is outside measurement.
 o._twt.bootstrapReady(3);o._twt.state.stage=Stage::Active;
 o.recordTwtCycle(CyclePhase::Start);o._twt_cycles.pop(c);
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
  o.recordTwtCycle(CyclePhase::Start);if(o._twt_cycles.pop(c))return false;
  o._twt.bootstrapReady(1);
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

    def test_sta_disconnect_numeric_copy_and_latch_traces(self):
        import re
        from types import SimpleNamespace
        source = (ROOT/'main/host/network_quota.cpp').read_text()
        header = (ROOT/'main/host/network_quota.h').read_text()
        model = (ROOT/'main/host/mosaico_twt_model.h').read_text()
        serial = (ROOT/'main/debug/serial_debug.cpp').read_text()
        callback = source.split('void NetworkQuota::twtEvent',1)[1].split('void NetworkQuota::cancelTwtTrial',1)[0]
        sta = callback.split('event == WIFI_EVENT_STA_DISCONNECTED)',1)[1].split('else if (event == WIFI_EVENT_ITWT_TEARDOWN',1)[0]
        for text in ('copied.kind = 2;', 'copied.eventUs = esp_timer_get_time();',
                     'copied.staReason = e.reason;', 'copied.staReasonValid = true;',
                     'const wifi_event_sta_disconnected_t*'):
            self.assertIn(text, sta)
        for forbidden in ('._twt.', '._twt_cache', 'ssid', 'bssid', 'password', 'esp_wifi_', 'ESP_LOG'):
            self.assertNotIn(forbidden, sta)
        self.assertIn('uint16_t staReason = 0;', header)
        drain = source.split('} else if (e.kind == 2) {',1)[1].split('} else if (e.kind == 3)',1)[0]
        self.assertLess(drain.index('_twt.recordStaDisconnect'), drain.index('if (_twt.live())'))
        self.assertIn('if (_twt.live()) { ++_twt.state.losses; cancelTwtTrial(Stop::Lost); }', drain)
        record = model.split('constexpr void recordStaDisconnect',1)[1].split('}',1)[0].split('{',1)[1]
        # Execute the four actual production numeric assignments with mock state.
        assignments = '\n'.join(line.strip().rstrip(';') for line in record.strip().splitlines())
        self.assertEqual(len(assignments.splitlines()), 4)
        for mode in ('On', 'Baseline'):
            state = SimpleNamespace(id=29, staDisconnect=SimpleNamespace())
            for live in (True, False): # event can drain after prior check/cleanup ended trial
                for valid, reason in ((True, 204), (True, 0), (False, 0)):
                    exec(assignments, {}, dict(state=state, reason=reason, eventUs=123456789, valid=valid))
                    self.assertEqual(vars(state.staDisconnect), dict(reason=reason,eventUs=123456789,valid=valid,ownerTrialId=29))
                    # End/Off keep the same state, next start retains only the last diagnostic.
                    last = state.staDisconnect
                    self.assertIn('const auto lastDisconnect = state.staDisconnect;', model)
                    self.assertIn('state.staDisconnect = lastDisconnect;', model)
                    next_state = SimpleNamespace(id=30, staDisconnect=last)
                    self.assertEqual(next_state.staDisconnect.ownerTrialId,29)
        self.assertIn('copied.setup.trigger = e.config.trigger;', callback)
        self.assertIn('copied.setup.flowType = e.config.flow_type;', callback)
        accept = model.split('constexpr bool accept(',1)[1]
        self.assertNotIn('.trigger', accept)
        self.assertNotIn('.flowType', accept)
        for name in ('sta_disconnect_valid=', 'sta_disconnect_reason=', 'sta_disconnect_time_us=',
                     'sta_owner_trial_id=', 'actual_trigger=', 'actual_flow_type='):
            self.assertIn(name, serial)
        self.assertIn('t.actual.status, unsigned(t.actual.reason)', serial)

    def test_owner_and_serial_boundaries(self):
        source = (ROOT/'main/host/network_quota.cpp').read_text()
        serial = (ROOT/'main/debug/serial_debug.cpp').read_text()
        event = source.split('void NetworkQuota::twtEvent', 1)[1].split('void NetworkQuota::cancelTwtTrial', 1)[0]
        for forbidden in ('esp_wifi_', 'MosaicoOta::', 'GetHAL(', 'nvs_', 'ESP_LOG', 'new '):
            self.assertNotIn(forbidden, event)
        command = serial.split('if (std::strcmp(command, "twt")', 1)[1].split('#endif', 1)[0]
        self.assertNotIn('esp_wifi_', command)
        self.assertIn('requestTwtTrial', command); self.assertIn('twtSnapshot', command)
        self.assertIn('requestTwtTrial(requested, leaseSeconds, announced ?', command)
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
        self.assertIn('const bool linkReady = associated && _connected;', service)
        self.assertIn('const bool controlReady = !GetTailnetQuota().enabled() || GetTailnetQuota().ready();', service)
        self.assertIn('if (!linkReady && (_twt.state.stage != Stage::Armed ||', service)
        self.assertIn('_twt_bootstrap_attempted && _twt_bootstrap_id == _twt.state.id', service)
        self.assertIn('_twt_bootstrap_ok && _twt_bootstrap_id == _twt.state.id', service)
        self.assertIn('linkReady && controlReady && locked && _twt.state.stage == Stage::Armed', service)
        self.assertIn('_twt.state.controlReady = controlReady;', service)
        self.assertIn('control_ready=%u', command)
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
