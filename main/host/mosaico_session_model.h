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
}
template<class State> constexpr void markKnown(State& state, unsigned slot, uint32_t now, bool semanticChanged = false) {
    // LAST is first evidence or last c/b/e status change, not heartbeat receipt.
    if (!(state.knownMask & (1U << slot)) || semanticChanged) state.lastThreadStatusMs[slot] = now;
    state.knownMask |= static_cast<uint8_t>(1U << slot);
}
}
