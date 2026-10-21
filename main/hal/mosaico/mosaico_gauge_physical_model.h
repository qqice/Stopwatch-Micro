#pragma once
#include "mosaico_gauge_units_model.h"

// Pure snapshot conversion only. Caller captures native data, sample metadata
// and context under ONE transaction/context lock. This does not verify hardware,
// authorize a parameter write, or reinterpret any historical raw record.
namespace mosaico_gauge_physical {
enum class Phase : uint8_t { Unknown, Original, Pending, Applied, Restoring };

struct UnitContext {
    int64_t factor = 0; // Missing context is unknown, never implicit scale 1.
    uint64_t unitEpoch = 0;
    uint64_t generation = 0;
    Phase phase = Phase::Unknown;
    bool verified = false;
};

struct SampleMeta {
    int64_t factor = 0;
    uint64_t unitEpoch = 0;
    uint64_t generation = 0;
    bool valid = false; // All quantities below must have valid reads.
};

// Wide signed inputs ensure invalid reads cannot wrap into a valid register.
struct NativeQuantities {
    int64_t currentMilliamp = 0;
    int64_t averageCurrentMilliamp = 0;
    int64_t remainingCapacityMilliampHour = 0;
    int64_t fullChargeCapacityMilliampHour = 0;
    int64_t designCapacityMilliampHour = 0;
};

constexpr bool matching(const SampleMeta& sample, const UnitContext& context) {
    return sample.valid && context.verified &&
        sample.factor == context.factor &&
        sample.unitEpoch == context.unitEpoch &&
        sample.generation == context.generation &&
        ((context.phase == Phase::Original && context.factor == 1) ||
         (context.phase == Phase::Applied &&
          (context.factor == 2 || context.factor == 4)));
}

template<class Native>
struct Snapshot {
    Native native; // Unmodified copy: SOC/mV/temp/time/flags/raw diagnostics etc.
    UnitContext context;
    SampleMeta sampleMeta;
    bool valid = false;
    int32_t currentMicroamp = 0;
    int32_t averageCurrentMicroamp = 0;
    uint32_t remainingCapacityMicroampHour = 0;
    uint32_t fullChargeCapacityMicroampHour = 0;
    uint32_t designCapacityMicroampHour = 0;
};

template<class Native>
constexpr Snapshot<Native> makeSnapshot(const Native& native,
                                        const NativeQuantities& quantities,
                                        const SampleMeta& sample,
                                        const UnitContext& context) {
    Snapshot<Native> out{native, context, sample};
    if (!matching(sample, context)) return out;
    int32_t current = 0, average = 0;
    uint32_t remaining = 0, full = 0, design = 0;
    using namespace mosaico_gauge_units;
    if (!currentMicroamp(quantities.currentMilliamp, context.factor, current) ||
        !currentMicroamp(quantities.averageCurrentMilliamp, context.factor, average) ||
        !capacityMicroampHour(quantities.remainingCapacityMilliampHour, context.factor, remaining) ||
        !capacityMicroampHour(quantities.fullChargeCapacityMilliampHour, context.factor, full) ||
        !capacityMicroampHour(quantities.designCapacityMilliampHour, context.factor, design)) return out;
    out.currentMicroamp = current;
    out.averageCurrentMicroamp = average;
    out.remainingCapacityMicroampHour = remaining;
    out.fullChargeCapacityMicroampHour = full;
    out.designCapacityMicroampHour = design;
    out.valid = true;
    return out;
}

template<class Native>
constexpr bool chargingAbove3Milliamp(const Snapshot<Native>& snapshot) {
    return snapshot.valid && snapshot.currentMicroamp > 3000;
}

template<class Native>
constexpr bool traceAbove5Milliamp(const Snapshot<Native>& snapshot) {
    return snapshot.valid && snapshot.currentMicroamp > 5000;
}
} // namespace mosaico_gauge_physical
