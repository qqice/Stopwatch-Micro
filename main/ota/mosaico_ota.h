#pragma once
#include <cstddef>
#include <cstdint>
namespace MosaicoOta {
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
