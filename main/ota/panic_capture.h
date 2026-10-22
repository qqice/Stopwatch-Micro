#pragma once
#include <sdkconfig.h>
#include <cstddef>
#include <cstdint>
#if defined(MOSAICO_BOARD) && CONFIG_IDF_TARGET_ESP32S31 && CONFIG_IDF_TARGET_ARCH_RISCV
// Normal context only; the RTC record survives queries and is replaced on panic.
void MosaicoPanicReport();
bool MosaicoPanicStatus(char* out, size_t length);
bool MosaicoPanicNumeric(uint32_t* words, size_t count); // Closed 11-word RAM copy.
// Closed caller identifiers. Zero means no active project integrity probe.
enum class MosaicoHeapProbeStage : uint32_t {
    ScanBefore = 1, ScanAfter = 2, TailnetBefore = 3, TailnetAfter = 4,
    DownloadBefore = 5, DownloadChunkBoundary = 6, DownloadComplete = 7, OtaHealth = 8
};
struct MosaicoHeapProbeToken { uint32_t core, value; };
MosaicoHeapProbeToken MosaicoHeapProbeEnter(MosaicoHeapProbeStage stage);
void MosaicoHeapProbeExit(MosaicoHeapProbeToken token);
// Normal context only. Saves entry core so exit never clears a different core.
// Per-core values identify entry-core observed scopes, not necessarily panic-core
// callers: an unpinned task may migrate. A newer token cannot be cleared by an
// older exit. Nested/preempted scopes can lose evidence; no scheduler is changed.
class MosaicoHeapProbeScope {
public:
    explicit MosaicoHeapProbeScope(MosaicoHeapProbeStage stage) : token(MosaicoHeapProbeEnter(stage)) {}
    ~MosaicoHeapProbeScope() { MosaicoHeapProbeExit(token); }
    MosaicoHeapProbeScope(const MosaicoHeapProbeScope&) = delete;
    MosaicoHeapProbeScope& operator=(const MosaicoHeapProbeScope&) = delete;
private:
    MosaicoHeapProbeToken token;
};
// Numeric phase/offset only, without content or credentials.
void MosaicoOtaBreadcrumb(uint32_t phase, uint32_t offset);
#endif
