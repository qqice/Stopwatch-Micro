/*
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include <cstdint>

#include <freertos/FreeRTOS.h>

struct HostBridgeSnapshot {
    bool online                   = false;
    bool usageAvailable           = false;
    bool usageStale               = false;
    bool resetAvailable           = false;
    uint16_t remainingBasisPoints = 0;
    uint32_t resetSeconds         = 0;
    uint8_t resetCredits          = 0;
    uint32_t revision             = 0;
};

class HostBridge {
public:
    bool applyUsage(uint32_t sequence, uint16_t remainingBasisPoints, uint32_t resetEpoch, uint32_t capturedEpoch,
                    uint8_t resetCredits, uint32_t receivedAtMs);
    bool applyNetworkUsage(uint16_t remainingBasisPoints, uint32_t resetEpoch, uint32_t capturedEpoch,
                           uint8_t resetCredits, uint32_t receivedAtMs);
    HostBridgeSnapshot snapshot(uint32_t nowMs) const;

    uint32_t lastUsageSequence() const;

private:
    bool apply(bool network, uint32_t sequence, uint16_t remainingBasisPoints, uint32_t resetEpoch,
               uint32_t capturedEpoch, uint8_t resetCredits, uint32_t receivedAtMs);
    static constexpr uint32_t BridgeStaleMs      = 130000;
    static constexpr uint32_t UsageUnavailableMs = 600000;

    uint32_t _last_usage_sequence    = 0;
    uint32_t _usage_received_at_ms   = 0;
    uint32_t _reset_epoch            = 0;
    uint32_t _captured_epoch         = 0;
    uint32_t _revision               = 0;
    uint16_t _remaining_basis_points = 0;
    uint8_t _reset_credits           = 0;
    bool _has_usage                  = false;
    mutable portMUX_TYPE _mux        = portMUX_INITIALIZER_UNLOCKED;
};

HostBridge& GetHostBridge();
