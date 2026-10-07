#pragma once
#include <hal/ble/codex_micro_ble.h>
namespace MosaicoSessions {
struct Snapshot {
    CodexMicroState state{};
    bool ready = false, starting = false, failed = false, radioRequestedEnabled = false;
    uint32_t nowMs = 0;
};
void init(); // Synchronous HAL-after/network-before startup only.
void setEnabled(bool enabled); // GUI callback: RAM-only lease request.
bool enabled(); // Effective lease; no radio or HAL calls.
void service(bool locked, bool otaBusy); // Main owner, outside LVGL.
bool snapshot(Snapshot& out); // Cached copy; contention preserves caller cache.
Snapshot snapshot(); // Read-only diagnostics.
}
