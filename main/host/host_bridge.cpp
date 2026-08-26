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
    if (sequence == 0 || remainingBasisPoints > 10000 || resetCredits > 99 ||
        !sequenceNewer(sequence, _last_usage_sequence)) {
        return false;
    }
    _last_usage_sequence    = sequence;
    _remaining_basis_points = remainingBasisPoints;
    _reset_epoch            = resetEpoch;
    _captured_epoch         = capturedEpoch;
    _reset_credits          = resetCredits;
    _usage_received_at_ms   = receivedAtMs;
    _has_usage              = true;
    ++_revision;
    return true;
}

HostBridgeSnapshot HostBridge::snapshot(uint32_t nowMs) const
{
    HostBridgeSnapshot copy;
    const uint32_t usageAge   = _has_usage ? nowMs - _usage_received_at_ms : UINT32_MAX;
    copy.online               = _has_usage && usageAge <= BridgeStaleMs;
    copy.usageAvailable       = _has_usage && usageAge <= UsageUnavailableMs;
    copy.usageStale           = copy.usageAvailable && usageAge > BridgeStaleMs;
    copy.remainingBasisPoints = _remaining_basis_points;
    copy.resetCredits         = _reset_credits;
    copy.revision             = _revision;

    if (copy.usageAvailable && _reset_epoch > _captured_epoch) {
        copy.resetAvailable           = true;
        const uint32_t initialSeconds = _reset_epoch - _captured_epoch;
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
    return _last_usage_sequence;
}
