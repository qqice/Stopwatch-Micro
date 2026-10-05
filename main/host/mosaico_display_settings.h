#pragma once
#include "mosaico_display_settings_model.h"
namespace MosaicoDisplay {
void init();
Snapshot snapshot();
bool snapshot(Snapshot& out); // Contention leaves the caller's cache untouched.
bool request(Config config); // Short RAM-only try-lock, no NVS or HAL calls.
void service(); // Sole network owner; no flash writes during OTA/boot-health work.
bool claimNetworkOwner();
void startFallbackOwner(); // Missing network configuration/setup failure only.
}
