#pragma once
#include <cstdint>

namespace MosaicoOta {
constexpr bool useManualInstallPolicy(bool confirmedPhysicalBypass, bool automaticMode) {
    return confirmedPhysicalBypass || !automaticMode;
}
// USB is connection evidence, not measured VBUS. An enumerated USB manual
// install must not dereference gauge telemetry or historical diagnosis. Battery
// operation retains fresh valid/sealed and voltage conditions, not SOC/capacity
// calibration/profile eligibility. The legacy critical argument is ignored.
template<typename Telemetry>
constexpr bool manualInstallPowerSafe(const Telemetry& b, bool externalPresent, bool critical)
{
    (void)critical;
    if (externalPresent) return true;
    return b.valid && ((b.operationStatus >> 1) & 3) == 3 &&
        !(b.operationStatus & 0x0401) && b.voltageMv >= 3900;
}
} // namespace MosaicoOta
