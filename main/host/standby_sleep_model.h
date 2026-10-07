/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>
namespace StandbySleep {
struct Model {
    int64_t leaseUntil=0, awakeUntil=0;
    bool viewLocked=false, displaySafe=false, fault=false, uartPending=false;
    constexpr bool request(uint32_t seconds, int64_t now) {
        if(seconds<30 || seconds>300)return false;
        leaseUntil=now+int64_t(seconds)*1000000;return true;
    }
    constexpr void off() { leaseUntil=0; }
    constexpr void wake(int64_t now) { if(leaseUntil)awakeUntil=now+500000; }
    constexpr void service(int64_t now, bool ota) {
        if(leaseUntil && (now>=leaseUntil || !viewLocked || fault || ota))off();
    }
    constexpr bool eligible(int64_t now, uint32_t cpu, bool locked, bool wireless) const {
        return leaseUntil && now<leaseUntil && viewLocked && locked && displaySafe && !fault && !wireless && cpu==160;
    }
    constexpr bool uartBlocked(int64_t now) const { return uartPending || now<awakeUntil; }
};
}
