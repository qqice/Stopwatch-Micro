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
void uartRxConfigured(bool ready, int32_t error);
void uartRecoveryConfigured(bool ready, int32_t error); // Standby one-time wake owner only.
void uartEvent(bool wake, bool data, uint32_t queueDepth); // Existing queue consumer, never ISR.
void uartOwner(bool owned); // Only SerialDebug's freshly installed UART0 driver.
void serialState(bool busy, bool activity); // Task context; independent 500 ms receive hold.
void viewState(bool safe); // Same authoritative view eligibility as standby sleep.
void usbEvent(); // TinyUSB task callback, notify only; never an ISR.
void wait(bool locked, bool ota, bool usb, bool wifi, bool ble);
struct Snapshot {
    bool supported=false, enabled=false, gpio=false, uart=false;
    uint32_t gpioWakes=0, uartWakes=0, eventWaits=0, maxWaitUs=0, requestedMs=20;
    bool recoveryReady=false, rxReady=false;
    int32_t requestedWakeMode=0, appliedWakeMode=-1, recoveryError=0;
    uint32_t wakeThreshold=3, rxFullThreshold=120, queueCapacity=8, queuePeak=0;
    uint32_t wakeEvents=0, dataEvents=0, readNotifies=0, errorNotifies=0;
    uint32_t lastWakeUs=0, lastDataUs=0, lastReadUs=0;
    uint32_t lastWaitNotify=0, lastWaitUs=0, lastWaitMs=20, maxWaitMs=20;
    Cause lastWaitCause=Cause::Disabled, maxWaitCause=Cause::Disabled;
    int32_t error=0;Cause cause=Cause::Disabled;
};
Snapshot snapshot(); // Memory-only, no PM dump or driver logging.
}
