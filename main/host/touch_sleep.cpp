/* SPDX-License-Identifier: MIT */
#include "touch_sleep.h"
#include <sdkconfig.h>
#include <hal/hal.h>
#include <host/standby_sleep.h>
#include <ota/mosaico_ota.h>
#include <esp_ota_ops.h>
#include <esp_timer.h>
#include <esp_system.h>
#include <esp_log.h>
#include <atomic>
#include <mutex>
#include <cstdio>
#if defined(MOSAICO_BOARD) && CONFIG_IDF_TARGET_ESP32S31 && CONFIG_MOSAICO_CST_SLEEP_TRIAL
#define CST_TRIAL_SUPPORTED 1
#else
#define CST_TRIAL_SUPPORTED 0
#endif
namespace TouchSleep {
namespace {
// Model is app_main-owned. The mutex arbitrates only admission reservations;
// never hold it across HAL/LVGL/I2C/OTA APIs. OTA guards may nest on one owner.
Model model;
std::mutex ownerMutex;
std::atomic<bool> reserved{false}, needRestore{false};
AdmissionModel admission;
bool validRunning() {
 const auto* p=esp_ota_get_running_partition();esp_ota_img_states_t state=ESP_OTA_IMG_UNDEFINED;
 return p && esp_ota_get_state_partition(p,&state)==ESP_OK && state==ESP_OTA_IMG_VALID;
}
bool stagedOta() {
 MosaicoOta::UiSnapshot s{};
 return !MosaicoOta::copyUiSnapshot(s) || (s.stage!=MosaicoOta::UiStage::Idle && s.stage!=MosaicoOta::UiStage::Complete && s.stage!=MosaicoOta::UiStage::Failed);
}
}
OtaAdmission::OtaAdmission() {
#if CST_TRIAL_SUPPORTED
 std::lock_guard<std::mutex> guard(ownerMutex);
 if(!admission.enterOta()) {needRestore.store(true,std::memory_order_release);return;}
#endif
 allowed_=true;
}
OtaAdmission::~OtaAdmission() {
#if CST_TRIAL_SUPPORTED
 if(allowed_) {std::lock_guard<std::mutex> guard(ownerMutex);admission.leaveOta();}
#endif
}
bool request120(bool locked,bool safeView,bool asyncBusy) {
#if CST_TRIAL_SUPPORTED
 if(!locked || !safeView || asyncBusy || MosaicoOta::busy() || MosaicoOta::healthPending() || stagedOta() || !validRunning() || !GetHAL().touchSleepAdapterReady())return false;
 {
  std::lock_guard<std::mutex> guard(ownerMutex);
  // Recheck the OTA publication under the same admission fence. No OTA locks.
  if(model.consumed || !admission.reserveTouch(MosaicoOta::busy() || MosaicoOta::healthPending()))return false;
  if(!model.request(esp_timer_get_time())) {admission.releaseTouch();return false;}
  reserved.store(true,std::memory_order_release);needRestore.store(false,std::memory_order_release);
 }
 GetHAL().setTouchIdlePolling(true);
 return true;
#else
 (void)locked;(void)safeView;(void)asyncBusy;return false;
#endif
}
void off() {needRestore.store(true,std::memory_order_release);}
void wakeRequested() {if(reserved.load(std::memory_order_acquire))needRestore.store(true,std::memory_order_release);}
bool blocksTouch() {return reserved.load(std::memory_order_acquire);}
bool active() {return reserved.load(std::memory_order_acquire);}
void service(bool locked,bool conflict) {
#if CST_TRIAL_SUPPORTED
 const int64_t now=esp_timer_get_time();
 model.tick(now,needRestore.exchange(false,std::memory_order_acq_rel) || !locked || conflict);
 if(model.transition() || model.failed)StandbySleep::viewState(locked,false,model.failed);
 if(!model.critical()) {
  bool released=false;
  {std::lock_guard<std::mutex> guard(ownerMutex);released=reserved.exchange(false,std::memory_order_acq_rel);if(released)admission.releaseTouch();}
  if(released)GetHAL().setTouchIdlePolling(locked);
  return;
 }
 if(model.phase==Phase::SleepRequested || model.phase==Phase::Settle)return;
 if(model.phase==Phase::RebootPending) {
  // A reserved trial cannot admit a new OTA owner. Still recheck actual busy,
  // staged install and in-flight calls before the authorized ordinary restart.
  bool safe=false;{std::lock_guard<std::mutex> guard(ownerMutex);safe=admission.touchReserved && admission.otaInFlight==0;}
  if(now>=model.deadlineUs && safe && !MosaicoOta::busy() && !MosaicoOta::healthPending() && !stagedOta()) {
   ESP_LOGE("CST trial","normal restart after failed restore entry_error=%ld restore_error=%ld",static_cast<long>(model.error),static_cast<long>(model.restoreError));
   esp_restart(); // Existing startup performs full LCD initialization. No isolated RST pulse.
  }return;
 }
 int32_t rc=ESP_OK;uint8_t echo=0;bool released=false;
 const auto command=model.writeCommand();
 if(command) {model.beforeWrite();rc=GetHAL().touchSleepWrite(command);}
 else if(model.phase==Phase::Probe) {++model.reads;rc=GetHAL().touchSleepProbe();}
 else if(model.phase==Phase::HandshakeEcho || model.phase==Phase::ModeEcho) {
  uint8_t bytes[4]{};++model.reads;rc=GetHAL().touchSleepRead(0x0002,bytes,model.phase==Phase::HandshakeEcho?4:2);echo=bytes[1];
 } else if(model.phase==Phase::VerifyRelease) {
  ++model.reads;rc=GetHAL().touchSleepReleaseProof(released);
 }
 model.complete(esp_timer_get_time(),rc,echo,released);
#else
 (void)locked;(void)conflict;
#endif
}
Snapshot snapshot() {
 Snapshot s;s.model=model;s.supported=CST_TRIAL_SUPPORTED;s.reserved=reserved.load(std::memory_order_acquire);
 {std::lock_guard<std::mutex> guard(ownerMutex);s.otaInFlight=admission.otaInFlight;}return s;
}
void status(char* out,std::size_t capacity) {
 const auto s=snapshot();const auto& m=s.model;const int64_t now=esp_timer_get_time();
 std::snprintf(out,capacity,"supported=%d phase=%s restoring=%d reserved=%d consumed=%d failed=%d entry_attempts=%lu restore_attempts=%lu write_commands=%lu read_actions=%lu echo=%02x last_cmd=%04x last_i2c_error=%ld entry_error=%ld entry_error_phase=%s failed_echo=%02x restore_error=%ld restore_error_phase=%s lease_remaining_ms=%llu sleep_request_us=%llu restore_us=%llu release_proven=%d normal_verified=%d normal_report_valid=%d restore_deadline_us=%llu ota_inflight=%lu sleep_verified=0 reset_pulse=0 rail_write=0 nvs_write=0 ram_only=1",
  s.supported,phaseName(m.phase),m.restoring,s.reserved,m.consumed,m.failed,static_cast<unsigned long>(m.entryAttempts),static_cast<unsigned long>(m.restoreAttempts),static_cast<unsigned long>(m.writes),static_cast<unsigned long>(m.reads),m.echo,m.lastCommand,static_cast<long>(m.lastI2cError),static_cast<long>(m.error),phaseName(m.errorPhase),m.failedEcho,static_cast<long>(m.restoreError),phaseName(m.restoreErrorPhase),
  static_cast<unsigned long long>(m.leaseUntilUs>now?(m.leaseUntilUs-now)/1000:0),static_cast<unsigned long long>(m.sleepRequestUs),static_cast<unsigned long long>(m.restoredUs?m.restoredUs-m.restoreStartedUs:0),m.releaseProven,m.normalVerified,m.normalReportValid,static_cast<unsigned long long>(m.restoreDeadlineUs),static_cast<unsigned long>(s.otaInFlight));
}
}
