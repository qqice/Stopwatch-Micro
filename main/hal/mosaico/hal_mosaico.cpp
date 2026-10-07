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
#include "mosaico_touch_power_model.h"
#include "mosaico_lvgl_tick_model.h"
#include "mosaico_orientation_model.h"
#include "bmi270.h"
#include "driver/gpio.h"
#include "soc/io_mux_reg.h"
#include "soc/gpio_sig_map.h"
#include "driver/i2c_master.h"
#include "driver/spi_master.h"
#include "esp_lcd_co5300.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_interface.h"
#include "esp_lcd_touch_cst9217.h"
#include "esp_lvgl_port.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"
#include <host/standby_sleep.h>
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
// Four strips per full screen instead of twelve. Two RGB565 PSRAM buffers
// cost 150KiB more than the old 40-row setting; no full-page snapshot or
// increased idle refresh rate. Keep the DMA transfer bound and LVGL coupled.
constexpr int DisplayBufferRows = 120;
static_assert(Resolution % DisplayBufferRows == 0 && DisplayBufferRows % 2 == 0);
// Matches the current CodexMicroView LockedBrightness without changing the
// shared view or the S3 backend's brightness behavior.
constexpr int WakeDimThreshold = 8;
esp_lcd_panel_io_handle_t panel_io = nullptr;
esp_lcd_panel_handle_t panel = nullptr;
esp_lcd_touch_handle_t touch = nullptr;
lv_display_t* display = nullptr;
std::atomic<bool> port_ready{false};
uint32_t lvgl_timer_period_ms=0; // Immutable after release publication of port_ready.
#if CONFIG_MOSAICO_LVGL_MONOTONIC_TICK
std::atomic<uint32_t> lvgl_tick_offset{0};
uint32_t monotonic_lvgl_tick() {
    return mosaico_lvgl_tick::tick(mosaico_lvgl_tick::milliseconds(esp_timer_get_time()),
        lvgl_tick_offset.load(std::memory_order_relaxed));
}
#endif
std::atomic<bool> touch_idle_polling{false};
bool touch_wait_for_release = false; // LVGL mutex owned; blocks pre-wake held fingers in cache too.
std::mutex sleep_io_mutex;
mosaico_sleep_io::Model sleep_io_model;
std::atomic<bool> sleep_io_ready{false};
mosaico_sleep_io::Read read_sleep_io(int pin) {
    gpio_io_config_t io{};
    const esp_err_t error=gpio_get_io_config(static_cast<gpio_num_t>(pin),&io);
    return {error,{static_cast<uint16_t>(io.fun_sel),static_cast<uint16_t>(io.sig_out),static_cast<uint8_t>(io.drv),
        bool(io.ie),bool(io.oe),bool(io.oe_ctrl_by_periph),bool(io.oe_inv),bool(io.od),bool(io.pu),bool(io.pd)},bool(io.slp_sel)};
}
#if CONFIG_MOSAICO_SLEEP_IO_RETENTION && CONFIG_IDF_TARGET_ESP32S31
void apply_sleep_io(mosaico_sleep_io::Role role,int pin,uint16_t signal,bool peripheral) {
    std::lock_guard<std::mutex> guard(sleep_io_mutex);
    sleep_io_model.state.enabled=true;sleep_io_model.gpioFunction=PIN_FUNC_GPIO;sleep_io_model.gpioSignal=SIG_GPIO_OUT_IDX;
    if(sleep_io_model.add(role,pin,signal,peripheral,ESP_ERR_INVALID_ARG)) {
        if(!GPIO_IS_VALID_OUTPUT_GPIO(pin)) {
            sleep_io_model.state.unsafe|=mosaico_sleep_io::bit(pin);sleep_io_model.failure(ESP_ERR_INVALID_ARG);
        } else sleep_io_model.apply(role,read_sleep_io,[](int configuredPin) {
            return gpio_sleep_sel_dis(static_cast<gpio_num_t>(configuredPin));
        },ESP_ERR_INVALID_STATE);
    }
    sleep_io_ready.store(false,std::memory_order_release); // Final readback after all HAL configuration.
}
#endif
void refresh_sleep_io_locked() {
    for(unsigned index=0;index<mosaico_sleep_io::Count;++index) {
        const int pin=sleep_io_model.state.pins[index].pin;
        if(pin>=0)sleep_io_model.observe(index,read_sleep_io(pin),ESP_ERR_INVALID_STATE);
    }
    sleep_io_model.state.ready=sleep_io_model.ready();
    sleep_io_ready.store(sleep_io_model.state.ready,std::memory_order_release);
}
std::atomic<bool> unused_gates_off{false};
// Serialized by the LVGL port mutex once it exists. Keep physical brightness
// separate from the requested setting while a wake redraw is pending.
int panel_brightness = -1;
int pending_wake_brightness = -1;
bool pending_boot_display = false;
i2c_master_dev_handle_t gauge = nullptr;
std::mutex battery_mutex;
// All mutating transactions take this BEFORE battery_mutex. Recursive so the
// boot wrapper can serialize its public Access/Nominal/Reconcile/Restore steps.
std::recursive_mutex gauge_transaction_mutex;
// Private, transaction-mutex owned privilege: only a durable boot intent may
// enable it. Public manual access/nominal/restore retains its strict policy.
bool boot_factory_reload_scope = false;
Hal::GaugeBootReloadInfo gauge_boot_info;
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

// Dedicated 12.5 Hz cache task; no LVGL callback performs motion I2C.
portMUX_TYPE motion_mux = portMUX_INITIALIZER_UNLOCKED;
Hal::MotionOrientation motion_snapshot;
std::atomic<bool> motion_idle{false};
TaskHandle_t motion_task_handle = nullptr;
std::atomic<uint16_t> display_degrees{0};
std::atomic<bool> orientation_healthy{true};
esp_lcd_panel_t rotation_control{}; // Only control interface; draw handle remains the real panel.
mosaico_orientation::ControlResult rotation_result;

esp_err_t checked_swap(esp_lcd_panel_t*, bool swap)
{
    return rotation_result.record(esp_lcd_panel_swap_xy(panel, swap));
}
esp_err_t checked_mirror(esp_lcd_panel_t*, bool x, bool y)
{
    return rotation_result.record(esp_lcd_panel_mirror(panel, x, y));
}


esp_err_t motion_write(bmi270_handle_t* imu, uint8_t reg, uint8_t value)
{
    const uint8_t command[] = {reg, value};
    const esp_err_t result = i2c_master_transmit(imu->i2c_handle, command, sizeof(command), 25);
    // BMI270 APS writes require >=450us before the next bus transaction.
    // A second tick covers entry close to a tick boundary without busy-spinning.
    // This includes the APS-enable write immediately before first ACC enable.
    if (result == ESP_OK) vTaskDelay(pdMS_TO_TICKS(1) + 1);
    return result;
}

void motion_task(void* bus)
{
    Hal::MotionOrientation snapshot;
    bmi270_handle_t* imu = nullptr;
    bmi270_driver_config_t config{};
    config.addr = 0x69;
    config.interface = BMI270_USE_I2C;
    config.i2c_bus = static_cast<i2c_master_bus_handle_t>(bus);
    snapshot.initStage = 1;
    snapshot.error = bmi270_create(&config, &imu); // ID checked before blob upload.
    if (snapshot.error == ESP_OK) {
        snapshot.initStage = 2;
        snapshot.error = bmi270_get_chip_id(imu, &snapshot.chipId);
        if (snapshot.error == ESP_OK && snapshot.chipId != BMI270_CHIP_ID) snapshot.error = ESP_ERR_NOT_FOUND;
    }
    // Deliberately never call bmi270_start: it enables gyro + temperature.
    // ACC_CONF=0x26: power-optimized AVG4 at 25 Hz, range +/-2g.
    if (snapshot.error == ESP_OK) { snapshot.initStage = 3; snapshot.error = motion_write(imu, 0x7d, 0); }
    if (snapshot.error == ESP_OK) snapshot.error = bmi270_set_acce_range(imu, BMI270_ACC_RANGE_2_G);
    if (snapshot.error == ESP_OK) snapshot.error = motion_write(imu, 0x40, 0x26);
    if (snapshot.error == ESP_OK) snapshot.error = motion_write(imu, 0x7c, 0x01); // Advanced power saving.
    snapshot.available = snapshot.error == ESP_OK;
    snapshot.idle = motion_idle.load();
    portENTER_CRITICAL(&motion_mux); motion_snapshot = snapshot; portEXIT_CRITICAL(&motion_mux);
    if (!snapshot.available) {
        if (imu) { motion_write(imu, 0x7d, 0); bmi270_delete(imu); }
        ESP_LOGW(Tag, "BMI270 unavailable stage=%u error=%ld", snapshot.initStage, static_cast<long>(snapshot.error));
        for (;;) ulTaskNotifyTake(pdTRUE, portMAX_DELAY); // Nonfatal; handle remains valid.
    }
    mosaico_orientation::Model model;
    bool enabled = false;
    for (;;) {
        const bool idle = motion_idle.load();
        snapshot.idle = idle;
        if (enabled == idle) {
            snapshot.error = motion_write(imu, 0x7d, idle ? 0x00 : 0x04); // Only accelerometer.
            if (snapshot.error == ESP_OK) { enabled = !idle; if (enabled) vTaskDelay(pdMS_TO_TICKS(80) + 2); }
            model.resetPending(); snapshot.valid = false;
        }
        if (!idle && enabled && snapshot.error == ESP_OK) {
            snapshot.error = bmi270_get_acce_data(imu, &snapshot.ax, &snapshot.ay, &snapshot.az);
            snapshot.valid = snapshot.error == ESP_OK;
            if (snapshot.valid) {
                snapshot.sampleUs = esp_timer_get_time(); snapshot.samples++;
                if (model.sample(snapshot.ax, snapshot.ay, snapshot.az, snapshot.sampleUs / 1000)) snapshot.generation++;
                snapshot.degrees = model.degrees;
            } else { snapshot.readErrors++; model.resetPending(); }
        }
        portENTER_CRITICAL(&motion_mux); motion_snapshot = snapshot; portEXIT_CRITICAL(&motion_mux);
        // Notifications wake on policy changes; locked task blocks indefinitely.
        ulTaskNotifyTake(pdTRUE, idle && !enabled ? portMAX_DELAY : pdMS_TO_TICKS(80) + 1);
        // A read failure is retried only on the next bounded sample, not a spin.
        if (enabled && !motion_idle.load()) snapshot.error = ESP_OK;
    }
}

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
            if (pending_boot_display) {
                ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(panel, true));
                pending_boot_display = false;
            }
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
    if (touch_idle_polling.load(std::memory_order_relaxed) || !orientation_healthy.load()) {
        // Also protects against a forced/event-driven indev read while its
        // timer is paused. Software RELEASE only: no driver I2C or read count.
        data->state = LV_INDEV_STATE_RELEASED;
        return;
    }
    const int64_t now = esp_timer_get_time();
    const int64_t previous = touch_last_us.exchange(now, std::memory_order_relaxed);
    touch_reads.fetch_add(1, std::memory_order_relaxed);
    if (previous > 0 && now > previous) update_max(touch_max_gap_us, static_cast<uint32_t>(now - previous));
    Hal::TouchPoint point;
    uint16_t x = 0, y = 0;
    uint8_t count = 0;
    const bool sample_valid = touch && esp_lcd_touch_read_data(touch) == ESP_OK;
    const bool contact = sample_valid && esp_lcd_touch_get_coordinates(touch, &x, &y, nullptr, &count, 1) && count;
    if (contact && x < Resolution && y < Resolution) {
        point = {1, static_cast<int>(x), static_cast<int>(y)};
    }
    touch_wait_for_release = mosaico_touch_power::waitForPhysicalRelease(touch_wait_for_release, sample_valid, contact);
    if (touch_wait_for_release) point = {}; // No cached or LVGL phantom press before a proven release.
    // LVGL rotates its raw point later in indev_pointer_proc. Cache separately
    // transformed logical coordinates for main-button/diagnostic consumers.
    auto logical = point;
    if (point.num && display) {
        lv_point_t rotated{point.x, point.y};
        lv_display_rotate_point(display, &rotated);
        logical.x = rotated.x; logical.y = rotated.y;
    }
    portENTER_CRITICAL(&touch_mux);
    cached_touch = logical;
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
                               sample.fullMah <= mosaico_gauge::MaxReasonableFcc &&
                               mosaico_gauge::configExitAccepted(sample.operationStatus) &&
                               ((sample.operationStatus >> 1) & 3) == 3;
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
    if (ok && boot_factory_reload_scope && !ownedUnresolvedRestore && ((operation >> 1) & 3) == 1) {
        uint16_t dc = 0, fcc = 0;
        return gauge_word(0x3C, dc) && gauge_word(0x12, fcc) &&
            dc == mosaico_gauge::FactoryMah && fcc == mosaico_gauge::FactoryMah &&
            mosaico_gauge::bootReloadPhysical(operation, mv, temperature,
                static_cast<int16_t>(current), static_cast<int16_t>(average));
    }
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
            if (operation & 1) return false;
            if (enter) {
                if (((operation >> 1) & 3) != 1) return false;
                if (operation & 0x0400) return true;
            } else {
                if ((operation & 0x0400) && ((operation >> 1) & 3) != 1) return false;
                if (mosaico_gauge::configExitAccepted(operation)) return true;
            }
        }
        gauge_delay_ms(100);
    }
    return false;
}

bool gauge_exit_config()
{
    uint16_t operation = 0;
    if (!gauge_word(0x3A, operation) || (operation & 1)) return false;
    if (!(operation & 0x0400))
        return mosaico_gauge::configExitAccepted(operation) || gauge_wait_config(false);
    if (((operation >> 1) & 3) != 1) return false; // CFG writes/recovery STILL require existing FA.
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

bool gauge_sealed_prior_verified();

bool gauge_verify_pair(const GaugePair& desired, bool standard)
{
    for (unsigned i = 0; i < 2; ++i) {
        if (standard) {
            uint16_t operation = 0;
            if (!gauge_word(0x3A, operation) || !mosaico_gauge::configExitAccepted(operation)) return false;
            if (((operation >> 1) & 3) == 3) {
                // Sealed MAC DM may be inaccessible/stale. Completed FA-phase
                // MAC readbacks + expected prior3 access ledger permit standard
                // terminal pair verification without reopening or rewriting.
                if (!gauge_sealed_prior_verified() || !gauge_identity() || !gauge_standard_matches(desired)) return false;
                continue;
            }
        }
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

// TI SLUUBD4A 6.1 public default access sequence, with the word split into
// separately addressed bytes as verified by the local board reference. These
// are Control inputs, not key-memory writes. Never log or journal word values.
enum class AccessWord : uint16_t { UnsealFirst = 0x0414, UnsealSecond = 0x3672, Full = 0xFFFF, Seal = 0x0030 };

bool gauge_access_word(AccessWord word)
{
    if (word != AccessWord::UnsealFirst && word != AccessWord::UnsealSecond &&
        word != AccessWord::Full && word != AccessWord::Seal) return false;
    const uint16_t value = static_cast<uint16_t>(word);
    const uint8_t low[] = {0x00, static_cast<uint8_t>(value)};
    const uint8_t high[] = {0x01, static_cast<uint8_t>(value >> 8)};
    gauge_feed();
    if (i2c_master_transmit(gauge, low, sizeof(low), 20) != ESP_OK) return false;
    gauge_delay_ms(1);
    if (i2c_master_transmit(gauge, high, sizeof(high), 20) != ESP_OK) return false;
    gauge_delay_ms(100);
    return true;
}

bool gauge_wait_security(uint8_t expected)
{
    for (unsigned i = 0; i < 20; ++i) {
        uint16_t operation = 0;
        if (gauge_word(0x3A, operation)) {
            if (operation & 0x0401) return false;
            if (((operation >> 1) & 3) == expected) return true;
        }
        gauge_delay_ms(100);
    }
    return false;
}

esp_err_t gauge_load_access(mosaico_gauge::AccessJournal& journal, const uint8_t mac[6])
{
    nvs_handle_t handle = 0;
    esp_err_t result = nvs_open("gaugeacc", NVS_READONLY, &handle);
    if (result != ESP_OK) return result;
    size_t size = sizeof(journal);
    result = nvs_get_blob(handle, "access_v1", &journal, &size);
    nvs_close(handle);
    if (result == ESP_OK && (size != sizeof(journal) || !mosaico_gauge::validAccessJournal(journal, mac))) return ESP_FAIL;
    return result;
}

bool gauge_sealed_prior_verified()
{
    uint8_t mac[6]{};
    if (esp_efuse_mac_get_default(mac) != ESP_OK) return false;
    mosaico_gauge::AccessJournal access{};
    return gauge_load_access(access, mac) == ESP_OK && access.priorSecurity == 3 &&
           access.unsealVerified && access.fullVerified && !mosaico_gauge::failedDefaultAttempt(access) &&
           (access.state == static_cast<uint8_t>(mosaico_gauge::AccessState::Opened) ||
            access.state == static_cast<uint8_t>(mosaico_gauge::AccessState::Restored));
}

bool gauge_store_access(mosaico_gauge::AccessJournal& journal, const uint8_t mac[6])
{
    mosaico_gauge::seal(journal);
    if (!mosaico_gauge::validAccessJournal(journal, mac)) return false;
    gauge_feed();
    nvs_handle_t handle = 0;
    if (nvs_open("gaugeacc", NVS_READWRITE, &handle) != ESP_OK) return false;
    esp_err_t result = nvs_set_blob(handle, "access_v1", &journal, sizeof(journal));
    if (result == ESP_OK) result = nvs_commit(handle);
    nvs_close(handle);
    gauge_feed();
    mosaico_gauge::AccessJournal readback{};
    return result == ESP_OK && gauge_load_access(readback, mac) == ESP_OK &&
           std::memcmp(&journal, &readback, sizeof(journal)) == 0;
}

bool gauge_access_preflight(uint16_t& operation, GaugePair& profile, bool bootReadonlyProbe = false)
{
    uint16_t soc = 0, mv = 0, temperature = 0, current = 0, average = 0, remaining = 0;
    bool ok = gauge_word(0x3A, operation);
    ok &= gauge_word(0x2C, soc); ok &= gauge_word(0x08, mv); ok &= gauge_word(0x06, temperature);
    ok &= gauge_word(0x0C, current); ok &= gauge_word(0x14, average);
    ok &= gauge_word(0x3C, profile.design); ok &= gauge_word(0x12, profile.fcc); ok &= gauge_word(0x10, remaining);
    if (!ok || !profile.fcc || remaining > profile.fcc) return false;
    if (boot_factory_reload_scope || bootReadonlyProbe)
        return profile.design == mosaico_gauge::FactoryMah && profile.fcc == mosaico_gauge::FactoryMah &&
            mosaico_gauge::bootReloadPhysical(operation, mv, temperature,
            static_cast<int16_t>(current), static_cast<int16_t>(average));
    return mosaico_gauge::quietAccess(operation, soc, mv, temperature,
        static_cast<int16_t>(current), static_cast<int16_t>(average));
}

uint8_t gauge_observed_security()
{
    uint16_t operation = 0;
    return gauge_word(0x3A, operation) ? static_cast<uint8_t>((operation >> 1) & 3) : 0;
}

bool gauge_return_access(const mosaico_gauge::AccessJournal& journal)
{
    uint16_t operation = 0;
    if (!gauge_word(0x3A, operation) || (operation & 1) || ((operation >> 1) & 3) == 0) return false;
    // Protective cleanup first. Do not seal a still-active or unknown CFG.
    if ((operation & 0x0400) && !gauge_exit_config()) return false;
    if (!gauge_word(0x3A, operation) || (operation & 0x0401)) return false;
    const uint8_t current = static_cast<uint8_t>((operation >> 1) & 3);
    if (current == journal.priorSecurity) return gauge_wait_security(journal.priorSecurity);
    // Never self-seal an originally unknown-key UNSEALED/FULL_ACCESS unit.
    // Prior2 OPEN is deliberately refused; prior1 RESTORE can only be a no-op.
    if (journal.priorSecurity != 3) return false;
    gauge_delay_ms(5000); // let any incomplete access-key state machine expire.
    if (!gauge_word(0x3A, operation) || (operation & 0x0401)) return false;
    gauge_access_word(AccessWord::Seal); // one attempt; readback determines actual protection.
    return gauge_wait_security(3);
}

esp_err_t gauge_load_reload(mosaico_gauge::ReloadJournal& journal, const uint8_t mac[6])
{
    nvs_handle_t handle = 0;
    esp_err_t result = nvs_open("gaugecal", NVS_READONLY, &handle);
    if (result != ESP_OK) return result;
    size_t size = sizeof(journal);
    result = nvs_get_blob(handle, "reload_v1", &journal, &size);
    nvs_close(handle);
    if (result == ESP_OK && (size != sizeof(journal) || !mosaico_gauge::validReloadJournal(journal, mac))) return ESP_FAIL;
    return result;
}

bool gauge_store_reload(mosaico_gauge::ReloadJournal& journal, const uint8_t mac[6])
{
    mosaico_gauge::seal(journal);
    if (!mosaico_gauge::validReloadJournal(journal, mac)) return false;
    gauge_feed();
    nvs_handle_t handle = 0;
    if (nvs_open("gaugecal", NVS_READWRITE, &handle) != ESP_OK) return false;
    esp_err_t result = nvs_set_blob(handle, "reload_v1", &journal, sizeof(journal));
    if (result == ESP_OK) result = nvs_commit(handle);
    nvs_close(handle);
    gauge_feed();
    mosaico_gauge::ReloadJournal readback{};
    return result == ESP_OK && gauge_load_reload(readback, mac) == ESP_OK &&
           std::memcmp(&journal, &readback, sizeof(journal)) == 0;
}
} // namespace

bool Hal::gaugeAccess(GaugeAccessAction action, char* reason, size_t reasonSize)
{
    using namespace mosaico_gauge;
    std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    std::lock_guard<std::mutex> lock(battery_mutex);
    const auto report = [&](bool success, const char* why) {
        if (reason && reasonSize) std::snprintf(reason, reasonSize, "%s", why);
        sample_battery_locked(true);
        return success;
    };
    if (action != GaugeAccessAction::Open && action != GaugeAccessAction::Restore)
        return report(false, "blocked_access_action");
    if (!gauge || !selftest()) return report(false, "blocked_access_gauge_or_model");
    const auto mac = getFactoryMac();
    AccessJournal journal{};
    const esp_err_t loaded = gauge_load_access(journal, mac.data());
    const bool saved = loaded == ESP_OK;
    if (loaded != ESP_OK && loaded != ESP_ERR_NVS_NOT_FOUND)
        return report(false, "blocked_access_journal_crc_unit");
    if (action == GaugeAccessAction::Restore) {
        if (!saved) return report(false, "blocked_access_no_journal");
        if (!gauge_identity()) return report(false, "critical_access_restore_identity");
        // Closing prior SEALED is protective cleanup, not a capacity write:
        // it must remain reachable after nominal reinit changes SOC/mirrors.
        const bool restored = gauge_return_access(journal);
        journal.lastSecurity = gauge_observed_security();
        journal.state = static_cast<uint8_t>(restored ? AccessState::Restored : AccessState::Failed);
        const bool recorded = gauge_store_access(journal, mac.data());
        if (!restored) return report(false, "critical_access_exit_or_prior_security");
        if (!recorded) return report(false, "critical_access_restore_journal");
        return report(true, journal.priorSecurity == 3 ? "access_restored_sealed" : "access_restored_prior_noop");
    }
    uint16_t operation = 0;
    GaugePair profile;
    if (!gauge_access_preflight(operation, profile)) return report(false, "blocked_access_quiet_full_temperature");
    if (!gauge_identity()) return report(false, "blocked_identity_0220");
    bool candidate = capacityPolicy(profile.design, profile.fcc);
    if (!candidate) {
        Journal nominal{};
        candidate = gauge_load_journal(nominal, mac.data()) == ESP_OK &&
                    restorePairAllowed(nominal, profile.design, profile.fcc);
    }
    if (!candidate) return report(false, "blocked_access_capacity_candidate");
    const uint8_t prior = static_cast<uint8_t>((operation >> 1) & 3);
    if (saved && journal.state != static_cast<uint8_t>(AccessState::Restored)) {
        if (journal.state == static_cast<uint8_t>(AccessState::Opened) && prior == 1 &&
            (journal.priorSecurity == 1 || (journal.unsealVerified && journal.fullVerified)))
            return report(true, "access_already_open_verified");
        return report(false, "blocked_access_pending_restore_first");
    }
    if (saved && failedDefaultAttempt(journal)) return report(false, "blocked_default_attempt_already_failed");
    if (prior == 2) return report(false, "blocked_prior2_unknown_return_path");
    journal = {};
    journal.magic = 0x47414331U; journal.version = 1; journal.deviceType = DeviceType; journal.unitMah = 1;
    journal.state = static_cast<uint8_t>(AccessState::Pending); journal.priorSecurity = prior;
    journal.lastSecurity = prior; journal.baseDesign = profile.design; journal.baseFcc = profile.fcc;
    journal.operationBefore = operation;
    std::memcpy(journal.mac, mac.data(), sizeof(journal.mac));
    if (!gauge_store_access(journal, mac.data())) return report(false, "blocked_access_journal_prepare");
    GaugePair recheck;
    uint16_t recheckOperation = 0;
    if (!gauge_access_preflight(recheckOperation, recheck) || !same_pair(profile, recheck) ||
        ((recheckOperation >> 1) & 3) != prior) return report(false, "blocked_access_preflight_changed_pending");
    const auto abort = [&]() {
        // Same bounded protective restore as the explicit RESTORE command;
        // no guessed/retried unseal or full-access words in the abort path.
        const bool restored = gauge_return_access(journal);
        journal.lastSecurity = gauge_observed_security();
        journal.state = static_cast<uint8_t>(restored ? AccessState::Restored : AccessState::Failed);
        const bool recorded = gauge_store_access(journal, mac.data());
        return report(false, restored && recorded ? "failed_access_prior_restored" : "critical_access_prior_restore");
    };
    if (prior == 3) {
        // Attempt flags are durable BEFORE words; a crash never permits replay.
        journal.unsealAttempted = 1;
        if (!gauge_store_access(journal, mac.data())) return abort();
        gauge_delay_ms(5000);
        if (!gauge_access_preflight(recheckOperation, recheck) || ((recheckOperation >> 1) & 3) != 3 ||
            !same_pair(profile, recheck)) return abort();
        const bool sent = gauge_access_word(AccessWord::UnsealFirst) && gauge_access_word(AccessWord::UnsealSecond);
        const bool observed = gauge_wait_security(2);
        if (!sent || !observed) return abort();
        journal.unsealVerified = 1; journal.lastSecurity = 2;
        journal.fullAttempted = 1;
        if (!gauge_store_access(journal, mac.data())) return abort();
        gauge_delay_ms(5000);
        if (!gauge_access_preflight(recheckOperation, recheck) || ((recheckOperation >> 1) & 3) != 2 ||
            !same_pair(profile, recheck)) return abort();
        const bool fullSent = gauge_access_word(AccessWord::Full) && gauge_access_word(AccessWord::Full);
        const bool fullObserved = gauge_wait_security(1);
        if (!fullSent || !fullObserved) return abort();
        journal.fullVerified = 1; journal.lastSecurity = 1;
    }
    journal.state = static_cast<uint8_t>(AccessState::Opened);
    if (!gauge_store_access(journal, mac.data())) return abort();
    return report(true, prior == 3 ? "access_opened_default_verified" : "access_already_full_prior_recorded");
}

void Hal::i2c_init()
{
    // V1.0-only dedicated unused gates: CODEC_PW56 active-high (guide/LDO),
    // PA_CTRL45 -> NS4150B CTRL low=Shutdown (core schematic p4/datasheet),
    // MOTOR8 active-high (guide). Set LOW output latches BEFORE enabling output
    // to avoid an enable pulse. Do not change system rails, LEDs or sensors.
    for (const gpio_num_t pin : {GPIO_NUM_56, GPIO_NUM_45, GPIO_NUM_8}) {
        ESP_ERROR_CHECK(gpio_set_level(pin, 0));
    }
    gpio_config_t unused{};
    unused.pin_bit_mask = mosaico_touch_power::UnusedGateMask;
    unused.mode = GPIO_MODE_OUTPUT;
    ESP_ERROR_CHECK(gpio_config(&unused));
    unused_gates_off.store(true, std::memory_order_relaxed); // Command success only; no electrical claim.
    // Active-low peripheral rail only. Never touch whole-device power GPIO57,
    // boot straps, charger, audio-enable or NAND/expansion pins.
    gpio_config_t rail{};
    rail.pin_bit_mask = 1ULL << 60;
    rail.mode = GPIO_MODE_OUTPUT;
    ESP_ERROR_CHECK(gpio_config(&rail));
    ESP_ERROR_CHECK(gpio_set_level(GPIO_NUM_60, 0));
#if CONFIG_MOSAICO_SLEEP_IO_RETENTION && CONFIG_IDF_TARGET_ESP32S31
    // Preserve the ALREADY configured outputs, including the original rail LOW.
    // Role collection uses the original cfg masks; no new normal-level writes.
    unsigned roleIndex=0;
    for(unsigned pin=0;pin<64;++pin) if(unused.pin_bit_mask & mosaico_sleep_io::bit(pin))
        apply_sleep_io(static_cast<mosaico_sleep_io::Role>(roleIndex++),pin,SIG_GPIO_OUT_IDX,false);
    for(unsigned pin=0;pin<64;++pin) if(rail.pin_bit_mask & mosaico_sleep_io::bit(pin))
        apply_sleep_io(mosaico_sleep_io::Role::Rail,pin,SIG_GPIO_OUT_IDX,false);
#endif
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
    std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
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
        if (restored) {
            uint16_t finalOperation = 0;
            if (gauge_word(0x3A, finalOperation) && ((finalOperation >> 1) & 3) == 3)
                journal.state = static_cast<uint8_t>(State::VerifiedSealedPrior3);
        }
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
        uint16_t finalOperation = 0;
        if (!gauge_word(0x3A, finalOperation) || !configExitAccepted(finalOperation) ||
            ((finalOperation >> 1) & 3) != 1) {
            // A real terminal readback/security failure while sealed must not
            // launch a futile/unapproved CFG rollback or replay access words.
            journal.state = static_cast<uint8_t>(State::ExitFailed);
            gauge_store_journal(journal, mac.data());
            sample_battery_locked(true);
            return report(false, "critical_unknown_terminal_security");
        }
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
        uint16_t finalOperation = 0;
        if (gauge_word(0x3A, finalOperation) && ((finalOperation >> 1) & 3) == 3)
            journal.state = static_cast<uint8_t>(State::VerifiedSealedPrior3);
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

bool Hal::gaugeReconcileNominal(char* reason, size_t reasonSize)
{
    using namespace mosaico_gauge;
    std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    std::lock_guard<std::mutex> lock(battery_mutex);
    const auto report = [&](bool ok, const char* why) {
        if (reason && reasonSize) std::snprintf(reason, reasonSize, "%s", why);
        return ok;
    };
    if (!gauge || !selftest()) return report(false, "blocked_reconcile_gauge_or_model");
    const auto mac = getFactoryMac();
    Journal nominal{};
    AccessJournal access{};
    if (gauge_load_journal(nominal, mac.data()) != ESP_OK || nominal.action != 1 || nominal.targetDesign != NominalMah)
        return report(false, "blocked_reconcile_nominal_journal");
    // Only an interrupted forward operation (or this audit's idempotent
    // terminal state) may be reconciled. Never relabel a completed restore,
    // rollback or successful apply using later coincidental target readings.
    if (nominal.state != static_cast<uint8_t>(State::Pending) &&
        nominal.state != static_cast<uint8_t>(State::ExitFailed) &&
        nominal.state != static_cast<uint8_t>(State::VerifiedSealedPrior3))
        return report(false, "blocked_reconcile_not_pending_forward");
    if (nominal.state == static_cast<uint8_t>(State::VerifiedSealedPrior3) &&
        (nominal.lastDesign != nominal.targetDesign || nominal.lastFcc != nominal.targetFcc))
        return report(false, "blocked_reconcile_not_target_terminal");
    if (gauge_load_access(access, mac.data()) != ESP_OK || access.priorSecurity != 3 ||
        access.state != static_cast<uint8_t>(AccessState::Restored) || access.lastSecurity != 3 ||
        !access.unsealVerified || !access.fullVerified || failedDefaultAttempt(access))
        return report(false, "blocked_reconcile_prior3_access_evidence");
    // Current endpoint audit, NOT a rewrite of the historical failed script.
    // Sealed standard commands and the harmless identity selector are sufficient;
    // no DM selector, CFG command, access words or parameter writes are issued.
    for (unsigned read = 0; read < 2; ++read) {
        if (!gauge_identity()) return report(false, "blocked_identity_0220");
        sample_battery_locked(true);
        const auto& sample = battery_telemetry;
        if (!sample.valid || !sample.capacityValid || !configExitAccepted(sample.operationStatus) ||
            ((sample.operationStatus >> 1) & 3) != 3 || sample.designMah != nominal.targetDesign ||
            sample.fullMah != nominal.targetFcc || sample.remainingMah > sample.fullMah)
            return report(false, "blocked_reconcile_observed_pair_or_state");
    }
    // State 7 encodes CRC-bound OBSERVED FINAL SEC3 + expected priorSEC3 and
    // retains the legacy 36-byte backup format. It does not assert that 0091
    // universally auto-seals, or that the original transaction returned PASS.
    nominal.state = static_cast<uint8_t>(State::VerifiedSealedPrior3);
    nominal.lastDesign = battery_telemetry.designMah;
    nominal.lastFcc = battery_telemetry.fullMah;
    if (!gauge_store_journal(nominal, mac.data())) return report(false, "critical_reconcile_journal_readback");
    return report(true, "reconciled_std_target_observed_sec3_expected_prior3");
}

Hal::GaugeBootReloadInfo Hal::gaugeBootReloadInfo() const
{
    // No I2C/NVS and no triggering work. Waiting here prevents a runtime-reset
    // guard from observing an unfinished access/nominal/reload transaction.
    std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    return gauge_boot_info;
}

Hal::GaugeBootReloadStatus Hal::gaugeBootReload(char* reason, size_t reasonSize)
{
    using namespace mosaico_gauge;
    using Status = GaugeBootReloadStatus;
    std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    const auto finish = [&](Status status, const char* why) {
        gauge_boot_info.status = status;
        std::snprintf(gauge_boot_info.reason, sizeof(gauge_boot_info.reason), "%s", why);
        if (reason && reasonSize) std::snprintf(reason, reasonSize, "%s", why);
        return status;
    };
    // Only Deferred readonly probes may be revisited by the caller's scheduler.
    if (gauge_boot_info.attempted || gauge_boot_info.status != Status::Deferred) {
        if (reason && reasonSize) std::snprintf(reason, reasonSize, "%s", gauge_boot_info.reason);
        return gauge_boot_info.status;
    }
    ReloadJournal latch{};
    const auto mac = getFactoryMac();
    {
        // Release the nonrecursive battery mutex BEFORE calling public methods.
        // The outer recursive transaction mutex remains held across every step.
        std::lock_guard<std::mutex> battery(battery_mutex);
        if (!gauge || !selftest()) return finish(Status::Skipped, "boot_skip_unknown_gauge_or_model");
        const esp_err_t latchLoaded = gauge_load_reload(latch, mac.data());
        if (latchLoaded != ESP_OK && latchLoaded != ESP_ERR_NVS_NOT_FOUND)
            return finish(Status::Critical, "boot_critical_corrupt_or_unknown_failure_latch");
        if (latchLoaded == ESP_OK && latch.state != static_cast<uint8_t>(ReloadState::Completed)) {
            // A power cut can interrupt finally cleanup. Never retry access or
            // parameters automatically; capture readonly SEC/CFG for the info
            // getter and require explicit protective access-restore by the owner.
            uint16_t operation = 0;
            char details[128]{};
            if (gauge_word(0x3A, operation)) std::snprintf(details, sizeof(details),
                "boot_critical_%s_no_retry observed_sec=%u cfg=%u cal=%u",
                latch.state == static_cast<uint8_t>(ReloadState::Pending) ? "pending" : "failed",
                (operation >> 1) & 3, !!(operation & 0x0400), !!(operation & 1));
            else std::snprintf(details, sizeof(details), "boot_critical_persistent_failure_no_retry_security_unreadable");
            return finish(Status::Critical, details);
        }
        Journal nominal{};
        AccessJournal access{};
        if (gauge_load_journal(nominal, mac.data()) != ESP_OK || gauge_load_access(access, mac.data()) != ESP_OK)
            return finish(Status::Skipped, "boot_skip_unknown_or_invalid_unit_history");
        if (!bootHistoryEligible(nominal, access))
            return finish(Status::Skipped, "boot_skip_history_not_verified_sealed_prior3");
        sample_battery_locked(true);
        const auto sample = battery_telemetry;
        if (!sample.valid || !sample.capacityValid) return finish(Status::Deferred, "boot_deferred_readonly_telemetry");
        if (!configExitAccepted(sample.operationStatus) || ((sample.operationStatus >> 1) & 3) != 3)
            return finish(Status::Deferred, "boot_deferred_not_idle_sealed_cfg_clear");
        if (keepLearnedNominal(sample.designMah, sample.fullMah))
            return finish(Status::Skipped, "boot_skip_already65_preserve_learned_fcc");
        if (!bootFactoryPairEligible(nominal, sample.designMah, sample.fullMah))
            return finish(Status::Skipped, "boot_skip_nonfactory_or_backup_mismatch_no_writes");
        uint16_t operation = 0;
        GaugePair profile;
        if (!gauge_access_preflight(operation, profile, true) || ((operation >> 1) & 3) != 3 ||
            !bootFactoryPairEligible(nominal, profile.design, profile.fcc))
            return finish(Status::Deferred, "boot_deferred_reload_voltage_temperature_load");
        if (!gauge_identity()) return finish(Status::Deferred, "boot_deferred_readonly_identity");
        // Durable per-unit Pending latch BEFORE any automatic access attempt.
        // Unknown/pending/corrupt records on a later boot never retry themselves.
        latch = {};
        latch.magic = 0x47423635U; latch.version = 1; latch.deviceType = DeviceType; latch.unitMah = 1;
        latch.state = static_cast<uint8_t>(ReloadState::Pending);
        latch.targetDesign = NominalMah; latch.targetFcc = NominalMah;
        std::memcpy(latch.mac, mac.data(), sizeof(latch.mac));
        if (!gauge_store_reload(latch, mac.data())) return finish(Status::Critical, "boot_critical_prepare_failure_latch");
    }
    gauge_boot_info.attempted = true; // one attempt per boot, even when OPEN later refuses/aborts.
    // Permission begins only AFTER history/factory checks and durable Pending.
    // The recursive transaction lock above prevents any manual caller sharing
    // this permission; all exits (including finally failures) revoke it.
    struct BootReloadScope {
        BootReloadScope() { boot_factory_reload_scope = true; }
        ~BootReloadScope() { boot_factory_reload_scope = false; }
    } reloadScope;
    char phaseReason[128]{}, closeReason[128]{};
    const bool opened = gaugeAccess(GaugeAccessAction::Open, phaseReason, sizeof(phaseReason));
    const bool applied = opened && gaugeSetNominalCapacity(FactoryMah, NominalMah, false, phaseReason, sizeof(phaseReason));
    // Finally is unconditional after entering the access-attempt scope. No key
    // or parameter retries are scheduled, regardless of which step failed.
    const bool closed = gaugeAccess(GaugeAccessAction::Restore, closeReason, sizeof(closeReason));
    bool safelySealed = false;
    uint16_t finalOperation = 0;
    bool finalStatusRead = false;
    {
        std::lock_guard<std::mutex> battery(battery_mutex);
        finalStatusRead = gauge_word(0x3A, finalOperation);
        safelySealed = finalStatusRead && configExitAccepted(finalOperation) && ((finalOperation >> 1) & 3) == 3;
    }
    bool reconciled = false;
    if (opened && applied && closed && safelySealed) {
        // Boot-owned closure only: applied==true in THIS serialized transaction
        // authorizes closing its successful Committed result after finally Seal.
        // Do not broaden the public interrupted-operation reconcile whitelist.
        std::lock_guard<std::mutex> battery(battery_mutex);
        Journal terminal{};
        AccessJournal access{};
        reconciled = gauge_load_journal(terminal, mac.data()) == ESP_OK &&
                     gauge_load_access(access, mac.data()) == ESP_OK && terminal.action == 1 &&
                     (terminal.state == static_cast<uint8_t>(State::Committed) ||
                      terminal.state == static_cast<uint8_t>(State::VerifiedSealedPrior3)) &&
                     terminal.targetDesign == NominalMah && terminal.targetFcc == NominalMah &&
                     terminal.lastDesign == terminal.targetDesign && terminal.lastFcc == terminal.targetFcc &&
                     access.state == static_cast<uint8_t>(AccessState::Restored) && access.priorSecurity == 3 &&
                     access.lastSecurity == 3 && access.unsealVerified && access.fullVerified && !failedDefaultAttempt(access);
        for (unsigned read = 0; read < 2 && reconciled; ++read) {
            reconciled = gauge_identity();
            sample_battery_locked(true);
            const auto& sample = battery_telemetry;
            reconciled = reconciled && sample.valid && sample.capacityValid && configExitAccepted(sample.operationStatus) &&
                         ((sample.operationStatus >> 1) & 3) == 3 && sample.designMah == terminal.targetDesign &&
                         sample.fullMah == terminal.targetFcc && sample.remainingMah <= sample.fullMah;
        }
        if (reconciled) {
            terminal.state = static_cast<uint8_t>(State::VerifiedSealedPrior3);
            reconciled = gauge_store_journal(terminal, mac.data());
        }
        if (!reconciled) std::snprintf(phaseReason, sizeof(phaseReason), "boot_owned_terminal_closure_failed");
    }
    const bool success = opened && applied && closed && safelySealed && reconciled;
    latch.state = static_cast<uint8_t>(success ? ReloadState::Completed : ReloadState::Failed);
    bool recorded = false;
    {
        std::lock_guard<std::mutex> battery(battery_mutex);
        recorded = gauge_store_reload(latch, mac.data());
    }
    if (!recorded) return finish(Status::Critical, "boot_critical_final_failure_latch_readback");
    if (!closed || !safelySealed) {
        char details[128]{};
        if (finalStatusRead) std::snprintf(details, sizeof(details),
            "boot_critical_final_cleanup observed_sec=%u cfg=%u cal=%u", (finalOperation >> 1) & 3,
            !!(finalOperation & 0x0400), !!(finalOperation & 1));
        else std::snprintf(details, sizeof(details), "boot_critical_final_cleanup_security_unreadable");
        return finish(Status::Critical, details);
    }
    if (!success) {
        char details[128]{};
        std::snprintf(details, sizeof(details), "boot_critical_attempt_failed_sealed_no_retry: %.72s", phaseReason);
        return finish(Status::Critical, details);
    }
    return finish(Status::Applied, "boot_applied65_experimental_sealed_verified_once");
}

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
    // S31's per-transaction bit-length cap splits a color strip into several
    // queued transfers. Reserve extra DMA descriptors for those split tails;
    // this allocates descriptors, not another pixel buffer. The LCD driver
    // still obtains/caps each transaction via spi_bus_get_max_transaction_len.
    spi.max_transfer_sz = Resolution * DisplayBufferRows * 4;
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
    // Driver 2.1.0 default sequence, except zero brightness and no DISPON.
    // Its default enables a fully bright panel before any pixel RAM is drawn.
    // Keep the panel off until the first LVGL frame drains through tx_param.
    static const uint8_t zero[] = {0x00}, mode[] = {0x80}, control[] = {0x20}, full[] = {0xFF};
    static const uint8_t columns[] = {0x00, 0x06, 0x01, 0xDD}, rows[] = {0x00, 0x00, 0x01, 0xD1};
    static const co5300_lcd_init_cmd_t darkInit[] = {
        {0xFE, zero, 0, 0}, {0xC4, mode, 1, 0}, {0x35, zero, 0, 10},
        {0x53, control, 1, 10}, {0x51, zero, 1, 10}, {0x63, full, 1, 10},
        {0x2A, columns, 4, 0}, {0x2B, rows, 4, 0}, {0x11, zero, 0, 60},
    };
    co5300_vendor_config_t vendor{};
    vendor.init_cmds = darkInit;
    vendor.init_cmds_size = sizeof(darkInit) / sizeof(darkInit[0]);
    vendor.flags.use_qspi_interface = 1;
    esp_lcd_panel_dev_config_t config{};
    config.reset_gpio_num = GPIO_NUM_42;
    config.rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB;
    config.bits_per_pixel = 16;
    config.vendor_config = &vendor;
    ESP_ERROR_CHECK(esp_lcd_new_panel_co5300(panel_io, &config, &panel));
    ESP_ERROR_CHECK(esp_lcd_panel_reset(panel));
    ESP_ERROR_CHECK(esp_lcd_panel_init(panel));
    ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(panel, false));
    panel_brightness = 0;
    pending_boot_display = true;
    pending_wake_brightness = getBackLightBrightness(true);
#if CONFIG_MOSAICO_SLEEP_IO_RETENTION && CONFIG_IDF_TARGET_ESP32S31
    // Exact SPI2 matrix signals are validated; unknown mux/role modes fail closed.
    apply_sleep_io(mosaico_sleep_io::Role::Reset,config.reset_gpio_num,SIG_GPIO_OUT_IDX,false);
    apply_sleep_io(mosaico_sleep_io::Role::CS,io.cs_gpio_num,SPI2_CS_PAD_OUT_IDX,true);
    apply_sleep_io(mosaico_sleep_io::Role::Clock,spi.sclk_io_num,SPI2_CK_PAD_OUT_IDX,true);
    apply_sleep_io(mosaico_sleep_io::Role::Data0,spi.data0_io_num,SPI2_D_PAD_OUT_IDX,true);
    apply_sleep_io(mosaico_sleep_io::Role::Data1,spi.data1_io_num,SPI2_Q_PAD_OUT_IDX,true);
    apply_sleep_io(mosaico_sleep_io::Role::Data2,spi.data2_io_num,SPI2_WP_PAD_OUT_IDX,true);
    apply_sleep_io(mosaico_sleep_io::Role::Data3,spi.data3_io_num,SPI2_HOLD_PAD_OUT_IDX,true);
#endif
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
#if CONFIG_MOSAICO_LVGL_MONOTONIC_TICK
    // Only the existing port timer changes; handler deadlines and touch cadence
    // remain owned by LVGL. Its lv_tick_inc no longer supplies the public tick.
    config.timer_period_ms = mosaico_lvgl_tick::MonotonicTimerPeriodMs;
#else
    // Legacy period supplies both esp_timer scheduling and lv_tick_inc.
    config.timer_period_ms = mosaico_touch_power::LvglTickPeriodMs;
#endif
    ESP_ERROR_CHECK(lvgl_port_init(&config));
#if CONFIG_MOSAICO_LVGL_MONOTONIC_TICK
    // Bootstrap directly: the HAL readiness flag must not become visible before
    // offset/callback installation. The port task has already completed lv_init.
    if (!lvgl_port_lock(0)) ESP_ERROR_CHECK(ESP_ERR_TIMEOUT);
    const uint32_t prior_tick=lv_tick_get();
    const uint32_t monotonic_ms=mosaico_lvgl_tick::milliseconds(esp_timer_get_time());
    lvgl_tick_offset.store(mosaico_lvgl_tick::startupOffset(prior_tick,monotonic_ms),std::memory_order_relaxed);
    lv_tick_set_cb(monotonic_lvgl_tick);
    lvgl_timer_period_ms=config.timer_period_ms;
    port_ready.store(true,std::memory_order_release);
#else
    lvgl_timer_period_ms=config.timer_period_ms;
    port_ready.store(true,std::memory_order_release);
    if (!lvglLock()) ESP_ERROR_CHECK(ESP_ERR_TIMEOUT);
#endif
    lvgl_port_display_cfg_t disp_config{};
    disp_config.io_handle = panel_io;
    disp_config.panel_handle = panel;
    rotation_control.swap_xy = checked_swap;
    rotation_control.mirror = checked_mirror;
    rotation_result.reset();
    disp_config.control_handle = &rotation_control;
    disp_config.buffer_size = Resolution * DisplayBufferRows;
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
    // The pinned port only registers its rotation callback at add_disp; it
    // does not issue initial MADCTL writes. Establish portrait explicitly,
    // through the same checked proxy while the panel is still dark and the
    // LVGL mutex excludes every flush. Do not assume callback registration
    // itself supplies these two calls.
    rotation_result.reset();
    checked_swap(&rotation_control, false);
    checked_mirror(&rotation_control, false, false);
    if (!rotation_result.ok()) {
        orientation_healthy.store(false);
        ESP_LOGE(Tag, "Initial rotation uncertain calls=%u error=%ld", rotation_result.calls, static_cast<long>(rotation_result.error));
    }
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
    lv_timer_t* touchTimer = lv_indev_get_read_timer(lvTouchpad);
    lv_timer_set_period(touchTimer, mosaico_touch_power::AwakePeriodMs); // NEVER period 0.
    if (touch_idle_polling.load(std::memory_order_relaxed)) {
        lv_timer_pause(touchTimer); // Includes an idle request made before port initialization.
        lv_indev_reset(lvTouchpad, nullptr);
        lv_indev_read(lvTouchpad); // idleguard publishes software RELEASE without touching the driver.
    }
    lv_obj_set_style_bg_color(lv_screen_active(), lv_color_black(), LV_PART_MAIN);
    bootLogo = std::make_unique<BootLogo>();
    lvglUnlock();
    if (xTaskCreate(motion_task, "motion-cache", 4096, _i2c_bus, 2, &motion_task_handle) != pdPASS) {
        portENTER_CRITICAL(&motion_mux); motion_snapshot.error = ESP_ERR_NO_MEM; portEXIT_CRITICAL(&motion_mux);
    }
    ESP_LOGI(Tag, "CO5300/CST9217 480x480; geometry requires physical acceptance");
#if CONFIG_MOSAICO_SLEEP_IO_RETENTION && CONFIG_IDF_TARGET_ESP32S31
    { std::lock_guard<std::mutex> guard(sleep_io_mutex);refresh_sleep_io_locked(); }
#endif
}

bool Hal::lvglLock() { return port_ready && lvgl_port_lock(0); }
void Hal::lvglUnlock() { if (port_ready) lvgl_port_unlock(); }

void Hal::setTouchIdlePolling(bool idle)
{
    if (!port_ready) {
        // Preserve a startup request; lvgl_init applies it to the new timer.
        touch_idle_polling.store(idle, std::memory_order_relaxed);
        portENTER_CRITICAL(&touch_mux);
        cached_touch = TouchPoint{};
        portEXIT_CRITICAL(&touch_mux);
        return;
    }
    if (!lvglLock()) return;
    if (touch_idle_polling.load(std::memory_order_relaxed) == idle) {
        lvglUnlock();
        return; // No reset, task wake or invalidation on repeated requests.
    }
    touch_idle_polling.store(idle, std::memory_order_relaxed);
    portENTER_CRITICAL(&touch_mux);
    cached_touch = TouchPoint{};
    portEXIT_CRITICAL(&touch_mux);
    if (lvTouchpad) {
        lv_timer_t* timer = lv_indev_get_read_timer(lvTouchpad);
        if (timer) {
            lv_indev_reset(lvTouchpad, nullptr); // Cancel old object/drag/long-press state.
            if (idle) {
                lv_timer_pause(timer);
                touch_wait_for_release = false;
                lv_indev_read(lvTouchpad); // Forces indev state RELEASED via the no-I2C idle callback.
            } else {
                touch_wait_for_release = true;
                lv_indev_wait_release(lvTouchpad);
                lv_timer_set_period(timer, mosaico_touch_power::AwakePeriodMs);
                lv_timer_resume(timer);
                lv_timer_reset(timer);
                lv_timer_ready(timer);
                // Wake the existing port task, not a new task/timer, so an
                // external button-driven wake gets a fresh sample promptly.
                ESP_ERROR_CHECK(lvgl_port_task_wake(LVGL_PORT_EVENT_USER, nullptr));
            }
        }
    }
    lvglUnlock();
}

Hal::SleepIoInfo Hal::sleepIoRetentionInfo() const {
    SleepIoInfo info;
    { std::lock_guard<std::mutex> guard(sleep_io_mutex);refresh_sleep_io_locked();info=sleep_io_model.state; }
    // No GPIO mutation in this report. A detected inconsistency does immediately
    // block LS through the existing recovery lock AFTER releasing our mutex.
    if(info.enabled && !info.ready)StandbySleep::cancelForActivity();
    return info;
}
bool Hal::sleepIoRetentionReady() const {
#if CONFIG_MOSAICO_SLEEP_IO_RETENTION
    return sleep_io_ready.load(std::memory_order_acquire);
#elif CONFIG_ESP_SLEEP_GPIO_RESET_WORKAROUND || CONFIG_PM_SLP_DISABLE_GPIO
    return false;
#else
    return true;
#endif
}
Hal::TouchPollingInfo Hal::touchPollingInfo() const
{
    const bool idle = touch_idle_polling.load(std::memory_order_relaxed);
    TouchPollingInfo info{idle, mosaico_touch_power::pollingPeriodMs(idle), unused_gates_off.load(std::memory_order_relaxed)};
    if(port_ready.load(std::memory_order_acquire)) {
        info.lvglTimerPeriodMs=lvgl_timer_period_ms;
#if CONFIG_MOSAICO_LVGL_MONOTONIC_TICK
        info.monotonicTick=true;
#endif
    }
    return info;
}
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
            if (locked && display && (pending_boot_display || (target > WakeDimThreshold &&
                ((panel_brightness >= 0 && panel_brightness <= WakeDimThreshold) || pending_wake_brightness >= 0)))) {
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

Hal::MotionOrientation Hal::motionOrientation() const
{
    portENTER_CRITICAL(&motion_mux); auto snapshot = motion_snapshot; portEXIT_CRITICAL(&motion_mux);
    snapshot.idle = motion_idle.load();
    if (snapshot.idle) snapshot.valid = false;
    return snapshot;
}
void Hal::setMotionIdle(bool idle)
{
    if (motion_idle.exchange(idle) != idle && motion_task_handle) xTaskNotifyGive(motion_task_handle);
}
uint16_t Hal::getDisplayOrientation() const { return display_degrees.load(); }
bool Hal::isDisplayOrientationHealthy() const { return orientation_healthy.load(); }
bool Hal::setDisplayOrientation(uint16_t degrees)
{
    if (degrees != 0 && degrees != 90 && degrees != 180 && degrees != 270) return false;
    if (!display || !panel || !orientation_healthy.load() || !lvglLock()) return false;
    if (display_degrees.load() != degrees) {
        // tx_param drains the SPI color queue before MADCTL hardware rotation.
        // Hold LVGL mutex throughout, excluding any new partial-buffer flush.
        const esp_err_t drained = esp_lcd_panel_co5300_set_brightness(panel, panel_brightness < 0 ? 0 : panel_brightness);
        if (drained != ESP_OK) { lvglUnlock(); return false; }
        if (lvTouchpad) { lv_indev_reset(lvTouchpad, nullptr); lv_indev_wait_release(lvTouchpad); }
        touch_wait_for_release = true;
        portENTER_CRITICAL(&touch_mux); cached_touch = {}; portEXIT_CRITICAL(&touch_mux);
        // esp_lvgl_port RESOLUTION_CHANGED handler applies CO5300 mirror/swap;
        // flags.sw_rotate stays false: no full-frame software buffers.
        const uint16_t oldDegrees = display_degrees.load();
        rotation_result.reset();
        lv_display_set_rotation(display, static_cast<lv_display_rotation_t>(degrees / 90));
        const bool applied = rotation_result.ok();
        if (!applied) {
            const int32_t forwardError = rotation_result.error;
            const unsigned forwardCalls = rotation_result.calls;
            rotation_result.reset();
            // LVGL has committed its target before firing the synchronous event;
            // changing back triggers both checked hardware operations again.
            lv_display_set_rotation(display, static_cast<lv_display_rotation_t>(oldDegrees / 90));
            const auto outcome = mosaico_orientation::rotationOutcome(false, rotation_result.ok());
            if (outcome == mosaico_orientation::RotationOutcome::Unsafe) {
                orientation_healthy.store(false);
                // No I2C from this point: subsequent indev reads release only.
                if (lvTouchpad) {
                    lv_timer_t* timer = lv_indev_get_read_timer(lvTouchpad);
                    if (timer) lv_timer_pause(timer);
                    lv_indev_read(lvTouchpad);
                }
            }
            ESP_LOGE(Tag, "Rotation rejected error=%ld calls=%u rollback=%ld calls=%u healthy=%d",
                     static_cast<long>(forwardError), forwardCalls,
                     static_cast<long>(rotation_result.error), rotation_result.calls, orientation_healthy.load());
            lv_obj_invalidate(lv_display_get_screen_active(display));
            lvglUnlock(); return false;
        }
        display_degrees.store(degrees);
        lv_obj_invalidate(lv_display_get_screen_active(display));
    }
    lvglUnlock(); return true;
}
