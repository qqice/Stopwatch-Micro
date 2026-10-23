#pragma once
#include "mosaico_gauge_cold_trial_model.h"
#include "mosaico_gauge_cold_evidence_model.h"
#include "mosaico_gauge_recovery_model.h"
#include <mutex>

// One-shot supervisor integrated with the existing closed GCT transaction.
// No raw access keys, arbitrary commands or DM addresses are exposed here.
namespace MosaicoGaugeCold {
struct RestoreRequest {bool requested=false;uint32_t sequence=0;mosaico_gauge_cold_trial::Nonce boot{};};
struct QuietHooks {
    // Root only locks the display/checks an existing host overlay. It does NOT
    // change the9 settings: persisted GCT Pending/Applied already holds radios.
    // enter must not invent phase2; release still checks the actual quota/SDK.
    bool (*enter)()=nullptr;
    bool (*leave)()=nullptr;
};
void setQuietHooks(QuietHooks hooks);
void earlyPrefetch(const mosaico_gauge_status::Snapshot&,const mosaico_gauge_engine::Snapshot&,uint32_t opaqueError=0);
bool startup(); // after independent NAND DMA reservation, before RF owners
bool poll(RestoreRequest* request=nullptr); // true: cold owns/holds recovery
// On clean Original, historical signed metadata is read-only; no actual-slot
// attestation or current-session generation/nonce authority is implied.
bool status(char*,size_t);
bool evidencePage(size_t index,char*,size_t); // Cached SHA-checked192B slices only
bool bindFragment(const char* base64Fragment,char*,size_t);
bool apply(char*,size_t);
bool handoff(char*,size_t);
bool recover(char*,size_t);
bool cancel(char*,size_t);
// Caller holds OTA lease -> gauge transaction -> battery unique_lock. NAND
// commits temporarily unlock ONLY battery, re-lock on every return path.
bool armSeam(std::unique_lock<std::mutex>& battery);
bool beginRestore(std::unique_lock<std::mutex>& battery);
bool beforeRestoreKeys(std::unique_lock<std::mutex>& battery);
bool isOwnedAbort();
bool restoreSeam(std::unique_lock<std::mutex>& battery);
// Invoke outside battery_mutex, after the existing coordinator has returned.
void onApplied(bool success,bool knownNoDirty=false);
bool onRestored(const mosaico_gauge_recovery::Snapshot&,
                const mosaico_gauge_recovery::Observation&,bool freshSealedStandard);
bool fallbackAllowed(); // unknown cold outcomes prohibit old automatic keys
bool keyWindowAllowed(); // RAM-only owner + forward600/restore720/newboot absolute120 gate
bool blocksAutomaticBankPulse(); // cold-only transient hold, no setting writes
void onRestoreFailed(); // root must call for any failed dispatched attempt
bool protectedState(); // atomic, no I2C/storage/locks
bool ownsOperation(); // exact current task, for root's narrow owner exceptions
}
