/* SPDX-License-Identifier: MIT */
#include "standby_sleep.h"
#include <main_idle_wait.h>
#include "uart_fifo_recovery_model.h"
#include "standby_sleep_model.h"
#include <sdkconfig.h>
#include <hal/hal.h>
#include <esp_attr.h>
#include <esp_pm.h>
#include <esp_timer.h>
#include <esp_sleep.h>
#include <ota/mosaico_ota.h>
#include <driver/gpio.h>
#include <driver/uart.h>
#include <driver/uart_wakeup.h>
#include <mutex>
#include <atomic>
#include <cstdio>
#include <cstring>
#include <cstdlib>

// Neither diagnostic nor normal policy can accidentally activate in an ordinary/unsafe profile.
#if CONFIG_IDF_TARGET_ESP32S31 && CONFIG_PM_ENABLE && CONFIG_FREERTOS_USE_TICKLESS_IDLE && CONFIG_PM_PROFILING && CONFIG_PM_LIGHT_SLEEP_CALLBACKS && !CONFIG_PM_SLP_SPIRAM_HALFSLEEP_ENABLED && !CONFIG_ESP_SLEEP_POWER_DOWN_FLASH && !CONFIG_ESP_SLEEP_SET_FLASH_DPD && !CONFIG_PM_POWER_DOWN_PERIPHERAL_IN_LIGHT_SLEEP && !CONFIG_PM_POWER_DOWN_CPU_IN_LIGHT_SLEEP && !CONFIG_PM_ESP_SLEEP_POWER_DOWN_CPU && !CONFIG_ESP_SYSTEM_PM_POWER_DOWN_CPU && (!(CONFIG_ESP_SLEEP_GPIO_RESET_WORKAROUND || CONFIG_PM_SLP_DISABLE_GPIO) || CONFIG_MOSAICO_SLEEP_IO_RETENTION)
#define STANDBY_SLEEP_SUPPORTED 1
#else
#define STANDBY_SLEEP_SUPPORTED 0
#endif
namespace StandbySleep {
namespace {
std::mutex mutex;
#if CONFIG_MOSAICO_STANDBY_AUTO_LIGHT_SLEEP
Model model=initialModel(STANDBY_SLEEP_SUPPORTED,true);
#else
Model model=initialModel(STANDBY_SLEEP_SUPPORTED,false);
#endif
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
    if(err!=ESP_OK) { lastError=err;fatal=true;model.pause();wanted=false;return; }
    held=need;
}
void refreshRecovery(int64_t now) {
    // Off requests block immediately until the existing PM owner disables LS.
    const bool requested=model.requested(now);
    lockRecovery((applied || requested) && (!wanted || model.uartBlocked(now) || !requested));
}
bool initialize() {
#if STANDBY_SLEEP_SUPPORTED
    // Wake/clock setup is boot-scoped. A closed SerialDebug owner invalidates
    // the FIFO event-wait gate until reboot; never silently reconfigure clocks.
    if(initialized)return true;
    if(!ready || setupAttempted)return false;
    setupAttempted=true;
    esp_err_t err=esp_pm_lock_create(ESP_PM_NO_LIGHT_SLEEP,0,"uart_recovery",&recoveryLock);
    if(err==ESP_OK)err=esp_sleep_pd_config(ESP_PD_DOMAIN_VDDSDIO,ESP_PD_OPTION_ON);
    if(err==ESP_OK)err=gpio_wakeup_enable(GPIO_NUM_7,GPIO_INTR_LOW_LEVEL);
    if(err==ESP_OK)err=esp_sleep_enable_gpio_wakeup();
    uart_wakeup_cfg_t uart{};
#if CONFIG_MOSAICO_UART_FIFO_RECOVERY && SOC_UART_WAKEUP_SUPPORT_FIFO_THRESH_MODE && SOC_PM_SUPPORT_PMU_CLK_ICG
    uart.wakeup_mode=UART_WK_MODE_FIFO_THRESH;uart.rx_fifo_threshold=UartFifoRecovery::WakeThreshold;
#elif CONFIG_MOSAICO_UART_FIFO_RECOVERY
    err=ESP_ERR_NOT_SUPPORTED; // Never silently apply only part of the candidate.
#else
    uart.wakeup_mode=UART_WK_MODE_ACTIVE_THRESH;uart.rx_edge_threshold=3;
#endif
    if(err==ESP_OK)err=uart_wakeup_setup(UART_NUM_0,&uart);
    if(err==ESP_OK)err=esp_sleep_enable_uart_wakeup(UART_NUM_0);
    esp_pm_sleep_cbs_register_config_t callbacks{};
    callbacks.enter_cb=enterSleep;callbacks.exit_cb=exitSleep;
    if(err==ESP_OK)err=esp_pm_light_sleep_register_cbs(&callbacks);
    MainIdleWait::uartRecoveryConfigured(err==ESP_OK,err);
    // Do not call uart_wakeup_clear: FIFO clear forces global XTAL OFF/IOMUX
    // GATE, rather than restoring an earlier owner's policy. Boot-scoped only.
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
bool monitoring() { std::lock_guard<std::mutex> guard(mutex);return model.automaticPolicy || model.leaseUntil || applied; }
void uartReady(bool value) {
    std::lock_guard<std::mutex> guard(mutex);ready=value;
    if(!ready) { model.pause();wanted=false;refreshRecovery(esp_timer_get_time()); }
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
    MainIdleWait::viewState(locked && safe && !fault);
    model.service(esp_timer_get_time());
    if(!locked || !safe || fault)wanted=false;
    refreshRecovery(esp_timer_get_time());
}
void service(bool ota, bool activeBle) {
    std::lock_guard<std::mutex> guard(mutex);bleActive=activeBle;
    // Re-read the actual atomics under our mutex: a stale false main-loop sample
    // must not clear an OTA gate published concurrently by the OTA owner.
    model.otaBlocked=ota || MosaicoOta::busy() || MosaicoOta::healthPending();
    const int64_t now=esp_timer_get_time();
    if(activeBle)model.pause();
    model.service(now);
    if(model.otaBlocked || activeBle || !model.requested(now))wanted=false;
    refreshRecovery(now);
}
bool request(uint32_t seconds) {
    std::lock_guard<std::mutex> guard(mutex);
    if(!GetHAL().sleepIoRetentionReady() || seconds<30 || seconds>300 || !ready || !model.viewLocked || !model.displaySafe || model.fault || bleActive || model.otaBlocked || MosaicoOta::busy() || MosaicoOta::healthPending())return false;
    if(fatal || !initialize() || !model.request(seconds,esp_timer_get_time()))return false;
    wanted=false;refreshRecovery(esp_timer_get_time());return true;
}
bool enableAutomatic(bool confirmed) {
    std::lock_guard<std::mutex> guard(mutex);
    if(!GetHAL().sleepIoRetentionReady() || !confirmed || !STANDBY_SLEEP_SUPPORTED || !ready || fatal)return false;
    // Unlike a diagnostic trial, policy activation can be requested while awake.
    // Wake sources/PM callbacks are initialized only when allow() sees ALL gates.
    model.enableAutomatic(true);wanted=false;refreshRecovery(esp_timer_get_time());return true;
}
void cancelForActivity() {
    std::lock_guard<std::mutex> guard(mutex);model.pause();wanted=false;refreshRecovery(esp_timer_get_time());
}
void otaActivity() {
    std::lock_guard<std::mutex> guard(mutex);
    model.otaBlocked=true;model.pause();wanted=false;refreshRecovery(esp_timer_get_time());
}
void off() {
    std::lock_guard<std::mutex> guard(mutex);model.off();wanted=false;refreshRecovery(esp_timer_get_time());
}
bool allow(uint32_t cpu, bool locked, bool wifi) {
    std::lock_guard<std::mutex> guard(mutex);
    const int64_t now=esp_timer_get_time();
    // allow() never clears the stored OTA gate; only the authoritative service
    // may release it after observing actual busy/health atomics both false.
    if(MosaicoOta::busy() || MosaicoOta::healthPending())model.otaBlocked=true;
    const bool ioSafe=GetHAL().sleepIoRetentionReady();
    if(!ioSafe || wifi || bleActive)model.pause();
    model.service(now);
    const bool safe=ioSafe && !fatal && ready && model.eligible(now,cpu,locked,wifi || bleActive);
    wanted=safe && (initialized || initialize());
    refreshRecovery(now);return wanted;
}
void beforeConfigure(bool lightSleep) {
    std::lock_guard<std::mutex> guard(mutex);if(applied && !lightSleep)captureCounts();
}
void configured(bool lightSleep, int32_t error) {
    std::lock_guard<std::mutex> guard(mutex);
    if(error) { lastError=error;fatal=true;model.pause();wanted=false; }
    else applied=lightSleep;
    refreshRecovery(esp_timer_get_time());
}
Snapshot snapshot() {
    std::lock_guard<std::mutex> guard(mutex);captureCounts();
    Snapshot out{};const int64_t now=esp_timer_get_time();
    if(MosaicoOta::busy() || MosaicoOta::healthPending())model.otaBlocked=true;
    model.service(now);
    out.supported=STANDBY_SLEEP_SUPPORTED;out.lease=model.leaseUntil!=0;out.configured=applied;
    out.eligible=wanted && model.requested(now) && model.displaySafe && !model.otaBlocked && !model.fault;
    out.uartBlocked=model.uartBlocked(now);out.uartReady=ready;
    out.automaticPolicy=model.automaticPolicy;out.policyMode=model.mode(now);
    out.activeMode=out.configured && out.eligible && !out.uartBlocked ? out.policyMode : Mode::Off;
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
