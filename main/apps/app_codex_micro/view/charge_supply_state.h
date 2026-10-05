#pragma once
#include <cstdint>

namespace mosaico_charge {
// User-accepted UI heuristic, not physical 5V evidence or OTA power proof.
// A positive-current RAM anchor survives full-charge/taper deadband samples.
// Without that anchor, exactly 100% SOC in the deadband selects the supply
// profile (including cold boot); a drop to 99% releases this SOC-only inference.
// Negative current or invalid telemetry clears both states. No USB/NVS state.
struct ChargeSupplyState {
    bool external = false;
    bool chargedAnchor = false;
    constexpr void update(bool valid, int32_t currentMa, uint8_t soc) {
        if (!valid || currentMa < -3) { external = false; chargedAnchor = false; }
        else if (currentMa > 3) { external = true; chargedAnchor = true; }
        else external = chargedAnchor || soc == 100;
    }
};
}
