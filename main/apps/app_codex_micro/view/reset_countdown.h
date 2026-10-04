/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>
namespace mosaico_time {
struct Countdown { bool known; uint64_t minutes; };
constexpr Countdown countdown(bool available, uint32_t capturedEpoch, uint32_t resetEpoch, uint64_t nowEpoch) {
    if (!available || !capturedEpoch || !resetEpoch) return {false, 0};
    const uint64_t seconds = resetEpoch > nowEpoch ? resetEpoch - nowEpoch : 0;
    return {true, (seconds + 59) / 60};
}
static_assert(!countdown(false, 100, 200, 100).known && !countdown(true, 0, 200, 100).known);
static_assert(countdown(true, 100, 160, 100).minutes == 1 && countdown(true, 100, 161, 100).minutes == 2);
static_assert(countdown(true, 100, 100, 101).known && countdown(true, 100, 100, 101).minutes == 0);
static_assert(countdown(true, 100, 86500, 100).minutes == 1440);
} // namespace mosaico_time
