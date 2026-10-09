/* SPDX-License-Identifier: MIT */
#pragma once
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
void mosaico_console_init(void);
// Stack observations, not a VBUS/power-presence assertion. Memory-only read.
typedef struct {
    uint32_t mounted, connected, suspended, effective_active;
    uint32_t wakeup_ready, sleep_safe;
    int32_t wakeup_error;
    uint32_t mounts, unmounts, suspends, resumes, rx_events;
} mosaico_usb_snapshot_t;
mosaico_usb_snapshot_t mosaico_console_usb_snapshot(void);
#ifdef __cplusplus
}
#endif
