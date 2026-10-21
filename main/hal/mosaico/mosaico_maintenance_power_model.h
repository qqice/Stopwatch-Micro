#pragma once
#include <cstdint>
namespace mosaico_maintenance_power {
// SLUUBD4A OperationStatus: INITCOMP bit5, CFGUPMODE bit10, CALMD bit0.
// Bit7 is NOT the initialization-complete bit.
constexpr bool safeOperation(uint16_t op) {
    return ((op >> 1) & 3) == 3 && !(op & 0x0401) && (op & 0x0020);
}
template<class Sample> constexpr bool safe(const Sample& s) {
    return safeOperation(s.operationStatus) &&
        ((s.designMah == 65 && s.fullMah == 65) ||
         (s.designMah == 3000 && s.fullMah == 3000)) &&
        s.voltageMv >= 3900 && s.voltageMv <= 4350 &&
        s.temperatureDeciK >= 2832 && s.temperatureDeciK <= 3131 &&
        s.currentMa >= -130 && s.currentMa <= 130 &&
        s.averageCurrentMa >= -130 && s.averageCurrentMa <= 130;
}
} // namespace mosaico_maintenance_power
