#pragma once
#include "mosaico_gauge_scale_journal_model.h"

namespace mosaico_gauge_scale_journal {
// Dedicated namespace, two independent atomic NVS blobs. No legacy key/schema
// migration, erase, nvs_flash_init, gauge, network or OTA operations.
inline constexpr const char* nvsNamespace="gscale_v1";
inline constexpr const char* nvsSlotKeys[2]={"recover0","recover1"};
// Workspace is 5984 bytes on the current RISC-V ABI (use sizeof on other ABIs).
// Allocate it once in caller-owned PSRAM and exclusively own it for each call.
// next can be work.scratch; never use work.loaded.records as next. save does
// not refresh work.loaded after success: reload before selecting current state.
// Missing is not evidence of a scale-1 gauge. No failed status permits telemetry.
#ifdef ESP_PLATFORM
Status loadNvs(const std::array<uint8_t,6>& expectedMac,Workspace& work);
// Caller holds gauge transaction + OTA lease and stable-voltage admission for
// the ENTIRE call, including namespace opening/commit/readback. The component
// does not acquire/verify these protections. CRC is not authentication.
Status saveNvs(const Record& next,const WriteAdmission& admission,Workspace& work);
#endif
} // namespace mosaico_gauge_scale_journal

