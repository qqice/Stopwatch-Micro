// Local transport binding. Keep vendor source byte-identical and macros TU-local.
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include "esp_timer.h"
#include "i2c_bus.h"
#include "driver/i2c_master.h"
// Serialized by the caller's gauge transaction lock; no persistence or replay.
static unsigned mosaico_bq_attempts;
// Enabled only inside the cold reset helper. Vendor security APIs ignore key
// write errors; stop further writes after the first unknown transport ACK.
static bool mosaico_bq_track_writes;
static esp_err_t mosaico_bq_first_error;
void mosaico_bq_reset_attempts(void) { mosaico_bq_attempts = 0; }
unsigned mosaico_bq_get_attempts(void) { return mosaico_bq_attempts; }
// Managed handles are void*. Here they exclusively hold a non-owning HAL device
// token, never a managed bus. Deletion must not release the HAL-owned device.
static i2c_bus_device_handle_t mosaico_bq_device_create(i2c_bus_handle_t token, uint8_t addr, uint32_t speed)
{
    (void)speed;
    return token && addr == 0x55 ? token : NULL;
}
static esp_err_t mosaico_bq_device_delete(i2c_bus_device_handle_t *token)
{
    if (!token || !*token) return ESP_ERR_INVALID_ARG;
    *token = NULL;
    return ESP_OK;
}
static esp_err_t mosaico_bq_read(i2c_bus_device_handle_t token, uint8_t reg, size_t len, uint8_t *data)
{
    if (!token || !data || !len) return ESP_ERR_INVALID_ARG;
    if (len > 34) return ESP_ERR_INVALID_SIZE;
    return i2c_master_transmit_receive((i2c_master_dev_handle_t)token, &reg, 1, data, len, 100);
}
static esp_err_t mosaico_bq_write(i2c_bus_device_handle_t token, uint8_t reg, size_t len, const uint8_t *data)
{
    if (!token || !data || !len) return ESP_ERR_INVALID_ARG;
    if (len > 34) return ESP_ERR_INVALID_SIZE;
    if (mosaico_bq_track_writes && mosaico_bq_first_error != ESP_OK) return mosaico_bq_first_error;
    uint8_t packet[35];
    packet[0] = reg;
    memcpy(packet + 1, data, len);
    // Record intent before submitting a cleanup packet, even if ACK is unknown.
    if (reg == 0x00 && len == 2 && data[1] == 0x00) {
        if (data[0] == 0x91 || data[0] == 0x92) mosaico_bq_attempts |= 1;
        if (data[0] == 0x30) mosaico_bq_attempts |= 2;
    }
    const esp_err_t ret = i2c_master_transmit((i2c_master_dev_handle_t)token, packet, len + 1, 100);
    if (mosaico_bq_track_writes && ret != ESP_OK) mosaico_bq_first_error = ret;
    return ret;
}
#ifndef CONFIG_BQ27220_SEAL_SETTLE_MS
#define CONFIG_BQ27220_SEAL_SETTLE_MS 2000
#endif
#define i2c_bus_device_create mosaico_bq_device_create
#define i2c_bus_device_delete mosaico_bq_device_delete
#define i2c_bus_read_bytes mosaico_bq_read
#define i2c_bus_write_bytes mosaico_bq_write
#include "vendor/bq27220/bq27220.c"
#undef i2c_bus_device_create
#undef i2c_bus_device_delete
#undef i2c_bus_read_bytes
#undef i2c_bus_write_bytes

// Cold factory initialization only. This stack view borrows the HAL device;
// it must never be passed to delete. Vendor internals stay in their owning TU.
bool mosaico_bq_boot_factory_reset(i2c_master_dev_handle_t device, uint16_t capacity, bool bootFactoryReset)
{
    if (!bootFactoryReset || capacity != 65) return true;
    bq27220_data_t data = {.i2c_device_handle = (i2c_bus_device_handle_t)device};
    bq27220_handle_t handle = &data;
    uint16_t dc = 0, fcc = 0, raw = 0;
    if (bq27220_read_u16_checked(handle, COMMAND_DESIGN_CAPACITY, &dc) != ESP_OK ||
        bq27220_read_u16_checked(handle, COMMAND_FULL_CHARGE_CAPACITY, &fcc) != ESP_OK) return false;
    if (dc != 3000 || fcc != 3000) return true;
    if (bq27220_read_u16_checked(handle, COMMAND_OPERATION_STATUS, &raw) != ESP_OK ||
        (raw & 0x0401) || !(raw & 0x0020) || ((raw >> 1) & 3) < 2) return false;
    bool ok = false;
    mosaico_bq_track_writes = true;
    mosaico_bq_first_error = ESP_OK;
    // Vendor unseal uses its existing keys. Independently checked status is
    // mandatory: its public status accessor does not propagate read errors.
    if (bq27220_unseal(handle) == ESP_OK && mosaico_bq_first_error == ESP_OK &&
        bq27220_read_u16_checked(handle, COMMAND_OPERATION_STATUS, &raw) == ESP_OK &&
        ((raw >> 1) & 3) == OPERATION_STATUS_SEC_UNSEALED &&
        !(raw & 0x0401) && (raw & 0x0020)) {
        mosaico_bq_attempts |= 4; // Intent BEFORE transport, including unknown ACK.
        ESP_LOGI(TAG, "COLD_FACTORY_RESET_V1 intent=1 reset=0041 dc=%u fcc=%u op=%04x", dc, fcc, raw);
        ESP_LOGW(TAG, "COLD_FACTORY_RESET_V1_SUBMITTED");
        const int64_t deadline = esp_timer_get_time() + 4000000;
        const esp_err_t sent = bq27220_control(handle, CONTROL_RESET);
        // RESET can initially expose stale INITCOMP=1. Give the chip its
        // conservative 2 s reset settling window before accepting that bit.
        vTaskDelay(pdMS_TO_TICKS(2000) + 1);
        // Reads only after the single RESET submission. Never resend on timeout
        // or unknown ACK. Deadline includes transport time (+ one IO margin).
        do {
            if (bq27220_read_u16_checked(handle, COMMAND_OPERATION_STATUS, &raw) == ESP_OK &&
                (raw & 0x0020) && !(raw & 0x0401) && ((raw >> 1) & 3) >= 2) {
                ok = sent == ESP_OK &&
                    bq27220_read_u16_checked(handle, COMMAND_DESIGN_CAPACITY, &dc) == ESP_OK &&
                    bq27220_read_u16_checked(handle, COMMAND_FULL_CHARGE_CAPACITY, &fcc) == ESP_OK &&
                    dc == 3000 && fcc == 3000;
                break;
            }
            if (esp_timer_get_time() + 20000 >= deadline) break;
            delay_ms(20);
        } while (esp_timer_get_time() < deadline);
    }
    const esp_err_t primaryError = mosaico_bq_first_error;
    // Cleanup gets an independent ACK budget, never replays any keys/RESET.
    // Check status before calling the vendor API (its own accessor is lossy).
    mosaico_bq_first_error = ESP_OK;
    bool sealed = false;
    if (bq27220_read_u16_checked(handle, COMMAND_OPERATION_STATUS, &raw) == ESP_OK && !(raw & 0x0401)) {
        const unsigned sec = (raw >> 1) & 3;
        if (sec == OPERATION_STATUS_SEC_SEALED) sealed = true;
        else if (sec == OPERATION_STATUS_SEC_UNSEALED || sec == OPERATION_STATUS_SEC_FULL) {
            sealed = bq27220_seal(handle) == ESP_OK && mosaico_bq_first_error == ESP_OK &&
                bq27220_read_u16_checked(handle, COMMAND_OPERATION_STATUS, &raw) == ESP_OK &&
                ((raw >> 1) & 3) == OPERATION_STATUS_SEC_SEALED && !(raw & 0x0401);
        }
    }
    mosaico_bq_track_writes = false;
    ESP_LOGI(TAG, "COLD_FACTORY_RESET_V1 result=%u sealed=%u reset_try=%u dc=%u fcc=%u op=%04x primary_err=%d cleanup_err=%d",
             ok && sealed && primaryError == ESP_OK, sealed, !!(mosaico_bq_attempts & 4), dc, fcc, raw,
             primaryError, mosaico_bq_first_error);
    if (ok && sealed && primaryError == ESP_OK) ESP_LOGW(TAG, "COLD_FACTORY_RESET_V1_VERIFIED");
    else ESP_LOGE(TAG, "COLD_FACTORY_RESET_V1_FAILED");
    return ok && sealed && primaryError == ESP_OK;
}
