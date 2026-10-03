/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 * SPDX-FileCopyrightText: 2026 qqice and OpenFrameTap Mosaico contributors
 * SPDX-License-Identifier: MIT
 *
 * Board wiring and stock CO5300/CST9217 setup adapted from
 * OpenFrameTap-Mosaico/firmware/esp_mosaico/main/oft_board.c (MIT),
 * referencing espressif/esp_boards 0.6.1 ESP-Mosaico V1.0.
 * No camera, network, gauge correction, charger or NAND functionality.
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

bool gauge_word(uint8_t reg, uint16_t& value)
{
    uint8_t bytes[2];
    // Only a standard measurement-register pointer followed by a read. Never
    // issue control, unseal, data-memory, calibration or configuration writes.
    const esp_err_t result = i2c_master_transmit_receive(gauge, &reg, 1, bytes, sizeof(bytes), 20);
    esp_rom_delay_us(100); // TI bus-free timing, inherited from board reference.
    if (result != ESP_OK) return false;
    value = bytes[0] | (static_cast<uint16_t>(bytes[1]) << 8);
    return true;
}

void sample_battery_locked()
{
    const int64_t now = esp_timer_get_time();
    if (!gauge || (battery_sample_us && now - battery_sample_us < 5000000)) return;
    battery_sample_us = now;
    uint16_t soc = 0, mv = 0, raw_ma = 0;
    battery_valid = gauge_word(0x2c, soc) && gauge_word(0x08, mv) && gauge_word(0x0c, raw_ma) &&
                    soc <= 100 && mv >= 2000 && mv <= 5000;
    if (battery_valid) {
        battery_soc = static_cast<uint8_t>(soc);
        battery_mv = mv;
        battery_raw_ma = static_cast<int16_t>(raw_ma);
    }
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
