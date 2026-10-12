/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>

namespace mosaico_haptic {
struct TimerState {
    bool touched = false, mayRun = false;
    constexpr void configuring() { touched = true; mayRun = true; }
    constexpr void resuming() { mayRun = true; }
    constexpr bool needsPause() const { return touched && mayRun; }
    constexpr void paused(bool success) { if (success) mayRun = false; }
};
// Mutex-owned monotonic deadline, not callback identity: an old queued callback
// must never stop a newer pulse before that newer pulse's deadline.
struct Model {
    int64_t deadlineUs = 0, lastStartUs = 0;
    bool active = false, hasStarted = false;
    static constexpr uint16_t duration(uint16_t ms) { return ms > 50 ? 50 : ms; }
    static constexpr uint8_t strength(uint8_t percent) { return percent > 100 ? 100 : percent; }
    static constexpr uint32_t duty(uint8_t percent) { return uint32_t(strength(percent)) * 1023 / 100; }
    constexpr bool begin(int64_t now, uint16_t ms, uint8_t percent) {
        if (!duration(ms) || !strength(percent)) { stop(); return false; }
        if (hasStarted && (now < lastStartUs || now - lastStartUs < 60000)) return false;
        hasStarted = true; lastStartUs = now;
        deadlineUs = now + int64_t(duration(ms)) * 1000; active = true;
        return true;
    }
    constexpr int64_t remaining(int64_t now) const {
        return active && now < deadlineUs ? deadlineUs - now : 0;
    }
    constexpr void stop() { active = false; deadlineUs = 0; }
};
}
