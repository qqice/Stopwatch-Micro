/* SPDX-License-Identifier: MIT */
#pragma once
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
void mosaico_console_init(void);
// app_main task only, before StandbySleep::service; never a sleep/ISR callback.
void mosaico_console_usb_service_sleep(void);
// Stack observations, not a VBUS/power-presence assertion. Memory-only read.
typedef struct {
    uint32_t mounted, connected, suspended, effective_active, service_active;
    // Requested/configured software observations, not register or power proof.
    uint32_t bridge_pending, phy_hint_requested, cnnt_on_configured;
    int32_t cnnt_error;
    uint32_t hint_arms, wake_clears;
    uint32_t wakeup_ready, sleep_safe;
    int32_t wakeup_error;
    uint32_t mounts, unmounts, suspends, resumes, rx_events;
} mosaico_usb_snapshot_t;
mosaico_usb_snapshot_t mosaico_console_usb_snapshot(void);
#ifdef __cplusplus
}
#endif
