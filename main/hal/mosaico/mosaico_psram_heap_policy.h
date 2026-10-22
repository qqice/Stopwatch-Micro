// SPDX-License-Identifier: MIT
#pragma once
#include <cstdint>
// forwarded/forwardFailures/completed count recorded registrations only.
// After overflow these are not global call totals; faults makes health fail closed.
struct MosaicoPsramHeapPolicyDiagnostic {
    uint32_t start, end, bytes, skipped, forwarded, forwardFailures, faults, reserved, completed, rodataStart, rodataEnd;
    bool healthy, coherent;
};
// Fixed internal-RAM counters only; never inspects the quarantined memory.
void MosaicoPsramHeapPolicySnapshot(MosaicoPsramHeapPolicyDiagnostic& out);
bool MosaicoPsramHeapPolicyHealthy();
struct MosaicoPsramHeapPolicyRecord {
    uint32_t start, end, caps[3], action;
    int32_t result;
};
constexpr uint32_t MosaicoPsramHeapPolicyRecordCapacity=16;
bool MosaicoPsramHeapPolicyReadRecord(uint32_t index,MosaicoPsramHeapPolicyRecord& out);
