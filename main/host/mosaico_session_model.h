#pragma once
#include <array>
#include <cstdint>
namespace MosaicoSessions {
constexpr bool integerInRange(double value, uint32_t maximum) {
    return value >= 0 && value <= maximum && value == static_cast<double>(static_cast<uint32_t>(value));
}
constexpr bool validStatus(double id, double color, double brightness, double effect) {
    return integerInRange(id, 5) && integerInRange(color, 0xffffff) &&
        brightness >= 0 && brightness <= 1 && integerInRange(effect, 6);
}
constexpr bool radioAllowed(bool initialized, bool hidReady, bool requested, bool locked, bool otaBusy) {
    return initialized && hidReady && requested && !locked && !otaBusy;
}
template<class State> constexpr void clearKnown(State& state, uint32_t generation) {
    state.knownMask = 0; state.lastThreadStatusMs = {}; state.connectionGeneration = generation;
    state.threadStatusReceiptSequence = 0; state.threadStatusCompleteReceiptSequence = {};
    state.threadStatusCompleteReceiptMs = {};
    state.lastThreadStatusReceiptMs = 0;
}
template<class State> constexpr void markKnown(State& state, unsigned slot, uint32_t now, bool semanticChanged = false) {
    // LAST is first evidence or last c/b/e status change, not heartbeat receipt.
    if (!(state.knownMask & (1U << slot)) || semanticChanged) state.lastThreadStatusMs[slot] = now;
    state.knownMask |= static_cast<uint8_t>(1U << slot);
}
template<class State> constexpr void markReceipt(State& state, uint8_t mask, uint8_t completeMask, uint32_t now) {
    if (!mask) return;
    ++state.threadStatusReceiptSequence;
    state.lastThreadStatusReceiptMs = now;
    for (unsigned slot = 0; slot < 6; ++slot)
        if (completeMask & (1U << slot)) {
            state.threadStatusCompleteReceiptSequence[slot] = state.threadStatusReceiptSequence;
            state.threadStatusCompleteReceiptMs[slot] = now;
        }
}
struct LockedWindow {
    static constexpr uint32_t DurationMs = 8000, IntervalMs = 60000;
    bool active = false, attempted = false, timedOut = false;
    uint32_t intervalMs = IntervalMs;
    uint32_t startedMs = 0, endedMs = 0, count = 0, revision = 0, generation = 0;
    uint8_t freshMask = 0;
    int32_t error = 0;
    std::array<uint32_t, 6> baseline{};
    constexpr bool start(uint32_t now, uint32_t gen, const std::array<uint32_t, 6>& receipts) {
        if (active || (attempted && now - startedMs < intervalMs)) return false;
        active = attempted = true; timedOut = false; error = 0;
        startedMs = now; endedMs = 0; generation = gen; baseline = receipts; freshMask = 0;
        ++count; ++revision; return true;
    }
    constexpr bool expired(uint32_t now) const { return active && now - startedMs >= DurationMs; }
    constexpr void observe(uint32_t gen, uint8_t known, const std::array<uint32_t, 6>& receipts,
                           const std::array<uint32_t, 6>& receivedMs, bool connected, bool protocolReady) {
        if (!active || !connected) return;
        if (gen != generation) { generation = gen; baseline = {}; freshMask = 0; }
        if (!protocolReady) return;
        for (unsigned slot = 0; slot < 6; ++slot)
            if ((known & (1U << slot)) && receipts[slot] != baseline[slot] && receivedMs[slot] - startedMs < DurationMs)
                freshMask |= static_cast<uint8_t>(1U << slot);
    }
    constexpr void finish(uint32_t now, bool timeout = false, int32_t failure = 0) {
        if (!active) return;
        active = false; endedMs = now; timedOut = timeout; error = failure; ++revision;
    }
};
}
