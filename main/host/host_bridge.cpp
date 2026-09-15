/*
 * SPDX-License-Identifier: MIT
 */
#include "host_bridge.h"

namespace {

bool sequenceNewer(uint32_t candidate, uint32_t current)
{
    return current == 0 || static_cast<int32_t>(candidate - current) > 0;
}

}  // namespace

HostBridge& GetHostBridge()
{
    static HostBridge bridge;
    return bridge;
}

bool HostBridge::applyUsage(uint32_t sequence, uint16_t remainingBasisPoints, uint32_t resetEpoch,
                            uint32_t capturedEpoch, uint8_t resetCredits, uint32_t receivedAtMs)
{
    return apply(false, sequence, remainingBasisPoints, resetEpoch, capturedEpoch, resetCredits, receivedAtMs);
}

bool HostBridge::applyNetworkUsage(uint16_t remainingBasisPoints, uint32_t resetEpoch, uint32_t capturedEpoch,
                                   uint8_t resetCredits, uint32_t receivedAtMs)
{
    return apply(true, 0, remainingBasisPoints, resetEpoch, capturedEpoch, resetCredits, receivedAtMs);
}

bool HostBridge::apply(bool network, uint32_t sequence, uint16_t remainingBasisPoints, uint32_t resetEpoch,
                       uint32_t capturedEpoch, uint8_t resetCredits, uint32_t receivedAtMs)
{
    if ((!network && sequence == 0) || remainingBasisPoints > 10000 || capturedEpoch == 0 || resetCredits > 99) {
        return false;
    }
    portENTER_CRITICAL(&_mux);
    if (!network && !sequenceNewer(sequence, _last_usage_sequence)) {
        portEXIT_CRITICAL(&_mux);
        return false;
    }
    // Network snapshots must never advance the BLE sender's sequence space.
    if (!network) _last_usage_sequence = sequence;
    // A valid but older sample is acknowledged without replacing the current
    // value. Do not misreport this as a BLE stale-sequence retry condition.
    if (capturedEpoch < _captured_epoch) {
        portEXIT_CRITICAL(&_mux);
        return true;
    }
    _remaining_basis_points = remainingBasisPoints;
    _reset_epoch            = resetEpoch;
    _captured_epoch         = capturedEpoch;
    _reset_credits          = resetCredits;
    _usage_received_at_ms   = receivedAtMs;
    _has_usage              = true;
    ++_revision;
    portEXIT_CRITICAL(&_mux);
    return true;
}

HostBridgeSnapshot HostBridge::snapshot(uint32_t nowMs) const
{
    uint32_t usage_received_at_ms   = 0;
    uint32_t reset_epoch            = 0;
    uint32_t captured_epoch         = 0;
    uint32_t revision               = 0;
    uint16_t remaining_basis_points = 0;
    uint8_t reset_credits           = 0;
    bool has_usage                  = false;
    portENTER_CRITICAL(&_mux);
    usage_received_at_ms   = _usage_received_at_ms;
    reset_epoch            = _reset_epoch;
    captured_epoch         = _captured_epoch;
    revision               = _revision;
    remaining_basis_points = _remaining_basis_points;
    reset_credits          = _reset_credits;
    has_usage              = _has_usage;
    portEXIT_CRITICAL(&_mux);

    HostBridgeSnapshot copy;
    const uint32_t usageAge   = has_usage ? nowMs - usage_received_at_ms : UINT32_MAX;
    copy.online               = has_usage && usageAge <= BridgeStaleMs;
    copy.usageAvailable       = has_usage && usageAge <= UsageUnavailableMs;
    copy.usageStale           = copy.usageAvailable && usageAge > BridgeStaleMs;
    copy.remainingBasisPoints = remaining_basis_points;
    copy.resetCredits         = reset_credits;
    copy.revision             = revision;

    if (copy.usageAvailable && reset_epoch > captured_epoch) {
        copy.resetAvailable           = true;
        const uint32_t initialSeconds = reset_epoch - captured_epoch;
        const uint32_t elapsedSeconds = usageAge / 1000U;
        copy.resetSeconds             = elapsedSeconds < initialSeconds ? initialSeconds - elapsedSeconds : 0;
        if (copy.resetSeconds == 0 && usageAge > BridgeStaleMs) {
            copy.usageStale = true;
        }
    }
    return copy;
}

uint32_t HostBridge::lastUsageSequence() const
{
    portENTER_CRITICAL(&_mux);
    const uint32_t sequence = _last_usage_sequence;
    portEXIT_CRITICAL(&_mux);
    return sequence;
}
