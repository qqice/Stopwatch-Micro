#pragma once
#include <cstdint>

namespace mosaico_ota_reel {
enum class Action : uint8_t { Keep, Snap, Roll };
constexpr uint16_t RollMs = 240;

// While rolling, coalesce into the latest real sample without retargeting the
// draw in progress. Snap to the newest sample when the short roll finishes.
struct Progress {
    uint32_t started = 0, done = 0, total = 0;
    uint16_t latest = 0;
    bool seen = false, active = false;

    constexpr Action observe(uint32_t tick, uint16_t bp, uint32_t received,
                             uint32_t size, bool visible, bool samePhase) {
        const bool rebase = !seen || !visible || !samePhase || total != size ||
                            received < done || bp < latest || bp >= 10000;
        const bool more = bp > latest;
        latest = bp;
        done = received;
        total = size;
        seen = visible;
        if (rebase || (active && finished(tick))) {
            active = false;
            return Action::Snap;
        }
        if (active || !more) return Action::Keep;
        started = tick;
        active = true;
        return Action::Roll;
    }

    constexpr bool finished(uint32_t tick) const { return tick - started >= RollMs; }
    constexpr uint16_t offset(uint32_t tick, uint16_t duration) const {
        const uint32_t elapsed = tick - started;
        return elapsed >= RollMs ? duration : static_cast<uint16_t>(elapsed * duration / RollMs);
    }
};
} // namespace mosaico_ota_reel
