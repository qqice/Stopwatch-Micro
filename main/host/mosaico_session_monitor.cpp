#include "mosaico_session_monitor.h"
#include "mosaico_session_model.h"
#include <esp_log.h>
#include <esp_err.h>
#include <esp_timer.h>
#include <atomic>
#include <mutex>
namespace MosaicoSessions {
namespace {
std::atomic<bool> requested{false}, effective{false};
std::atomic<bool> lockedRefreshRequested{false};
std::atomic<bool> lockedRefreshActive{false};
std::mutex cacheMutex;
Snapshot cached;
bool beginSucceeded = false, initialized = false;
LockedWindow window;
CodexMicroState lockedCache;
bool lockedCacheValid = false, wasLocked = false;
uint32_t lockedCapturedMs = 0;
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
void setEnabled(bool value) {
    requested.store(value);
    if (!value && !lockedRefreshActive.load()) effective.store(false);
}
void requestLockedRefresh() { lockedRefreshRequested.store(true); }
bool enabled() { return effective.load(); }
void service(bool locked, bool otaBusy) {
    if (!initialized) return;
    const uint32_t now = static_cast<uint32_t>(esp_timer_get_time() / 1000);
    const auto before = GetCodexMicroBle().snapshot();
    const bool hidReady = beginSucceeded && GetCodexMicroBle().diagnostics().hidReady;
    bool failed = !beginSucceeded || GetCodexMicroBle().diagnostics().linkFailed;
    const bool asked = lockedRefreshRequested.exchange(false);
    if (!locked || otaBusy || failed) {
        lockedRefreshRequested.store(false);
        window.finish(now, false, failed ? ESP_FAIL : 0);
    }
    if (locked && !wasLocked && !failed && before.connected && before.protocolReady && before.knownMask) {
        lockedCache = before; lockedCacheValid = true;
        lockedCapturedMs = before.lastThreadStatusReceiptMs;
        window.freshMask = 0; // A seed is cached evidence, not a successful refresh.
    }
    if (asked && locked && !otaBusy && !failed && hidReady) {
        const bool wasActive = window.active;
        if (!window.start(now, before.connectionGeneration, before.threadStatusCompleteReceiptSequence) && !wasActive)
            lockedRefreshRequested.store(true); // Preserve cooldown intent across owner scheduling jitter.
    }
    wasLocked = locked;
    const auto collect = [&](const CodexMicroState& state) {
        // Replace a new live generation, but retain evidence across radio-down.
        if (state.connected && state.connectionGeneration != lockedCache.connectionGeneration) {
            lockedCache = state; lockedCacheValid = false; lockedCapturedMs = 0;
        }
        window.observe(state.connectionGeneration, state.knownMask,
            state.threadStatusCompleteReceiptSequence, state.threadStatusCompleteReceiptMs,
            state.connected, state.protocolReady && state.lastThreadStatusReceiptMs - window.startedMs < LockedWindow::DurationMs);
        if (window.freshMask && state.connected && state.protocolReady &&
            state.lastThreadStatusReceiptMs - window.startedMs < LockedWindow::DurationMs) {
            lockedCache = state;
            lockedCache.knownMask = window.freshMask;
            lockedCacheValid = true;
            uint32_t elapsed = 0;
            for (unsigned slot = 0; slot < 6; ++slot)
                if (window.freshMask & (1U << slot)) {
                    const uint32_t age = state.threadStatusCompleteReceiptMs[slot] - window.startedMs;
                    if (age < LockedWindow::DurationMs && age >= elapsed) {
                        elapsed = age; lockedCapturedMs = state.threadStatusCompleteReceiptMs[slot];
                    }
                }
        }
    };
    if (locked && window.active && !failed) {
        // A pre-deadline receipt is still valid if this cooperative owner was
        // scheduled just after the deadline; never admit a post-deadline frame.
        collect(before);
        if (window.freshMask == 0x3f) window.finish(now);
        else if (window.expired(now)) window.finish(now, true, ESP_ERR_TIMEOUT);
    }
    const bool allow = radioAllowed(beginSucceeded && !failed, hidReady,
        requested.load() || window.active, locked && !window.active, otaBusy);
    effective.store(allow);
    if (beginSucceeded && hidReady) {
        GetCodexMicroBle().requestRadioIdle(!allow);
        if (!failed) GetCodexMicroBle().poll();
    }
    failed = !beginSucceeded || GetCodexMicroBle().diagnostics().linkFailed;
    if (failed) effective.store(false);
    Snapshot next;
    next.state = GetCodexMicroBle().snapshot();
    if (locked && window.active && !failed) {
        collect(next.state);
        const uint32_t closingMs = static_cast<uint32_t>(esp_timer_get_time() / 1000);
        if (window.freshMask == 0x3f) window.finish(closingMs);
        else if (window.expired(closingMs)) window.finish(closingMs, true, ESP_ERR_TIMEOUT);
    }
    if (failed) window.finish(now, false, ESP_FAIL);
    const bool finalAllow = allow && !failed && (!locked || window.active);
    lockedRefreshActive.store(window.active);
    effective.store(finalAllow);
    if (!finalAllow && beginSucceeded && hidReady) GetCodexMicroBle().requestRadioIdle(true);
    next.ready = beginSucceeded && hidReady && !failed; next.starting = beginSucceeded && !hidReady && !failed;
    next.failed = failed; next.radioRequestedEnabled = finalAllow;
    next.nowMs = static_cast<uint32_t>(esp_timer_get_time() / 1000);
    next.lockedState = lockedCache; next.lockedCacheValid = lockedCacheValid; next.lockedCapturedMs = lockedCapturedMs;
    next.lockedRefreshing = window.active; next.lockedRefreshRevision = window.revision;
    next.freshnessKnownMask = window.freshMask; next.lockedRefreshTimedOut = window.timedOut;
    next.lockedRefreshError = window.error; next.lockedWindowStartedMs = window.startedMs;
    next.lockedWindowEndedMs = window.endedMs; next.lockedWindowCount = window.count;
    std::lock_guard<std::mutex> guard(cacheMutex); cached = next;
}
bool snapshot(Snapshot& out) {
    std::unique_lock<std::mutex> guard(cacheMutex, std::try_to_lock);
    if (!guard.owns_lock()) return false;
    out = cached; return true;
}
Snapshot snapshot() { std::lock_guard<std::mutex> guard(cacheMutex); return cached; }
}
