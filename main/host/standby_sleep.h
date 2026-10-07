/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>
namespace StandbySleep {
struct Snapshot {
    bool supported=false, lease=false, configured=false, eligible=false, uartBlocked=false, uartReady=false;
    uint32_t remainingSeconds=0, positiveIntervals=0, lockCreated=0, lockAcquired=0;
    uint64_t frameworkIntervalUs=0, successfulSleeps=0, rejectedSleeps=0;
    bool pmCountsValid=false;
    int32_t error=0;
};
bool monitoring();
void uartReady(bool ready);
void uartWake(); // Existing UART event consumer, never ISR/callback.
void uartTraffic(bool pending, bool activity);
void viewState(bool locked, bool safe, bool fault);
void service(bool ota, bool bleActive); // Existing main loop only.
bool request(uint32_t seconds=180);
void off();
bool allow(uint32_t cpuMHz, bool locked, bool wifiActive);
void beforeConfigure(bool lightSleep); // Capture SDK accepted/rejected counts before disabling LS.
void configured(bool lightSleep, int32_t error);
Snapshot snapshot(); // Task context only; bounded memory-only SDK profiling output.
}
