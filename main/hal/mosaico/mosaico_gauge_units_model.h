#pragma once
#include <cstdint>

// Pure, currently UNINTEGRATED preparation. No gauge/NVS/runtime configuration.
// SOC, voltage, time, temperature and flags deliberately have no conversion API.
namespace mosaico_gauge_units {
inline constexpr bool readyForHardware = false;

constexpr bool validFactor(int64_t factor) {
    return factor == 1 || factor == 2 || factor == 4;
}

// Wide inputs intentionally reject out-of-register-range values before narrowing.
// Failure leaves the caller's output unchanged. Successful results are exact.
constexpr bool currentMicroamp(int64_t native, int64_t factor, int32_t& out) {
    if (!validFactor(factor) || native < -32768 || native > 32767) return false;
    out = static_cast<int32_t>(native * (1000 / factor));
    return true;
}

constexpr bool capacityMicroampHour(int64_t native, int64_t factor, uint32_t& out) {
    if (!validFactor(factor) || native < 0 || native > 65535) return false;
    out = static_cast<uint32_t>(native * (1000 / factor));
    return true;
}

// Strict signed comparison. Threshold accepts physical int32 mA, including
// negative thresholds. Widened cross multiplication cannot overflow int64_t.
constexpr bool currentAboveMilliamp(int64_t native, int64_t factor,
                                    int64_t threshold, bool& out) {
    if (!validFactor(factor) || native < -32768 || native > 32767 ||
        threshold < (-2147483647LL - 1) || threshold > 2147483647LL) return false;
    out = native > threshold * factor;
    return true;
}

struct CounterSample {
    uint16_t raw;
    uint64_t boot;
    int64_t factor;
    uint64_t monotonicTime;
};

struct RelativeNativeCounterDelta {
    uint16_t previousRaw;
    uint16_t currentRaw;
    uint16_t moduloDelta;
    int32_t nativeDelta;
    // Scaled relative counter units * 1000, NOT absolute remaining capacity.
    int32_t scaledMilliunits;
};

// Caller must establish valid elapsed time AND a strict less-than-half-ring
// displacement bound from external evidence. Neither is inferred from raw data.
constexpr bool relativeNativeCounterDelta(const CounterSample& previous,
                                          const CounterSample& current,
                                          bool elapsedValid,
                                          bool withinHalfRingConfirmed,
                                          RelativeNativeCounterDelta& out) {
    if (!elapsedValid || !withinHalfRingConfirmed || previous.boot != current.boot ||
        previous.factor != current.factor || !validFactor(current.factor) ||
        current.monotonicTime <= previous.monotonicTime) return false;
    const auto modulo = static_cast<uint16_t>(
        static_cast<uint32_t>(current.raw) + 65536U - previous.raw);
    if (modulo == 32768) return false;
    const int32_t delta = modulo < 32768 ? static_cast<int32_t>(modulo) :
                                         static_cast<int32_t>(modulo) - 65536;
    out = {previous.raw, current.raw, modulo, delta,
           static_cast<int32_t>(delta * (1000 / current.factor))};
    return true;
}
} // namespace mosaico_gauge_units
