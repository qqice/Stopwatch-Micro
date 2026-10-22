#pragma once
#include "mosaico_factory_scale_storage_model.h"
#include "mosaico_factory_scale_nvs_budget_model.h"

namespace mosaico_factory_scale_journal {
inline constexpr const char* nvsNamespace="gscale_f3_v1";
inline constexpr const char* nvsSlotKeys[2]={"recover0","recover1"};
// Conservative initial available-entry admission: three 512-byte blobs (19 entries each)
// plus page/GC margin 16 and namespace 1. This is NOT a reservation; other NVS
// writers can consume space. Only initial creation requires this headroom;
// transitions must remain possible after mutation consumes initial headroom.
// Every NVS error still fails closed; never erase.
inline constexpr size_t nvsRequiredAvailableEntries=3*19+16+1;
#ifdef ESP_PLATFORM
Status loadNvs(const std::array<uint8_t,6>& expectedMac,StorageWorkspace& work);
// RO evidence only. Caller freshly decoded both slots and owns their exclusive
// transaction. Any iterator/size/stats error returns zero-credit evidence.
mosaico_factory_scale_budget::Usage reuseUsageNvs(const Loaded& loaded);
// Caller keeps transaction, OTA lease and stable-voltage protection for the
// ENTIRE call. No locks, voltage checks, or hardware writes are implemented here.
// next must not alias work.scratch or either loaded record. On success loaded
// is freshly reloaded; only Ready plus selected>=0 is a usable current record.
Status saveNvs(const Record& next,const WriteAdmission& admission,StorageWorkspace& work);
#endif
} // namespace mosaico_factory_scale_journal
