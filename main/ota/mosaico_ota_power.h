#pragma once
#include <cstdint>

namespace MosaicoOta {
// Pure policy shared by cached UI evidence and fresh backend telemetry.
// USB enumeration/positive current is the existing external-power heuristic,
// not measured VBUS. Capacity checks are needed only for manual battery install.
template<typename Telemetry>
constexpr bool manualInstallPowerSafe(const Telemetry& b, bool externalPresent, bool critical)
{
    if (critical || !b.valid || ((b.operationStatus >> 1) & 3) != 3 ||
        (b.operationStatus & 0x0401) || b.voltageMv < 3900) return false;
    if (externalPresent || b.currentMa > 3) return true;
    return b.capacityValid && b.nominalConfigured && b.reportedSoc >= 60 &&
        b.reportedSoc <= 100 && b.fullMah > 0 && b.remainingMah > 0 &&
        b.remainingMah <= b.fullMah &&
        static_cast<uint32_t>(b.remainingMah) * 100U >= static_cast<uint32_t>(b.fullMah) * 60U;
}
} // namespace MosaicoOta
