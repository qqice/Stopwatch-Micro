/* SPDX-License-Identifier: MIT */
#include "powerbank_keepalive.h"
#include <sdkconfig.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <atomic>
#include <mutex>
#if defined(MOSAICO_BOARD) && CONFIG_IDF_TARGET_ESP32S31 && CONFIG_PM_ENABLE
#define POWERBANK_PULSE_SUPPORTED 1
#include <esp_pm.h>
#include "network_quota.h"
#include "standby_sleep.h"
#include "mosaico_display_settings.h"
#include <ota/mosaico_ota.h>
#else
#define POWERBANK_PULSE_SUPPORTED 0
#endif

namespace PowerbankKeepalive {
namespace {
portMUX_TYPE mux=portMUX_INITIALIZER_UNLOCKED;
Model model;
AutoPolicy autoPolicy;
Snapshot stats;
TaskHandle_t task=nullptr;
bool fatal=false;
std::mutex admission;
#if POWERBANK_PULSE_SUPPORTED
esp_pm_lock_handle_t pmLock=nullptr; // Lazy, task-owned; never acquired by UART/ISR.
volatile uint32_t workSink=0;
bool safe() {
    portENTER_CRITICAL(&mux);
    const bool supplyAllowed=!model.automatic || autoPolicy.desired(esp_timer_get_time());
    portEXIT_CRITICAL(&mux);
    if(!supplyAllowed || MosaicoOta::busy() || MosaicoOta::healthPending())return false;
    auto& quota=GetNetworkQuota();
    if(!quota.displayLocked())return false;
    const auto p=quota.powerStats();
    return p.locked && !p.wifiRunning && p.cpuMHz==160 && StandbySleep::pulseEligible();
}
void pmFailure(State state, esp_err_t error) {
    portENTER_CRITICAL(&mux);
    model.off();fatal=true;stats.state=state;stats.error=error;++stats.pmErrors;
    portEXIT_CRITICAL(&mux);
}
// RAII releases on every acquired-lock exit, including abort/reconfigure/OTA.
struct PulseLock {
    bool held=false;
    bool acquire() {
        const auto error=esp_pm_lock_acquire(pmLock);
        if(error!=ESP_OK) {pmFailure(State::AcquireError,error);return false;}
        held=true;return true;
    }
    void release() {
        if(held) {
            held=false;
            const auto error=esp_pm_lock_release(pmLock);
            if(error!=ESP_OK)pmFailure(State::ReleaseError,error);
        }
    }
    ~PulseLock() { release(); }
};
// Wrap the entire reserved attempt, including PM creation/acquisition failures
// and gate aborts. Destruction follows runPulse's PM release, so cooldown starts
// no earlier than the actual end. Stopped/reconfigured generations are ignored.
struct PulseCompletion {
    uint32_t version;
    ~PulseCompletion() {
        portENTER_CRITICAL(&mux);
        model.completePulse(esp_timer_get_time(),version);
        portEXIT_CRITICAL(&mux);
    }
};
bool allowed(uint32_t version) {
    const bool gate=safe();const auto now=esp_timer_get_time();
    portENTER_CRITICAL(&mux);
    const bool result=model.pulseAllowed(now,version,gate);
    portEXIT_CRITICAL(&mux);
    return result;
}
void runPulse(uint32_t version, uint32_t duration) {
    PulseLock lock;
    if(!lock.acquire())return;
    if(!allowed(version))return;
    const auto publishNow=esp_timer_get_time();
    portENTER_CRITICAL(&mux);
    // Permission was sampled outside mux; off/reconfigure may have raced it.
    // Revalidate the lease/generation atomically with publishing Pulsing.
    if(!model.pulseAllowed(publishNow,version,true)) {
        portEXIT_CRITICAL(&mux);return; // PulseLock still releases by RAII.
    }
    stats.pulsing=true;stats.state=State::Pulsing;
    portEXIT_CRITICAL(&mux);
    const auto until=esp_timer_get_time()+int64_t(duration)*1000;
    bool aborted=false;
    // Low-priority dedicated task, no scheduler suspension or IRQ masking.
    // Re-evaluate gates every <=1ms of our CPU work (preemption can extend
    // wall time); never run this loop in esp_timer task or an ISR.
    while(esp_timer_get_time()<until) {
        if(!allowed(version)) {aborted=true;break;}
        const auto sliceUntil=esp_timer_get_time()+500;
        uint32_t work=workSink;
        do {
            for(unsigned i=0;i<64;++i)work=work*1664525U+1013904223U;
            workSink=work;
        } while(esp_timer_get_time()<sliceUntil && esp_timer_get_time()<until);
    }
    lock.release();
    portENTER_CRITICAL(&mux);
    stats.pulsing=false;if(aborted)++stats.aborted;else ++stats.pulses;
    // Do not overwrite Off/Reconfigured when a concurrent command stopped us.
    if(model.generation==version)stats.state=aborted ? State::Blocked : State::Waiting;
    portEXIT_CRITICAL(&mux);
}
void worker(void*) {
    for(;;) {
        const auto now=esp_timer_get_time();
        bool due=false;uint32_t version=0,duration=0;int64_t waitUs=0;
        const bool gate=safe();
        portENTER_CRITICAL(&mux);
        const bool wasEnabled=model.leaseUntil!=0;model.expire(now);
        if(wasEnabled && !model.leaseUntil)stats.state=State::Expired;
        if(model.due(now)) {
            due=model.take(now,gate);version=model.generation;duration=model.config.durationMs;
            if(!gate) {++stats.blocked;stats.state=State::Blocked;}
        }
        if(model.enabled(now)) {
            const auto deadline=model.automatic || model.nextDue<model.leaseUntil ? model.nextDue : model.leaseUntil;
            waitUs=deadline-now;
        }
        portEXIT_CRITICAL(&mux);
        if(due) {
            PulseCompletion completion{version};
            if(!pmLock) {
                const auto error=esp_pm_lock_create(ESP_PM_NO_LIGHT_SLEEP,0,"bank_pulse",&pmLock);
                if(error!=ESP_OK) {pmFailure(State::CreateError,error);continue;}
            }
            runPulse(version,duration);
            continue; // Recompute wait after bounded work; never catch up backlog.
        }
        const TickType_t timeout=waitUs>0 ? pdMS_TO_TICKS((waitUs+999)/1000)+1 : portMAX_DELAY;
        uint32_t notification=0;
        xTaskNotifyWait(0,UINT32_MAX,&notification,timeout);
    }
}
#endif
}
bool request(const Request& config) {
    if(!valid(config)) { // No allocation or model mutation for invalid bounds.
        portENTER_CRITICAL(&mux);++stats.invalidRequests;
        if(!model.leaseUntil)stats.state=State::Invalid;
        portEXIT_CRITICAL(&mux);return false;
    }
#if POWERBANK_PULSE_SUPPORTED
    std::unique_lock<std::mutex> guard(admission,std::try_to_lock);
    if(!guard.owns_lock())return false;
    portENTER_CRITICAL(&mux);const bool failed=fatal;portEXIT_CRITICAL(&mux);
    if(failed)return false; // PM failures latch off until reboot; never nest an uncertain lock.
    // An overlapping accepted configuration invalidates the active pulse first.
    portENTER_CRITICAL(&mux);
    if(model.leaseUntil || stats.pulsing)++stats.reconfigurations;
    portEXIT_CRITICAL(&mux);
    off();
    portENTER_CRITICAL(&mux);const auto reservation=model.generation;portEXIT_CRITICAL(&mux);
    if(!task) {
        TaskHandle_t created=nullptr;
        if(xTaskCreate(worker,"bank_pulse",3072,nullptr,1,&created)!=pdPASS) {
            portENTER_CRITICAL(&mux);stats.state=State::TaskError;stats.error=-1;portEXIT_CRITICAL(&mux);
            return false;
        }
        portENTER_CRITICAL(&mux);task=created;portEXIT_CRITICAL(&mux);
    }
    portENTER_CRITICAL(&mux);
    if(fatal || model.generation!=reservation) {portEXIT_CRITICAL(&mux);return false;}
    model.request(config,esp_timer_get_time());stats.config=config;stats.state=State::Waiting;stats.error=0;
    portEXIT_CRITICAL(&mux);
    xTaskNotify(task,1,eSetBits);return true;
#else
    portENTER_CRITICAL(&mux);stats.state=State::Unsupported;portEXIT_CRITICAL(&mux);return false;
#endif
}
void off() {
    portENTER_CRITICAL(&mux);model.off();autoPolicy.inhibited=true;stats.state=State::Off;const auto handle=task;portEXIT_CRITICAL(&mux);
    if(handle)xTaskNotify(handle,1,eSetBits);
}
void observeSupply(bool valid, bool external) {
    portENTER_CRITICAL(&mux);
    autoPolicy.observe(valid,external,esp_timer_get_time());
    const bool stopped=model.automatic && !autoPolicy.desired(esp_timer_get_time());
    if(stopped) {model.off();stats.state=State::Off;}
    const auto handle=task;
    portEXIT_CRITICAL(&mux);
    if(stopped && handle)xTaskNotify(handle,1,eSetBits);
}
void serviceAutomatic() {
#if POWERBANK_PULSE_SUPPORTED
    // No gauge polling or NVS writes: reuse the GUI classification and settings.
    static int64_t nextCheck=0;
    const auto now=esp_timer_get_time();
    if(now<nextCheck)return;
    nextCheck=now+250000;
    MosaicoDisplay::Snapshot display;
    if(!MosaicoDisplay::snapshot(display))return;
    std::unique_lock<std::mutex> guard(admission,std::try_to_lock);
    if(!guard.owns_lock())return;
    portENTER_CRITICAL(&mux);
    autoPolicy.configure(display.effectiveConfig.smartKeepalive);
    const bool wanted=autoPolicy.desired(now) && !fatal;
    if(!wanted && model.automatic) {model.off();stats.state=State::Off;}
    const bool start=wanted && !model.enabled(now);
    const auto reservation=model.generation;
    portEXIT_CRITICAL(&mux);
    if(start && !task) {
        TaskHandle_t created=nullptr;
        if(xTaskCreate(worker,"bank_pulse",3072,nullptr,1,&created)!=pdPASS) {
            portENTER_CRITICAL(&mux);fatal=true;stats.state=State::TaskError;stats.error=-1;portEXIT_CRITICAL(&mux);return;
        }
        portENTER_CRITICAL(&mux);task=created;portEXIT_CRITICAL(&mux);
    }
    portENTER_CRITICAL(&mux);
    if(start && !fatal && model.generation==reservation && autoPolicy.desired(esp_timer_get_time())) {
        model.startAutomatic(esp_timer_get_time());stats.config=model.config;stats.state=State::Waiting;stats.error=0;
    }
    const auto handle=task;
    portEXIT_CRITICAL(&mux);
    // Only transitions notify; no 250ms wakeup of the pulse task in steady state.
    if(start && handle)xTaskNotify(handle,1,eSetBits);
#endif
}
Snapshot boundedSnapshot() {
    const auto now=esp_timer_get_time();
    portENTER_CRITICAL(&mux);
    Snapshot out=stats;out.supported=POWERBANK_PULSE_SUPPORTED;out.enabled=model.enabled(now);
    out.automatic=model.automatic;out.configured=autoPolicy.configured;out.supplyExternal=autoPolicy.external;out.inhibited=autoPolicy.inhibited;
    out.remainingSeconds=out.enabled && !model.automatic ? uint32_t((model.leaseUntil-now+999999)/1000000) : 0;
    portEXIT_CRITICAL(&mux);return out;
}
}
