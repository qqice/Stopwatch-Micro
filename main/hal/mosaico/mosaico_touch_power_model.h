/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>

namespace mosaico_touch_power {
constexpr uint32_t AwakePeriodMs = 10;
constexpr uint32_t IdlePeriodMs = 50;
constexpr uint32_t LvglTickPeriodMs = 10;
constexpr uint32_t pollingPeriodMs(bool idle) { return idle ? IdlePeriodMs : AwakePeriodMs; }
constexpr bool unusedGate(unsigned pin) { return pin == 56 || pin == 45 || pin == 8; }
constexpr uint64_t UnusedGateMask = (1ULL << 56) | (1ULL << 45) | (1ULL << 8);
static_assert(pollingPeriodMs(false) == 10 && pollingPeriodMs(true) == 50, "Touch polling policy changed");
static_assert(LvglTickPeriodMs <= AwakePeriodMs, "Tick cannot be slower than awake polling");
static_assert(unusedGate(56) && unusedGate(45) && unusedGate(8) && !unusedGate(57) && !unusedGate(60) &&
              !unusedGate(3) && !unusedGate(37), "Unused gate whitelist changed");
} // namespace mosaico_touch_power
