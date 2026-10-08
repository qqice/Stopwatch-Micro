/* SPDX-License-Identifier: MIT
 * USB-OTG console uses the same public API/profile as OpenFrameTap Mosaico.
 * A deliberate 1200-baud touch requests volatile ROM download, not an eFuse
 * or bootloader modification. Recovery remains available before HAL starts. */
#include <stdio.h>
#include <stdbool.h>
#include <stdatomic.h>
#include <esp_system.h>
#include <esp_err.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <tinyusb.h>
#include <tinyusb_default_config.h>
#include <tinyusb_cdc_acm.h>
#include <tinyusb_console.h>
#include <soc/lp_system_reg.h>
#include "usb_console.h"
extern void mosaico_idle_usb_notify(void); // Existing app_main owner, task context.
static atomic_uint mounts, unmounts, suspends, resumes, rx_events;
static void suspended_event(void) { atomic_fetch_add(&suspends, 1); }
static void resumed_event(void)
{
    atomic_fetch_add(&resumes, 1);
    mosaico_idle_usb_notify();
}
// The public SDK event callback owns mount/unmount. Its optional suspend/resume
// callbacks default OFF; supply strong TinyUSB hooks only when SDK does not.
#ifndef CONFIG_TINYUSB_SUSPEND_CALLBACK
void tud_suspend_cb(bool remote_wakeup_en)
{
    (void)remote_wakeup_en;
    suspended_event();
}
#endif
#ifndef CONFIG_TINYUSB_RESUME_CALLBACK
void tud_resume_cb(void) { resumed_event(); }
#endif
static void device_event(tinyusb_event_t *event, void *arg)
{
    (void)arg;
    switch (event->id) {
    case TINYUSB_EVENT_ATTACHED:
        atomic_fetch_add(&mounts, 1);
        mosaico_idle_usb_notify();
        break;
    case TINYUSB_EVENT_DETACHED: atomic_fetch_add(&unmounts, 1); break;
#ifdef CONFIG_TINYUSB_SUSPEND_CALLBACK
    case TINYUSB_EVENT_SUSPENDED: suspended_event(); break;
#endif
#ifdef CONFIG_TINYUSB_RESUME_CALLBACK
    case TINYUSB_EVENT_RESUMED: resumed_event(); break;
#endif
    default: break;
    }
}
static void received(int interface, cdcacm_event_t *event)
{
    (void)interface; (void)event;
    atomic_fetch_add(&rx_events, 1);
    // Notify only: leave CDC FIFO/VFS ownership and all received bytes intact.
    mosaico_idle_usb_notify();
}
mosaico_usb_snapshot_t mosaico_console_usb_snapshot(void)
{
    mosaico_usb_snapshot_t s = {0};
    s.mounted = tud_mounted(); s.connected = tud_connected(); s.suspended = tud_suspended();
    s.effective_active = s.mounted && !s.suspended;
    s.mounts = atomic_load(&mounts); s.unmounts = atomic_load(&unmounts);
    s.suspends = atomic_load(&suspends); s.resumes = atomic_load(&resumes);
    s.rx_events = atomic_load(&rx_events);
    return s;
}
static atomic_bool download_requested;
static TaskHandle_t recovery_task;
static void coding_changed(int interface, cdcacm_event_t *event)
{
    (void)interface;
    if (event->line_coding_changed_data.p_line_coding->bit_rate == 1200) {
        atomic_store(&download_requested, true);
        xTaskNotifyGive(recovery_task);
    }
}
static void recovery_watch(void *arg)
{
    (void)arg;
    for (;;) {
        if (atomic_load(&download_requested)) {
            REG_SET_BIT(LP_SYSTEM_REG_SYS_CTRL_REG, LP_SYSTEM_REG_FORCE_DOWNLOAD_BOOT);
            esp_restart();
        }
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    }
}
void mosaico_console_init(void)
{
    if (xTaskCreate(recovery_watch, "usb_recovery", 3072, NULL, 4, &recovery_task) != pdPASS)
        ESP_ERROR_CHECK(ESP_ERR_NO_MEM);
    const tinyusb_config_t usb = TINYUSB_DEFAULT_CONFIG(device_event);
    ESP_ERROR_CHECK(tinyusb_driver_install(&usb));
    const tinyusb_config_cdcacm_t cdc = {.callback_rx = received, .callback_line_coding_changed = coding_changed};
    ESP_ERROR_CHECK(tinyusb_cdcacm_init(&cdc));
    ESP_ERROR_CHECK(tinyusb_console_init(TINYUSB_CDC_ACM_0));
    vTaskDelay(pdMS_TO_TICKS(3000));
    printf("DBG BOOT board=esp-mosaico console=tinyusb-cdc reset_reason=%d\n", esp_reset_reason());
    fflush(stdout);
}
