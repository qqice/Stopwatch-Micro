// SPDX-License-Identifier: MIT
#pragma once
#include <cstdint>
namespace MosaicoTsfProbe {
// Public numeric evidence only. The SDK callback has no request ID; at most one
// actual probe API invocation per boot prevents reassignment of a late callback.
// The probe intrinsically wakes the modem; it is not nonperturbing. TSF/local
// brackets do not establish precise RF wake time. A local deadline is not a
// driver cancellation: late callbacks remain accepted after deadline expiry.
struct Snapshot {
    uint64_t requestId = 0, processedId = 0;
    uint16_t trialId = 0;
    uint32_t timeoutMs = 0;
    bool queued = false, inflight = false, consumed = false;
    bool apiCalled = false, callbackSeen = false, deadlineExpired = false, sameTrial = false;
    bool drainAttempted = false, driverDrained = false;
    int drainResult = 0, flowBitmap = -1;
    int firstPpResult = 0, secondPpResult = 0;
    bool secondPpAttempted = false, timerCreateAttempted = false;
    bool timerCreated = false, timerStarted = false, timerSeen = false;
    int timerError = 0, deleteResult = 0;
    // resourceReclaimed means public delete accepted and owner relinquished the
    // handle; installed esp_timer deletion frees it asynchronously on timer task.
    bool reclaimAttempted = false, resourceReclaimed = false;
    int apiResult = 0, probeStatus = 0;
    uint8_t reason = 0;
    int64_t callbackLocalUs = 0, deadlineUs = 0;
    int64_t tsfBefore = 0, beforeStartUs = 0, beforeEndUs = 0;
    int64_t tsfAfter = 0, afterStartUs = 0, afterEndUs = 0;
    bool beforeValid = false, afterValid = false;
};
}
