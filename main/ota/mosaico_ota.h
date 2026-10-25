#pragma once
#include <cstddef>
#include <cstdint>
namespace MosaicoOta {
struct VerifiedImageDescriptor {uint32_t size;uint8_t hash[32];char version[32];};
// Signature/layout verification ONLY. No discovery, requests or OTA-state writes.
bool verifySignedImageDescriptor(const char* json,VerifiedImageDescriptor& out);
enum class UiStage : uint8_t { Idle, Checking, Available, WaitingPower, Downloading, Verifying, ReadyInstall, Installing, ReadyReboot, BootChecking, Complete, Failed };
struct UiSnapshot {
    uint32_t revision, size, received;
    UiStage stage;
    char currentVersion[32], targetVersion[32], sha256[65], signatureShort[17], error[48];
    int8_t currentSlot, targetSlot;
    bool signatureVerified, imageVerified, automaticInstall;
    uint16_t progressBasisPoints;
};
bool copyUiSnapshot(UiSnapshot& out);
bool requestCheck(); // Queue discovery; callbacks perform no network or telemetry work.
bool takeCheckRequest(); // Network owner only.
void finishCheck(bool transportOk);
bool approveInstall(const char* expectedSha);
bool requestReboot();
// LOCAL UART/USB CONFIRM only: caller attests stable USB-only external 5V.
// tud_mounted is connection evidence, NOT a physical 5V measurement.
// USB polling cannot guarantee detection of physical power loss.
// Maintenance OTA does not sample or gate on gauge/history/calibration.
// SHA is immutable for this RAM-only transaction. The legacy 30..600 TTL argument
// remains accepted but does not expire a live, continuously connected USB transaction.
bool requestMaintenanceUpdate(const char* expectedSha, uint32_t ttlSeconds);
bool requestMaintenanceReboot(const char* expectedSha, uint32_t ttlSeconds);
// Host attests SOLE stable external 5V (USB or separately verified V1).
// Not electrical detection. Synchronous lease covers reply, flush and restart.
// Never resend on unknown ACK. Historical Failed/Critical remain unchanged.
bool maintenanceRuntimeRestart(uint32_t expectedFailedCrc, void (*acceptedReply)());
bool maintenanceStatus(char* out, size_t length);
void processLocalRequests(); // Owner only, before radio/network readiness gates.
bool approveUpdate(const char* expectedSha); // DOWNLOAD only; must match the hash displayed in the caller snapshot.
void deferUpdate();
bool setAutomaticInstall(bool enabled);
bool automaticInstall();
bool discoverManifest(const char* json); // Network owner: signature verification and read-only discovery.
bool finishDownload();
bool installVerified(); // Network owner only, explicit consent or full-pipeline mode; manual flow does not reboot.
uint32_t imageCapacity(); // Exact current-layout cap; unknown layout returns zero.
bool busy();
bool blocksTwt(); // Atomic control state, independent of UI snapshot lock contention.
bool healthPending();
// RAM-only coherent copy. evaluated/passed bits: app=1,gap=2,HAL=4,frame=8,
// internal=16,PSRAM=32,integrity=64. Low two bits are current poll observations;
// other bits/values retain the last real 1Hz probe, timestamped by probeUs.
// Unevaluated probe values are zero, not a fresh failure. sdkState is the last
// actual startup SDK read, never synthetic VALID; markSucceeded reports only
// the original SDK mark-VALID call result.
struct HealthDiagnostic {
    uint32_t version=1, polls=0, evaluated=0, passed=0, halMask=0;
    uint32_t frames=0, internalFree=0, psramFree=0, healthyLoops=0, markAttempts=0;
    int64_t sampledUs=0, probeUs=0, loopGapUs=0, maxLoopGapUs=0, healthySinceUs=0;
    int32_t sdkState=-1, sdkError=0, markError=0;
    int8_t sdkSlot=-1;
    bool sdkRead=false, pending=false, appReady=false, markSucceeded=false, timedOut=false;
};
void copyHealthDiagnostic(HealthDiagnostic& out);
// Same-task, nonblocking lease; successful caller must release after NAND I/O.
bool tryStorageLease();
// Network-owner radio start only: same mutex/owner/depth, permits queued OTA.
// Successful caller releases with releaseStorageLease; not gauge/NAND admission.
bool tryNetworkStartLease();
void releaseStorageLease();
// Atomic RAM-only task identity: true only for a successful outstanding
// lease by this exact task, including recursive nesting.
bool currentTaskOwnsStorageLease();
uint32_t requestAgeMs();
bool request(); // Existing local bypass: explicit external-power confirmation AND active connected USB.
bool automaticCheckDue(); // Always false: permanent manual-only discovery policy.
bool requestAutomatic(const char* manifest);
bool takeRequest(); // Only the network owner consumes requests and writes images.
bool beginManifest(const char* json);
bool writeChunk(uint32_t offset, const uint8_t* data, size_t length);
bool finish();
void fail(const char* reason);
void status(char* out, size_t length);
void healthPoll(bool appLoopReady);
bool rollbackTest(); // Explicit diagnostic command only; no persistent test flag.
}
