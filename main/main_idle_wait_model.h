/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>
namespace MainIdleWait {
enum class Cause : uint8_t { Awake, Disabled, Unsupported, Gpio, Uart, Button, Ota, Usb, Wifi, Ble, Serial, View, Event };
constexpr const char* causeName(Cause c) {
    switch(c) {
    case Cause::Awake:return "awake";case Cause::Disabled:return "disabled";case Cause::Unsupported:return "unsupported";
    case Cause::Gpio:return "gpio_not_ready";case Cause::Uart:return "uart_not_ready";case Cause::Button:return "button_grace";
    case Cause::Ota:return "ota_health";case Cause::Usb:return "usb_mounted_cadence";case Cause::Wifi:return "wifi_active";
    case Cause::Ble:return "ble_active";case Cause::Serial:return "serial_busy";case Cause::View:return "unsafe_view";
    case Cause::Event:return "event_wait";
    }return "unknown";
}
struct Gates {
    bool locked=false, enabled=false, supported=false, gpio=false, uart=false, button=false;
    bool ota=false, usb=false, wifi=false, ble=false, serial=true, view=false;
};
constexpr Cause select(const Gates& g) {
    if(!g.locked)return Cause::Awake;
    if(!g.enabled)return Cause::Disabled;
    if(!g.supported)return Cause::Unsupported;
    if(!g.gpio)return Cause::Gpio;
    if(!g.uart)return Cause::Uart;
    if(g.button)return Cause::Button;
    if(g.ota)return Cause::Ota;
    if(g.usb)return Cause::Usb;
    if(g.wifi)return Cause::Wifi;
    if(g.ble)return Cause::Ble;
    if(g.serial)return Cause::Serial;
    if(!g.view)return Cause::View;
    return Cause::Event;
}
constexpr uint32_t waitMs(Cause c) { return c==Cause::Awake ? 10 : c==Cause::Event ? 500 : 20; }
struct ButtonGrace {
    uint32_t lastActivity=0;bool active=false;
    constexpr bool service(uint32_t now,bool rawLow,bool interrupt) {
        if(rawLow || interrupt) {lastActivity=now;active=true;}
        if(active && !rawLow && uint32_t(now-lastActivity)>=200)active=false;
        return active;
    }
};
}
