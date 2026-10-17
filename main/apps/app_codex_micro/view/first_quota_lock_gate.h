/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>

namespace mosaico_quota_display {
// GUI-owned, one-shot startup gate. Activity/settings never restart its deadline.
// Only automatic locking consults it; manual/diagnostic locks retain their policy.
struct FirstQuotaLockGate {
    static constexpr uint32_t TimeoutMs = 90000;
    const uint32_t startedMs;
    bool rendered = false, timedOut = false;
    constexpr explicit FirstQuotaLockGate(uint32_t now) : startedMs(now) {}
    constexpr void quotaRendered(bool known) { rendered = rendered || known; }
    constexpr bool allowsAutoLock(uint32_t now) {
        timedOut = timedOut || static_cast<uint32_t>(now - startedMs) >= TimeoutMs;
        return rendered || timedOut;
    }
};
} // namespace mosaico_quota_display
