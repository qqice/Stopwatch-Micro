#pragma once
#include "mosaico_display_settings_model.h"
namespace MosaicoDisplay {
void init();
Snapshot snapshot();
bool snapshot(Snapshot& out); // Contention leaves the caller's cache untouched.
bool request(Config config); // Short RAM-only try-lock, no NVS or HAL calls.
bool requestPersistentField(const char* field, int64_t value); // GUI saves only the selected field from BASE.
bool setTemporary(const char* field, int64_t value, uint32_t leaseSeconds = 180);
bool restoreTemporary();
bool saveTemporary(bool confirmed);
void service(); // Sole network owner; no flash writes during OTA/boot-health work.
bool claimNetworkOwner();
void startFallbackOwner(); // Missing network configuration/setup failure only.
}
