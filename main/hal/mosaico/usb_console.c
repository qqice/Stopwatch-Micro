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
    const tinyusb_config_t usb = TINYUSB_DEFAULT_CONFIG();
    ESP_ERROR_CHECK(tinyusb_driver_install(&usb));
    const tinyusb_config_cdcacm_t cdc = {.callback_line_coding_changed = coding_changed};
    ESP_ERROR_CHECK(tinyusb_cdcacm_init(&cdc));
    ESP_ERROR_CHECK(tinyusb_console_init(TINYUSB_CDC_ACM_0));
    vTaskDelay(pdMS_TO_TICKS(3000));
    printf("DBG BOOT board=esp-mosaico console=tinyusb-cdc reset_reason=%d\n", esp_reset_reason());
    fflush(stdout);
}
