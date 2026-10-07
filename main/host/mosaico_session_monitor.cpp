#include "mosaico_session_monitor.h"
#include "mosaico_session_model.h"
#include <esp_log.h>
#include <esp_timer.h>
#include <atomic>
#include <mutex>
namespace MosaicoSessions {
namespace {
std::atomic<bool> requested{false}, effective{false};
std::mutex cacheMutex;
Snapshot cached;
bool beginSucceeded = false, initialized = false;
}
void init() {
    if (initialized) return;
    initialized = true;
    { std::lock_guard<std::mutex> guard(cacheMutex); cached.starting = true; }
    GetCodexMicroBle().requestRadioIdle(true);
    beginSucceeded = GetCodexMicroBle().begin();
    { std::lock_guard<std::mutex> guard(cacheMutex);
      cached.starting = beginSucceeded && !GetCodexMicroBle().diagnostics().hidReady;
      cached.failed = !beginSucceeded;
      cached.ready = beginSucceeded && GetCodexMicroBle().diagnostics().hidReady; }
    if (!beginSucceeded) ESP_LOGE("MosaicoSessions", "BLE initialization failed; quota remains available");
}
void setEnabled(bool value) { requested.store(value); if (!value) effective.store(false); }
bool enabled() { return effective.load(); }
void service(bool locked, bool otaBusy) {
    if (!initialized) return;
    const bool hidReady = beginSucceeded && GetCodexMicroBle().diagnostics().hidReady;
    bool failed = !beginSucceeded || GetCodexMicroBle().diagnostics().linkFailed;
    const bool allow = radioAllowed(beginSucceeded && !failed, hidReady, requested.load(), locked, otaBusy);
    effective.store(allow);
    if (beginSucceeded && hidReady) {
        GetCodexMicroBle().requestRadioIdle(!allow);
        if (!failed) GetCodexMicroBle().poll();
    }
    failed = !beginSucceeded || GetCodexMicroBle().diagnostics().linkFailed;
    if (failed) effective.store(false);
    Snapshot next;
    next.state = GetCodexMicroBle().snapshot();
    next.ready = beginSucceeded && hidReady && !failed; next.starting = beginSucceeded && !hidReady && !failed;
    next.failed = failed; next.radioRequestedEnabled = allow && !failed;
    next.nowMs = static_cast<uint32_t>(esp_timer_get_time() / 1000);
    std::lock_guard<std::mutex> guard(cacheMutex); cached = next;
}
bool snapshot(Snapshot& out) {
    std::unique_lock<std::mutex> guard(cacheMutex, std::try_to_lock);
    if (!guard.owns_lock()) return false;
    out = cached; return true;
}
Snapshot snapshot() { std::lock_guard<std::mutex> guard(cacheMutex); return cached; }
}
