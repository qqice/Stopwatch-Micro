/* SPDX-License-Identifier: MIT */
#pragma once
#include <host/quota_monitor.h>

namespace mosaico_quota_display {
// Display validity is independent of the backend's ten-minute availability TTL.
constexpr bool hasSnapshot(const QuotaMonitorSnapshot& snapshot, bool knownRevision) {
    return knownRevision && snapshot.bucketCount > 0 && snapshot.capturedEpoch > 0;
}
constexpr void ageCache(QuotaMonitorSnapshot& snapshot, uint32_t nowMs) {
    const uint64_t age = static_cast<uint64_t>(snapshot.ageSecondsAtReceipt) +
                         (nowMs - snapshot.receivedAtMs) / 1000U;
    snapshot.ageSeconds = static_cast<uint32_t>(age > UINT32_MAX ? UINT32_MAX : age);
    snapshot.stale = age > 130;
    snapshot.available = age <= 600;
}
// Never present a previous window's percentage as current after its deadline.
// An unknown deadline does not invalidate a validated percentage.
constexpr bool percentKnown(const QuotaMonitorWindow& window, uint64_t nowEpoch) {
    return window.available && (!window.resetEpoch || nowEpoch < window.resetEpoch);
}
constexpr size_t primaryWindow(const QuotaMonitorBucket& bucket, uint64_t nowEpoch) {
    return percentKnown(bucket.windows[0], nowEpoch) ? 0 : 1;
}
} // namespace mosaico_quota_display
