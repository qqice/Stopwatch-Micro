/* SPDX-License-Identifier: MIT */
#pragma once
#include <host/quota_monitor.h>

namespace mosaico_quota_display {
// Display validity is independent of the backend's ten-minute availability TTL.
constexpr bool hasSnapshot(const QuotaMonitorSnapshot& snapshot, bool knownRevision) {
    return knownRevision && snapshot.bucketCount > 0 && snapshot.capturedEpoch > 0;
}
// GUI-owned age, advanced on every refresh. Backend copy age is modulo 32-bit
// uptime; repeated copies of the same revision must never reset display time.
struct DisplayAge {
    uint64_t ageMs = 0;
    uint32_t previousNowMs = 0, revision = 0;
    bool initialized = false;
    constexpr uint64_t secondsAt(uint32_t nowMs) const {
        return (ageMs + (initialized ? static_cast<uint32_t>(nowMs - previousNowMs) : 0U)) / 1000U;
    }
    constexpr void refresh(QuotaMonitorSnapshot& snapshot, bool copied, bool known, uint32_t nowMs) {
        if (copied && (!initialized || revision != snapshot.revision)) {
            ageMs = static_cast<uint64_t>(snapshot.ageSecondsAtReceipt) * 1000U +
                    static_cast<uint32_t>(nowMs - snapshot.receivedAtMs);
            revision = snapshot.revision;
            initialized = true;
        } else if (initialized) {
            ageMs += static_cast<uint32_t>(nowMs - previousNowMs);
        }
        previousNowMs = nowMs;
        if (!initialized || (!copied && !known)) return;
        const uint64_t age = ageMs / 1000U;
        snapshot.ageSeconds = static_cast<uint32_t>(age > UINT32_MAX ? UINT32_MAX : age);
        snapshot.stale = age > 130;
        snapshot.available = age <= 600;
    }
};
// Never present a previous window's percentage as current after its deadline.
// An unknown deadline does not invalidate a validated percentage.
constexpr bool percentKnown(const QuotaMonitorWindow& window, uint64_t nowEpoch) {
    return window.available && (!window.resetEpoch || nowEpoch < window.resetEpoch);
}
constexpr size_t primaryWindow(const QuotaMonitorBucket& bucket, uint64_t nowEpoch) {
    return percentKnown(bucket.windows[0], nowEpoch) ? 0 : 1;
}
} // namespace mosaico_quota_display
