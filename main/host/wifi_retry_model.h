/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>
namespace MosaicoWifiRetry {
// Sole network-owner state. Notifications must not restart the retry deadline.
struct Policy {
    static constexpr uint32_t LockedAttempts = 3;
    uint32_t attempts = 0;
    int64_t nextUs = 0;
    constexpr void reset() { attempts = 0; nextUs = 0; }
    constexpr bool exhausted(bool locked) const { return locked && attempts >= LockedAttempts; }
    constexpr uint32_t remainingMs(int64_t now) const {
        return nextUs > now ? static_cast<uint32_t>((nextUs - now + 999) / 1000) : 0;
    }
    constexpr void attempted(int64_t now, bool locked) {
        if (attempts < 3) ++attempts;
        const uint32_t delay = locked || attempts == 1 ? 5000 : attempts == 2 ? 15000 : 60000;
        nextUs = now + int64_t(delay) * 1000;
    }
};
}
