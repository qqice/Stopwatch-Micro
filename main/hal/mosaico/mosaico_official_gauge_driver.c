// Local transport binding. Keep vendor source byte-identical and macros TU-local.
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include "i2c_bus.h"
#include "driver/i2c_master.h"
// Serialized by the caller's gauge transaction lock; no persistence or replay.
static unsigned mosaico_bq_attempts;
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
    uint8_t packet[35];
    packet[0] = reg;
    memcpy(packet + 1, data, len);
    // Record intent before submitting a cleanup packet, even if ACK is unknown.
    if (reg == 0x00 && len == 2 && data[1] == 0x00) {
        if (data[0] == 0x91 || data[0] == 0x92) mosaico_bq_attempts |= 1;
        if (data[0] == 0x30) mosaico_bq_attempts |= 2;
    }
    return i2c_master_transmit((i2c_master_dev_handle_t)token, packet, len + 1, 100);
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
