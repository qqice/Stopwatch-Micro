/* SPDX-License-Identifier: MIT */
#pragma once
#include <array>
#include <cstdint>
#include "mosaico_touch_power_model.h"
namespace mosaico_sleep_io {
enum class Role : uint8_t { Unused0,Unused1,Unused2,Rail,Reset,CS,Clock,Data0,Data1,Data2,Data3,Count };
constexpr bool isolationSafe(bool resetWorkaround,bool disableGpio,bool retention) {
    return !(resetWorkaround || disableGpio) || retention;
}
constexpr unsigned Count=static_cast<unsigned>(Role::Count);
constexpr uint64_t bit(int pin) { return pin>=0 && pin<64 ? uint64_t(1)<<pin : 0; }
constexpr uint64_t panelAllowed=bit(42)|bit(44)|bit(50)|bit(36)|bit(51)|bit(35)|bit(9);
constexpr uint64_t allowed(Role role) {
    switch(role) {
    case Role::Unused0:case Role::Unused1:case Role::Unused2:return mosaico_touch_power::UnusedGateMask;
    case Role::Rail:return bit(60);
    case Role::Reset:case Role::Clock:return bit(42)|bit(44);
    case Role::CS:return bit(50);
    case Role::Data0:return bit(36);case Role::Data1:return bit(51);case Role::Data2:return bit(35);case Role::Data3:return bit(9);
    default:return 0;
    }
}
struct Normal {
    uint16_t function=0,signal=0;uint8_t drive=0;
    bool input=false,output=false,peripheralOE=false,invertedOE=false,openDrain=false,pullup=false,pulldown=false;
};
constexpr bool same(Normal a,Normal b) {
    return a.function==b.function && a.signal==b.signal && a.drive==b.drive && a.input==b.input &&
        a.output==b.output && a.peripheralOE==b.peripheralOE && a.invertedOE==b.invertedOE &&
        a.openDrain==b.openDrain && a.pullup==b.pullup && a.pulldown==b.pulldown;
}
struct Read {int32_t error=0;Normal normal{};bool sleepSelected=false;};
struct PinReport {
    int16_t pin=-1;uint16_t signal=0;bool peripheral=false;
    int8_t before=-1,after=-1,current=-1;
    int32_t beforeRc=0,applyRc=0,afterRc=0,currentRc=0;
};
struct Snapshot {
    bool enabled=false,ready=false,schemaError=false;int32_t error=0;
    uint64_t target=0,unused=0,rail=0,panel=0,beforeRead=0,beforeSelected=0,afterRead=0,afterSelected=0;
    uint64_t attempted=0,applyErrors=0,applied=0,failed=0,unsafe=0,normalSame=0,currentRead=0,currentSelected=0,currentNormalSame=0,currentFailed=0;
    std::array<PinReport,Count> pins{};
};
struct Model {
    Snapshot state{};std::array<Normal,Count> baseline{};uint16_t registeredRoles=0;
    uint16_t gpioFunction=1; // Supplied from the target's SDK PIN_FUNC_GPIO.
    constexpr void failure(int32_t error) { if(!state.error)state.error=error; }
    constexpr bool add(Role role,int pin,uint16_t signal,bool peripheral,int32_t invalid) {
        const unsigned index=static_cast<unsigned>(role);const uint64_t mask=bit(pin);
        if(index>=Count || !(allowed(role)&mask) || (state.target&mask) || (registeredRoles&(1U<<index))) {
            state.schemaError=true;failure(invalid);return false;
        }
        state.pins[index].pin=pin;state.pins[index].signal=signal;state.pins[index].peripheral=peripheral;
        registeredRoles|=1U<<index;state.target|=mask;
        if(index<3)state.unused|=mask;else if(role==Role::Rail)state.rail|=mask;else state.panel|=mask;
        return true;
    }
    constexpr bool safe(unsigned index,Normal normal) const {
        const auto& pin=state.pins[index];
        if(normal.drive>3 || normal.function!=gpioFunction || normal.signal!=pin.signal || normal.invertedOE || normal.openDrain)return false;
        // Peripheral OE can be inactive between SPI transactions: OE register 0
        // alone is NOT proof of an unconfigured/input pin. The exact SPI signal
        // and GPIO-matrix function must still match the configured role.
        return pin.peripheral ? (normal.output || normal.peripheralOE) :
            (normal.output && !normal.peripheralOE && !normal.input);
    }
    template<class Reader,class Disable> constexpr void apply(Role role,Reader read,Disable disable,int32_t invalid) {
        const unsigned index=static_cast<unsigned>(role);
        if(index>=Count || state.pins[index].pin<0) {state.schemaError=true;failure(invalid);return;}
        auto& pin=state.pins[index];const uint64_t mask=bit(pin.pin);
        const Read before=read(pin.pin);pin.beforeRc=before.error;
        if(before.error) {state.failed|=mask;failure(before.error);return;}
        state.beforeRead|=mask;pin.before=before.sleepSelected;if(before.sleepSelected)state.beforeSelected|=mask;
        if(!safe(index,before.normal)) {state.unsafe|=mask;failure(invalid);return;}
        baseline[index]=before.normal;
        state.attempted|=mask;pin.applyRc=disable(pin.pin);
        if(pin.applyRc) {state.applyErrors|=mask;state.failed|=mask;failure(pin.applyRc);}
        else state.applied|=mask;
        const Read after=read(pin.pin);pin.afterRc=after.error;
        if(after.error) {state.failed|=mask;failure(after.error);return;}
        state.afterRead|=mask;pin.after=after.sleepSelected;if(after.sleepSelected)state.afterSelected|=mask;
        if(same(before.normal,after.normal))state.normalSame|=mask;
        else {state.unsafe|=mask;failure(invalid);}
        if(after.sleepSelected) {state.failed|=mask;failure(invalid);}
    }
    constexpr void observe(unsigned index,Read current,int32_t invalid) {
        auto& pin=state.pins[index];const uint64_t mask=bit(pin.pin);pin.currentRc=current.error;pin.current=-1;
        state.currentRead&=~mask;state.currentSelected&=~mask;state.currentNormalSame&=~mask;state.currentFailed&=~mask;
        if(current.error) {state.currentFailed|=mask;failure(current.error);return;}
        state.currentRead|=mask;pin.current=current.sleepSelected;
        if(current.sleepSelected) {state.currentSelected|=mask;state.currentFailed|=mask;failure(invalid);}
        if(safe(index,current.normal) && same(baseline[index],current.normal))state.currentNormalSame|=mask;
        else {state.currentFailed|=mask;failure(invalid);}
    }
    constexpr bool ready() const {
        return state.enabled && !state.error && !state.schemaError && registeredRoles==((1U<<Count)-1) &&
            state.unused==mosaico_touch_power::UnusedGateMask && state.rail==bit(60) && state.panel==panelAllowed &&
            state.applied==state.target && state.afterRead==state.target && !state.afterSelected &&
            state.normalSame==state.target && state.currentRead==state.target && !state.currentSelected &&
            state.currentNormalSame==state.target && !state.failed && !state.unsafe && !state.currentFailed;
    }
};
}
