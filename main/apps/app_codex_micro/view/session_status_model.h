#pragma once
#include <cstdint>
#include <array>
#include <limits>

namespace mosaico_sessions_ui {
enum class Status : uint8_t { Unknown, Unassigned, Idle, Thinking, Complete, Wait, Error };
constexpr bool finite(float v) {
    return v == v && v >= -std::numeric_limits<float>::max() && v <= std::numeric_limits<float>::max();
}
// Semantic hints from the native six-slot lighting palette, not all-client
// thread enumeration. Brightness zero is a preference, not "unassigned".
template <typename Light>
constexpr Status classify(const Light& light) {
    const auto effect = static_cast<uint8_t>(light.effect);
    if (!finite(light.brightness) || light.brightness < 0 || light.brightness > 1 ||
        !finite(light.speed) || light.speed < 0 || light.color > 0xffffff || effect > 6) return Status::Unknown;
    if (!effect) return light.color == 0 ? Status::Unassigned : Status::Unknown;
    if (effect == 3) return Status::Unknown; // Rainbow overrides a stable colour; opaque magic is not a status signal.
    if (!light.color) return Status::Unknown;
    const int r=(light.color>>16)&255, g=(light.color>>8)&255, b=light.color&255;
    const int high=r>g?(r>b?r:b):(g>b?g:b);
    const int low=r<g?(r<b?r:b):(g<b?g:b);
    if (high-low < 48 && high > 150) return Status::Idle;
    if (r > 170 && g > 105 && b < 120 && r >= g && g-b >= 32) return Status::Wait;
    if (b > 150 && b-r >= 48 && b-g >= 32) return Status::Thinking;
    if (r > 170 && r-g >= 48 && r-b >= 48) return Status::Error;
    if (g > 150 && g-r >= 48 && g-b >= 32) return Status::Complete;
    return Status::Unknown;
}
template <typename State>
constexpr bool live(const State& state, bool backendReady) {
    return backendReady && state.ready && state.connected && state.protocolReady && state.connectionGeneration != 0;
}
struct Counts { unsigned known=0, running=0, waiting=0, errors=0; bool live=false; };
template <typename State>
constexpr Counts counts(const State& state, bool backendReady) {
    Counts out; out.live=live(state,backendReady);
    if (!out.live) return out;
    for (unsigned i=0; i<6; ++i) if (state.knownMask & (1U<<i)) {
        const auto status=classify(state.threads[i]);
        if (status == Status::Unknown) continue;
        ++out.known;
        out.running += status==Status::Thinking;
        out.waiting += status==Status::Wait;
        out.errors += status==Status::Error;
    }
    return out;
}
// Partial evidence gives lower bounds, never exact zero totals for unknown slots.
constexpr std::array<char,8> counterLabel(unsigned count, unsigned known) {
    if (known < 6 && !count) return {'-','-',0};
    if (known < 6) return {static_cast<char>('0'+count),'+','?','/','6',0};
    return {static_cast<char>('0'+count),'/','6',0};
}
constexpr uint32_t ageSeconds(uint32_t now, uint32_t last) {
    const uint32_t elapsed=now-last;
    return elapsed < 0x80000000U ? elapsed/1000U : 0;
}
// A live handshake is event-driven; old lighting is still current until a new
// status or disconnect. Last-change age must never invent a stale timeout.
constexpr bool stale(bool connected, unsigned known) { return !connected && known != 0; }
}
