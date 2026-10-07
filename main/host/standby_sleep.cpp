/* SPDX-License-Identifier: MIT */
#include "standby_sleep.h"
#include "standby_sleep_model.h"
#include <sdkconfig.h>
#include <esp_attr.h>
#include <esp_pm.h>
#include <esp_timer.h>
#include <esp_sleep.h>
#include <driver/gpio.h>
#include <driver/uart.h>
#include <driver/uart_wakeup.h>
#include <mutex>
#include <atomic>
#include <cstdio>
#include <cstring>
#include <cstdlib>

// This diagnostic cannot accidentally activate in an ordinary/unsafe profile.
#if CONFIG_IDF_TARGET_ESP32S31 && CONFIG_PM_ENABLE && CONFIG_FREERTOS_USE_TICKLESS_IDLE && CONFIG_PM_PROFILING && CONFIG_PM_LIGHT_SLEEP_CALLBACKS && !CONFIG_PM_SLP_SPIRAM_HALFSLEEP_ENABLED && !CONFIG_ESP_SLEEP_POWER_DOWN_FLASH && !CONFIG_ESP_SLEEP_SET_FLASH_DPD && !CONFIG_PM_POWER_DOWN_PERIPHERAL_IN_LIGHT_SLEEP && !CONFIG_PM_POWER_DOWN_CPU_IN_LIGHT_SLEEP
#define STANDBY_SLEEP_SUPPORTED 1
#else
#define STANDBY_SLEEP_SUPPORTED 0
#endif
namespace StandbySleep {
namespace {
std::mutex mutex;
Model model;
bool ready=false, initialized=false, setupAttempted=false, fatal=false, applied=false, wanted=false, bleActive=false, held=false;
int32_t lastError=0;
esp_pm_lock_handle_t recoveryLock=nullptr;
uint64_t successes=0, rejects=0;
bool pmCountsValid=false;
// All callback storage lives in internal DRAM; no 64-bit atomic lib calls on RV32.
DRAM_ATTR std::atomic<uint32_t> intervalSequence{0}, intervalLo{0}, intervalHi{0}, intervalCount{0};
static_assert(std::atomic<uint32_t>::is_always_lock_free,"IRAM callbacks require native 32-bit atomics");
#if STANDBY_SLEEP_SUPPORTED
esp_err_t IRAM_ATTR enterSleep(int64_t, void*) { return ESP_OK; }
esp_err_t IRAM_ATTR exitSleep(int64_t us, void*) {
    if(us>0) {
        intervalSequence.fetch_add(1,std::memory_order_relaxed);
        const uint64_t total=(uint64_t(intervalHi.load(std::memory_order_relaxed))<<32) |
            intervalLo.load(std::memory_order_relaxed);
        const uint64_t next=total+static_cast<uint64_t>(us);
        intervalLo.store(static_cast<uint32_t>(next),std::memory_order_relaxed);
        intervalHi.store(static_cast<uint32_t>(next>>32),std::memory_order_relaxed);
        intervalCount.fetch_add(1,std::memory_order_relaxed);
        intervalSequence.fetch_add(1,std::memory_order_release);
    }
    return ESP_OK;
}
#endif
void lockRecovery(bool need) {
    if(!recoveryLock || need==held)return;
    const esp_err_t err=need ? esp_pm_lock_acquire(recoveryLock) : esp_pm_lock_release(recoveryLock);
    if(err!=ESP_OK) { lastError=err;fatal=true;model.off();wanted=false;return; }
    held=need;
}
void refreshRecovery(int64_t now) {
    // Off requests block immediately until the existing PM owner disables LS.
    lockRecovery((applied || model.leaseUntil) && (!wanted || model.uartBlocked(now) || !model.leaseUntil));
}
bool initialize() {
#if STANDBY_SLEEP_SUPPORTED
    if(initialized)return true;
    if(!ready || setupAttempted)return false;
    setupAttempted=true;
    esp_err_t err=esp_pm_lock_create(ESP_PM_NO_LIGHT_SLEEP,0,"uart_recovery",&recoveryLock);
    if(err==ESP_OK)err=esp_sleep_pd_config(ESP_PD_DOMAIN_VDDSDIO,ESP_PD_OPTION_ON);
    if(err==ESP_OK)err=gpio_wakeup_enable(GPIO_NUM_7,GPIO_INTR_LOW_LEVEL);
    if(err==ESP_OK)err=esp_sleep_enable_gpio_wakeup();
    uart_wakeup_cfg_t uart{};uart.wakeup_mode=UART_WK_MODE_ACTIVE_THRESH;uart.rx_edge_threshold=3;
    if(err==ESP_OK)err=uart_wakeup_setup(UART_NUM_0,&uart);
    if(err==ESP_OK)err=esp_sleep_enable_uart_wakeup(UART_NUM_0);
    esp_pm_sleep_cbs_register_config_t callbacks{};
    callbacks.enter_cb=enterSleep;callbacks.exit_cb=exitSleep;
    if(err==ESP_OK)err=esp_pm_light_sleep_register_cbs(&callbacks);
    if(err!=ESP_OK) { lastError=err;fatal=true;return false; }
    initialized=true;return true;
#else
    lastError=ESP_ERR_NOT_SUPPORTED;return false;
#endif
}
// PM mode eligibility time is not measured silicon sleep residency. SDK's
// accepted/rejected counters are read through its public task-context API.
void captureCounts() {
#if STANDBY_SLEEP_SUPPORTED
    if(!applied)return;
    static char buffer[8192]; // Bounded, mutex-protected, never in a sleep callback.
    std::memset(buffer,0,sizeof(buffer));
    FILE* stream=fmemopen(buffer,sizeof(buffer)-1,"w");
    if(!stream) { pmCountsValid=false;return; }
    const esp_err_t err=esp_pm_dump_locks(stream);
    const bool complete=std::fflush(stream)==0 && !std::ferror(stream);
    std::fclose(stream);
    const char* accepted=std::strstr(buffer,"light_sleep_counts:");
    const char* rejected=std::strstr(buffer,"light_sleep_reject_counts:");
    if(err==ESP_OK && complete && accepted && rejected) {
        successes=std::strtoull(accepted+std::strlen("light_sleep_counts:"),nullptr,10);
        rejects=std::strtoull(rejected+std::strlen("light_sleep_reject_counts:"),nullptr,10);
        pmCountsValid=true;
    } else pmCountsValid=false;
#endif
}
}
bool monitoring() { std::lock_guard<std::mutex> guard(mutex);return model.leaseUntil || applied; }
void uartReady(bool value) {
    std::lock_guard<std::mutex> guard(mutex);ready=value;
    if(!ready) { model.off();wanted=false;refreshRecovery(esp_timer_get_time()); }
}
void uartWake() {
    std::lock_guard<std::mutex> guard(mutex);model.wake(esp_timer_get_time());refreshRecovery(esp_timer_get_time());
}
void uartTraffic(bool pending, bool activity) {
    std::lock_guard<std::mutex> guard(mutex);
    model.uartPending=pending;
    // Every received chunk refreshes the same bounded wake window, including a
    // preamble when the first UART_WAKEUP event was lost to queue saturation.
    const int64_t now=esp_timer_get_time();if(activity)model.wake(now);refreshRecovery(now);
}
void viewState(bool locked, bool safe, bool fault) {
    std::lock_guard<std::mutex> guard(mutex);
    model.viewLocked=locked;model.displaySafe=safe;model.fault=fault;
    model.service(esp_timer_get_time(),false);
    if(!locked || !safe || fault)wanted=false;
    refreshRecovery(esp_timer_get_time());
}
void service(bool ota, bool activeBle) {
    std::lock_guard<std::mutex> guard(mutex);bleActive=activeBle;
    const int64_t now=esp_timer_get_time();model.service(now,ota);
    if(ota || activeBle || !model.leaseUntil)wanted=false;
    refreshRecovery(now);
}
bool request(uint32_t seconds) {
    std::lock_guard<std::mutex> guard(mutex);
    if(seconds<30 || seconds>300 || !ready || !model.viewLocked || !model.displaySafe || model.fault || bleActive)return false;
    if(fatal || !initialize() || !model.request(seconds,esp_timer_get_time()))return false;
    wanted=false;refreshRecovery(esp_timer_get_time());return true;
}
void off() {
    std::lock_guard<std::mutex> guard(mutex);model.off();wanted=false;refreshRecovery(esp_timer_get_time());
}
bool allow(uint32_t cpu, bool locked, bool wifi) {
    std::lock_guard<std::mutex> guard(mutex);
    const int64_t now=esp_timer_get_time();
    model.service(now,false);
    wanted=initialized && !fatal && ready && model.eligible(now,cpu,locked,wifi || bleActive);
    refreshRecovery(now);return wanted;
}
void beforeConfigure(bool lightSleep) {
    std::lock_guard<std::mutex> guard(mutex);if(applied && !lightSleep)captureCounts();
}
void configured(bool lightSleep, int32_t error) {
    std::lock_guard<std::mutex> guard(mutex);
    if(error) { lastError=error;fatal=true;model.off();wanted=false; }
    else applied=lightSleep;
    refreshRecovery(esp_timer_get_time());
}
Snapshot snapshot() {
    std::lock_guard<std::mutex> guard(mutex);captureCounts();
    Snapshot out{};const int64_t now=esp_timer_get_time();model.service(now,false);
    out.supported=STANDBY_SLEEP_SUPPORTED;out.lease=model.leaseUntil!=0;out.configured=applied;
    out.eligible=wanted;out.uartBlocked=model.uartBlocked(now);out.uartReady=ready;
    out.remainingSeconds=model.leaseUntil ? static_cast<uint32_t>((model.leaseUntil-now+999999)/1000000) : 0;
    out.error=lastError;out.successfulSleeps=successes;out.rejectedSleeps=rejects;out.pmCountsValid=pmCountsValid;
    for(unsigned attempt=0;attempt<8;++attempt) {
        const uint32_t before=intervalSequence.load(std::memory_order_acquire);
        if(before&1)continue;
        const uint32_t lo=intervalLo.load(std::memory_order_relaxed),hi=intervalHi.load(std::memory_order_relaxed);
        const uint32_t count=intervalCount.load(std::memory_order_relaxed);
        if(before==intervalSequence.load(std::memory_order_acquire)) {out.frameworkIntervalUs=(uint64_t(hi)<<32)|lo;out.positiveIntervals=count;break;}
    }
    esp_pm_lock_stats_t stats[ESP_PM_LOCK_MAX]{};
    if(esp_pm_get_lock_stats_all(stats)==ESP_OK) {
        out.lockCreated=stats[ESP_PM_NO_LIGHT_SLEEP].created;out.lockAcquired=stats[ESP_PM_NO_LIGHT_SLEEP].acquired;
    }
    refreshRecovery(now);return out;
}
}
