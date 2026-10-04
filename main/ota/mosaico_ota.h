#pragma once
#include <cstddef>
#include <cstdint>
namespace MosaicoOta {
enum class UiStage : uint8_t { Idle, Available, WaitingPower, Downloading, Verifying, ReadyInstall, Installing, BootChecking, Complete, Failed };
struct UiSnapshot {
    uint32_t revision, size, received;
    UiStage stage;
    char currentVersion[32], targetVersion[32], sha256[65], signatureShort[17], error[48];
    int8_t currentSlot, targetSlot;
    bool signatureVerified, imageVerified, automaticInstall;
    uint16_t progressBasisPoints;
};
bool copyUiSnapshot(UiSnapshot& out);
bool approveUpdate(const char* expectedSha); // Must match the hash displayed in the caller snapshot.
void deferUpdate();
bool setAutomaticInstall(bool enabled);
bool automaticInstall();
bool discoverManifest(const char* json); // Network owner: signature verification and read-only discovery.
bool finishDownload();
bool installVerified(); // Network owner only, after >=1500 ms ReadyInstall display window.
bool busy();
bool healthPending();
uint32_t requestAgeMs();
bool request(); // Caller must obtain explicit confirmation of USB/external power.
bool automaticCheckDue(); // Network owner only; never wakes radio or bypasses the VALID/power gate.
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
