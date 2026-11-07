#pragma once
#include <cstdint>

namespace mosaico_dot {
// Sampling only: animation phase remains derived from the wall-clock epoch.
// Retain sub-frame remainder instead of adding the UI polling delay each frame.
class AnimationCadence {
public:
    static constexpr uint32_t PeriodMs = 50;
    constexpr bool sample(uint32_t tick, bool eligible) {
        if (!eligible) { _armed = false; return false; }
        if (!_armed) { _armed = true; _deadlineBase = tick; return true; }
        const uint32_t elapsed = tick - _deadlineBase;
        if (elapsed < PeriodMs) return false;
        // At most one sample, even after a stall. Skip missed deadlines without
        // a loop; the next deadline is in the future, never an overdue backlog.
        _deadlineBase += elapsed / PeriodMs * PeriodMs;
        return true;
    }
private:
    uint32_t _deadlineBase = 0;
    bool _armed = false;
};
}
