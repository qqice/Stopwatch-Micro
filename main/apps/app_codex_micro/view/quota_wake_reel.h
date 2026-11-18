/* SPDX-License-Identifier: MIT */
#pragma once
#include "credit_takeover.h"

namespace mosaico_quota_wake {
struct Value {
    char text[mosaico_credit::MaxBalanceDigits + 1]{};
    uint32_t resetEpoch = 0, durationMinutes = 0;
    bool credits = false;
};
struct Reading {
    std::array<Value, 2> windows{};
    uint32_t capturedEpoch = 0;
};
struct Gate {
    uint64_t wakeUs = 0;
    uint32_t revision = 0, epoch = 0;
    bool waiting = false;
    constexpr void begin(uint64_t at, uint32_t rev, uint32_t captured, bool haveReference) {
        wakeUs = at; revision = rev; epoch = captured; waiting = haveReference;
    }
    constexpr bool eligible(bool fresh, uint64_t receivedUs, uint32_t rev, uint32_t captured) const {
        return waiting && fresh && receivedUs > wakeUs && rev != revision && captured > epoch;
    }
    constexpr void cancel() { waiting = false; }
};
constexpr bool sameWindow(const Value& a, const Value& b) {
    return a.resetEpoch && a.resetEpoch == b.resetEpoch &&
           a.durationMinutes && a.durationMinutes == b.durationMinutes;
}
constexpr Reading visibleReference(const Reading& old, const Reading& next) {
    if (old.capturedEpoch && old.capturedEpoch == next.capturedEpoch) {
        Reading stable = next;
        for (unsigned j = 0; j < stable.windows.size(); ++j) {
            const auto& previous = old.windows[j];
            auto& value = stable.windows[j];
            if (!sameWindow(previous, value) || previous.credits != value.credits ||
                !mosaico_credit::identical(previous.text, value.text))
                value = {};
        }
        return stable;
    }
    return next;
}
// A pre-lock reference was fresh when shown. It remains historical evidence
// throughout the same cycle, but can never match after expiry or unit changes.
constexpr const Value* changed(const Reading& old, const Value& next, uint64_t epoch) {
    if (!next.text[0] || !old.capturedEpoch || epoch < old.capturedEpoch || epoch >= next.resetEpoch)
        return nullptr;
    const Value* match = nullptr;
    for (const auto& v : old.windows) {
        if (!sameWindow(v, next)) continue;
        if (match) return nullptr;
        match = &v;
    }
    return match && match->text[0] && match->credits == next.credits &&
           !mosaico_credit::identical(match->text, next.text) ? match : nullptr;
}
} // namespace mosaico_quota_wake
