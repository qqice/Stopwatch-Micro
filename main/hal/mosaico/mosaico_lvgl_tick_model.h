/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>
namespace mosaico_lvgl_tick {
constexpr uint32_t MonotonicTimerPeriodMs=1000;
constexpr uint32_t milliseconds(int64_t microseconds) {
    return static_cast<uint32_t>(microseconds/1000);
}
constexpr uint32_t startupOffset(uint32_t priorTick, uint32_t monotonicMs) {
    return priorTick-monotonicMs;
}
constexpr uint32_t tick(uint32_t monotonicMs, uint32_t offset) {
    return monotonicMs+offset; // LVGL uses modulo-2^32 elapsed arithmetic.
}
}
