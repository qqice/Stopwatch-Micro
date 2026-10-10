#pragma once
#include <cstddef>
#include <cstdint>
namespace MosaicoOta {
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
void processLocalRequests(); // Owner only, before radio/network readiness gates.
bool approveUpdate(const char* expectedSha); // DOWNLOAD only; must match the hash displayed in the caller snapshot.
void deferUpdate();
bool setAutomaticInstall(bool enabled);
bool automaticInstall();
bool discoverManifest(const char* json); // Network owner: signature verification and read-only discovery.
bool finishDownload();
bool installVerified(); // Network owner only, explicit consent or full-pipeline mode; manual flow does not reboot.
bool busy();
bool blocksTwt(); // Atomic control state, independent of UI snapshot lock contention.
bool healthPending();
uint32_t requestAgeMs();
bool request(); // Caller must obtain explicit confirmation of USB/external power.
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
