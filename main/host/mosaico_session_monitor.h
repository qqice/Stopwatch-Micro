#pragma once
#include <hal/ble/codex_micro_ble.h>
namespace MosaicoSessions {
struct Snapshot {
    CodexMicroState state{};
    bool ready = false, starting = false, failed = false, radioRequestedEnabled = false;
    uint32_t nowMs = 0;
    CodexMicroState lockedState{};
    bool lockedCacheValid = false;
    uint32_t lockedCapturedMs = 0;
    bool lockedRefreshing = false;
    uint32_t lockedRefreshRevision = 0;
    uint8_t freshnessKnownMask = 0;
    bool lockedRefreshTimedOut = false;
    int32_t lockedRefreshError = 0;
    uint32_t lockedWindowStartedMs = 0, lockedWindowEndedMs = 0, lockedWindowCount = 0;
};
void init(); // Synchronous HAL-after/network-before startup only.
void setEnabled(bool enabled); // GUI callback: RAM-only lease request.
void requestLockedRefresh(); // RAM-only coalesced minute request; main enforces duty bounds.
bool enabled(); // Effective lease; no radio or HAL calls.
void service(bool locked, bool otaBusy); // Main owner, outside LVGL.
bool snapshot(Snapshot& out); // Cached copy; contention preserves caller cache.
Snapshot snapshot(); // Read-only diagnostics.
}
