/* SPDX-License-Identifier: MIT
 * USB-OTG console uses the same public API/profile as OpenFrameTap Mosaico.
 * A deliberate 1200-baud touch requests volatile ROM download, not an eFuse
 * or bootloader modification. Recovery remains available before HAL starts. */
#include <stdio.h>
#include <stdbool.h>
#include <stdatomic.h>
#include <esp_system.h>
#include <esp_err.h>
#include <esp_sleep.h>
#include <sdkconfig.h>
#include <soc/soc_caps.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <tinyusb.h>
#include <tinyusb_default_config.h>
#include <tinyusb_cdc_acm.h>
#include <tinyusb_console.h>
#include <soc/lp_system_reg.h>
#include "usb_console.h"
#if CONFIG_IDF_TARGET_ESP32S31 && SOC_PM_SUPPORT_USB_WAKEUP && SOC_PM_SUPPORT_CNNT_PD
#include <esp_private/usb_phy.h>
#endif
extern void mosaico_idle_usb_notify(void); // Existing app_main owner, task context.
static atomic_bool wakeup_ready;
static atomic_int wakeup_error;
static atomic_uint mounts, unmounts, suspends, resumes, rx_events;
// Callback generation is never cleared by the service: events racing an arm
// remain pending and fail the sleep gate closed. Only app_main owns PHY calls.
static atomic_uint bridge_requested = 1, bridge_serviced;
static atomic_bool phy_hint_requested, cnnt_on_configured;
static atomic_uint hint_arms, wake_clears;
static atomic_int cnnt_error;
static void bridge_event(void)
{
    atomic_fetch_add(&bridge_requested, 1);
    mosaico_idle_usb_notify();
}
void mosaico_console_usb_service_sleep(void)
{
#if CONFIG_IDF_TARGET_ESP32S31 && SOC_PM_SUPPORT_USB_WAKEUP && SOC_PM_SUPPORT_CNNT_PD
    if (!atomic_load(&wakeup_ready)) return;
    const unsigned generation = atomic_load(&bridge_requested);
    // Also clear a previous idle arm on every main-loop return (including
    // non-USB wake). This is an LP wake hint, not TinyUSB suspend state.
    usb_phy_set_otg_suspend_state(false);
    atomic_store(&phy_hint_requested, false);
    usb_phy_clear_otg_wakeup_status();
    atomic_fetch_add(&wake_clears, 1);
    if (!((tud_mounted() || tud_connected()) && !tud_suspended()) &&
        generation == atomic_load(&bridge_requested)) {
        usb_phy_set_otg_suspend_state(true);
        atomic_fetch_add(&hint_arms, 1);
        atomic_store(&phy_hint_requested, true);
        // Roll back if an event or active bus appeared during the arm.
        if (generation != atomic_load(&bridge_requested) ||
            ((tud_mounted() || tud_connected()) && !tud_suspended())) {
            usb_phy_set_otg_suspend_state(false);
            atomic_store(&phy_hint_requested, false);
            usb_phy_clear_otg_wakeup_status();
            atomic_fetch_add(&wake_clears, 1);
            return;
        }
    }
    atomic_store(&bridge_serviced, generation);
#endif
}
static void suspended_event(void) { atomic_fetch_add(&suspends, 1); bridge_event(); }
static void resumed_event(void)
{
    atomic_fetch_add(&resumes, 1);
    bridge_event();
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
        bridge_event();
        break;
    case TINYUSB_EVENT_DETACHED: atomic_fetch_add(&unmounts, 1); bridge_event(); break;
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
    bridge_event();
}
mosaico_usb_snapshot_t mosaico_console_usb_snapshot(void)
{
    mosaico_usb_snapshot_t s = {0};
    s.mounted = tud_mounted(); s.connected = tud_connected(); s.suspended = tud_suspended();
    s.effective_active = s.mounted && !s.suspended;
    s.service_active = (s.mounted || s.connected) && !s.suspended;
    s.bridge_pending = atomic_load(&bridge_requested) != atomic_load(&bridge_serviced);
    s.phy_hint_requested = atomic_load(&phy_hint_requested);
    s.cnnt_on_configured = atomic_load(&cnnt_on_configured);
    s.cnnt_error = atomic_load(&cnnt_error);
    s.hint_arms = atomic_load(&hint_arms); s.wake_clears = atomic_load(&wake_clears);
    s.wakeup_ready = atomic_load(&wakeup_ready);
    s.wakeup_error = atomic_load(&wakeup_error);
    // Fail closed until boot-scoped public wake setup succeeds, even detached.
    // Enumeration traffic precedes mount; connected is not power proof.
    s.sleep_safe = s.wakeup_ready && s.phy_hint_requested && !s.bridge_pending && !s.service_active;
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
    } else {
        mosaico_idle_usb_notify();
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
// Keep host enumeration time in a fault-reset loop so 1200-baud recovery
// remains usable even if the next HAL operation immediately panics again.
static bool boot_recovery_grace_required(esp_reset_reason_t reason)
{
    return reason != ESP_RST_POWERON && reason != ESP_RST_SW;
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
    // S31 USB peripheral wake is public and boot-scoped. Do not reinitialize
    // TinyUSB or claim that host remote-wakeup/VBUS proves external power.
    esp_err_t wake_err = ESP_ERR_NOT_SUPPORTED;
#if CONFIG_IDF_TARGET_ESP32S31 && SOC_PM_SUPPORT_USB_WAKEUP && SOC_PM_SUPPORT_CNNT_PD
    wake_err = esp_sleep_enable_usb_wakeup();
    const esp_err_t pd_err = esp_sleep_pd_config(ESP_PD_DOMAIN_CNNT, ESP_PD_OPTION_ON);
    atomic_store(&cnnt_error, pd_err);
    atomic_store(&cnnt_on_configured, pd_err == ESP_OK);
    if (wake_err == ESP_OK) wake_err = pd_err;
#else
    atomic_store(&cnnt_error, ESP_ERR_NOT_SUPPORTED);
#endif
    atomic_store(&wakeup_error, wake_err);
    atomic_store(&wakeup_ready, wake_err == ESP_OK);
    const esp_reset_reason_t reset_reason = esp_reset_reason();
    if (boot_recovery_grace_required(reset_reason))
        vTaskDelay(pdMS_TO_TICKS(3000));
    printf("DBG BOOT board=esp-mosaico console=tinyusb-cdc reset_reason=%d recovery_grace_ms=%u\n",
           reset_reason, boot_recovery_grace_required(reset_reason) ? 3000U : 0U);
    fflush(stdout);
}
