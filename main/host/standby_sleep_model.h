/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>
namespace StandbySleep {
enum class Mode : uint8_t { Off, Diagnostic, Automatic };
constexpr const char* modeName(Mode mode) {
    return mode==Mode::Automatic ? "automatic" : mode==Mode::Diagnostic ? "diagnostic" : "off";
}
struct Model {
    int64_t leaseUntil=0, awakeUntil=0;
    bool supported=true, automaticPolicy=false, otaBlocked=false;
    bool viewLocked=false, displaySafe=false, fault=false, uartPending=false;
    constexpr bool requested(int64_t now) const { return automaticPolicy || (leaseUntil && now<leaseUntil); }
    constexpr Mode mode(int64_t now) const {
        return automaticPolicy ? Mode::Automatic : leaseUntil && now<leaseUntil ? Mode::Diagnostic : Mode::Off;
    }
    constexpr bool request(uint32_t seconds, int64_t now) {
        if(!supported || seconds<30 || seconds>300)return false;
        automaticPolicy=false;leaseUntil=now+int64_t(seconds)*1000000;return true;
    }
    constexpr bool enableAutomatic(bool confirmed) {
        if(!supported || !confirmed)return false;
        leaseUntil=0;automaticPolicy=true;return true;
    }
    constexpr void off() { leaseUntil=0;automaticPolicy=false; }
    constexpr void pause() { leaseUntil=0;displaySafe=false; }
    constexpr void wake(int64_t now) { if(requested(now))awakeUntil=now+500000; }
    constexpr void service(int64_t now) {
        if(leaseUntil && (now>=leaseUntil || !viewLocked || !displaySafe || fault || otaBlocked))leaseUntil=0;
    }
    constexpr bool eligible(int64_t now, uint32_t cpu, bool locked, bool wireless) const {
        return supported && requested(now) && viewLocked && locked && displaySafe && !fault && !otaBlocked && !wireless && cpu==160;
    }
    constexpr bool uartBlocked(int64_t now) const { return uartPending || now<awakeUntil; }
};
constexpr Model initialModel(bool supported, bool automaticFlag) {
    Model model;model.supported=supported;model.automaticPolicy=supported && automaticFlag;return model;
}
}
