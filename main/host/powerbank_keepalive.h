/* SPDX-License-Identifier: MIT */
#pragma once
#include "powerbank_keepalive_model.h"
namespace PowerbankKeepalive {
// Numeric diagnostic states, not evidence of bank current or retention.
enum class State : uint8_t { Off, Waiting, Blocked, Pulsing, Expired, Unsupported, Invalid,
    TaskError, CreateError, AcquireError, ReleaseError, Reconfigured };
struct Snapshot {
    bool supported=false, enabled=false, pulsing=false;
    State state=State::Off;
    Request config{};
    uint32_t remainingSeconds=0, pulses=0, aborted=0, blocked=0, pmErrors=0;
    uint32_t invalidRequests=0, reconfigurations=0;
    int32_t error=0;
};
// Task context only; explicit diagnostic request. Default boot allocates nothing.
bool request(const Request& config=Request{});
void off();
Snapshot boundedSnapshot();
}
