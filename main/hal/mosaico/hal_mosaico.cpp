/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 * SPDX-FileCopyrightText: 2026 qqice and OpenFrameTap Mosaico contributors
 * SPDX-License-Identifier: MIT
 *
 * Board wiring and stock CO5300/CST9217 setup adapted from
 * OpenFrameTap-Mosaico/firmware/esp_mosaico/main/oft_board.c (MIT),
 * referencing espressif/esp_boards 0.6.1 ESP-Mosaico V1.0.
 * Nominal-capacity transaction references oft_gauge.c and TI SLUUBD4A;
 * no virtual-capacity/gain/EDV correction, camera, charger or NAND functionality.
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 */
#include "../hal.h"
#include "../utils/settings/settings.h"
#include "mosaico_gauge_model.h"
#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "driver/spi_master.h"
#include "esp_lcd_co5300.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_touch_cst9217.h"
#include "esp_lvgl_port.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"
#include "esp_clk_tree.h"
#include "esp_cache.h"
#include "esp_heap_caps.h"
#include "esp_task_wdt.h"
#include "esp_mac.h"
#include "nvs.h"
#if CONFIG_IDF_TARGET_ESP32S31
#include "hal/clk_tree_ll.h"
#endif
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <algorithm>
#include <atomic>
#include <mutex>

namespace {
constexpr char Tag[] = "HAL-Mosaico";
constexpr int Resolution = 480;
// Matches the current CodexMicroView LockedBrightness without changing the
// shared view or the S3 backend's brightness behavior.
constexpr int WakeDimThreshold = 8;
esp_lcd_panel_io_handle_t panel_io = nullptr;
esp_lcd_panel_handle_t panel = nullptr;
esp_lcd_touch_handle_t touch = nullptr;
lv_display_t* display = nullptr;
bool port_ready = false;
// Serialized by the LVGL port mutex once it exists. Keep physical brightness
// separate from the requested setting while a wake redraw is pending.
int panel_brightness = -1;
int pending_wake_brightness = -1;
i2c_master_dev_handle_t gauge = nullptr;
std::mutex battery_mutex;
bool battery_valid = false;
uint8_t battery_soc = 0;
uint16_t battery_mv = 0;
int16_t battery_raw_ma = 0;
int64_t battery_sample_us = 0;
Hal::BatteryTelemetry battery_telemetry;
portMUX_TYPE touch_mux = portMUX_INITIALIZER_UNLOCKED;
Hal::TouchPoint cached_touch;
std::atomic<uint32_t> frames{0}, refresh_calls{0}, refresh_max_us{0};
std::atomic<uint32_t> touch_reads{0}, touch_max_gap_us{0};
std::atomic<int64_t> touch_last_us{0}, refresh_start_us{0};
std::atomic<int8_t> lvgl_core{-1};

void update_max(std::atomic<uint32_t>& maximum, uint32_t value)
{
    uint32_t previous = maximum.load(std::memory_order_relaxed);
    while (value > previous && !maximum.compare_exchange_weak(previous, value, std::memory_order_relaxed)) {}
}

void round_area(lv_area_t* area)
{
    // CO5300 requires even starts and even dimensions. Round BEFORE drawing;
    // the LVGL 9 port also uses this hook when splitting partial-buffer strips.
    area->x1 &= ~1;
    area->y1 &= ~1;
    area->x2 |= 1;
    area->y2 |= 1;
}

void observe_refresh(lv_event_t* event)
{
    if (lv_event_get_code(event) == LV_EVENT_REFR_START) {
        refresh_start_us.store(esp_timer_get_time(), std::memory_order_relaxed);
        refresh_calls.fetch_add(1, std::memory_order_relaxed);
        lvgl_core.store(static_cast<int8_t>(xPortGetCoreID()), std::memory_order_relaxed);
    } else if (lv_event_get_code(event) == LV_EVENT_REFR_READY) {
        const int64_t start = refresh_start_us.exchange(0, std::memory_order_relaxed);
        if (start) update_max(refresh_max_us, static_cast<uint32_t>(esp_timer_get_time() - start));
        if (pending_wake_brightness >= 0 && panel) {
            // REFR_READY alone only proves that the refresh was submitted.
            // CO5300 brightness uses SPI tx_param, which drains queued color
            // DMA transactions before sending the command: redraw before light.
            const int brightness = pending_wake_brightness;
            pending_wake_brightness = -1;
            ESP_ERROR_CHECK(esp_lcd_panel_co5300_set_brightness(panel, brightness));
            panel_brightness = brightness;
            ESP_LOGI(Tag, "Wake redraw completed; brightness=%d", brightness);
        }
    }
}

void observe_flush(lv_event_t* event)
{
    auto* disp = static_cast<lv_display_t*>(lv_event_get_target(event));
    // Count submitted final chunks, not individual partial-buffer transfers.
    // LV_EVENT_FLUSH_FINISH means flush_cb returned, not DMA/physical completion.
    if (lv_display_flush_is_last(disp)) frames.fetch_add(1, std::memory_order_relaxed);
}

void read_touch(lv_indev_t*, lv_indev_data_t* data)
{
    const int64_t now = esp_timer_get_time();
    const int64_t previous = touch_last_us.exchange(now, std::memory_order_relaxed);
    touch_reads.fetch_add(1, std::memory_order_relaxed);
    if (previous > 0 && now > previous) update_max(touch_max_gap_us, static_cast<uint32_t>(now - previous));
    Hal::TouchPoint point;
    uint16_t x = 0, y = 0;
    uint8_t count = 0;
    if (touch && esp_lcd_touch_read_data(touch) == ESP_OK &&
        esp_lcd_touch_get_coordinates(touch, &x, &y, nullptr, &count, 1) && count &&
        x < Resolution && y < Resolution) {
        point = {1, static_cast<int>(x), static_cast<int>(y)};
    }
    portENTER_CRITICAL(&touch_mux);
    cached_touch = point;
    portEXIT_CRITICAL(&touch_mux);
    data->state = point.num ? LV_INDEV_STATE_PRESSED : LV_INDEV_STATE_RELEASED;
    if (point.num) {
        data->point.x = point.x;
        data->point.y = point.y;
    }
}

void gauge_feed()
{
    // Only feed the calling task if it is subscribed; never disable the WDT.
    if (esp_task_wdt_status(nullptr) == ESP_OK) esp_task_wdt_reset();
}

void gauge_delay_ms(uint32_t ms)
{
    // CFG transitions need >=2 s. Split waits to service the watchdog and idle
    // task instead of one long sleep, with strictly bounded total waits.
    while (ms) {
        const uint32_t chunk = std::min<uint32_t>(ms, 100);
        gauge_feed();
        vTaskDelay(pdMS_TO_TICKS(chunk) + 1);
        ms -= chunk;
    }
    gauge_feed();
}

bool gauge_read(uint8_t reg, uint8_t* bytes, size_t size)
{
    gauge_feed();
    const esp_err_t result = i2c_master_transmit_receive(gauge, &reg, 1, bytes, size, 20);
    esp_rom_delay_us(100); // TI bus-free timing, inherited from board reference.
    gauge_feed();
    return result == ESP_OK;
}

bool gauge_word(uint8_t reg, uint16_t& value)
{
    uint8_t bytes[2];
    const bool result = gauge_read(reg, bytes, sizeof(bytes));
    if (!result) return false;
    value = bytes[0] | (static_cast<uint16_t>(bytes[1]) << 8);
    return true;
}

void sample_battery_locked(bool refresh = false)
{
    const int64_t now = esp_timer_get_time();
    if (!gauge || (!refresh && battery_sample_us && now - battery_sample_us < 5000000)) return;
    battery_sample_us = now;
    // TI SLUUBD4A standard read-word commands. Always read all fields, so a
    // failed capacity/status read cannot leave apparently fresh cached values.
    Hal::BatteryTelemetry sample;
    uint16_t soc = 0, current = 0, average = 0;
    bool core = gauge_word(0x2c, soc);
    core &= gauge_word(0x08, sample.voltageMv);
    core &= gauge_word(0x0c, current);
    core &= gauge_word(0x14, average);
    bool capacities = gauge_word(0x10, sample.remainingMah);
    capacities &= gauge_word(0x12, sample.fullMah);
    capacities &= gauge_word(0x3c, sample.designMah);
    bool status = gauge_word(0x3a, sample.operationStatus);
    status &= gauge_word(0x0a, sample.batteryStatus);
    status &= gauge_word(0x2e, sample.stateOfHealth);
    status &= gauge_word(0x2a, sample.cycleCount);
    sample.reportedSoc = static_cast<uint8_t>(soc);
    sample.currentMa = static_cast<int16_t>(current);
    sample.averageCurrentMa = static_cast<int16_t>(average);
    sample.valid = core && status && soc <= 100 && sample.voltageMv >= 2000 && sample.voltageMv <= 5000;
    sample.capacityValid = capacities && sample.designMah > 0 && sample.fullMah > 0 &&
                           sample.remainingMah <= sample.fullMah;
    // Conservative nominal consistency only: 65 physical mAh, sane capacity
    // registers and valid raw SOC. Never infer learned FCC or pack accuracy.
    sample.nominalConfigured = sample.valid && sample.capacityValid && sample.designMah == 65 &&
                               sample.fullMah <= mosaico_gauge::MaxReasonableFcc;
    battery_telemetry = sample;
    battery_valid = sample.valid;
    if (battery_valid) {
        battery_soc = sample.reportedSoc;
        battery_mv = sample.voltageMv;
        battery_raw_ma = sample.currentMa;
    }
}

enum class GaugeCommand : uint16_t { Identity = 0x0001, EnterConfig = 0x0090, ExitReinit = 0x0091 };

bool gauge_control(GaugeCommand command)
{
    // Closed command set: no seal/unseal, security keys, ROM, OTP, calibration,
    // charger controls or arbitrary command interface.
    if (command != GaugeCommand::Identity && command != GaugeCommand::EnterConfig &&
        command != GaugeCommand::ExitReinit) return false;
    const uint16_t value = static_cast<uint16_t>(command);
    const uint8_t packet[] = {0, static_cast<uint8_t>(value), static_cast<uint8_t>(value >> 8)};
    gauge_feed();
    const bool ok = i2c_master_transmit(gauge, packet, sizeof(packet), 20) == ESP_OK;
    esp_rom_delay_us(100);
    gauge_feed();
    return ok;
}

bool gauge_mac_read(uint16_t selector, uint8_t block[36])
{
    using mosaico_gauge::Field;
    if (selector != static_cast<uint16_t>(Field::Design) && selector != static_cast<uint16_t>(Field::InitialFcc))
        return false;
    const uint8_t packet[] = {0x3E, static_cast<uint8_t>(selector), static_cast<uint8_t>(selector >> 8)};
    gauge_feed();
    if (i2c_master_transmit(gauge, packet, sizeof(packet), 20) != ESP_OK) return false;
    gauge_delay_ms(20);
    if (!gauge_read(0x3E, block, 2)) return false;
    // Match the board reference's word-sized reads of 40..61 rather than
    // assuming a 36-byte auto-increment starting at an unreadable selector.
    for (unsigned i = 0; i < 34; i += 2) {
        if (!gauge_read(static_cast<uint8_t>(0x40 + i), block + 2 + i, 2)) return false;
    }
    return mosaico_gauge::authenticateDmResponse(block, selector);
}

bool gauge_identity()
{
    // TI DEVICE_NUMBER returns 0220 on MACData, not the control-status word.
    // Only the harmless identity selector is sent; never firmware/security keys.
    if (!gauge_control(GaugeCommand::Identity)) return false;
    gauge_delay_ms(20);
    for (unsigned i = 0; i < 2; ++i) {
        uint16_t identity = 0;
        if (!gauge_word(0x40, identity) || identity != mosaico_gauge::DeviceType) return false;
    }
    return true;
}

struct GaugePair { uint16_t design = 0; uint16_t fcc = 0; };

bool gauge_mac_word(mosaico_gauge::Field field, uint16_t& value)
{
    uint8_t block[36]{};
    if (!gauge_mac_read(static_cast<uint16_t>(field), block)) return false;
    value = (static_cast<uint16_t>(block[2]) << 8) | block[3]; // DM U2 is big-endian.
    return true;
}

bool gauge_pair(GaugePair& pair)
{
    bool ok = gauge_mac_word(mosaico_gauge::Field::Design, pair.design);
    ok &= gauge_mac_word(mosaico_gauge::Field::InitialFcc, pair.fcc);
    return ok;
}

bool same_pair(const GaugePair& a, const GaugePair& b) { return a.design == b.design && a.fcc == b.fcc; }

bool gauge_standard_matches(const GaugePair& pair)
{
    uint16_t dc = 0, fcc = 0;
    bool ok = gauge_word(0x3C, dc);
    ok &= gauge_word(0x12, fcc);
    return ok && dc == pair.design && fcc == pair.fcc;
}

bool gauge_quiet_full(uint16_t& operation, bool ownedUnresolvedRestore = false)
{
    uint16_t soc = 0, mv = 0, temperature = 0, current = 0, average = 0;
    bool ok = gauge_word(0x3A, operation);
    ok &= gauge_word(0x2C, soc);
    ok &= gauge_word(0x08, mv);
    ok &= gauge_word(0x06, temperature);
    ok &= gauge_word(0x0C, current);
    ok &= gauge_word(0x14, average);
    return ok && (ownedUnresolvedRestore ?
        mosaico_gauge::quietFullRestore(operation, soc, mv, temperature, static_cast<int16_t>(current),
                                       static_cast<int16_t>(average), true) :
        mosaico_gauge::quietFull(operation, soc, mv, temperature, static_cast<int16_t>(current),
                                static_cast<int16_t>(average)));
}

bool gauge_wait_config(bool enter)
{
    gauge_delay_ms(2000); // TI: wait at least 2 seconds before reading CFGUPDATE.
    for (unsigned attempt = 0; attempt < 10; ++attempt) {
        uint16_t operation = 0;
        if (gauge_word(0x3A, operation)) {
            if (((operation >> 1) & 3) != 1 || (operation & 1)) return false;
            if (!!(operation & 0x0400) == enter) return true;
        }
        gauge_delay_ms(100);
    }
    return false;
}

bool gauge_exit_config()
{
    uint16_t operation = 0;
    if (!gauge_word(0x3A, operation) || ((operation >> 1) & 3) != 1 || (operation & 1)) return false;
    if (!(operation & 0x0400)) return true;
    // Reinitialize the nominal capacity registers using the documented CFG
    // exit command; do not reset hardware, change security, or program OTP.
    const bool sent = gauge_control(GaugeCommand::ExitReinit);
    const bool exited = gauge_wait_config(false);
    return sent && exited;
}

bool gauge_write_field(const mosaico_gauge::Journal& journal, mosaico_gauge::Field field, uint16_t value)
{
    if (!mosaico_gauge::allowedValue(journal, field, value)) return false;
    uint16_t operation = 0;
    if (!gauge_word(0x3A, operation) || ((operation >> 1) & 3) != 1 || !(operation & 0x0400) || (operation & 1))
        return false;
    const uint16_t address = static_cast<uint16_t>(field);
    const uint8_t packet[] = {0x3E, static_cast<uint8_t>(address), static_cast<uint8_t>(address >> 8),
                             static_cast<uint8_t>(value >> 8), static_cast<uint8_t>(value)};
    gauge_feed();
    if (i2c_master_transmit(gauge, packet, sizeof(packet), 20) != ESP_OK) return false;
    gauge_delay_ms(1); // >=250 us MAC processing time, not merely I2C bus-free.
    const uint8_t commit[] = {0x60, mosaico_gauge::macChecksum(packet + 1, 4), 6};
    if (i2c_master_transmit(gauge, commit, sizeof(commit), 20) != ESP_OK) return false;
    gauge_delay_ms(20);
    // One parameter write attempt only, then two independent authenticated reads.
    for (unsigned i = 0; i < 2; ++i) {
        uint16_t readback = 0;
        if (!gauge_mac_word(field, readback) || readback != value) return false;
    }
    return true;
}

bool gauge_verify_pair(const GaugePair& desired, bool standard)
{
    for (unsigned i = 0; i < 2; ++i) {
        GaugePair actual;
        if (!gauge_pair(actual) || !same_pair(actual, desired) || (standard && !gauge_standard_matches(desired)))
            return false;
    }
    return true;
}

esp_err_t gauge_load_journal(mosaico_gauge::Journal& journal, const uint8_t mac[6])
{
    nvs_handle_t handle = 0;
    esp_err_t result = nvs_open("gaugecal", NVS_READONLY, &handle);
    if (result != ESP_OK) return result;
    size_t length = sizeof(journal);
    result = nvs_get_blob(handle, "tx_v1", &journal, &length);
    nvs_close(handle);
    if (result == ESP_OK && (length != sizeof(journal) || !mosaico_gauge::validJournal(journal, mac))) return ESP_FAIL;
    return result;
}

bool gauge_store_journal(mosaico_gauge::Journal& journal, const uint8_t mac[6])
{
    mosaico_gauge::seal(journal);
    if (!mosaico_gauge::validJournal(journal, mac)) return false;
    nvs_handle_t handle = 0;
    gauge_feed();
    if (nvs_open("gaugecal", NVS_READWRITE, &handle) != ESP_OK) return false;
    esp_err_t result = nvs_set_blob(handle, "tx_v1", &journal, sizeof(journal));
    if (result == ESP_OK) result = nvs_commit(handle);
    nvs_close(handle);
    gauge_feed();
    // Reopen for readback only AFTER successful flash commit. Never erase NVS.
    mosaico_gauge::Journal readback{};
    return result == ESP_OK && gauge_load_journal(readback, mac) == ESP_OK &&
           std::memcmp(&journal, &readback, sizeof(journal)) == 0;
}

bool gauge_rollback(const mosaico_gauge::Journal& journal, const GaugePair& before, bool touchedDc, bool touchedFcc)
{
    bool ok = true;
    if (touchedDc) ok &= gauge_write_field(journal, mosaico_gauge::Field::Design, before.design);
    if (touchedFcc) ok &= gauge_write_field(journal, mosaico_gauge::Field::InitialFcc, before.fcc);
    return ok && gauge_verify_pair(before, false);
}
} // namespace

void Hal::i2c_init()
{
    // Active-low peripheral rail only. Never touch whole-device power GPIO57,
    // boot straps, charger, audio-enable or NAND/expansion pins.
    gpio_config_t rail{};
    rail.pin_bit_mask = 1ULL << 60;
    rail.mode = GPIO_MODE_OUTPUT;
    ESP_ERROR_CHECK(gpio_config(&rail));
    ESP_ERROR_CHECK(gpio_set_level(GPIO_NUM_60, 0));
    delay(100);
    i2c_master_bus_config_t config{};
    config.i2c_port = I2C_NUM_0;
    config.sda_io_num = GPIO_NUM_0;
    config.scl_io_num = GPIO_NUM_1;
    config.clk_source = I2C_CLK_SRC_DEFAULT;
    config.glitch_ignore_cnt = 7;
    config.flags.enable_internal_pullup = true;
    ESP_ERROR_CHECK(i2c_new_master_bus(&config, &_i2c_bus));
}

void Hal::i2c_detect()
{
    // No broad probe scan: enumerate only devices actually needed by this HAL.
    ESP_LOGI(Tag, "I2C0 SDA=0 SCL=1; touch and read-only BQ27220 only");
}

void Hal::pmic_init()
{
    i2c_device_config_t config{};
    config.dev_addr_length = I2C_ADDR_BIT_LEN_7;
    config.device_address = 0x55;
    config.scl_speed_hz = 100000;
    if (i2c_master_bus_add_device(_i2c_bus, &config, &gauge) != ESP_OK) {
        ESP_LOGW(Tag, "BQ27220 unavailable; battery telemetry disabled");
        return;
    }
    std::lock_guard<std::mutex> lock(battery_mutex);
    sample_battery_locked();
    if (battery_valid) {
        ESP_LOGI(Tag, "BQ27220 read-only: SOC=%u voltage=%u mV raw current=%d mA (no scaling/correction)",
                 battery_soc, battery_mv, battery_raw_ma);
    } else ESP_LOGW(Tag, "BQ27220 measurements unavailable/invalid");
}

bool Hal::pmic_ready() const
{
    std::lock_guard<std::mutex> lock(battery_mutex);
    return gauge && battery_valid;
}

uint8_t Hal::getBatteryLevel()
{
    std::lock_guard<std::mutex> lock(battery_mutex);
    sample_battery_locked();
    return battery_valid ? battery_soc : 0;
}

bool Hal::isBatteryLevelValid() const
{
    std::lock_guard<std::mutex> lock(battery_mutex);
    return gauge && battery_valid;
}

bool Hal::isBatteryCharging(bool strict)
{
    (void)strict;
    std::lock_guard<std::mutex> lock(battery_mutex);
    sample_battery_locked();
    // Only positive gauge current is observable here. This does NOT identify
    // external power (e.g. USB while full); no charger pins/config are touched.
    return battery_valid && battery_raw_ma > 3;
}

Hal::BatteryTelemetry Hal::batteryTelemetry(bool refresh)
{
    std::lock_guard<std::mutex> lock(battery_mutex);
    sample_battery_locked(refresh);
    return battery_telemetry;
}

bool Hal::gaugeSetNominalCapacity(uint16_t expectedOld, uint16_t target65, bool restore,
                                 char* reason, size_t reasonSize)
{
    using namespace mosaico_gauge;
    std::lock_guard<std::mutex> lock(battery_mutex);
    const auto report = [&](bool success, const char* why) {
        if (reason && reasonSize) std::snprintf(reason, reasonSize, "%s", why);
        return success;
    };
    if (target65 != NominalMah) return report(false, "blocked_target_not_65");
    if (!selftest()) return report(false, "blocked_safety_selftest");
    if (!gauge) return report(false, "blocked_standard_telemetry");
    // Restore must validate its durable, board-bound backup BEFORE normal
    // standard-mirror/CFG gates. Those mirrors can legitimately lag mixed DM
    // writes when a previous transaction was interrupted inside CFGUPDATE.
    const auto mac = getFactoryMac();
    Journal journal{};
    const esp_err_t loaded = gauge_load_journal(journal, mac.data());
    const bool saved = loaded == ESP_OK;
    if (loaded != ESP_OK && loaded != ESP_ERR_NVS_NOT_FOUND) return report(false, "blocked_journal_crc_unit_identity");
    if (restore && !saved) return report(false, "blocked_restore_no_journal");
    sample_battery_locked(true);
    if (restore) {
        uint16_t operation = 0;
        if (!gauge_word(0x3A, operation)) return report(false, "blocked_restore_status_read");
        if (((operation >> 1) & 3) != 1) return report(false, "blocked_not_full_access");
        if (operation & 1) return report(false, "blocked_cal_active");
        const bool ownedUnresolved = recoveryState(journal.state);
        if ((operation & 0x0400) && !ownedUnresolved)
            return report(false, "blocked_cfg_without_unresolved_journal");
        if (!gauge_quiet_full(operation, ownedUnresolved)) return report(false, "blocked_quiet_full_temperature");
        if (!gauge_identity()) return report(false, "blocked_identity_0220");
        GaugePair before;
        if (!gauge_pair(before)) return report(false, "blocked_dm_mac_checksum_echo");
        if (!restorePairAllowed(journal, before.design, before.fcc))
            return report(false, "blocked_restore_old_target_pair");
        // For explicit restore expectedOld ALWAYS means observable DM_DC, not
        // the possibly stale standard mirror. Report both to permit a typed,
        // informed next call without guessing or a generic DM-read interface.
        if (before.design != expectedOld) {
            uint16_t stdDesign = 0;
            const bool stdRead = gauge_word(0x3C, stdDesign);
            if (reason && reasonSize) {
                if (stdRead) std::snprintf(reason, reasonSize,
                    "blocked_expected_dm observed_dm_design=%u std_design=%u", before.design, stdDesign);
                else std::snprintf(reason, reasonSize,
                    "blocked_expected_dm observed_dm_design=%u std_design=unreadable", before.design);
            }
            return false;
        }
        const GaugePair desired{journal.originalDesign, journal.changedFcc ? journal.originalFcc : before.fcc};
        journal.action = 2;
        // If no CFG is active and both actual DM + standard mirrors are already
        // OLD, resolve the journal without any parameter or control-mode write.
        if (!(operation & 0x0400) && same_pair(before, desired) && gauge_verify_pair(desired, true)) {
            journal.state = static_cast<uint8_t>(State::Restored);
            journal.lastDesign = desired.design; journal.lastFcc = desired.fcc;
            const bool recorded = gauge_store_journal(journal, mac.data());
            return report(recorded, recorded ? "already_restored" : "critical_final_journal_readback");
        }
        journal.state = static_cast<uint8_t>(State::Pending);
        journal.lastDesign = 0; journal.lastFcc = 0;
        // Keep the original non-CFG operationBefore: it proves the original
        // access state. No re-unseal/security transition occurs during recovery.
        if (!gauge_store_journal(journal, mac.data())) {
            // An owned existing CFG must still have a reachable safe exit when
            // a NEW recovery intent cannot be persisted. Do not write fields;
            // retain the previously durable backup and report exit-only failure.
            if (operation & 0x0400) {
                const bool exited = gauge_exit_config();
                sample_battery_locked(true);
                return report(false, exited ? "critical_recovery_journal_exit_only" : "critical_recovery_journal_cfg_active");
            }
            return report(false, "blocked_journal_prepare");
        }
        GaugePair recheck;
        if (!gauge_quiet_full(operation, ownedUnresolved) || !gauge_pair(recheck) || !same_pair(recheck, before)) {
            const bool exited = gauge_exit_config(); // exit only; no OLD/TARGET parameter rewrite.
            journal.state = static_cast<uint8_t>(exited ? State::RollbackFailed : State::ExitFailed);
            gauge_store_journal(journal, mac.data());
            sample_battery_locked(true);
            return report(false, exited ? "blocked_recovery_preflight_exit_only" : "critical_cfg_exit_or_security");
        }
        // Existing owned CFG skips ENTER entirely. If ENTER previously never
        // reached the device, this path can enter once now and restore OLD.
        bool entered = (operation & 0x0400) != 0;
        if (!entered) {
            const bool sent = gauge_control(GaugeCommand::EnterConfig);
            const bool observed = gauge_wait_config(true);
            entered = sent && observed;
        }
        const bool changeDc = before.design != desired.design;
        const bool changeFcc = journal.changedFcc && before.fcc != desired.fcc;
        // This branch is rollback-to-OLD only, never rollback-to-partial TARGET.
        // Each changed field gets at most one OLD write + two verified reads.
        bool restored = entered && gauge_verify_pair(before, false) &&
                        gauge_rollback(journal, desired, changeDc, changeFcc);
        const bool exited = gauge_exit_config(); // mandatory attempt even on read/write/enter failure.
        restored = restored && exited && gauge_verify_pair(desired, true);
        journal.state = static_cast<uint8_t>(!exited ? State::ExitFailed : restored ? State::Restored : State::RollbackFailed);
        if (restored) { journal.lastDesign = desired.design; journal.lastFcc = desired.fcc; }
        const bool recorded = gauge_store_journal(journal, mac.data());
        sample_battery_locked(true);
        if (!exited) return report(false, "critical_cfg_exit_or_security");
        if (!restored) return report(false, "critical_restore_old_readback");
        if (!recorded) return report(false, "critical_final_journal_readback");
        return report(true, ownedUnresolved ? "restored_interrupted_journal_old" : "restored_original_nominal");
    }

    // APPLY retains every original gate, including complete standard telemetry
    // and standard == DM consistency. Only RESTORE has the explicit exception.
    if (!gauge || !battery_telemetry.valid || !battery_telemetry.capacityValid)
        return report(false, "blocked_standard_telemetry");
    if (battery_telemetry.designMah != expectedOld) return report(false, "blocked_expected_old");
    if (((battery_telemetry.operationStatus >> 1) & 3) != 1) return report(false, "blocked_not_full_access");
    if (battery_telemetry.operationStatus & 0x0401) return report(false, "blocked_cfg_or_cal_active");
    uint16_t operation = 0;
    if (!gauge_quiet_full(operation)) return report(false, "blocked_quiet_full_temperature");
    if (!gauge_identity()) return report(false, "blocked_identity_0220");
    GaugePair before;
    if (!gauge_pair(before)) return report(false, "blocked_dm_mac_checksum_echo");
    if (before.design != expectedOld || !gauge_standard_matches(before))
        return report(false, "blocked_standard_dm_mismatch");

    if (!restore && saved && (journal.state == static_cast<uint8_t>(State::Pending) ||
        journal.state == static_cast<uint8_t>(State::RollbackFailed) || journal.state == static_cast<uint8_t>(State::ExitFailed)))
        return report(false, "blocked_pending_explicit_restore");

    GaugePair desired;
    bool changeFcc = false;
    {
        if (!capacityPolicy(before.design, before.fcc)) return report(false, "blocked_capacity_policy");
        if (before.design == NominalMah) return report(true, "already_nominal_not_learned");
        changeFcc = before.design == FactoryMah && before.fcc == FactoryMah;
        desired = {NominalMah, changeFcc ? NominalMah : before.fcc};
        if (saved && (journal.originalDesign != before.design || journal.originalFcc != before.fcc ||
                      journal.changedFcc != static_cast<uint8_t>(changeFcc)))
            return report(false, "blocked_existing_backup_mismatch");
        if (!saved) {
            journal.magic = 0x47433635U; journal.version = 1; journal.deviceType = DeviceType; journal.unitMah = 1;
            std::memcpy(journal.mac, mac.data(), sizeof(journal.mac));
            journal.originalDesign = before.design; journal.originalFcc = before.fcc;
            journal.targetDesign = desired.design; journal.targetFcc = desired.fcc;
            journal.changedFcc = static_cast<uint8_t>(changeFcc);
        }
    }
    journal.action = restore ? 2 : 1;
    journal.operationBefore = operation;
    journal.state = static_cast<uint8_t>(State::Pending);
    journal.lastDesign = 0; journal.lastFcc = 0; // Outcome is not yet known.
    // Durable backup + pending intent and byte-for-byte readback precede CFG.
    if (!gauge_store_journal(journal, mac.data())) return report(false, "blocked_journal_prepare");
    GaugePair recheck;
    if (!gauge_quiet_full(operation) || !gauge_pair(recheck) || !same_pair(recheck, before) ||
        !gauge_standard_matches(before)) return report(false, "blocked_preflight_changed_pending");

    const bool enterSent = gauge_control(GaugeCommand::EnterConfig);
    const bool entered = gauge_wait_config(true);
    bool forward = enterSent && entered && gauge_verify_pair(before, false);
    const bool changeDc = before.design != desired.design;
    bool touchedDc = false, touchedFcc = false;
    if (forward && changeDc) {
        touchedDc = true; // A failed I2C ACK can still have reached the device.
        forward = gauge_write_field(journal, Field::Design, desired.design);
    }
    if (forward && changeFcc) {
        touchedFcc = true;
        forward = gauge_write_field(journal, Field::InitialFcc, desired.fcc);
    }
    if (forward) forward = gauge_verify_pair(desired, false);
    bool rolledBack = false;
    if (!forward && (touchedDc || touchedFcc))
        rolledBack = gauge_rollback(journal, before, touchedDc, touchedFcc);
    // Attempt safe exit even if enter/status/read/write failed. No security
    // transitions are attempted; unknown or changed security is a hard error.
    bool exited = gauge_exit_config();
    if (!exited) {
        journal.state = static_cast<uint8_t>(State::ExitFailed);
        gauge_store_journal(journal, mac.data()); // Best effort; original pending backup already exists.
        sample_battery_locked(true);
        return report(false, "critical_cfg_exit_or_security");
    }
    if (forward && !gauge_verify_pair(desired, true)) {
        // Final live standard-register readback disagrees: one rollback phase,
        // not another apply attempt. Reenter only for that explicit rollback.
        const bool rollbackEnterSent = gauge_control(GaugeCommand::EnterConfig);
        const bool rollbackEntered = gauge_wait_config(true);
        rolledBack = rollbackEnterSent && rollbackEntered &&
                     gauge_rollback(journal, before, touchedDc, touchedFcc);
        exited = gauge_exit_config();
        forward = false;
    }
    if (!forward && !touchedDc && !touchedFcc) rolledBack = gauge_verify_pair(before, true);
    if (!forward && rolledBack) rolledBack = exited && gauge_verify_pair(before, true);
    if (!exited) journal.state = static_cast<uint8_t>(State::ExitFailed);
    else if (forward) journal.state = static_cast<uint8_t>(restore ? State::Restored : State::Committed);
    else journal.state = static_cast<uint8_t>(rolledBack ? State::RolledBack : State::RollbackFailed);
    if (forward || rolledBack) {
        const GaugePair final = forward ? desired : before;
        journal.lastDesign = final.design; journal.lastFcc = final.fcc;
    }
    const bool recorded = gauge_store_journal(journal, mac.data());
    sample_battery_locked(true);
    if (!exited) return report(false, "critical_cfg_exit_or_security");
    if (!forward && !rolledBack) return report(false, "critical_rollback_readback");
    if (!recorded) return report(false, "critical_final_journal_readback");
    if (!forward) return report(false, "failed_transaction_rolled_back");
    return report(true, restore ? "restored_original_nominal" : "nominal_set_not_learned");
}

bool Hal::gaugeSafetySelfTest() const { return mosaico_gauge::selftest(); }

Hal::MosaicoClockDiagnostics Hal::displayClockDiagnostics() const
{
    MosaicoClockDiagnostics result;
    result.clockErrors[0] = esp_clk_tree_src_get_freq_hz(SOC_MOD_CLK_CPU, ESP_CLK_TREE_SRC_FREQ_PRECISION_CACHED,
                                                       &result.cpuHz);
#if CONFIG_IDF_TARGET_ESP32S31
    result.clockErrors[1] = esp_clk_tree_src_get_freq_hz(SOC_MOD_CLK_SYS, ESP_CLK_TREE_SRC_FREQ_PRECISION_CACHED,
                                                       &result.sysHz);
    const uint32_t divider = clk_ll_mem_get_divider();
    if (result.clockErrors[0] == ESP_OK && divider) result.memBusDerivedHz = result.cpuHz / divider;
#else
    result.clockErrors[1] = ESP_ERR_NOT_SUPPORTED;
#endif
    result.clockErrors[2] = esp_clk_tree_src_get_freq_hz(SOC_MOD_CLK_APB, ESP_CLK_TREE_SRC_FREQ_PRECISION_CACHED,
                                                       &result.apbHz);
    result.directDmaTrue = panel_io != nullptr; // display_init sets psram_dma_direct=true.
    return result;
}

bool Hal::displayRamProbe(uint32_t& errors)
{
    // Explicit, bounded probe of an owned allocation, never live draw buffers.
    constexpr size_t Bytes = 4096;
    errors = 0;
    auto* buffer = static_cast<uint32_t*>(heap_caps_aligned_alloc(64, Bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (!buffer) return false;
    for (size_t i = 0; i < Bytes / sizeof(uint32_t); ++i) buffer[i] = 0xA5C39E71U ^ static_cast<uint32_t>(i);
    const esp_err_t writeback = esp_cache_msync(buffer, Bytes, ESP_CACHE_MSYNC_FLAG_DIR_C2M | ESP_CACHE_MSYNC_FLAG_TYPE_DATA);
    const esp_err_t invalidate = writeback == ESP_OK ?
        esp_cache_msync(buffer, Bytes, ESP_CACHE_MSYNC_FLAG_DIR_M2C | ESP_CACHE_MSYNC_FLAG_TYPE_DATA) : writeback;
    if (writeback == ESP_OK && invalidate == ESP_OK) {
        for (size_t i = 0; i < Bytes / sizeof(uint32_t); ++i) {
            if (buffer[i] != (0xA5C39E71U ^ static_cast<uint32_t>(i))) ++errors;
        }
    }
    heap_caps_free(buffer);
    return writeback == ESP_OK && invalidate == ESP_OK && errors == 0;
}

bool Hal::pmic_get_pwr_btn_state() { return false; }
void Hal::ioe_init() { ESP_LOGI(Tag, "No M5 IO expander; audio/vibration unsupported"); }
bool Hal::ioe_ready() const { return false; }
void Hal::ioe_tp_reset() {} // Board driver has no controllable touch reset GPIO.
void Hal::ioe_speaker_enable(bool) {} // No unverified amplifier GPIO writes.

void Hal::display_init()
{
    // Same values as CO5300_PANEL_BUS_QSPI_CONFIG, but C++ aggregate
    // designator order differs from the IDF 6.1 spi_bus_config_t layout.
    spi_bus_config_t spi{};
    spi.sclk_io_num = 44;
    spi.data0_io_num = 36;
    spi.data1_io_num = 51;
    spi.data2_io_num = 35;
    spi.data3_io_num = 9;
    spi.max_transfer_sz = Resolution * 40 * 2;
    ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST, &spi, SPI_DMA_CH_AUTO));
    esp_lcd_panel_io_spi_config_t io{};
    io.cs_gpio_num = GPIO_NUM_50;
    io.dc_gpio_num = GPIO_NUM_NC;
    io.spi_mode = 0;
    io.pclk_hz = 40 * 1000 * 1000;
    io.trans_queue_depth = 10;
    io.on_color_trans_done = nullptr;
    io.user_ctx = nullptr;
    io.lcd_cmd_bits = 32;
    io.lcd_param_bits = 8;
    io.flags.quad_mode = true;
    io.flags.psram_dma_direct = true;
    ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi(static_cast<esp_lcd_spi_bus_handle_t>(SPI2_HOST), &io, &panel_io));
    co5300_vendor_config_t vendor{};
    vendor.flags.use_qspi_interface = 1;
    esp_lcd_panel_dev_config_t config{};
    config.reset_gpio_num = GPIO_NUM_42;
    config.rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB;
    config.bits_per_pixel = 16;
    config.vendor_config = &vendor;
    ESP_ERROR_CHECK(esp_lcd_new_panel_co5300(panel_io, &config, &panel));
    ESP_ERROR_CHECK(esp_lcd_panel_reset(panel));
    ESP_ERROR_CHECK(esp_lcd_panel_init(panel));
    ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(panel, true));
    setBackLightBrightness(getBackLightBrightness(true));
}

bool Hal::display_ready() const { return panel && display; }

void Hal::touchpad_init()
{
    esp_lcd_panel_io_handle_t io = nullptr;
    esp_lcd_panel_io_i2c_config_t io_config{};
    io_config.dev_addr = ESP_LCD_TOUCH_IO_I2C_CST9217_ADDRESS;
    io_config.control_phase_bytes = 1;
    io_config.dc_bit_offset = 0;
    io_config.lcd_cmd_bits = 8;
    io_config.flags.disable_control_phase = 1;
    io_config.scl_speed_hz = 400000;
    ESP_ERROR_CHECK(esp_lcd_new_panel_io_i2c(_i2c_bus, &io_config, &io));
    esp_lcd_touch_config_t config{};
    config.x_max = Resolution;
    config.y_max = Resolution;
    config.rst_gpio_num = GPIO_NUM_NC;
    config.int_gpio_num = GPIO_NUM_6;
    config.levels.reset = 0;
    config.levels.interrupt = 0;
    ESP_ERROR_CHECK(esp_lcd_touch_new_i2c_cst9217(io, &config, &touch));
}

bool Hal::touch_ready() const { return touch && lvTouchpad; }

void Hal::lvgl_init()
{
    lvgl_port_cfg_t config{};
    config.task_priority = 3;
    config.task_stack = 7168;
    config.task_affinity = 1;
    config.task_max_sleep_ms = 500;
    config.task_stack_caps = MALLOC_CAP_INTERNAL | MALLOC_CAP_DEFAULT;
    config.timer_period_ms = 5;
    ESP_ERROR_CHECK(lvgl_port_init(&config));
    port_ready = true;
    if (!lvglLock()) ESP_ERROR_CHECK(ESP_ERR_TIMEOUT);
    lvgl_port_display_cfg_t disp_config{};
    disp_config.io_handle = panel_io;
    disp_config.panel_handle = panel;
    disp_config.buffer_size = Resolution * 40;
    disp_config.double_buffer = true;
    disp_config.hres = Resolution;
    disp_config.vres = Resolution;
    disp_config.color_format = LV_COLOR_FORMAT_RGB565;
    disp_config.rounder_cb = round_area;
    disp_config.flags.buff_dma = true;
    disp_config.flags.buff_spiram = true;
    disp_config.flags.swap_bytes = true;
    display = lvgl_port_add_disp(&disp_config);
    if (!display) ESP_ERROR_CHECK(ESP_ERR_NO_MEM);
    lv_display_add_event_cb(display, observe_refresh, LV_EVENT_REFR_START, nullptr);
    lv_display_add_event_cb(display, observe_refresh, LV_EVENT_REFR_READY, nullptr);
    lv_display_add_event_cb(display, observe_flush, LV_EVENT_FLUSH_FINISH, nullptr);
    // Only this LVGL read callback samples the driver. getTouchPoint returns
    // its last snapshot, so UI callers cannot consume the same report twice.
    lvTouchpad = lv_indev_create();
    if (!lvTouchpad) ESP_ERROR_CHECK(ESP_ERR_NO_MEM);
    lv_indev_set_type(lvTouchpad, LV_INDEV_TYPE_POINTER);
    lv_indev_set_display(lvTouchpad, display);
    lv_indev_set_read_cb(lvTouchpad, read_touch);
    lv_timer_set_period(lv_indev_get_read_timer(lvTouchpad), 10);
    lv_obj_set_style_bg_color(lv_screen_active(), lv_color_black(), LV_PART_MAIN);
    bootLogo = std::make_unique<BootLogo>();
    lvglUnlock();
    ESP_LOGI(Tag, "CO5300/CST9217 480x480; geometry requires physical acceptance");
}

bool Hal::lvglLock() { return port_ready && lvgl_port_lock(0); }
void Hal::lvglUnlock() { if (port_ready) lvgl_port_unlock(); }
void Hal::startLvglUpdate()
{
    if (!lvglLock()) return;
    const esp_err_t result = lvgl_port_resume();
    lvglUnlock();
    // The common HAL API permits repeated start/stop calls. An already-running
    // tick timer is not fatal (port_ready proves that initialization succeeded).
    if (result != ESP_ERR_INVALID_STATE) ESP_ERROR_CHECK(result);
}

void Hal::stopLvglUpdate()
{
    if (!lvglLock()) return;
    const esp_err_t result = lvgl_port_stop();
    lvglUnlock();
    if (result != ESP_ERR_INVALID_STATE) ESP_ERROR_CHECK(result);
    portENTER_CRITICAL(&touch_mux);
    cached_touch = TouchPoint{};
    portEXIT_CRITICAL(&touch_mux);
}

Hal::TouchPoint Hal::getTouchPoint()
{
    portENTER_CRITICAL(&touch_mux);
    const TouchPoint point = cached_touch;
    portEXIT_CRITICAL(&touch_mux);
    return point;
}

uint32_t GetDisplayFrameCount() { return frames.load(std::memory_order_relaxed); }

Hal::PerformanceDiagnostics Hal::performanceDiagnostics() const
{
    // esp_lvgl_port owns lv_timer_handler and exposes no whole-handler hook.
    // Legacy handler fields represent measured display refresh cycles/duration
    // on this backend, NOT fabricated whole-handler or CPU utilization values.
    return {refresh_calls.load(), refresh_max_us.load(), touch_reads.load(), touch_max_gap_us.load(), lvgl_core.load()};
}

void Hal::resetPerformanceDiagnostics()
{
    refresh_calls.store(0);
    refresh_max_us.store(0);
    refresh_start_us.store(0);
    touch_reads.store(0);
    touch_max_gap_us.store(0);
    touch_last_us.store(0);
}

void Hal::setBackLightBrightness(int brightness, bool saveToSettings)
{
    const int target = std::clamp(brightness, 0, 100);
    if (panel) {
        const bool locked = port_ready && lvglLock();
        if (!port_ready || locked) {
            _bl_brightness = target;
            if (locked && display && target > WakeDimThreshold &&
                ((panel_brightness >= 0 && panel_brightness <= WakeDimThreshold) || pending_wake_brightness >= 0)) {
                // Wake paths hide the lock/overlay under the same recursive
                // mutex before this call. Full-screen invalidation also covers
                // further UI changes made before the caller releases that lock.
                // Repeated high requests replace the target, never brighten early.
                pending_wake_brightness = target;
                lv_obj_invalidate(lv_display_get_screen_active(display));
            } else {
                // Re-lock/dim cancels an uncommitted wake. Startup and ordinary
                // active-state brightness adjustments remain immediate.
                pending_wake_brightness = -1;
                ESP_ERROR_CHECK(esp_lcd_panel_co5300_set_brightness(panel, target));
                panel_brightness = target;
            }
        }
        if (locked) lvglUnlock();
    } else _bl_brightness = target;
    if (saveToSettings) {
        Settings settings(std::string(SettingsNs), true);
        settings.SetInt("bl_lev", target);
    }
}

int Hal::getBackLightBrightness(bool loadFromSettings)
{
    if (loadFromSettings) {
        Settings settings(std::string(SettingsNs), false);
        _bl_brightness = static_cast<int>(std::clamp<int32_t>(settings.GetInt("bl_lev", 80), 10, 100));
    }
    return _bl_brightness;
}

void Hal::button_init()
{
    gpio_config_t config{};
    config.pin_bit_mask = 1ULL << 7;
    config.mode = GPIO_MODE_INPUT;
    config.pull_up_en = GPIO_PULLUP_ENABLE;
    ESP_ERROR_CHECK(gpio_config(&config));
    getButtonConfig(true);
    _buttons_ready = true;
}

void Hal::updateButtonStates()
{
    const uint32_t now = millis();
    btnA.setRawState(now, false);
    btnPwr.setRawState(now, false);
    btnB.setRawState(now, _buttons_ready && gpio_get_level(GPIO_NUM_7) == 0);
}

void Hal::setButtonConfig(ButtonConfig config, bool saveToSettings)
{
    _btn_config = config;
    if (saveToSettings) {
        Settings settings(std::string(SettingsNs), true);
        settings.SetBool("btn_sfx", config.sfxEnabled);
        settings.SetBool("btn_vibrate", config.vibrateEnabled);
    }
}

const Hal::ButtonConfig& Hal::getButtonConfig(bool loadFromSettings)
{
    if (loadFromSettings) {
        Settings settings(std::string(SettingsNs), false);
        _btn_config.sfxEnabled = settings.GetBool("btn_sfx", false);
        _btn_config.vibrateEnabled = settings.GetBool("btn_vibrate", true);
    }
    return _btn_config;
}

void Hal::audio_init() { ESP_LOGW(Tag, "Codec/microphone/vibration unvalidated and disabled"); }
bool Hal::audio_ready() const { return false; }
bool Hal::vibrator_ready() const { return false; }
bool Hal::audioSuspended() const { return true; }
// Software tone-generator format only; this does not claim a working codec.
int Hal::getAudioSampleRate() { return 44100; }
void Hal::audioPlay(std::vector<int16_t>&, bool) {}
void Hal::setMicrophoneMeterEnabled(bool) {}
bool Hal::isMicrophoneMeterEnabled() { return false; }
float Hal::getMicrophoneLevel() { return 0.0f; }
void Hal::vibrate(uint16_t, uint8_t) {}
void Hal::stopVibrate() {}

void Hal::setSpeakerVolume(int volume, bool saveToSettings)
{
    _spk_volume = std::clamp(volume, 0, 100);
    if (saveToSettings) {
        Settings settings(std::string(SettingsNs), true);
        settings.SetInt("spk_vol", _spk_volume);
    }
}

int Hal::getSpeakerVolume(bool loadFromSettings)
{
    if (loadFromSettings) {
        Settings settings(std::string(SettingsNs), false);
        _spk_volume = static_cast<int>(std::clamp<int32_t>(settings.GetInt("spk_vol", 80), 0, 100));
    }
    return _spk_volume;
}
