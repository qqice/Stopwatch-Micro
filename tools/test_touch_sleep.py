"""Offline CST single lease/protocol/failure/OTA fencing fixtures; no devices."""
from pathlib import Path
import re,subprocess,tempfile,unittest
R=Path(__file__).resolve().parents[1]
C=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
def function(s,name):
 a=s.index(name);b=s.index('{',a);depth=1;e=b+1
 while depth:depth+=(s[e]=='{')-(s[e]=='}');e+=1
 return s[a:e]
class TouchSleepTests(unittest.TestCase):
 def compile(self,code,name):
  if not C:self.skipTest('cross compiler unavailable')
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'fixture.cpp';p.write_text(code)
   q=subprocess.run([str(C[-1]),'-std=c++17','-fsyntax-only','-I'+str(R),str(p)],capture_output=True,text=True)
   log=R/'.artifacts/mosaico'/('cst-trial-'+name+'.log');log.write_text(q.stdout+q.stderr)
   self.assertEqual(q.returncode,0,str(log))
 def test_actual_model_protocol_expiry_release_and_single_use(self):
  self.compile(r'''#include "main/host/touch_sleep_model.h"
using namespace TouchSleep;
constexpr void step(Model& m,int64_t now,uint8_t echo=0,bool released=false){
 if(m.writeCommand())m.beforeWrite();else ++m.reads;
 m.complete(now,0,echo,released);
}
constexpr bool cases(){
 Model m;if(m.critical()||m.blocksTouch()||m.consumed)return false;
 if(!m.request(0)||m.request(0))return false;
 step(m,0);if(m.writeCommand()!=0xD11E)return false;
 step(m,10);step(m,20);step(m,30,0x1E);
 if(m.writeCommand()!=0xD101)return false;
 step(m,40);step(m,50,0x01);m.tick(10049,false);if(m.phase!=Phase::Settle)return false;
 m.tick(10050,false);if(m.writeCommand()!=0xD105)return false;
 step(m,10060);if(m.phase!=Phase::SleepRequested||m.leaseUntilUs!=120010060)return false;
 m.tick(120010059,false);if(m.restoring)return false;
 m.tick(120010060,false);if(!m.restoring||m.restoreAttempts!=1)return false;
 step(m,120010070);step(m,120010080);step(m,120010090,0x1E);
 if(m.writeCommand()!=0xD109)return false;
 step(m,120010100);step(m,120010110,0x09);m.tick(120020110,false);
 step(m,120020120,0,false);if(m.phase!=Phase::VerifyRelease||!m.blocksTouch()||m.releaseProven)return false;
 step(m,120020130,0,true);if(m.phase!=Phase::Complete||m.critical()||!m.releaseProven||m.request(200000000))return false;
 return m.writes==7 && m.entryAttempts==1 && m.restoreAttempts==1;
}
static_assert(cases(),"exact command sequence, 10 ms settle, 120 s TTL, genuine release, no second lease");
''','model')
 def test_actual_model_wake_errors_held_deadline_and_loop_gap(self):
  self.compile(r'''#include "main/host/touch_sleep_model.h"
using namespace TouchSleep;
constexpr bool cases(){
 Model untouched;untouched.request(0);untouched.complete(0,17);if(untouched.phase!=Phase::FailedRestored||untouched.writes||untouched.critical())return false;
 Model m;m.request(0);m.complete(0,0);m.beforeWrite();m.complete(0,18);
 if(!m.failed||!m.restoring||m.phase!=Phase::Handshake1)return false;
 m.complete(1,19);if(m.phase!=Phase::RebootPending||m.restoreError!=19||m.error!=18)return false;
 Model held;held.consumed=true;held.dirty=true;held.phase=Phase::SleepRequested;held.leaseUntilUs=120000000;
 held.tick(100,true);if(!held.restoring||held.restoreDeadlineUs!=5000100)return false;
 held.phase=Phase::VerifyRelease;held.normalVerified=true;held.complete(200,0,0,false);
 held.tick(5000099,false);if(held.phase!=Phase::VerifyRelease)return false;
 held.tick(9999999,false);if(held.phase!=Phase::HeldClosed||held.failed||held.critical()||held.releaseProven)return false;
 Model unproven;unproven.dirty=true;unproven.phase=Phase::SleepRequested;unproven.restore(0);
 unproven.tick(9999999,false);return unproven.phase==Phase::RebootPending && unproven.failed;
}
static_assert(cases(),"wake/off precede TTL, errors never restart trial, held closes without bus-fault reboot, loop gaps honor deadline");
''','failure')
 def test_actual_held_closed_latch_and_late_i2c_completion(self):
  self.compile(r'''#include "main/host/touch_sleep_model.h"
#include "main/hal/mosaico/mosaico_touch_power_model.h"
using namespace TouchSleep;
constexpr bool cases(){
 Model late;late.dirty=true;late.phase=Phase::SleepRequested;late.restore(0);
 late.phase=Phase::VerifyRelease;late.normalVerified=true;
 late.complete(5040000,0,0,true);if(late.phase!=Phase::RebootPending||late.releaseProven)return false;
 Model held;held.dirty=true;held.phase=Phase::SleepRequested;held.restore(0);
 held.phase=Phase::VerifyRelease;held.normalVerified=true;held.complete(4990000,0,0,false);
 held.complete(5040000,0,0,true);if(held.phase!=Phase::HeldClosed||held.releaseProven||held.failed||held.critical())return false;
 Model errorAfterHeld;errorAfterHeld.dirty=true;errorAfterHeld.phase=Phase::SleepRequested;errorAfterHeld.restore(0);
 errorAfterHeld.phase=Phase::VerifyRelease;errorAfterHeld.normalVerified=true;errorAfterHeld.complete(4990000,0,0,false);
 errorAfterHeld.complete(5040000,31,0,false);
 if(errorAfterHeld.phase!=Phase::RebootPending||errorAfterHeld.restoreError!=31||errorAfterHeld.lastI2cError!=31)return false;
 bool waiting=true;
 waiting=mosaico_touch_power::waitForPhysicalRelease(waiting,true,true);if(!waiting)return false;
 waiting=mosaico_touch_power::waitForPhysicalRelease(waiting,false,false);if(!waiting)return false;
 waiting=mosaico_touch_power::waitForPhysicalRelease(waiting,true,false);if(waiting)return false;
 return !mosaico_touch_power::waitForPhysicalRelease(waiting,true,true);
}
static_assert(cases(),"late I2C cannot pass strict deadline; held terminal normal/input gate ignores held+invalid until real release");
''','held-latch')
 def test_actual_admission_both_interleavings_and_nested_flight(self):
  self.compile(r'''#include "main/host/touch_sleep_model.h"
using namespace TouchSleep;
constexpr bool cases(){
 AdmissionModel a;if(!a.reserveTouch(false)||a.enterOta()||a.otaInFlight)return false;
 a.releaseTouch();if(!a.enterOta()||!a.enterOta()||a.reserveTouch(false))return false;
 a.leaveOta();if(a.reserveTouch(false))return false;a.leaveOta();
 if(a.reserveTouch(true)||!a.reserveTouch(false))return false;
 // HTTP discovery authoritative busy outlives the function's flight token.
 AdmissionModel b;if(!b.enterOta())return false;bool checking=true;b.leaveOta();
 if(b.reserveTouch(checking))return false;checking=false;
 return b.reserveTouch(checking);
}
static_assert(cases(),"touch-first and OTA-first fence, nested guards, HTTP interval busy protection");
''','admission')
 def test_actual_ota_noop_prefix_does_not_construct_restore_guard(self):
  ota=(R/'main/ota/mosaico_ota.cpp').read_text()
  for name in ['bool takeCheckRequest()', 'bool takeRequest()', 'void processLocalRequests()', 'bool installVerified()']:
   body=function(ota,name);prefix=body[:body.index('TouchSleep::OtaAdmission')]
   self.assertIn('return',prefix,name)
  due=function(ota,'bool automaticCheckDue()');finish=function(ota,'void finishCheck(')
  self.assertEqual(' '.join(re.sub(r'//[^\n]*','',due).split()),'bool automaticCheckDue() { return false; }')
  self.assertIn('TouchSleep::OtaAdmission',function(ota,'bool requestCheck()'))
  manual=function(ota,'bool takeCheckRequest()')
  self.assertIn('checking.store(true); StandbySleep::otaActivity(); publish(UiStage::Checking)',manual)
  self.assertIn('checking.store(false)',finish)
  network=(R/'main/host/network_quota.cpp').read_text()
  self.assertIn('MosaicoOta::finishCheck(downloaded)',network)
 def test_extracted_actual_noop_and_http_busy_lifetime(self):
  ota=(R/'main/ota/mosaico_ota.cpp').read_text()
  due=function(ota,'bool automaticCheckDue()').replace('bool automaticCheckDue()', 'constexpr bool automaticCheckDue()')
  finish=function(ota,'void finishCheck(').replace('void finishCheck(', 'constexpr void finishCheck(')
  take=function(ota,'bool takeCheckRequest()').replace('bool takeCheckRequest()', 'constexpr bool takeCheckRequest()')
  snippets=[due,take,finish]
  for signature in ['bool takeRequest()', 'void processLocalRequests()', 'bool installVerified()']:
   full=function(ota,signature);prefix=full[:full.index('TouchSleep::OtaAdmission')]
   ret='return;' if signature.startswith('void') else 'return true;'
   snippets.append('constexpr '+prefix+'const auto touchGuard=admit(); if(!touchGuard)'+('return;' if signature.startswith('void') else 'return false;')+ret+'}')
  actual='\n'.join(snippets).replace('std::lock_guard<std::mutex> guard(lock);','').replace('TouchSleep::OtaAdmission touchGuard;', 'const auto touchGuard=admit();').replace('StandbySleep::otaActivity()', 'otaActivity()')
  self.compile(r'''#include "main/host/touch_sleep_model.h"
#define CONFIG_MOSAICO_CST_SLEEP_TRIAL 1
#define CONFIG_IDF_TARGET_ESP32S31 1
// RX observer is a separate owner concern; preserve actual touch-admission prefix.
namespace mosaico_rx_observer { constexpr bool request(bool){return true;} constexpr void service(bool){} }
enum class UiStage{Idle,Checking,Failed,ReadyInstall,WaitingPower,Installing};
template<class T>struct Value{T value;constexpr T load()const{return value;}constexpr void store(T x){value=x;}constexpr T exchange(T x){T old=value;value=x;return old;}constexpr operator T()const{return value;}};
struct Actor {
 TouchSleep::AdmissionModel gate;
 Value<bool> imageReady{false},selected{false},checking{false},checkQueued{false},active{false},approvedRequest{false},pending{false},installQueued{false},rebootQueued{false};
 Value<UiStage> statusStage{UiStage::Idle};Value<const char*> autoState{nullptr};
 int64_t now=0;unsigned guardCalls=0,restoreRequests=0,clockCalls=0,activityCalls=0,publishCalls=0;
 constexpr bool busy(){return checking.load()||checkQueued.load()||active.load()||approvedRequest.load()||installQueued.load()||rebootQueued.load();}
 constexpr bool admit(){++guardCalls;if(!gate.enterOta()){++restoreRequests;return false;}gate.leaveOta();return true;}
 constexpr int64_t esp_timer_get_time(){++clockCalls;return now;}
 constexpr bool currentReady(){return true;}
 constexpr void otaActivity(){++activityCalls;}
 constexpr void publish(UiStage stage,const char* =nullptr){++publishCalls;statusStage.store(stage);}
'''+actual+r'''
};
constexpr bool cases(){
 Actor a;a.gate.reserveTouch(false);
 if(a.automaticCheckDue()||a.takeCheckRequest()||a.takeRequest()||a.installVerified())return false;
 a.processLocalRequests();if(a.guardCalls||a.restoreRequests)return false;
 // Automatic discovery is permanently inert, even beyond the former hourly cadence.
 for(int i=0;i<10;++i){a.now+=3600000000LL;if(a.automaticCheckDue())return false;}
 if(a.guardCalls||a.restoreRequests||a.clockCalls||a.activityCalls||a.publishCalls||a.checking.load()||a.autoState.load()!=nullptr||a.statusStage.load()!=UiStage::Idle||!a.gate.touchReserved)return false;
 // Actual manual consumer retains its request if touch admission is denied.
 a.checkQueued.store(true);
 if(a.takeCheckRequest()||a.guardCalls!=1||a.restoreRequests!=1||a.checking.load()||!a.checkQueued.load())return false;
 a.gate.releaseTouch();if(!a.takeCheckRequest()||!a.checking.load()||a.checkQueued.load()||a.statusStage.load()!=UiStage::Checking)return false;
 // Same production manual take/finish bodies: HTTP busy outlives the flight token.
 const unsigned guards=a.guardCalls,activities=a.activityCalls,publishes=a.publishCalls;
 if(a.automaticCheckDue()||a.guardCalls!=guards||a.activityCalls!=activities||a.publishCalls!=publishes||!a.checking.load()||a.statusStage.load()!=UiStage::Checking)return false;
 if(a.gate.reserveTouch(a.busy()))return false;
 a.finishCheck(true);if(a.busy()||a.statusStage.load()!=UiStage::Idle)return false;
 return a.gate.reserveTouch(a.busy());
}
static_assert(cases(),"automatic discovery never touches lease/state; actual manual take/finish fence HTTP lifetime");
''','actual-ota-probes')
 def test_hal_exact_narrow_wire_deadlines_and_no_gpio_fallback(self):
  hal=(R/'main/hal/mosaico/hal_mosaico.cpp').read_text();source=(R/'main/host/touch_sleep.cpp').read_text()
  adapter=hal[hal.index('bool Hal::touchSleepAdapterReady'):hal.index('void Hal::lvgl_init')]
  for bad in ['gpio_set_level','gpio_reset_pin','gpio_hold','esp_lcd_panel_reset','nvs_set_', 'i2c_master_bus_reset']:
   self.assertNotIn(bad,adapter+source)
  self.assertIn('i2c_master_transmit(touch_control,bytes,sizeof(bytes),25)',adapter)
  self.assertIn('i2c_master_transmit_receive(touch_control,address,sizeof(address),bytes,count,25)',adapter)
  self.assertIn('command!=0xD11E && command!=0xD101 && command!=0xD105 && command!=0xD109',adapter)
  self.assertIn('if(data[6]!=0xAB)return ESP_ERR_INVALID_RESPONSE',adapter)
  self.assertIn('released=(data[5]&0x7F)==0',adapter)
  self.assertIn('if(released)return ESP_OK',adapter)
  self.assertIn('config.rst_gpio_num = GPIO_NUM_NC',hal)
  setter=function(hal,'void Hal::setTouchIdlePolling(')
  self.assertLess(setter.index('TouchSleep::blocksTouch()'),setter.index('lvglLock()'))
  self.assertIn('touch_wait_for_release = true',setter)
  self.assertIn('TouchSleep::blocksTouch() || !orientation_healthy',hal)
  self.assertIn('admission.touchReserved && admission.otaInFlight==0',source)
  self.assertIn('!MosaicoOta::busy() && !MosaicoOta::healthPending() && !stagedOta()',source)
 def test_reference_pinned_exact_bytes_and_unsupported_reset(self):
  p=R/'.artifacts/private/cst92xx-readonly-0e4cede3/src'
  if not p.exists():self.skipTest('private audited reference unavailable')
  reg=(p/'registers.rs').read_text(encoding='utf8');driver=(p/'driver.rs').read_text(encoding='utf8');mode=(p/'mode.rs').read_text(encoding='utf8');reset=(p/'reset_pin.rs').read_text(encoding='utf8')
  for n,v in [('REG_SLEEP_MODE','0xD105'),('REG_NORMAL_MODE','0xD109'),('REG_MODE_HANDSHAKE','0xD11E'),('REG_MODE_STATUS','0x0002')]:
   self.assertIn(f'{n}: u16 = {v}',reg)
  self.assertIn('self.set_mode(RunMode::DebugInfo)',function(driver,'pub fn sleep('))
  setmode=function(driver,'pub fn set_mode(')
  self.assertEqual(setmode.count('self.write(&handshake)'),2)
  self.assertIn('read_buffer[1] == handshake[1]',setmode)
  self.assertIn('status[1] != mode_cmd',setmode)
  self.assertIn('delay_ms(10)',setmode)
  self.assertIn('Wakeup = 0x03',mode);self.assertIn('Unverified',mode)
  self.assertIn('no-ops',reset)
if __name__=='__main__':unittest.main()
