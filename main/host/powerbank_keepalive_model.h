/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>
namespace PowerbankKeepalive {
struct Request {
    uint32_t leaseSeconds=180, periodSeconds=15, durationMs=500;
};
constexpr bool valid(const Request& r) {
    return r.leaseSeconds>=180 && r.leaseSeconds<=1800 &&
        r.periodSeconds>=5 && r.periodSeconds<=20 && r.durationMs>=100 && r.durationMs<=1000 &&
        r.durationMs * 20U <= r.periodSeconds * 1000U; // At most 5% CPU-work duty.
}
// Microsecond monotonic time only. RAM-only finite experiment; never automatic.
struct Model {
    Request config{};
    int64_t leaseUntil=0, nextDue=0;
    uint32_t generation=0;
    constexpr bool enabled(int64_t now) const { return leaseUntil!=0 && now<leaseUntil; }
    constexpr void off() { leaseUntil=0;nextDue=0;++generation; }
    constexpr bool request(const Request& r, int64_t now) {
        if(!valid(r))return false;
        off();config=r;leaseUntil=now+int64_t(r.leaseSeconds)*1000000;
        nextDue=now+int64_t(r.periodSeconds)*1000000;return true;
    }
    constexpr void expire(int64_t now) { if(leaseUntil && now>=leaseUntil)off(); }
    constexpr bool due(int64_t now) const { return enabled(now) && now>=nextDue; }
    // Skip missed/blocked slots; do not accumulate a catch-up pulse burst.
    constexpr bool take(int64_t now, bool safe) {
        expire(now);if(!due(now))return false;
        nextDue=now+int64_t(config.periodSeconds)*1000000;return safe;
    }
    // Schedule from actual completion, not reservation/take time: preemption
    // must never turn a late pulse into an immediate catch-up pulse.
    constexpr void completePulse(int64_t now, uint32_t version) {
        if(!enabled(now) || version!=generation)return;
        const auto cooldown=now+int64_t(config.periodSeconds)*1000000;
        if(nextDue<cooldown)nextDue=cooldown;
    }
    constexpr bool pulseAllowed(int64_t now, uint32_t version, bool safe) const {
        return safe && enabled(now) && version==generation;
    }
};
}
