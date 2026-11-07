#pragma once
#include <ota/mosaico_ota.h>

// GUI presentation dependencies only: transfer counters never change layout.
namespace mosaico_view_update {
constexpr bool sameText(const char* a, const char* b) {
    while (*a && *a == *b) { ++a; ++b; }
    return *a == *b;
}
constexpr bool sameOtaStatic(const MosaicoOta::UiSnapshot& a, const MosaicoOta::UiSnapshot& b) {
    return a.stage == b.stage && a.workPhase == b.workPhase &&
        a.currentSlot == b.currentSlot && a.targetSlot == b.targetSlot &&
        a.signatureVerified == b.signatureVerified && a.imageVerified == b.imageVerified &&
        a.deltaTransfer == b.deltaTransfer && a.modeKnown == b.modeKnown &&
        sameText(a.currentVersion,b.currentVersion) && sameText(a.targetVersion,b.targetVersion) &&
        sameText(a.sha256,b.sha256) && sameText(a.signatureShort,b.signatureShort) &&
        sameText(a.error,b.error);
}
constexpr bool quotaBodyCovered(bool settingsOpen, bool settingsAnimating) {
    return settingsOpen && !settingsAnimating;
}
struct TrendKey {
    uint32_t epoch=0, revision=0, style=0;
    constexpr bool operator==(const TrendKey& b) const { return epoch==b.epoch && revision==b.revision && style==b.style; }
};
}
