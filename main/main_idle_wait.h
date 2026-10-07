/* SPDX-License-Identifier: MIT */
#pragma once
#include "main_idle_wait_model.h"
namespace MainIdleWait {
// Scoped to the existing app_main task; no task, timer, or hardware power owner.
class Lifetime {
public:
    Lifetime(); ~Lifetime();
    Lifetime(const Lifetime&)=delete;Lifetime& operator=(const Lifetime&)=delete;
};
bool setEnabled(bool enabled); // RAM-only; unsupported profiles cannot enable.
void uartOwner(bool owned); // Only SerialDebug's freshly installed UART0 driver.
void serialState(bool busy, bool activity); // Task context; independent 500 ms receive hold.
void viewState(bool safe); // Same authoritative view eligibility as standby sleep.
void wait(bool locked, bool ota, bool usb, bool wifi, bool ble);
struct Snapshot {
    bool supported=false, enabled=false, gpio=false, uart=false;
    uint32_t gpioWakes=0, uartWakes=0, eventWaits=0, maxWaitUs=0, requestedMs=20;
    int32_t error=0;Cause cause=Cause::Disabled;
};
Snapshot snapshot(); // Memory-only, no PM dump or driver logging.
}
