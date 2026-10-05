#pragma once
#include <cstdint>

namespace mosaico_charge {
// UI-only RAM inference from the gauge's signed current, not OTA power proof.
// A positive-current anchor survives full-charge/deadband samples. Discharge
// clears it; invalid telemetry fails conservative. No USB, SOC or NVS evidence.
// Cold boot at full charge/current zero has no anchor and uses the battery
// profile until charging current is observed; no MCU-readable 5V flag exists.
struct ChargeSupplyState {
    bool external = false;
    constexpr void update(bool valid, int32_t currentMa) {
        if (!valid || currentMa < -3) external = false;
        else if (currentMa > 3) external = true;
    }
};
}
