/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 * SPDX-FileCopyrightText: 2026 qqice and OpenFrameTap Mosaico contributors
 * SPDX-License-Identifier: MIT
 *
 * Board wiring and stock CO5300/CST9217 setup adapted from
 * OpenFrameTap-Mosaico/firmware/esp_mosaico/main/oft_board.c (MIT),
 * referencing espressif/esp_boards 0.6.1 ESP-Mosaico V1.0.
 * Boot CEDV profile uses the pinned Espressif BQ27220 driver and board values.
 * Legacy nominal transactions reference oft_gauge.c and TI SLUUBD4A.
 * No virtual-capacity/gain correction or charger programming.
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
#include "../legacy_factory_mac.h"
#include "../utils/settings/settings.h"
#include "mosaico_gauge_model.h"
#include "mosaico_official_gauge.h"
#include "mosaico_fcc_limit_journal_codec.h"
#include "mosaico_ocv_diagnostic_model.h"
#include "mosaico_threshold_audit_model.h"
#include "mosaico_gauge_calibration_read_model.h"
#include "mosaico_gauge_public_extra_read_model.h"
#include "mosaico_near_full.h"
#include "mosaico_gauge_cold_runtime.h"
#include <host/network_quota.h>
#include <host/mosaico_system_log.h>
#ifndef MOSAICO_GAUGE_DEFER_ENGINE_DIAGNOSTICS
#define MOSAICO_GAUGE_DEFER_ENGINE_DIAGNOSTICS 1
#endif
#include <hal/ble/codex_micro_ble.h>
#include <ota/mosaico_ota.h>
#include "mosaico_boot_splash_model.h"
#include <debug/boot_trace.h>
#include "mosaico_touch_power_model.h"
#include "mosaico_lvgl_tick_model.h"
#include "mosaico_orientation_model.h"
#include "mosaico_haptic_model.h"
#include "bmi270.h"
#include "driver/gpio.h"
#include "driver/ledc.h"
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
#include "esp_system.h"
#include <host/standby_sleep.h>
#include <host/touch_sleep.h>
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
#if CONFIG_MOSAICO_CST_SLEEP_TRIAL && CONFIG_IDF_TARGET_ESP32S31
i2c_master_dev_handle_t touch_control = nullptr; // One app-owned control adapter, same existing bus/address.
#endif
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
// Core V1.0 and V1.2.1 schematics both map MOTOR to GPIO8 (p2/p3).
// This does not broaden the existing V1.0 sleep-IO assumptions or authorize
// codec, PA, rail or gauge writes; motor polarity follows the factory driver.
std::mutex haptic_mutex;
mosaico_haptic::Model haptic_model;
esp_timer_handle_t haptic_timer = nullptr;
bool haptic_initialized = false, haptic_channel_attached = false, haptic_blocked = false;
mosaico_haptic::TimerState haptic_pwm; // haptic_mutex owned; failures remain unknown.
std::atomic<bool> haptic_ready{false}, haptic_active{false};

esp_err_t haptic_pause_locked() {
    if (!haptic_pwm.needsPause()) return ESP_OK;
    const esp_err_t result = ledc_timer_pause(LEDC_LOW_SPEED_MODE, LEDC_TIMER_1);
    haptic_pwm.paused(result == ESP_OK);
    // SDK ledc.c: pause fails only without the driver context (or bad args).
    // Public deconfigure also requires that same context AND an already paused
    // timer; it cannot rescue a pause failure. Never guess private power APIs.
    return result;
}

bool haptic_off_locked() {
    haptic_model.stop();
    if (haptic_timer) esp_timer_stop(haptic_timer); // Inactive / dispatched is benign.
    esp_err_t stopped = ESP_OK;
    if (haptic_channel_attached) stopped = ledc_stop(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_1, 0);
    // GPIO output latch is LOW before detaching the peripheral. Restore the
    // original GPIO mapping so existing sleep-IO baselines/masks remain valid.
    const esp_err_t latch = gpio_set_level(GPIO_NUM_8, 0);
    gpio_config_t motor{};
    motor.pin_bit_mask = 1ULL << 8;
    motor.mode = GPIO_MODE_OUTPUT;
    const esp_err_t detached = gpio_config(&motor);
    haptic_channel_attached = false;
    const bool off = latch == ESP_OK && detached == ESP_OK;
    // Stop output/detach GPIO LOW FIRST: pausing a nonzero PWM alone could
    // freeze it high. ledc_stop stops the channel, not the 4 kHz timer counter.
    const esp_err_t paused = haptic_pause_locked();
    haptic_active.store(!off || paused != ESP_OK, std::memory_order_release); // Unknown is not sleep-safe.
    if (!off || stopped != ESP_OK || paused != ESP_OK) {
        haptic_ready.store(false, std::memory_order_release);
        ESP_LOGW(Tag, "Motor OFF stop=%ld latch=%ld gpio=%ld pause=%ld", long(stopped), long(latch), long(detached), long(paused));
    }
    return off && stopped == ESP_OK && paused == ESP_OK;
}

void haptic_timeout(void*) {
    std::lock_guard<std::mutex> guard(haptic_mutex);
    if (!haptic_model.active) return;
    const int64_t remaining = haptic_model.remaining(esp_timer_get_time());
    if (remaining == 0) { haptic_off_locked(); return; }
    // A callback already queued before stop/restart can run against a new
    // deadline. Keep the new timer if armed; otherwise restore the one-shot.
    // Never repeatedly extend the deadline; a rearm failure forces LOW.
    if (!esp_timer_is_active(haptic_timer) && esp_timer_start_once(haptic_timer, remaining) != ESP_OK) {
        haptic_ready.store(false, std::memory_order_release);
        haptic_off_locked();
    }
}

esp_err_t haptic_attach_locked() {
    ledc_channel_config_t channel{};
    channel.gpio_num = GPIO_NUM_8;
    channel.speed_mode = LEDC_LOW_SPEED_MODE;
    channel.channel = LEDC_CHANNEL_1;
    channel.timer_sel = LEDC_TIMER_1;
    channel.duty = 0;
    channel.sleep_mode = LEDC_SLEEP_MODE_NO_ALIVE_NO_PD; // Never keep motor alive in sleep.
    haptic_channel_attached = true; // Even a partially failed config needs OFF cleanup.
    return ledc_channel_config(&channel);
}

void haptic_init_locked() {
    if (haptic_initialized) return; // Nonfatal failure stays disabled; no retry loop.
    haptic_initialized = true;
    ledc_timer_config_t timer{};
    timer.speed_mode = LEDC_LOW_SPEED_MODE;
    timer.duty_resolution = LEDC_TIMER_10_BIT;
    timer.timer_num = LEDC_TIMER_1;
    // Factory YAML omits frequency. esp-board-manager's published periph_ledc
    // schema defaults to 4 kHz; factory's exact resolved manager is not locked
    // in the reference snapshot. Physical drive acceptance remains required.
    timer.freq_hz = 4000;
    timer.clk_cfg = LEDC_AUTO_CLK;
    haptic_pwm.configuring(); // config automatically resumes; failure may be partial.
    esp_err_t result = ledc_timer_config(&timer);
    // No channel is attached yet, so GPIO8 remains LOW even if pause fails.
    const esp_err_t paused = haptic_pause_locked();
    if (result == ESP_OK) result = paused;
    if (result == ESP_OK) result = haptic_attach_locked();
    if (result == ESP_OK) {
        esp_timer_create_args_t args{};
        args.callback = haptic_timeout;
        args.name = "motor-off";
        result = esp_timer_create(&args, &haptic_timer);
    }
    const bool off = haptic_off_locked();
    haptic_ready.store(result == ESP_OK && off, std::memory_order_release);
    if (result != ESP_OK) ESP_LOGW(Tag, "Motor unavailable error=%ld", long(result));
}
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
// Implemented in the narrowly scoped runtime include below. All calls own the
// transaction lock; sample/context access also owns battery_mutex.
bool scale_boot_fence_locked();
// Normal capacity maintenance never dispatches an experimental auto-restore.
bool normal_gauge_fence_locked();
void gauge_legacy_cache_init_locked();
struct NormalGaugeStorageLease {
    bool held=false,acquired=false;
    NormalGaugeStorageLease() {
        // A request arriving during our transaction may block NEW forward
        // work, but must never prevent its owner from finally sealing.
        if(MosaicoOta::currentTaskOwnsStorageLease())held=true;
        else if(!MosaicoOta::busy()&&!MosaicoOta::healthPending())acquired=held=MosaicoOta::tryStorageLease();
    }
    ~NormalGaugeStorageLease(){if(acquired)MosaicoOta::releaseStorageLease();}
};
#define MOSAICO_FACTORY_SCALE_RUNTIME 1
#define MOSAICO_GAUGE_CONTRAST_RUNTIME 1
#define MOSAICO_GAUGE_COLD_RUNTIME 1
bool cold_poll_supervisor();
bool contrast_present_locked();
bool contrast_boot_fence_locked();
mosaico_gauge_physical::UnitContext contrast_context_locked();
bool contrast_poll_locked();
bool contrast_protected();
bool contrast_audit_allowed_locked();
bool contrast_maintenance_original_cached_locked();
bool contrast_maintenance_cached_safe_locked();
bool factory_scale_present_locked();
bool factory_scale_boot_fence_locked();
mosaico_gauge_physical::UnitContext factory_scale_context_locked();
void factory_scale_invalidate_locked();
bool factory_scale_poll_locked();
bool factory_scale_protected();
bool factory_scale_audit_allowed_locked();
size_t factory_scale_reuse_credit_locked();
bool factory_scale_maintenance_cached_safe_locked();
bool factory_scale_maintenance_original_cached_locked();
mosaico_gauge_physical::UnitContext scale_unit_context_locked();
bool scale_journal_missing_locked();
void scale_invalidate_context_locked();
void native_recovery_revoke_locked();
void native_recovery_check_sample_locked(uint16_t dc,uint16_t fcc,uint16_t op,bool valid);
bool scale_safety_mismatch_locked();
// Private, transaction-mutex owned privilege: only a durable boot intent may
// enable it. Public manual access/nominal/restore retains its strict policy.
bool boot_factory_reload_scope = false;
uint16_t normal_gauge_target = 0; // transaction-owned cache of normal_mah, not units proof.
// Per-call cleanup outcome, exclusively owned by gauge_transaction_mutex.
// Boot must not repeat an OPEN abort's protective Seal, even if it failed.
bool gauge_access_cleanup_attempted = false;
bool gauge_access_cleanup_succeeded = false;
Hal::GaugeBootReloadInfo gauge_boot_info;
Hal::GaugeDiagnosticSnapshot gauge_boot_before{}, gauge_boot_after{};
Hal::GaugeEngineSnapshot gauge_engine_before{}, gauge_engine_after{};
Hal::GaugeDiagnosticSnapshot gauge_boot_post_exit{};
Hal::GaugeEngineSnapshot gauge_engine_post_exit{};
// Volatile per-boot diagnostic latch; only the transaction owner may change it.
bool gauge_ocv_attempted=false;
bool gauge_threshold_audit_attempted=false;
// Small RAM-only snapshot; never a permission or persistence source. Both mutexes protect it.
mosaico_threshold::Values gauge_threshold_cached{};
mosaico_calibration::Values gauge_calibration_cached{};
mosaico_public_extra::Values gauge_public_extra_cached{};
uint32_t gauge_public_extra_generation=0;
uint32_t gauge_threshold_cached_generation=0;
static_assert(sizeof(gauge_threshold_cached)+sizeof(gauge_threshold_cached_generation)<200);
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
        const bool requestedIdle = motion_idle.load();
        const bool trialIdle = GetHAL().gaugeScaleTrialActive(); // Cached atomic; never gauge I2C.
        const bool idle = requestedIdle || trialIdle;
        if (enabled == idle) {
            snapshot.error = motion_write(imu, 0x7d, idle ? 0x00 : 0x04); // Only accelerometer.
            if (snapshot.error == ESP_OK) { enabled = !idle; if (enabled) vTaskDelay(pdMS_TO_TICKS(80) + 2); }
            model.resetPending(); snapshot.valid = false;
        }
        snapshot.idle = !enabled; // Report actual enabled state, not a second policy read.
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
        // Explicit idle wakes by notification; protection-only idle must recheck
        // because clearing the cached trial fence does not notify this task.
        ulTaskNotifyTake(pdTRUE, idle && !enabled ? (requestedIdle ? portMAX_DELAY : pdMS_TO_TICKS(250) + 1) : pdMS_TO_TICKS(80) + 1);
        // A read failure is retried only on the next bounded sample, not a spin.
        if (enabled && !idle) snapshot.error = ESP_OK;
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
    if (GetHAL().gaugeScaleTrialActive()) {
        touch_wait_for_release = true;
        portENTER_CRITICAL(&touch_mux); cached_touch = {}; portEXIT_CRITICAL(&touch_mux);
        data->state = LV_INDEV_STATE_RELEASED;
        return; // Boot-awake recovery: no touch I2C, no stale cached contact.
    }
    if (touch_idle_polling.load(std::memory_order_relaxed) || TouchSleep::blocksTouch() || !orientation_healthy.load()) {
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
    // Preserve the full requested minimum in monotonic time. Per-chunk +1
    // ticks accumulated padding; tick phase can also make a delay shorter.
    // The deadline handles both without reducing any CFG/security wait.
    const int64_t until = esp_timer_get_time() + int64_t(ms) * 1000;
    for (;;) {
        const int64_t remaining = until - esp_timer_get_time();
        if (remaining <= 0) break;
        gauge_feed();
        const uint32_t chunk = static_cast<uint32_t>(std::min<int64_t>(100, (remaining + 999) / 1000));
        vTaskDelay(std::max<TickType_t>(1, pdMS_TO_TICKS(chunk)));
    }
    gauge_feed();
}

// Optional stack-owned capture, passed only by the locked maintenance provider.
// Stages: 1=initial standard reads, 2=engine transmit, 3=engine read,
// 4=final standard reads. Identity validation is represented by its validMask,
// independently of the first real transport error (never fabricate an errno).
struct MaintenanceGaugeTransportDiagnostic {
    esp_err_t firstError = ESP_OK;
    uint8_t firstReg = 0, firstStage = 0;
    void record(esp_err_t error, uint8_t reg, uint8_t stage) {
        if (error != ESP_OK && firstError == ESP_OK) {
            firstError = error; firstReg = reg; firstStage = stage;
        }
    }
};

bool gauge_read_diagnostic(uint8_t reg, uint8_t* bytes, size_t size,
                           MaintenanceGaugeTransportDiagnostic* diagnostic, uint8_t stage)
{
    gauge_feed();
    const esp_err_t result = i2c_master_transmit_receive(gauge, &reg, 1, bytes, size, 20);
    esp_rom_delay_us(100); // TI bus-free timing, inherited from board reference.
    gauge_feed();
    if (diagnostic) diagnostic->record(result, reg, stage);
    return result == ESP_OK;
}

bool gauge_read(uint8_t reg, uint8_t* bytes, size_t size)
{
    return gauge_read_diagnostic(reg, bytes, size, nullptr, 1);
}

bool gauge_word_diagnostic(uint8_t reg, uint16_t& value,
                          MaintenanceGaugeTransportDiagnostic* diagnostic, uint8_t stage)
{
    uint8_t bytes[2];
    const bool result = gauge_read_diagnostic(reg, bytes, sizeof(bytes), diagnostic, stage);
    if (!result) return false;
    value = bytes[0] | (static_cast<uint16_t>(bytes[1]) << 8);
    return true;
}

// Keep the exact two-argument Reader/function-pointer contract for all callers.
bool gauge_word(uint8_t reg, uint16_t& value)
{
    return gauge_word_diagnostic(reg, value, nullptr, 1);
}

// Requires the same transaction -> battery lock order as standard diagnostics.
void gauge_engine_locked(Hal::GaugeEngineSnapshot& out,uint8_t phase,uint8_t outcome,
                         MaintenanceGaugeTransportDiagnostic* diagnostic = nullptr)
{
    out={};out.sampledUs=esp_timer_get_time();out.phase=phase;out.outcome=outcome;
    const auto units=scale_unit_context_locked();
    out.units={units.factor,units.unitEpoch,units.generation,units.verified,uint8_t(units.phase)};
    if(mosaico_gauge_engine::startupDeferred(MOSAICO_GAUGE_DEFER_ENGINE_DIAGNOSTICS,
                                            uint64_t(out.sampledUs)/1000)){
        out.validMask=mosaico_gauge_engine::StartupDeferred;
        return; // No sleep, MAC transmit, or diagnostic retry; preserve phase/time/units.
    }
    if(!gauge)return;
    // Serialized by gauge_transaction_mutex. Mark only the first actual group
    // in the deferred variant, not skipped calls or NAND collection/flush time.
    static bool firstEngineDiagnosticSent=false;
    struct EngineTransport {
        bool markFirst=false,started=false;
        MaintenanceGaugeTransportDiagnostic* diagnostic = nullptr;
        bool transmit(const uint8_t* p,size_t n){
            gauge_feed();
            if(!gauge)return false;
            if(markFirst&&!started){
                started=true;
                MosaicoSystemLog::engineDiagnosticEvent(true,0,uint64_t(esp_timer_get_time())/1000);
            }
            const esp_err_t result=i2c_master_transmit(gauge,p,n,20);
            if(diagnostic)diagnostic->record(result,n?p[0]:0,2);
            return result==ESP_OK;
        }
        void delayMs(uint32_t ms){gauge_delay_ms(ms);}
        bool gauge_read(uint8_t reg,uint8_t* p,size_t n){return gauge_read_diagnostic(reg,p,n,diagnostic,3);}
        bool read(uint8_t reg,uint8_t* p,size_t n){
            if(reg==0x3e&&n==2)return gauge_read(reg,p,n);
            if(reg!=0x40||(n!=11&&n!=23&&n!=17))return false;
            // Board-proven word reads, with an exact final odd byte.
            for(size_t i=0;i<n;i+=2){
                const size_t count=n-i>=2?2:1;
                if(!gauge_read(static_cast<uint8_t>(0x40+i),p+i,count))return false;
            }
            return true;
        }
    } transport;
    transport.diagnostic=diagnostic;
    transport.markFirst=MOSAICO_GAUGE_DEFER_ENGINE_DIAGNOSTICS&&!firstEngineDiagnosticSent;
    const bool ok=mosaico_gauge_engine::read(transport,out);
    if(transport.started){
        firstEngineDiagnosticSent=true;
        MosaicoSystemLog::engineDiagnosticEvent(false,ok?1:2,uint64_t(esp_timer_get_time())/1000);
    }
}

// Requires gauge_transaction_mutex -> battery_mutex. This adapter is closed:
// only Control(0x0056), no mutating gauge_control or generic selector exposure.
struct GaugeStatusReadTransport {
    bool transmit(const uint8_t* packet, size_t count) {
        gauge_feed();
        return gauge && i2c_master_transmit(gauge,packet,count,20)==ESP_OK;
    }
    void delayMs(uint32_t ms) { gauge_delay_ms(ms); }
    bool read(uint8_t reg, uint8_t* data, size_t count) { return gauge_read(reg,data,count); }
};

void gauge_diagnostic_locked(Hal::GaugeDiagnosticSnapshot& out, uint8_t phase, uint8_t outcome)
{
    using namespace mosaico_gauge_status;
    out={}; out.phase=phase; out.outcome=outcome; out.sampledUs=esp_timer_get_time();
    const auto units=scale_unit_context_locked();
    out.units={units.factor,units.unitEpoch,units.generation,units.verified,uint8_t(units.phase)};
    if(!gauge)return;
    const auto word=[&](uint8_t reg,uint16_t& value,uint32_t bit) {
        if(gauge_word(reg,value))out.validMask|=bit;
    };
    word(0x2c,out.rawSoc,Soc); word(0x10,out.remainingMah,Remaining);
    word(0x12,out.fullMah,Full); word(0x3c,out.designMah,Design);
    word(0x08,out.voltageMv,Voltage);
    uint16_t current=0,average=0;
    word(0x0c,current,Current); word(0x14,average,Average);
    out.currentMa=static_cast<int16_t>(current); out.averageCurrentMa=static_cast<int16_t>(average);
    word(0x22,out.rawCoulombMah,RawCoulomb); word(0x2a,out.cycleCount,Cycles);
    word(0x3a,out.operationStatus,Operation); word(0x0a,out.batteryStatus,Battery);
    GaugeStatusReadTransport transport;
    uint32_t protocolFlags=0;
    if(readGaugingStatus(transport,out.gaugingStatus,out.rawEcho16,protocolFlags))out.validMask|=Gauging;
    out.validMask|=protocolFlags;
    if((out.validMask & (Gauging|Battery))==(Gauging|Battery)) {
        out.mode=decodeMode(out.gaugingStatus,out.batteryStatus);
        if(out.mode!=Unknown)out.validMask|=ModeValid;
    }
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
    sample.temperatureValid = gauge_word(0x06, sample.temperatureDeciK);
    // Optional standard read, once per existing cached sweep; independent of
    // capacity/core validity and never another MAC/security transaction.
    sample.rawCoulombValid = gauge_word(0x22, sample.rawCoulombMah);
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
    if(core&&status&&capacities)
        native_recovery_check_sample_locked(sample.designMah,sample.fullMah,sample.operationStatus,
                                           sample.valid&&sample.capacityValid);
    auto units=scale_unit_context_locked();
    // A RAM-loss/profile/security change revokes this boot's full131 proof.
    // Transient unreadable samples are invalid, not evidence of a new profile.
    if(units.verified&&core&&status&&capacities&&
       (!sample.valid||!sample.capacityValid||sample.designMah!=65*units.factor||
        sample.fullMah>mosaico_gauge::MaxReasonableFcc*units.factor||
        !mosaico_gauge::configExitAccepted(sample.operationStatus)||((sample.operationStatus>>1)&3)!=3)){
        native_recovery_revoke_locked();
        scale_invalidate_context_locked();units=scale_unit_context_locked();
    }
    sample.unitFactor=units.factor;sample.unitEpoch=units.unitEpoch;
    sample.unitGeneration=units.generation;sample.unitVerified=units.verified;
    sample.knownSafetyCalibrationMismatch=scale_safety_mismatch_locked();
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
#ifdef MOSAICO_GAUGE_COLD_RUNTIME
    if(command!=GaugeCommand::Identity&&!MosaicoGaugeCold::keyWindowAllowed())return false;
#endif
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

// Read-only selector: deliberately not a Field (the capacity write policy stays closed).
enum class GaugeProfileSelector : uint16_t { GaugingConfig = 0x929B };

bool gauge_mac_read(uint16_t selector, uint8_t block[36])
{
    using mosaico_gauge::Field;
    if (selector != static_cast<uint16_t>(Field::Design) && selector != static_cast<uint16_t>(Field::InitialFcc) &&
        selector != static_cast<uint16_t>(GaugeProfileSelector::GaugingConfig))
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

// Closed selector set, with independent authenticated reads of each word. The
// caller holds both locks, including exclusive ownership of the MAC selector.
template <typename Reader>
constexpr bool gauge_profile_read_stable(Reader read, uint16_t (&values)[3], const char*& reason)
{
    constexpr uint16_t selectors[] = {
        static_cast<uint16_t>(GaugeProfileSelector::GaugingConfig),
        static_cast<uint16_t>(mosaico_gauge::Field::InitialFcc),
        static_cast<uint16_t>(mosaico_gauge::Field::Design)};
    uint16_t first[3]{};
    for (unsigned pass = 0; pass < 2; ++pass) {
        for (unsigned field = 0; field < 3; ++field) {
            uint8_t block[36]{};
            if (!read(selectors[field], block) ||
                !mosaico_gauge::authenticateDmResponse(block, selectors[field])) {
                reason = "dm_denied_io_or_auth";
                return false;
            }
            const uint16_t value = (static_cast<uint16_t>(block[2]) << 8) | block[3];
            if (!pass) first[field] = value;
            else if (first[field] != value) {
                reason = "dm_unstable";
                return false;
            }
        }
    }
    if (first[0] > 0x1FFF) {
        reason = "dm_config_range";
        return false;
    }
    for (unsigned field = 0; field < 3; ++field) values[field] = first[field];
    reason = "validated";
    return true;
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

bool gauge_exit_config(bool officialCleanup = false)
{
    uint16_t operation = 0;
    if (!gauge_word(0x3A, operation) || (operation & 1)) return false;
    if (!(operation & 0x0400))
        return mosaico_gauge::configExitAccepted(operation) || gauge_wait_config(false);
    if (((operation >> 1) & 3) != 1) return false; // CFG writes/recovery STILL require existing FA.
    // Reinitialize the nominal capacity registers using the documented CFG
    // exit command; do not reset hardware, change security, or program OTP.
    // Official baseline cleanup does not inherit the retired cold-runtime gate.
    const uint8_t exit[] = {0x00, 0x91, 0x00};
    const bool sent = officialCleanup ? i2c_master_transmit(gauge, exit, sizeof(exit), 100) == ESP_OK :
                                       gauge_control(GaugeCommand::ExitReinit);
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

// A normal target setting, not a trial/lease or a replacement of tx_v1.
// Missing/zero means no automatic reload. Preserve every historical blob.
esp_err_t gauge_load_normal_target(uint16_t& target)
{
    target=0;
    normal_gauge_target=0;
    nvs_handle_t handle=0;
    esp_err_t result=nvs_open("gaugecal",NVS_READONLY,&handle);
    if(result!=ESP_OK)return result;
    result=nvs_get_u16(handle,"normal_mah",&target);
    nvs_close(handle);
    if(result==ESP_OK&&target!=0&&target!=mosaico_gauge::NominalMah)return ESP_FAIL;
    if(result==ESP_OK)normal_gauge_target=target;
    return result;
}
bool gauge_store_normal_target(uint16_t target)
{
    if(target!=0&&target!=mosaico_gauge::NominalMah)return false;
    nvs_handle_t handle=0;
    if(nvs_open("gaugecal",NVS_READWRITE,&handle)!=ESP_OK)return false;
    esp_err_t result=nvs_set_u16(handle,"normal_mah",target);
    if(result==ESP_OK)result=nvs_commit(handle);
    nvs_close(handle);
    uint16_t observed=0;
    return result==ESP_OK&&gauge_load_normal_target(observed)==ESP_OK&&observed==target;
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
#ifdef MOSAICO_GAUGE_COLD_RUNTIME
    if(!MosaicoGaugeCold::keyWindowAllowed())return false;
#endif
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
    std::array<uint8_t, 6> mac{};
    if (!hal_legacy_factory_mac::read(mac, [](uint8_t* received) {
            return esp_efuse_mac_get_default(received);
        })) return false;
    mosaico_gauge::AccessJournal access{};
    return gauge_load_access(access, mac.data()) == ESP_OK && access.priorSecurity == 3 &&
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

// Public complement ONLY: exact authenticated read selection, never parameter commit.
bool gauge_public_extra_read(mosaico_public_extra::Selector selector,uint8_t block[36])
{
    if(!gauge||!mosaico_public_extra::allowed(selector))return false;
    const uint16_t value=static_cast<uint16_t>(selector);
    const uint8_t packet[]={0x3E,static_cast<uint8_t>(value),static_cast<uint8_t>(value>>8)};
    gauge_feed();
    if(i2c_master_transmit(gauge,packet,sizeof(packet),20)!=ESP_OK)return false;
    gauge_delay_ms(20);
    if(!gauge_read(0x3E,block,2))return false;
    for(unsigned i=0;i<34;i+=2)if(!gauge_read(static_cast<uint8_t>(0x40+i),block+2+i,2))return false;
    // Unlike legacy readers, an FF echo is not accepted for these new fields.
    return block[0]==static_cast<uint8_t>(value)&&block[1]==static_cast<uint8_t>(value>>8)&&
        block[35]==36&&block[34]==mosaico_gauge::macChecksum(block,34);
}

// Calibration-only typed MAC selection. No arbitrary address or parameter commit.
bool gauge_calibration_read(mosaico_calibration::Selector selector,uint8_t block[36])
{
    if(!mosaico_calibration::allowed(selector))return false;
    const uint16_t value=static_cast<uint16_t>(selector);
    const uint8_t packet[]={0x3E,static_cast<uint8_t>(value),static_cast<uint8_t>(value>>8)};
    gauge_feed();
    if(i2c_master_transmit(gauge,packet,sizeof(packet),20)!=ESP_OK)return false;
    gauge_delay_ms(20);
    if(!gauge_read(0x3E,block,2))return false;
    for(unsigned i=0;i<34;i+=2)if(!gauge_read(static_cast<uint8_t>(0x40+i),block+2+i,2))return false;
    return mosaico_gauge::authenticateDmResponse(block,value);
}

// Threshold-only typed MAC selection. No arbitrary address or parameter commit.
bool gauge_threshold_read(mosaico_threshold::Selector selector,uint8_t block[36])
{
    if(!mosaico_threshold::allowed(selector))return false;
    const uint16_t value=static_cast<uint16_t>(selector);
    const uint8_t packet[]={0x3E,static_cast<uint8_t>(value),static_cast<uint8_t>(value>>8)};
    gauge_feed();
    if(i2c_master_transmit(gauge,packet,sizeof(packet),20)!=ESP_OK)return false;
    gauge_delay_ms(20);
    if(!gauge_read(0x3E,block,2))return false;
    for(unsigned i=0;i<34;i+=2)if(!gauge_read(static_cast<uint8_t>(0x40+i),block+2+i,2))return false;
    return mosaico_gauge::authenticateDmResponse(block,value);
}

// Explicit runtime backend plus separately boot-latched ColdFactory privilege.
// Neither path mutates the legacy nominal/access journals.
struct GaugeFccBackend {
    mosaico_fcc_limit::Journal saved{}, runtimeProof{};
    mosaico_fcc_limit::Dm lastObservedDm{};
    bool observedValid=false;
    mosaico_fcc_limit::QuietQualification qualification{};
    mosaico_fcc_limit::Mode mode=mosaico_fcc_limit::Mode::Runtime;
    bool initialColdLoad=true;
    GaugeFccBackend()=default;
    explicit GaugeFccBackend(const mosaico_fcc_limit::Journal& permission)
        : runtimeProof(permission),mode(mosaico_fcc_limit::Mode::ColdFactory) {}
    bool cold() const {return mode==mosaico_fcc_limit::Mode::ColdFactory;}
    const char* key() const {return cold()?mosaico_fcc_limit::coldJournalKey:mosaico_fcc_limit::journalKey;}
    const char* why = "preflight_rejected";
    bool dirty = false;
    bool reject(const char* value) { why=value; return false; }
    bool runtimeGate() {
        const auto p=GetNetworkQuota().powerStats();
        const auto b=GetCodexMicroBle().diagnostics();
        bool otaAllowed=!MosaicoOta::busy()&&!MosaicoOta::healthPending();
        if(cold()&&MosaicoOta::healthPending()) {
            MosaicoOta::UiSnapshot ui{};
            // Normal pending-image startup is not an update/install transaction.
            otaAllowed=MosaicoOta::copyUiSnapshot(ui)&&ui.stage==MosaicoOta::UiStage::BootChecking;
        }
        return ((cold() || (p.locked && p.phase==2)) && !p.wifiRunning && !b.advertising && !b.connected &&
            otaAllowed) || reject("runtime_radio_or_ota_gate");
    }
    mosaico_fcc_limit::Load loadRaw(const char* requestedKey, mosaico_fcc_limit::Journal& j) {
        using namespace mosaico_fcc_limit;
        if(std::strcmp(requestedKey,key())) return Load::Invalid;
        const auto mac=GetHAL().getFactoryMac();
        nvs_handle_t h=0;esp_err_t e=nvs_open("gaugecal",NVS_READONLY,&h);
        if(e==ESP_ERR_NVS_NOT_FOUND)return Load::Absent;
        if(e!=ESP_OK){why="journal_open";return Load::Invalid;}
        Record r{};size_t size=sizeof(r);e=nvs_get_blob(h,key(),r,&size);nvs_close(h);
        if(e==ESP_ERR_NVS_NOT_FOUND)return Load::Absent;
        if(e!=ESP_OK||size!=sizeof(r)||!decode(r,mac.data(),j,mode)){why="journal_invalid";return Load::Invalid;}
        saved=j;why="journal_present_no_replay";return Load::Present;
    }
    mosaico_fcc_limit::Load journalLoad(const char* requestedKey,mosaico_fcc_limit::Journal& j) {
        using namespace mosaico_fcc_limit;
        const auto result=loadRaw(requestedKey,j);
        const bool first=initialColdLoad;initialColdLoad=false;
        // Only the outer startup attempted/Pending latch grants this new cold
        // transaction. Last Verified is replaceable, never Pending/Failed/corrupt.
        if(cold()&&first&&result==Load::Present&&j.state==State::Verified)return Load::Absent;
        return result;
    }
    bool journalSaveReadback(const char* requestedKey,const mosaico_fcc_limit::Journal& j) {
        using namespace mosaico_fcc_limit;
        if(std::strcmp(requestedKey,key()))return reject("journal_key");
        const char* priorWhy=why;
        Record bytes{};if(!encode(j,bytes,mode))return reject("journal_shape");
        nvs_handle_t h=0;esp_err_t e=nvs_open("gaugecal",NVS_READWRITE,&h);
        if(e!=ESP_OK)return reject("journal_write_open");
        gauge_feed();e=nvs_set_blob(h,key(),bytes,sizeof(bytes));
        if(e==ESP_OK) { e=nvs_commit(h); }
        nvs_close(h);
        gauge_feed();
        if(e!=ESP_OK)return reject("journal_commit");
        Journal actual{};if(loadRaw(key(),actual)!=Load::Present)return reject("journal_readback");
        Record readback{};if(!encode(actual,readback,mode)||!equalRecord(bytes,readback))return reject("journal_compare");
        saved=actual;why=priorWhy;return true;
    }
    bool observedSecurity(mosaico_fcc_limit::Security& s) {
        uint16_t op=0;if(!gauge_word(0x3A,op))return reject("security_io");
        s={static_cast<uint8_t>((op>>1)&3),bool(op&0x0400),bool(op&1),bool(op&0x20)};
        return true;
    }
    bool quietSnapshot(mosaico_fcc_limit::Quiet& q) {
        uint16_t temp=0,current=0,average=0;
        bool ok=gauge_word(0x3A,q.operation);ok&=gauge_word(0x2C,q.soc);ok&=gauge_word(0x08,q.mv);
        ok&=gauge_word(0x3C,q.standardDc);ok&=gauge_word(0x12,q.standardFcc);
        ok&=gauge_word(0x06,temp);ok&=gauge_word(0x0C,current);ok&=gauge_word(0x14,average);
        q.temperatureDeciK=static_cast<int16_t>(temp);q.current=static_cast<int16_t>(current);q.average=static_cast<int16_t>(average);
        return (runtimeGate()&&ok)||reject("quiet_snapshot_or_runtime_gate");
    }
    bool physical(bool cfg,uint8_t sec) {
        mosaico_fcc_limit::Quiet q{};
        if(!quietSnapshot(q))return false;
        const uint16_t op=q.operation;
        if(bool(op&0x0400)!=cfg||((op>>1)&3)!=sec)return reject("fresh_security");
        q.operation=static_cast<uint16_t>(op&~0x0400U);
        if(cold()) {
            if((q.standardDc!=3000&&(!cfg||q.standardDc!=65))||
               (q.standardFcc!=3000&&(!cfg||q.standardFcc!=65)))return reject("cold_fresh_capacity");
            q.standardDc=3000;q.standardFcc=3000;
            return mosaico_fcc_limit::coldQuiet(q)||reject("cold_fresh_physical");
        }
        if(q.standardDc!=65||(q.standardFcc!=65&&q.standardFcc!=100))return reject("fresh_capacity");
        q.standardFcc=100;
        return mosaico_fcc_limit::quiet(q)||reject("fresh_physical_not_quiet_high_voltage");
    }

    bool prepareRuntimeQuietQualification() {
        using namespace mosaico_fcc_limit;
        if(cold())return true;
        // The caller owns the transaction mutex, not battery_mutex. Reject
        // existing history/latches before spending the quiet interval; no save.
        {
            std::lock_guard<std::mutex> battery(battery_mutex);
            Journal old{};Identity id{};
            if(loadRaw(journalKey,old)!=Load::Absent)return reject("qualification_existing_or_invalid_record");
            if(!identity(id)||id.device!=0x0220||!id.historicalNominalProof||!id.historicalAccessProof)
                return reject("qualification_history_or_identity");
        }
        for(;;) {
            Quiet q{};Security s{};bool read=false;
            {
                // UI telemetry can run between samples; this lock is never
                // retained during the one-second watchdog-fed qualification wait.
                std::lock_guard<std::mutex> battery(battery_mutex);
                read=quietSnapshot(q)&&observedSecurity(s);
            }
            // Timestamp AFTER completed reads: lock/I2C stalls count as gaps.
            const int64_t now=esp_timer_get_time();
            if(!read||!qualification.sample(q,s,now)) {
                qualification.failed=true;
                return reject("qualification_interrupted_or_not_quiet");
            }
            if(qualification.ready(now))return true;
            const int64_t remaining=QuietQualification::durationUs-(now-qualification.firstUs);
            gauge_delay_ms(static_cast<uint32_t>(std::min<int64_t>(1000,(remaining+999)/1000)));
        }
    }
    bool qualifyRuntimeQuiet() {
        if(cold())return true;
        // Called with battery_mutex held immediately before the first durable
        // intent. It consumes only a just-completed proof, never waits 120s again.
        if(!qualification.ready(esp_timer_get_time())||!physical(false,3)||
           !qualification.ready(esp_timer_get_time()))return reject("qualification_stale_or_fresh_gate");
        return true;
    }

    bool identity(mosaico_fcc_limit::Identity& id) {
        using namespace mosaico_gauge;
        if(!gauge||!selftest()||!runtimeGate()||!gauge_identity())return reject("identity_or_runtime");
        const auto mac=GetHAL().getFactoryMac();Journal nominal{};AccessJournal access{};ReloadJournal reload{};
        const esp_err_t r=gauge_load_reload(reload,mac.data());
        const bool reloadOk=cold()?(r==ESP_OK&&reload.state==uint8_t(ReloadState::Pending)&&gauge_boot_info.attempted):
            (r==ESP_ERR_NVS_NOT_FOUND||(r==ESP_OK&&reload.state==uint8_t(ReloadState::Completed)));
        if(!reloadOk||gauge_load_journal(nominal,mac.data())!=ESP_OK||gauge_load_access(access,mac.data())!=ESP_OK||
           !bootHistoryEligible(nominal,access))return reject("legacy_history_or_failure_latch");
        id.device=DeviceType;for(unsigned i=0;i<6;++i)id.mac[i]=mac[i];
        id.historicalNominalProof=true;id.historicalAccessProof=true;
        if(cold()) {
            GaugeFccBackend reader;mosaico_fcc_limit::Journal actual{};
            mosaico_fcc_limit::Record a{},b{};
            if(reader.loadRaw(mosaico_fcc_limit::journalKey,actual)!=mosaico_fcc_limit::Load::Present||
               !mosaico_fcc_limit::verifiedRuntimePermission(actual)||
               !mosaico_fcc_limit::encode(actual,a)||!mosaico_fcc_limit::encode(runtimeProof,b)||
               !mosaico_fcc_limit::equalRecord(a,b))return reject("cold_runtime_permission");
            id.authorizedColdProfile=true;id.coldOriginalConfig=actual.before.config;
        }
        return true;
    }

    bool sendUnsealPair() {
        gauge_delay_ms(5000);
        if(!physical(false,3))return false;
        dirty=true;
        const bool sent=gauge_access_word(AccessWord::UnsealFirst)&&gauge_access_word(AccessWord::UnsealSecond);
        const bool observed=gauge_wait_security(2);
        return (sent&&observed)||reject("unseal_failed_no_retry");
    }
    bool sendFullPair() {
        gauge_delay_ms(5000);
        if(!physical(false,2))return false;
        const bool sent=gauge_access_word(AccessWord::Full)&&gauge_access_word(AccessWord::Full);
        const bool observed=gauge_wait_security(1);
        return (sent&&observed)||reject("full_failed_no_retry");
    }
    bool dmSnapshot(mosaico_fcc_limit::Dm& dm) {
        uint16_t v[3]{};const char* reason="";
        if(!gauge_profile_read_stable(gauge_mac_read,v,reason))return reject(reason);
        dm={v[0],v[2],v[1]};
        lastObservedDm=dm;
        observedValid=true;
        return true;
    }
    bool enterCfg() {return physical(false,1)&&gauge_control(GaugeCommand::EnterConfig);}
    bool waitCfg() {return gauge_wait_config(true);}
    // Closed selectors, separate from old Field/allowedValue APIs and journals.
    // The DC selector is unreachable from the runtime mode.
    enum class Parameter : uint16_t { Config=0x929B,Fcc=0x929D,Dc=0x929F };
    bool writeParameter(Parameter parameter,uint16_t value) {
        using namespace mosaico_fcc_limit;
        if(!saved.backupValid||!saved.fullAccessVerified||!physical(true,1))return reject("write_preflight");
        Dm now{};if(!dmSnapshot(now)||
            (now.dc!=saved.before.dc&&now.dc!=saved.target.dc)||
            (now.config!=saved.before.config&&now.config!=saved.target.config)||
            (now.fcc!=saved.before.fcc&&now.fcc!=saved.target.fcc))return reject("write_unknown_profile");
        const bool config=parameter==Parameter::Config, dc=parameter==Parameter::Dc;
        if(dc&&!cold())return reject("runtime_dc_write_forbidden");
        const uint16_t before=config?saved.before.config:(dc?saved.before.dc:saved.before.fcc);
        const uint16_t target=config?saved.target.config:(dc?saved.target.dc:saved.target.fcc);
        const bool forward=value==target;
        const uint16_t flag=config?(forward?Config:RestoreConfig):(dc?(forward?Dc:RestoreDc):(forward?Fcc:RestoreFcc));
        if(!(saved.intents&flag)||(value!=before&&value!=target)||
           (config&&((before^target)!=limitBit))||
           (!config&&!dc&&parameter!=Parameter::Fcc))return reject("write_intent_or_value");
        // Recheck after authenticated reads, immediately before the single wire attempt.
        if(!physical(true,1))return false;
        const uint16_t selector=static_cast<uint16_t>(parameter);
        const uint8_t packet[]={0x3E,static_cast<uint8_t>(selector),static_cast<uint8_t>(selector>>8),
            static_cast<uint8_t>(value>>8),static_cast<uint8_t>(value)};
        gauge_feed();dirty=true;
        if(i2c_master_transmit(gauge,packet,sizeof(packet),20)!=ESP_OK)return reject("parameter_ack");
        gauge_delay_ms(1);
        const uint8_t commit[]={0x60,mosaico_gauge::macChecksum(packet+1,4),6};
        if(i2c_master_transmit(gauge,commit,sizeof(commit),20)!=ESP_OK)return reject("parameter_commit_ack");
        gauge_delay_ms(20);
        for(unsigned i=0;i<2;++i){uint8_t block[36]{};
            if(!gauge_mac_read(selector,block)||((uint16_t(block[2])<<8)|block[3])!=value)return reject("parameter_readback");}
        return true;
    }
    bool writeConfigOnly(uint16_t value){return writeParameter(Parameter::Config,value);}
    bool writeFccOnly(uint16_t value){return writeParameter(Parameter::Fcc,value);}
    bool writeDcOnly(uint16_t value){return cold()?writeParameter(Parameter::Dc,value):reject("runtime_dc_write_forbidden");}
    bool exitCfg() {
        mosaico_fcc_limit::Security s{};if(!observedSecurity(s))return false;
        if(!s.cfg)return s.initialized&&!s.cal; // SEC2/noCFG needs no exit command.
        const bool exited=gauge_exit_config();
        // Observer only: the caller already owns transaction -> battery locks.
        // Preserve the original exit/2s wait, capture before reseal/verification,
        // and never retry a failed diagnostic or observe runtime FCC repair.
        if(exited && cold() && !gauge_boot_post_exit.phase) {
            gauge_diagnostic_locked(gauge_boot_post_exit,mosaico_gauge_status::PostExit,0);
            gauge_engine_locked(gauge_engine_post_exit,mosaico_gauge_status::PostExit,0);
        }
        return exited;
    }
    bool reseal() {
        using namespace mosaico_fcc_limit;
        // Cleanup is independent of NVS/radios, but never Seal active/unknown
        // CFG or CAL. Match the legacy protective access-return safety rule.
        Security first{};
        if(!observedSecurity(first)||!normal(first)||first.sec<1||first.sec>3)
            return reject("seal_blocked_cfg_cal_or_unknown");
        if(first.sec==3)return true;
        gauge_delay_ms(5000);
        Security fresh{};
        if(!observedSecurity(fresh)||!normal(fresh)||fresh.sec<1||fresh.sec>3)
            return reject("seal_blocked_after_grace");
        if(fresh.sec==3)return true;
        (void)gauge_access_word(AccessWord::Seal);
        return gauge_wait_security(3);
    }
    bool verifyTarget(mosaico_fcc_limit::Dm target,bool sealed) {
        using namespace mosaico_fcc_limit;
        if(!sealed){Dm a{},b{};return dmSnapshot(a)&&dmSnapshot(b)&&same(a,target)&&same(b,target);}
        for(unsigned i=0;i<2;++i){Security sec{};uint16_t dc=0,fcc=0;
            if(!gauge_identity()||!observedSecurity(sec)||sec.sec!=3||!normal(sec)||
                !gauge_word(0x3C,dc)||!gauge_word(0x12,fcc)||dc!=65||fcc!=65)return reject("final_standard_identity_security");}
        Dm dm{};if(dmSnapshot(dm))return same(dm,target)||reject("sealed_dm_mismatch");
        Journal durable{};
        if(loadRaw(key(),durable)!=Load::Present)return reject("sealed_proof_load");
        const uint16_t required=Unseal|Full|Enter|Config|TargetVerified|(durable.before.fcc!=65?Fcc:0)|(cold()?Dc:0);
        return (durable.state==State::Pending&&durable.backupValid&&durable.fullAccessVerified&&
            (durable.intents&required)==required&&same(durable.target,target))||reject("sealed_proof_invalid");
    }
};

struct GaugeThresholdBackend {
    using Journal=mosaico_threshold::Journal;
    using Load=mosaico_threshold::Load;
    static constexpr const char* Key="thresh_aud_v1";
    uint16_t expectedConfig=0x112A;
    bool normalTarget65=false;
    mosaico_threshold::Values values{};
    mosaico_calibration::Values calibration{};
    mosaico_public_extra::Values publicExtra{};
    bool readPublicExtra=false;
    const char* why="admission_refused";
    Load journalLoadState=Load::Absent;bool journalLoaded=false;uint8_t lastReadJournalState=0;
    uint16_t controlStatus=0;bool controlReadable=false;
    bool standardSeen=false;int64_t lastStandardUs=0;
    bool beforeStandardValid=false,afterStandardValid=false;
    struct Standard {uint16_t op=0,soc=0,cycles=0,mv=0,temp=0,dc=0,fcc=0;int16_t current=0,average=0;} before{},after{};
    bool reject(const char* reason){why=reason;return false;}
    bool runtimeGate() {
        const auto p=GetNetworkQuota().powerStats();const auto b=GetCodexMicroBle().diagnostics();
        return p.locked&&p.phase==2&&!p.wifiRunning&&!b.advertising&&!b.connected&&!MosaicoOta::busy()&&!MosaicoOta::healthPending();
    }
    bool standard(Standard& s) {
        if(standardSeen){
            const int64_t now=esp_timer_get_time();
            if(now<lastStandardUs)return false;
            const int64_t left=1000000-(now-lastStandardUs);
            if(left>0)gauge_delay_ms(static_cast<uint32_t>((left+999)/1000));
            if(esp_timer_get_time()<lastStandardUs+1000000)return false;
        }
        const int64_t started=esp_timer_get_time();
        uint16_t current=0,average=0;
        bool ok=gauge_word(0x3A,s.op);ok&=gauge_word(0x2C,s.soc);ok&=gauge_word(0x2A,s.cycles);
        ok&=gauge_word(0x08,s.mv);ok&=gauge_word(0x06,s.temp);ok&=gauge_word(0x3C,s.dc);ok&=gauge_word(0x12,s.fcc);
        ok&=gauge_word(0x0C,current);ok&=gauge_word(0x14,average);s.current=static_cast<int16_t>(current);s.average=static_cast<int16_t>(average);
        const int64_t finished=esp_timer_get_time();standardSeen=true;lastStandardUs=finished;
        return ok&&finished>=started;
    }
    bool physical(uint8_t security,Standard* observed=nullptr) {
        Standard s{};
        if(!runtimeGate()||!standard(s))return reject("physical_or_runtime_gate");
        if(observed){*observed=s;if(observed==&before)beforeStandardValid=true;}
        return (((s.op>>1)&3)==security&&!(s.op&0x0401)&&(s.op&0x20)&&
            s.dc==65&&s.fcc==65&&s.soc<=100&&s.mv>=3500&&s.mv<=4350&&s.temp>=2832&&s.temp<=3181&&
            s.current>=-130&&s.current<=130&&s.average>=-130&&s.average<=130)||reject("physical_or_runtime_gate");
    }
    void readControl() {
        uint16_t discarded=0;
        const bool first=gauge_word(0x00,discarded);
        controlReadable=gauge_word(0x00,controlStatus)&&first&&controlStatus!=0xFFA5;
    }
    bool wasAttempted()const{return gauge_threshold_audit_attempted;}
    bool claimAttempt(){if(wasAttempted())return false;gauge_threshold_audit_attempted=true;return true;}
    Load load(Journal& journal) {
        journalLoaded=true;journalLoadState=Load::Invalid;
        const auto mac=GetHAL().getFactoryMac();nvs_handle_t h=0;
        esp_err_t e=nvs_open("gaugeaudit",NVS_READONLY,&h);
        if(e==ESP_ERR_NVS_NOT_FOUND)return journalLoadState=Load::Absent;
        if(e!=ESP_OK){why="audit_journal_open";return Load::Invalid;}
        mosaico_threshold::Record r{};size_t n=sizeof(r);e=nvs_get_blob(h,Key,r,&n);nvs_close(h);
        if(e==ESP_ERR_NVS_NOT_FOUND)return journalLoadState=Load::Absent;
        if(e!=ESP_OK||n!=sizeof(r)||!mosaico_threshold::decode(r,mac.data(),journal)){why="audit_journal_invalid";return Load::Invalid;}
        lastReadJournalState=static_cast<uint8_t>(journal.state);why="audit_journal_present";return journalLoadState=Load::Present;
    }
    bool saveReadback(const Journal& journal) {
        mosaico_threshold::Record bytes{},actual{};
        if(!mosaico_threshold::encode(journal,bytes))return reject("audit_journal_shape");
        nvs_handle_t h=0;esp_err_t e=nvs_open("gaugeaudit",NVS_READWRITE,&h);
        if(e!=ESP_OK)return reject("audit_journal_write_open");
        gauge_feed();e=nvs_set_blob(h,Key,bytes,sizeof(bytes));
        if(e==ESP_OK){e=nvs_commit(h);}
        nvs_close(h);gauge_feed();
        if(e!=ESP_OK)return reject("audit_journal_commit");
        const char* previous=why;Journal readback{};
        if(load(readback)!=Load::Present||!mosaico_threshold::encode(readback,actual)||!mosaico_threshold::equal(bytes,actual))return reject("audit_journal_readback");
        why=previous;return true;
    }
    bool preflight(uint8_t outMac[6]) {
        if(!gauge||!physical(3,&before)||!gauge_identity())return reject("sealed_admission_or_identity");
        const auto mac=GetHAL().getFactoryMac();mosaico_gauge::Journal nominal{};mosaico_gauge::AccessJournal access{};
        GaugeFccBackend reader;mosaico_fcc_limit::Journal runtime{};
        if(gauge_load_journal(nominal,mac.data())!=ESP_OK||gauge_load_access(access,mac.data())!=ESP_OK||
           !mosaico_gauge::bootHistoryEligible(nominal,access))return reject("known_key_and_runtime_history_required");
        if(!normalTarget65){
            if(reader.loadRaw(mosaico_fcc_limit::journalKey,runtime)!=mosaico_fcc_limit::Load::Present||
               !mosaico_fcc_limit::verifiedRuntimePermission(runtime))return reject("known_key_and_runtime_history_required");
            expectedConfig=runtime.target.config;
        }
        readControl();
        for(unsigned i=0;i<6;++i)outMac[i]=mac[i];
        return true;
    }
    bool unseal() {
        gauge_delay_ms(5000);
        if(!physical(3))return false;
        const bool sent=gauge_access_word(AccessWord::UnsealFirst)&&gauge_access_word(AccessWord::UnsealSecond);
        const bool observed=gauge_wait_security(2);return (sent&&observed)||reject("unseal_once_failed");
    }
    bool full() {
        gauge_delay_ms(5000);
        if(!physical(2))return false;
        const bool sent=gauge_access_word(AccessWord::Full)&&gauge_access_word(AccessWord::Full);
        const bool observed=gauge_wait_security(1);return (sent&&observed)||reject("full_once_failed");
    }
    bool readThresholds(){
        calibration={};
        if(normalTarget65){
            // Already in FA through the existing coordinator. Observe the actual
            // stable profile word; normal65 does not imply the old FCC-limit bit.
            uint16_t profile[3]{};const char* reason="dm_denied_io_or_auth";
            if(!gauge_profile_read_stable(gauge_mac_read,profile,reason))return reject(reason);
            if(profile[1]!=65||profile[2]!=65)return reject("normal_profile_capacity_anchor");
            expectedConfig=profile[0];
        }
        if(!mosaico_threshold::readStable(gauge_threshold_read,expectedConfig,values))return reject("dm_denied_auth_unstable_or_anchor");
        if(!mosaico_calibration::readStable(gauge_calibration_read,calibration))return reject("calibration_denied_auth_or_unstable");
        // Independent RAM-only result: never changes the 59-field journal/CRC.
        if(readPublicExtra)(void)mosaico_public_extra::readStable(gauge_public_extra_read,publicExtra);
        return true;
    }
    // Existing schema-2 journal covers only the 59 threshold fields. Calibration
    // is independently double-verified RAM-only evidence, not part of this CRC.
    uint32_t snapshotCrc()const{return mosaico_threshold::valuesCrc(values);}
    bool close() {
        uint16_t op=0;
        const auto normal=[](uint16_t value){return !(value&0x0401)&&(value&0x20)&&((value>>1)&3)>=1;};
        if(!gauge_word(0x3A,op)||!normal(op))return reject("close_unknown_cfg_cal");
        if(((op>>1)&3)!=3){
            gauge_delay_ms(5000);
            if(!gauge_word(0x3A,op)||!normal(op))return reject("close_after_grace_unknown_cfg_cal");
            if(((op>>1)&3)!=3){(void)gauge_access_word(AccessWord::Seal);if(!gauge_wait_security(3))return reject("close_seal_unverified");}
        }
        afterStandardValid=standard(after);
        const bool verified=afterStandardValid&&normal(after.op)&&((after.op>>1)&3)==3&&after.dc==65&&after.fcc==65&&gauge_identity();
        if(verified)readControl();
        return verified||reject("close_final_standard_identity");
    }
};

// Optional normal-profile setting; no record is created by boot/status.
mosaico_near::Snapshot* near_snapshots=nullptr;
bool near_before_known=false,near_after_known=false,near_boot_known=false,near_boot_pending=false;
uint16_t near_boot_value=0;
bool near_physical_mac(uint8_t (&mac)[6]) {
    // S31 default eFuse helper writes eight bytes; this SDK selector returns
    // the physical six-byte factory identity, not the retained legacy domain.
    return esp_read_mac(mac,ESP_MAC_EFUSE_FACTORY)==ESP_OK;
}
esp_err_t near_load(mosaico_near::Record& r) {
    uint8_t mac[6]{};if(!near_physical_mac(mac))return ESP_FAIL;
    nvs_handle_t h=0;auto e=nvs_open("gaugecal",NVS_READONLY,&h);if(e!=ESP_OK)return e;
    size_t n=r.size();e=nvs_get_blob(h,"near_v1",r.data(),&n);nvs_close(h);
    return e==ESP_OK&&(n!=r.size()||!mosaico_near::valid(r,mac))?ESP_FAIL:e;
}
bool near_save(mosaico_near::Record& r) {
    uint8_t mac[6]{};if(!near_physical_mac(mac))return false;
    mosaico_near::seal(r);if(!mosaico_near::valid(r,mac))return false;
    nvs_handle_t h=0;if(nvs_open("gaugecal",NVS_READWRITE,&h)!=ESP_OK)return false;
    gauge_feed();auto e=nvs_set_blob(h,"near_v1",r.data(),r.size());if(e==ESP_OK)e=nvs_commit(h);nvs_close(h);gauge_feed();
    mosaico_near::Record readback{};return e==ESP_OK&&near_load(readback)==ESP_OK&&r==readback;
}
bool near_read(uint16_t& value) {
    uint8_t b[36]{};if(!gauge_threshold_read(mosaico_threshold::Selector::NearFull,b))return false;
    value=(uint16_t(b[2])<<8)|b[3];return true;
}
bool near_write(uint16_t value) {
    uint16_t op=0;if(!mosaico_near::allowed(value)||!gauge_identity()||!gauge_word(0x3a,op)||
        ((op>>1)&3)!=1||!(op&0x0400)||(op&1))return false;
    const uint8_t packet[]={0x3e,0x6b,0x92,uint8_t(value>>8),uint8_t(value)};
    gauge_feed();if(i2c_master_transmit(gauge,packet,sizeof(packet),20)!=ESP_OK)return false;
    gauge_delay_ms(1);
    const uint8_t commit[]={0x60,mosaico_gauge::macChecksum(packet+1,4),6};
    if(i2c_master_transmit(gauge,commit,sizeof(commit),20)!=ESP_OK)return false;
    gauge_delay_ms(20);
    for(unsigned i=0;i<2;++i){uint16_t actual=0;if(!near_read(actual)||actual!=value)return false;}return true;
}
bool near_rollbackOwn(uint16_t before,uint16_t desired) {
    uint16_t op=0,a=0,b=0;
    if(!mosaico_near::allowed(before)||!mosaico_near::allowed(desired)||!gauge_identity()||
        !gauge_word(0x3a,op)||((op>>1)&3)!=1||!(op&0x0400)||(op&1)||
        !near_read(a)||!near_read(b)||a!=b)return false;
    if(a==before)return true; // Verified no-op, including A200's unknown ACK.
    if(a!=desired||before==desired)return false;
    return near_write(before); // Only the owned desired value authorizes ONE rollback.
}
bool near_snapshot(mosaico_near::Snapshot& out) {
    // Existing pure authenticated double-read functions, independent of old audit journals.
    uint16_t profile[3]{};const char* why="";
    mosaico_threshold::Values t{};mosaico_calibration::Values c{};mosaico_public_extra::Values e{};
    if(!gauge_profile_read_stable(gauge_mac_read,profile,why)||profile[1]!=65||profile[2]!=65||
        !mosaico_threshold::readStable(gauge_threshold_read,profile[0],t)||
        !mosaico_calibration::readStable(gauge_calibration_read,c)||!mosaico_public_extra::readStable(gauge_public_extra_read,e))return false;
    for(size_t i=0;i<59;++i){const auto f=mosaico_near::field(i);out[i]=f.width==1?t.raw[i]>>8:t.raw[i];}
    for(size_t i=0;i<7;++i)out[i+59]=mosaico_calibration::rawWord(c,i);
    for(size_t i=0;i<65;++i)out[i+66]=e.raw[i];
    return true;
}
void near_boot_apply() {
    using namespace mosaico_near;Record r{};const auto loaded=near_load(r);
    if(loaded!=ESP_OK){if(loaded!=ESP_ERR_NVS_NOT_FOUND)ESP_LOGW(Tag,"near_full invalid record; normal65 unchanged");return;}
    if(r[3]!=Active){ESP_LOGW(Tag,"near_full inactive phase=%u; no replay",r[3]);return;}
    r[3]=Pending;if(!near_save(r)){ESP_LOGW(Tag,"near_full intent failed; no write");return;}
    near_boot_pending=true;
    uint16_t before=0,again=0;
    if(!near_read(before)||!near_read(again)||before!=again||!allowed(before)){
        r[3]=Failed;(void)near_save(r);ESP_LOGW(Tag,"near_full unknown before; no write/retry");return;
    }
    const bool written=near_write(target(r)); // A=200 and B=5 both write exactly once.
    near_boot_known=written;if(written)near_boot_value=target(r);
    const bool rollback=!written&&near_rollbackOwn(before,target(r));
    if(!written)ESP_LOGW(Tag,"near_full cold failed rollback=%d; pending until final seal",rollback);
}
void near_boot_finish(bool safelySealedNative65) {
    if(!near_boot_pending)return;
    near_boot_pending=false;mosaico_near::Record r{};
    if(near_load(r)!=ESP_OK||r[3]!=mosaico_near::Pending){near_boot_known=false;return;}
    r[3]=safelySealedNative65&&near_boot_known?mosaico_near::Active:mosaico_near::Failed;
    if(!near_save(r)){near_boot_known=false;ESP_LOGW(Tag,"near_full final commit failed; no replay");}
    if(!safelySealedNative65)near_boot_known=false;
}
} // namespace

bool Hal::gaugeNearFull(const char* action,uint16_t expected,uint16_t target,char* details,size_t size)
{
    using namespace mosaico_near;
    const bool status=!std::strcmp(action,"status"),restore=!std::strcmp(action,"restore");
    NormalGaugeStorageLease lease;
    std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    Record r{};auto loaded=near_load(r);
    const auto report=[&](bool ok,const char* why){
        loaded=near_load(r);
        char raw[49]{};for(size_t i=0;i<r.size();++i)std::snprintf(raw+2*i,3,"%02x",r[i]);
        std::snprintf(details,size,"source=near_full mac_binding=physical_efuse reason=%s record_load=%d original_policy=200 original_known=%d target=%u phase=%u active=%d raw=%s boot-readback=%u boot_capture_stage=cfg_before_exit known=%d before=%d after=%d",
            why,int(loaded),loaded==ESP_OK,mosaico_near::target(r),r[3],loaded==ESP_OK&&r[3]==Active,raw,near_boot_value,near_boot_known,near_before_known,near_after_known);
        return ok;
    };
    if(status)return report(true,"readonly_no_gauge_access");
    if((std::strcmp(action,"set")&&!restore)||(!restore&&(!allowed(expected)||!allowed(target))))return report(false,"blocked_arguments");
    uint8_t physicalMac[6]{};if(!near_physical_mac(physicalMac))return report(false,"blocked_physical_mac");
    if(!lease.held||!normal_gauge_fence_locked())return report(false,"blocked_storage_or_recovery");
    if((loaded!=ESP_OK&&loaded!=ESP_ERR_NVS_NOT_FOUND)||(restore&&loaded!=ESP_OK)||
        (!restore&&loaded==ESP_OK&&r[3]!=Active&&r[3]!=Restored))return report(false,"blocked_record_no_retry");
    {
        std::lock_guard<std::mutex> battery(battery_mutex);
        GaugeThresholdBackend gate;gate.normalTarget65=true;uint8_t mac[6]{};
        if(!gate.preflight(mac))return report(false,"blocked_normal65_physical_history");
    }
    if(!near_snapshots)near_snapshots=static_cast<Snapshot*>(heap_caps_calloc(2,sizeof(Snapshot),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT));
    if(!near_snapshots)return report(false,"blocked_psram");
    near_before_known=near_after_known=false;
    char why[128]{},closeWhy[128]{};
    const bool opened=gaugeAccess(GaugeAccessAction::Open,why,sizeof(why));
    bool ok=false;const char* result="failed_open_no_parameter_write";
    if(opened){
        std::lock_guard<std::mutex> battery(battery_mutex);
        near_before_known=near_snapshot(near_snapshots[0]);
        const uint16_t before=near_before_known?near_snapshots[0][mosaico_near::index]:0;
        if(restore){target=200;expected=before;}
        if(!near_before_known||!allowed(before)||before!=expected||(loaded==ESP_ERR_NVS_NOT_FOUND&&before!=200))result="failed_before_or_expected_no_write";
        else {
            if(loaded==ESP_ERR_NVS_NOT_FOUND)r=make(physicalMac,target,Pending);
            r[3]=Pending;r[12]=target;r[13]=target>>8;
            if(!near_save(r))result="failed_backup_commit_no_write";
            else {
                GaugeThresholdBackend gate;uint16_t operation=0;
                const bool admitted=gate.runtimeGate()&&gauge_word(0x3a,operation)&&gauge_quiet_full(operation)&&gauge_identity();
                const bool entered=admitted&&gauge_control(GaugeCommand::EnterConfig)&&gauge_wait_config(true);
                const bool written=entered&&near_write(target);
                // 0091 can automatically SEALED the gauge: all authenticated
                // DM evidence is captured here in CFG, never after exit/reopen.
                near_after_known=entered&&near_snapshot(near_snapshots[1]);
                const bool otherDelta=near_after_known&&!unchanged(near_snapshots[0],near_snapshots[1]);
                const bool onlyOwn=written&&near_after_known&&near_snapshots[1][mosaico_near::index]==target&&!otherDelta;
                // Unknown I/O is never replayed. Only one known-before rollback,
                // and never change any of the other 130 observed fields.
                const bool rollback=entered&&!onlyOwn&&near_rollbackOwn(before,target);
                const bool exited=gauge_exit_config();
                ok=onlyOwn&&exited;
                result=ok?"single_field_verified":otherDelta?(rollback?"failed_other_fields_preserved_own_rollback":"failed_other_fields_preserved_unknown_own"):
                    rollback?"failed_known_before_rolled_back":"failed_unknown_no_retry";
            }
        }
    }
    const bool closed=opened?gaugeAccess(GaugeAccessAction::Restore,closeWhy,sizeof(closeWhy)):
        (!gauge_access_cleanup_attempted||gauge_access_cleanup_succeeded);
    bool native65=false;
    if(closed&&opened){std::lock_guard<std::mutex> battery(battery_mutex);uint16_t op=0,dc=0,fcc=0;
        native65=gauge_word(0x3a,op)&&mosaico_gauge::configExitAccepted(op)&&((op>>1)&3)==3&&
            gauge_word(0x3c,dc)&&gauge_word(0x12,fcc)&&dc==65&&fcc==65&&gauge_identity();}
    ok=ok&&closed&&native65;
    if(!closed||!native65)result="critical_final_seal_or_native65";
    if(opened&&r[0]=='N'&&r[3]==Pending){
        r[3]=ok?(restore?Restored:Active):Failed;
        if(!near_save(r)){ok=false;result="failed_final_record_no_retry";}
    }
    ESP_LOGI(Tag,"near_full %s target=%u success=%d",result,target,ok);
    return report(ok,result);
}
bool Hal::gaugeNearFullField(bool after,size_t i,char* details,size_t size)
{
    std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    if(i>=mosaico_near::count||!near_snapshots||!(after?near_after_known:near_before_known)){
        std::snprintf(details,size,"source=near_full cached=0");return false;
    }
    const auto f=mosaico_near::field(i);
    std::snprintf(details,size,"source=near_full stage=%s capture_stage=%s cached=1 index=%u count=131 address=%04x type=%s width=%u rawBE=%0*lx ram_only=1",
        after?"after":"before",after?"cfg_after_write":"fa_before_cfg",unsigned(i),f.address,f.type,f.width,int(f.width*2),static_cast<unsigned long>(near_snapshots[after?1:0][i]));return true;
}

bool Hal::gaugeAccess(GaugeAccessAction action, char* reason, size_t reasonSize)
{
    using namespace mosaico_gauge;
    NormalGaugeStorageLease lease;
    if(!lease.held){if(reason&&reasonSize)std::snprintf(reason,reasonSize,"blocked_ota_storage_lease");return false;}
    std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    if(action==GaugeAccessAction::Open&&(MosaicoOta::busy()||!normal_gauge_fence_locked())){if(reason&&reasonSize)std::snprintf(reason,reasonSize,"blocked_scale_recovery_required");return false;}
    std::lock_guard<std::mutex> lock(battery_mutex);
    gauge_access_cleanup_attempted = false;
    gauge_access_cleanup_succeeded = false;
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
        gauge_access_cleanup_attempted = true;
        const bool restored = gauge_return_access(journal);
        gauge_access_cleanup_succeeded = restored;
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
        gauge_access_cleanup_attempted = true;
        const bool restored = gauge_return_access(journal);
        gauge_access_cleanup_succeeded = restored;
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
    std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    i2c_device_config_t config{};
    config.dev_addr_length = I2C_ADDR_BIT_LEN_7;
    config.device_address = 0x55;
    config.scl_speed_hz = 400000; // Same gauge bus speed as the official Mosaico example.
    if (i2c_master_bus_add_device(_i2c_bus, &config, &gauge) != ESP_OK) {
        ESP_LOGW(Tag, "BQ27220 unavailable; battery telemetry disabled");
        return;
    }
    std::lock_guard<std::mutex> lock(battery_mutex);
    // Passive RAM-only evidence precedes any sample/boot-reload fence. No
    // access keys, CFG, Seal, parameter writer or NAND operation is used here.
    // Ordinary Original boots avoid this extra diagnostic read altogether.
    if (contrast_present_locked() && contrast_protected()) {
        Hal::GaugeDiagnosticSnapshot standard{};
        Hal::GaugeEngineSnapshot engine{};
        gauge_diagnostic_locked(standard, mosaico_gauge_status::BootBefore, 0);
        gauge_engine_locked(engine, mosaico_gauge_status::BootBefore, 0);
        MosaicoGaugeCold::earlyPrefetch(standard, engine);
    }
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
    // UI/policy owns refresh. BLE must not wait behind a long gauge transaction.
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
    // Only positive gauge current is observable here. This does NOT identify
    // external power (e.g. USB while full); no charger pins/config are touched.
    const auto physical=batteryPolicyTelemetry();
    return physical.valid && physical.currentMa > 3;
}

Hal::BatteryTelemetry Hal::batteryTelemetry(bool refresh)
{
    std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    std::lock_guard<std::mutex> lock(battery_mutex);
    sample_battery_locked(refresh);
    return battery_telemetry;
}

bool Hal::gaugeTraceTrySample(BatteryTelemetry& sample)
{
    if(MosaicoOta::busy()||MosaicoOta::healthPending())return false;
    std::unique_lock<std::recursive_mutex> transaction(gauge_transaction_mutex,std::try_to_lock);
    if(!transaction.owns_lock())return false;
    // Raw measurements, not physical-unit admission. NAND's independent
    // voltage/temperature gate must remain observable during recovery.
    std::unique_lock<std::mutex> battery(battery_mutex,std::try_to_lock);
    if(!battery.owns_lock()||!gauge||MosaicoOta::busy()||MosaicoOta::healthPending())return false;
    sample_battery_locked(true);
    sample=battery_telemetry;
    return true;
}

bool Hal::gaugeSetNominalCapacity(uint16_t expectedOld, uint16_t target65, bool restore,
                                 char* reason, size_t reasonSize)
{
    using namespace mosaico_gauge;
    NormalGaugeStorageLease lease;
    if(!lease.held){if(reason&&reasonSize)std::snprintf(reason,reasonSize,"blocked_ota_storage_lease");return false;}
    std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    if((!restore&&MosaicoOta::busy())||!normal_gauge_fence_locked()){if(reason&&reasonSize)std::snprintf(reason,reasonSize,"blocked_scale_recovery_required");return false;}
    // Existing command, one owner from SEALED through finally closure. Do not
    // hold the nonrecursive battery mutex while calling the public primitives.
    if(!restore&&expectedOld==FactoryMah&&target65==NominalMah) {
        bool sealed=false,alreadyNominal=false;
        {
            std::lock_guard<std::mutex> battery(battery_mutex);
            uint16_t op=0,dc=0,fcc=0;
            if(!gauge||!gauge_word(0x3a,op)) {
                if(reason&&reasonSize)std::snprintf(reason,reasonSize,"blocked_standard_telemetry");
                return false;
            }
            sealed=configExitAccepted(op)&&((op>>1)&3)==3;
            alreadyNominal=sealed&&gauge_word(0x3c,dc)&&gauge_word(0x12,fcc)&&keepLearnedNominal(dc,fcc);
        }
        if(sealed) {
            if(alreadyNominal)return gaugeReconcileNominal(reason,reasonSize); // no access/CFG/reinit
            char phaseReason[128]{},closeReason[128]{};
            const bool opened=gaugeAccess(GaugeAccessAction::Open,phaseReason,sizeof(phaseReason));
            const bool cleanupAttempted=gauge_access_cleanup_attempted;
            const bool cleanupSucceeded=gauge_access_cleanup_succeeded;
            // Successful OPEN is now FA, so this recursive call takes only the
            // unchanged authenticated two-field writer below (no second OPEN).
            const bool applied=opened&&gaugeSetNominalCapacity(expectedOld,target65,false,phaseReason,sizeof(phaseReason));
            const bool closed=opened?gaugeAccess(GaugeAccessAction::Restore,closeReason,sizeof(closeReason)):
                (!cleanupAttempted||cleanupSucceeded);
            if(!opened||!applied||!closed) {
                if(reason&&reasonSize)std::snprintf(reason,reasonSize,"%s",
                    !closed?"critical_nominal_final_seal":phaseReason);
                return false;
            }
            return gaugeReconcileNominal(reason,reasonSize);
        }
    }
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
            if(!recorded)return report(false,"critical_final_journal_readback");
            if(!gauge_store_normal_target(0))return report(false,"critical_normal_target_clear_readback");
            return report(true,"already_restored_normal_target_cleared");
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
        if(!gauge_store_normal_target(0))return report(false,"critical_normal_target_clear_readback");
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

    if (boot_factory_reload_scope && !restore) {
        // Applied boot paths replace the early admission snapshot with pre-CFG
        // evidence. Pair/standard checks above are already cached; these reads
        // never gate CFG, and rejected paths retain their early BootBefore.
        gauge_diagnostic_locked(gauge_boot_before,mosaico_gauge_status::BootBefore,
                                static_cast<uint8_t>(GaugeBootReloadStatus::Applied));
        gauge_engine_locked(gauge_engine_before,mosaico_gauge_status::BootBefore,
                            static_cast<uint8_t>(GaugeBootReloadStatus::Applied));
    }
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
    if (forward && boot_factory_reload_scope && !restore) near_boot_apply();
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

bool Hal::gaugeThresholdAudit(bool privileged,char* details,size_t detailsSize)
{
    std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    uint16_t normalTarget=0;
    const bool normalTarget65=privileged&&gauge_load_normal_target(normalTarget)==ESP_OK&&normalTarget==mosaico_gauge::NominalMah;
    if(privileged&&!(normalTarget65?normal_gauge_fence_locked():scale_boot_fence_locked())){if(details&&detailsSize)std::snprintf(details,detailsSize,"blocked_scale_recovery_required");return false;}
    std::lock_guard<std::mutex> battery(battery_mutex);
    gauge_threshold_cached={};gauge_threshold_cached_generation=0;
    gauge_calibration_cached={};
    gauge_public_extra_cached={};gauge_public_extra_generation=0;
    GaugeThresholdBackend backend;backend.readPublicExtra=privileged;backend.normalTarget65=normalTarget65;
    mosaico_threshold::Result result{};
    bool ok=false;
    if(privileged){mosaico_threshold::Coordinator<GaugeThresholdBackend> coordinator(backend);result=coordinator.run();ok=result.success;}
    else if(gauge){
        backend.beforeStandardValid=backend.standard(backend.before);backend.after=backend.before;backend.readControl();
        GaugeFccBackend reader;mosaico_fcc_limit::Journal runtime{};
        if(reader.loadRaw(mosaico_fcc_limit::journalKey,runtime)==mosaico_fcc_limit::Load::Present&&mosaico_fcc_limit::verifiedRuntimePermission(runtime))backend.expectedConfig=runtime.target.config;
        ok=backend.readThresholds();
    }
    if(result.success){gauge_threshold_cached=backend.values;gauge_threshold_cached_generation=result.journal.generation;}
    mosaico_calibration::publish(result.success,backend.calibration,gauge_calibration_cached);
    mosaico_public_extra::publish(result.success,result.closed,backend.publicExtra,gauge_public_extra_cached);
    if(result.success&&result.closed)gauge_public_extra_generation=result.journal.generation;
    if(details&&detailsSize){
        int count=std::snprintf(details,detailsSize,
            "cache_count=%u cache_generation=%lu schema=%u privileged=%d attempted=%d ram_attempted=%d readable=%u/%u anchors=%d profile1_anchor_match=%d profile_active=not_asserted control_valid=%d control_status=%04x control_batt_id=%u endian=BE2_U1first format=typed:decoded/rawBE reason=%s closed=%d audit_record=%s intended_state=%u last_read_state=%u generation=%lu before_standard_valid=%d after_standard_valid=%d before_soc=%u after_soc=%u before_cycles=%u after_cycles=%u before_sec=%u after_sec=%u calibration_count=%u calibration_ram_only=1 journal_crc_fields=59 extra_readable=%u/65 extra_complete=%d extra_error=%s ",
            gauge_threshold_cached.readable,static_cast<unsigned long>(gauge_threshold_cached_generation),result.attempted?result.journal.schema:0U,
            privileged,result.attempted,gauge_threshold_audit_attempted,backend.values.readable,static_cast<unsigned>(mosaico_threshold::fieldCount),backend.values.anchors,
            backend.values.readable>=3&&backend.values.raw[0]==65&&backend.values.raw[1]==65&&backend.values.raw[2]==0x112A,
            backend.controlReadable,backend.controlStatus,backend.controlStatus&7U,ok?"validated":backend.why,
            result.closed,!backend.journalLoaded?"not_loaded":(backend.journalLoadState==mosaico_threshold::Load::Absent?"absent":(backend.journalLoadState==mosaico_threshold::Load::Present?"present":"invalid")),
            result.attempted?static_cast<unsigned>(result.journal.state):0U,backend.lastReadJournalState,static_cast<unsigned long>(result.journal.generation),backend.beforeStandardValid,backend.afterStandardValid,backend.before.soc,backend.after.soc,
            backend.before.cycles,backend.after.cycles,(backend.before.op>>1)&3U,(backend.after.op>>1)&3U,gauge_calibration_cached.readable,gauge_public_extra_cached.readable,gauge_public_extra_cached.complete,mosaico_public_extra::errorName(gauge_public_extra_cached.error));
        size_t used=count>0?static_cast<size_t>(count):detailsSize;
        for(unsigned i=0;i<backend.values.readable&&i<mosaico_threshold::legacyFieldCount&&used<detailsSize;++i){
            const auto& field=mosaico_threshold::fields[i];
            count=std::snprintf(details+used,detailsSize-used,"%s=%ld/%04x ",field.name,
                static_cast<long>(mosaico_threshold::decoded(field.type,backend.values.raw[i])),backend.values.raw[i]);
            if(count>0)used+=static_cast<size_t>(count);
        }
    }
    return ok;
}

bool Hal::gaugeThresholdAuditField(size_t index,char* details,size_t detailsSize)
{
    std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    std::lock_guard<std::mutex> battery(battery_mutex);
    if(!details||detailsSize<160)return false;
    if(index>=mosaico_threshold::fieldCount&&index<mosaico_threshold::fieldCount+mosaico_calibration::fieldCount){
        const size_t i=index-mosaico_threshold::fieldCount;
        if(gauge_threshold_cached.readable!=mosaico_threshold::fieldCount||!gauge_threshold_cached_generation||gauge_calibration_cached.readable!=mosaico_calibration::fieldCount){
            std::snprintf(details,detailsSize,"cached=0 index=%u reason=no_complete_calibration_snapshot",static_cast<unsigned>(index));return false;
        }
        const auto& field=mosaico_calibration::fields[i];
        const bool opaque=field.type==mosaico_calibration::Type::F4;
        char decoded[16]="opaque";
        if(!opaque)std::snprintf(decoded,sizeof(decoded),"%ld",static_cast<long>(mosaico_calibration::decoded(gauge_calibration_cached,i)));
        const int count=std::snprintf(details,detailsSize,"cached=1 generation=%lu index=%u count=66 address=%04x name=%s type=%s width=%u raw=%0*lx decoded=%s ram_only=1 journal_crc_fields=59",
            static_cast<unsigned long>(gauge_threshold_cached_generation),static_cast<unsigned>(index),static_cast<unsigned>(field.selector),field.name,
            opaque?"F4":(field.type==mosaico_calibration::Type::I2?"I2":(field.type==mosaico_calibration::Type::I1?"I1":"U1")),
            field.width,int(field.width*2),static_cast<unsigned long>(mosaico_calibration::rawWord(gauge_calibration_cached,i)),decoded);
        return count>0&&static_cast<size_t>(count)<detailsSize;
    }
    if(gauge_threshold_cached.readable!=mosaico_threshold::fieldCount||!gauge_threshold_cached_generation||index>=mosaico_threshold::fieldCount){
        std::snprintf(details,detailsSize,"cached=0 index=%u count=%u reason=no_complete_snapshot_or_index",static_cast<unsigned>(index),gauge_threshold_cached.readable);
        return false;
    }
    const auto& field=mosaico_threshold::fields[index];
    const uint16_t raw=gauge_threshold_cached.raw[index];
    const int count=std::snprintf(details,detailsSize,"cached=1 generation=%lu schema=2 index=%u count=%u address=%04x %s=%ld/%04x profile_active=not_asserted",
        static_cast<unsigned long>(gauge_threshold_cached_generation),static_cast<unsigned>(index),static_cast<unsigned>(mosaico_threshold::fieldCount),
        static_cast<unsigned>(field.selector),field.name,static_cast<long>(mosaico_threshold::decoded(field.type,raw)),raw);
    return count>0&&static_cast<size_t>(count)<detailsSize;
}

// Pure cache inspection: available on UART, no gauge/security/NVS transaction.
bool Hal::gaugePublicExtraField(size_t index,char* details,size_t detailsSize)
{
    std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    std::lock_guard<std::mutex> battery(battery_mutex);
    if(!details||detailsSize<160)return false;
    if(index>=mosaico_public_extra::fieldCount){
        std::snprintf(details,detailsSize,"cached=0 reason=index_0..64");return false;
    }
    const auto& field=mosaico_public_extra::fields[index];
    const auto& cache=gauge_public_extra_cached;
    const bool complete=cache.complete&&cache.readable==mosaico_public_extra::fieldCount&&gauge_public_extra_generation;
    const int n=std::snprintf(details,detailsSize,
        "cached=%d index=%u address=%04x type=%s width=%u rawBE=%0*x generation=%lu readable=%u/65 complete=%d error=%s ram_only=1",
        complete,static_cast<unsigned>(index),static_cast<unsigned>(field.selector),mosaico_public_extra::typeName(field.type),field.width,
        field.width*2,complete?cache.raw[index]:0,static_cast<unsigned long>(gauge_public_extra_generation),cache.readable,complete,mosaico_public_extra::errorName(cache.error));
    return complete&&n>0&&static_cast<size_t>(n)<detailsSize;
}

// Closed standard-command snapshot. No MAC/control/security path is used.
template <typename Reader>
constexpr bool gauge_charge_read(Reader read,uint16_t (&values)[13])
{
    constexpr uint8_t registers[]={0x06,0x08,0x0A,0x0C,0x10,0x12,0x14,0x18,0x2C,0x30,0x32,0x3A,0x3C};
    uint16_t fresh[13]{};
    bool complete=true;
    for(unsigned i=0;i<13;++i)complete&=read(registers[i],fresh[i]);
    if(!complete)return false;
    for(unsigned i=0;i<13;++i)values[i]=fresh[i];
    return true;
}

// Independent OCV diagnostic backend. No FCC repair policy/journal is used.
struct GaugeOcvBackend {
    bool fullReadSeen=false;
    int64_t lastFullReadUs=0;
    bool wasAttempted() const {return gauge_ocv_attempted;}
    bool runtimeGate() const {
        const auto p=GetNetworkQuota().powerStats();
        const auto b=GetCodexMicroBle().diagnostics();
        return p.locked&&p.phase==2&&!p.wifiRunning&&!b.advertising&&!b.connected&&
            !MosaicoOta::busy()&&!MosaicoOta::healthPending();
    }
    bool identity() {
        std::lock_guard<std::mutex> battery(battery_mutex);
        return gauge&&runtimeGate()&&gauge_identity()&&runtimeGate();
    }
    bool waitFullSnapshotSlot() {
        if(!fullReadSeen)return true;
        const int64_t now=esp_timer_get_time();
        if(now<lastFullReadUs)return false;
        const int64_t left=1000000-(now-lastFullReadUs);
        // All rate waiting occurs BEFORE battery_mutex, including wire-fresh
        // and the first post-command sample. Never burst full register batches.
        if(left>0)gauge_delay_ms(static_cast<uint32_t>((left+999)/1000));
        return esp_timer_get_time()>=lastFullReadUs+1000000;
    }
    bool readFullLocked(mosaico_ocv::Sample& s) {
        const int64_t started=esp_timer_get_time();
        if((fullReadSeen&&started<lastFullReadUs+1000000)||!gauge||!runtimeGate())return false;
        const bool complete=mosaico_ocv::readSnapshot(gauge_word,s);
        const int64_t finished=esp_timer_get_time();
        fullReadSeen=true;lastFullReadUs=finished;
        return complete&&finished>=started&&runtimeGate();
    }
    bool snapshot(mosaico_ocv::Sample& s) {
        if(!waitFullSnapshotSlot())return false;
        std::lock_guard<std::mutex> battery(battery_mutex);
        return readFullLocked(s);
    }
    int64_t nowUs() const {return esp_timer_get_time();}
    void waitMs(uint32_t ms) {gauge_delay_ms(ms);}
    bool latchAttempt() {
        if(gauge_ocv_attempted)return false;
        gauge_ocv_attempted=true;
        return true;
    }
    mosaico_ocv::SendReport sendOcv(mosaico_ocv::Qualification& qualification) {
        using namespace mosaico_ocv;
        SendReport report{};
        if(!waitFullSnapshotSlot())return report;
        std::lock_guard<std::mutex> battery(battery_mutex);
        // RAM attempt is already consumed. Guard refusal means no packet,
        // distinct from a packet whose ACK is ambiguous. Recheck under the
        // SAME final battery lock that protects the single closed wire command.
        report.sampleValid=readFullLocked(report.sample);
        const int64_t now=esp_timer_get_time();
        if(!report.sampleValid||!qualification.sample(report.sample,now)||!qualification.ready(now)||!runtimeGate())return report;
        const uint8_t packet[]={0x00,0x0C,0x00};
        gauge_feed();
        const bool ack=i2c_master_transmit(gauge,packet,sizeof(packet),20)==ESP_OK;
        gauge_feed();
        report.result=ack?SendResult::Acked:SendResult::AckFailed;
        return report;
    }
};

bool Hal::gaugeOcvMeasure(char* details,size_t detailsSize)
{
    // This outer mutex excludes all gauge mutators, while the backend releases
    // battery_mutex for every one-second watchdog-fed wait so UI reads can run.
    std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    if(!scale_boot_fence_locked()){if(details&&detailsSize)std::snprintf(details,detailsSize,"blocked_scale_recovery_required");return false;}
    GaugeOcvBackend backend;
    mosaico_ocv::Coordinator<GaugeOcvBackend> coordinator(backend);
    const auto r=coordinator.run();
    if(details&&detailsSize)std::snprintf(details,detailsSize,
        "result=%s attempted=%d sent=%d wire_ack=%d edge=%d conditions=%d before_valid=%d after_valid=%d before_soc=%u after_soc=%u before_rm=%u after_rm=%u before_fcc=%u after_fcc=%u before_sec=%u after_sec=%u before_status=%04x after_status=%04x before_ocvcomp=%u after_ocvcomp=%u before_ocvfail=%u after_ocvfail=%u before_ocvgd=%u after_ocvgd=%u mv_min=%u mv_max=%u temp_dK_min=%u temp_dK_max=%u current_min=%d current_max=%d avg_min=%d avg_max=%d ram_one_attempt=1 no_calibration_claim=1",
        mosaico_ocv::resultName(r.result),r.attempted,r.sent,r.ack,r.edge,r.conditions,r.beforeValid,r.afterValid,
        r.before.soc,r.after.soc,r.before.remainingMah,r.after.remainingMah,r.before.fcc,r.after.fcc,
        (r.before.operation>>1)&3U,(r.after.operation>>1)&3U,r.before.batteryStatus,r.after.batteryStatus,
        (r.before.batteryStatus>>14)&1U,(r.after.batteryStatus>>14)&1U,
        (r.before.batteryStatus>>13)&1U,(r.after.batteryStatus>>13)&1U,
        (r.before.batteryStatus>>5)&1U,(r.after.batteryStatus>>5)&1U,
        r.range.minMv,r.range.maxMv,r.range.minTemp,r.range.maxTemp,
        r.range.minCurrent,r.range.maxCurrent,r.range.minAverage,r.range.maxAverage);
    return r.result==mosaico_ocv::Result::Confirmed;
}

bool Hal::gaugeChargeAudit(char* details, size_t detailsSize)
{
    std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    std::lock_guard<std::mutex> battery(battery_mutex);
    uint16_t v[13]{};
    const bool complete=gauge&&gauge_charge_read(gauge_word,v);
    if(details&&detailsSize) {
        if(!complete)std::snprintf(details,detailsSize,"valid=0 readonly=1 reason=standard_read_failed_or_unavailable");
        else std::snprintf(details,detailsSize,
            "valid=1 readonly=1 temperature_dK=%u voltage_mv=%u battery_status=%04x current_ma=%d remaining_mah=%u fcc_mah=%u average_current_ma=%d time_to_full_min=%u soc=%u charge_voltage_mv=%u charge_current_ma=%u op_status=%04x dc_mah=%u fc=%u tca=%u ocvcomp=%u ocvfail=%u ocvgd=%u",
            v[0],v[1],v[2],static_cast<int16_t>(v[3]),v[4],v[5],static_cast<int16_t>(v[6]),v[7],v[8],v[9],v[10],v[11],v[12],
            (v[2]>>9)&1U,(v[2]>>6)&1U,(v[2]>>14)&1U,(v[2]>>13)&1U,(v[2]>>5)&1U);
    }
    return complete;
}

bool Hal::gaugeProfileAudit(char* details, size_t detailsSize)
{
    std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    std::lock_guard<std::mutex> lock(battery_mutex);
    // Local observations only: no cached telemetry, journal, security or config updates.
    uint16_t dc = 0, fcc = 0, operation = 0, status = 0, soc = 0, mv = 0, current = 0;
    bool standard = false;
    uint16_t profile[3]{};
    const char* reason = "gauge_unavailable";
    bool readable = false;
    if (gauge) {
        standard = gauge_word(0x3C, dc);
        standard &= gauge_word(0x12, fcc);
        standard &= gauge_word(0x3A, operation);
        standard &= gauge_word(0x0A, status);
        standard &= gauge_word(0x2C, soc);
        standard &= gauge_word(0x08, mv);
        standard &= gauge_word(0x0C, current);
        readable = gauge_profile_read_stable(gauge_mac_read, profile, reason);
    }
    if (!standard && readable) reason = "standard_io";
    if (details && detailsSize) {
        const int used = std::snprintf(details, detailsSize,
            "readonly=1 std_valid=%d std_dc=%u std_fcc=%u op=%04x status=%04x soc=%u voltage_mv=%u current_ma=%d readable=%d stable=%d reason=%s",
            standard, dc, fcc, operation, status, soc, mv, static_cast<int16_t>(current),
            readable, readable, reason);
        if (readable && used >= 0 && static_cast<size_t>(used) < detailsSize)
            std::snprintf(details + used, detailsSize - static_cast<size_t>(used),
                " dm_dc=%d dm_fcc=%d cfg_byte0=%u cfg_byte1=%u cedv_hex=%04x fcc_limit=%u",
                static_cast<int16_t>(profile[2]), static_cast<int16_t>(profile[1]),
                profile[0] >> 8, profile[0] & 0xFF, profile[0], (profile[0] >> 8) & 1U);
    }
    return standard && readable;
}

bool Hal::gaugeFccLimitStatus(char* details, size_t detailsSize)
{
    using namespace mosaico_fcc_limit;
    std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    std::lock_guard<std::mutex> battery(battery_mutex);
    GaugeFccBackend runtimeReader,coldReader;
    coldReader.mode=Mode::ColdFactory;
    Journal runtime{},cold{};
    const Load runtimeLoad=runtimeReader.loadRaw(journalKey,runtime);
    const Load coldLoad=coldReader.loadRaw(coldJournalKey,cold);
    size_t used=0;
    if(details&&detailsSize) {
        details[0]='\0';
        const auto append=[&](const char* label,Load loaded,const Journal& j) {
            if(used>=detailsSize)return;
            const bool present=loaded==Load::Present;
            const char* record=present?"present":(loaded==Load::Absent?"absent":"invalid");
            const int count=std::snprintf(details+used,detailsSize-used,
                "%s_record=%s %s_state=%u %s_intents=%u %s_backup=%u %s_full_fa=%u %s_orig_cfg=%04x %s_orig_dc=%u %s_orig_fcc=%u %s_target_cfg=%04x %s_target_dc=%u %s_target_fcc=%u ",
                label,record,label,present?static_cast<unsigned>(j.state):0U,
                label,present?j.intents:0U,label,present&&j.backupValid,label,present&&j.fullAccessVerified,
                label,present?j.before.config:0U,label,present?j.before.dc:0U,label,present?j.before.fcc:0U,
                label,present?j.target.config:0U,label,present?j.target.dc:0U,label,present?j.target.fcc:0U);
            if(count>0)used+=static_cast<size_t>(count);
        };
        append("runtime",runtimeLoad,runtime);
        append("cold",coldLoad,cold);
        if(used<detailsSize)std::snprintf(details+used,detailsSize-used,"readonly=1");
    }
    return runtimeLoad!=Load::Invalid&&coldLoad!=Load::Invalid;
}

bool Hal::gaugeFccLimitRepair(char* details, size_t detailsSize)
{
    std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    if(!scale_boot_fence_locked()){if(details&&detailsSize)std::snprintf(details,detailsSize,"blocked_scale_recovery_required");return false;}
    GaugeFccBackend backend;
    auto outcome=mosaico_fcc_limit::Result::Rejected;
    if(backend.prepareRuntimeQuietQualification()) {
        std::lock_guard<std::mutex> battery(battery_mutex);
        mosaico_fcc_limit::Coordinator<GaugeFccBackend> coordinator(backend);
        outcome=coordinator.run();
    }
    if(outcome==mosaico_fcc_limit::Result::Verified)backend.why="verified_sealed_runtime_repair";
    const auto& j=backend.saved;
    if(details&&detailsSize)std::snprintf(details,detailsSize,
        "reason=%s result=%u dirty=%d journal_state=%u intents=%u backup=%d full_verified=%d orig_config=%04x orig_dc=%u orig_dm_fcc=%u target_config=%04x observed_valid=%d observed_config=%04x observed_dc=%u observed_fcc=%u runtime_only=1",
        backend.why,static_cast<unsigned>(outcome),backend.dirty,static_cast<unsigned>(j.state),j.intents,
        j.backupValid,j.fullAccessVerified,j.before.config,j.before.dc,j.before.fcc,j.target.config,
        backend.observedValid,backend.lastObservedDm.config,backend.lastObservedDm.dc,backend.lastObservedDm.fcc);
    return outcome==mosaico_fcc_limit::Result::Verified;
}

bool Hal::gaugeReconcileNominal(char* reason, size_t reasonSize)
{
    using namespace mosaico_gauge;
    NormalGaugeStorageLease lease;
    if(!lease.held){if(reason&&reasonSize)std::snprintf(reason,reasonSize,"blocked_ota_storage_lease");return false;}
    std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    if(!normal_gauge_fence_locked()){
        if(reason&&reasonSize)std::snprintf(reason,reasonSize,"blocked_scale_recovery_required");
        return false;
    }
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
    if (nominal.state != static_cast<uint8_t>(State::Committed) &&
        nominal.state != static_cast<uint8_t>(State::Pending) &&
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
    if(!gauge_store_normal_target(NominalMah))return report(false,"critical_normal_target_readback");
    return report(true, "reconciled_std_target_observed_sec3_normal65_persisted");
}

bool Hal::gaugeEngineSample(GaugeEngineSnapshot& out)
{
    std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    std::lock_guard<std::mutex> battery(battery_mutex);
    gauge_engine_locked(out,mosaico_gauge_status::Explicit,0);
    using namespace mosaico_gauge_engine;
    return (out.validMask&(Cedv|Smooth|DefinitionMatch))==(Cedv|Smooth|DefinitionMatch);
}

void Hal::gaugeBootEngine(GaugeEngineSnapshot& before,GaugeEngineSnapshot& after) const
{
    std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    before=gauge_engine_before;after=gauge_engine_after;
}

bool Hal::gaugeDiagnosticSample(GaugeDiagnosticSnapshot& out)
{
    std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    std::lock_guard<std::mutex> battery(battery_mutex);
    gauge_diagnostic_locked(out,mosaico_gauge_status::Explicit,0);
    return (out.validMask & mosaico_gauge_status::AllRaw)==mosaico_gauge_status::AllRaw;
}

void Hal::gaugeBootDiagnostic(GaugeDiagnosticSnapshot& before, GaugeDiagnosticSnapshot& after) const
{
    std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    before=gauge_boot_before; after=gauge_boot_after;
}

bool Hal::gaugeBootPostExit(GaugeDiagnosticSnapshot& standard,GaugeEngineSnapshot& engine) const
{
    // RAM-only; never reacquire the battery mutex or perform diagnostic I2C.
    std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    standard=gauge_boot_post_exit;engine=gauge_engine_post_exit;
    return standard.phase==mosaico_gauge_status::PostExit && engine.phase==mosaico_gauge_status::PostExit;
}

bool Hal::gaugeModeAudit(char* out, size_t len)
{
    GaugeDiagnosticSnapshot sample{};
    const bool ok=gaugeDiagnosticSample(sample);
    if(out && len)std::snprintf(out,len,
        "readonly=1 valid=%08lx gauging=%04x echo=%04x tx=%u echoio=%u dataio=%u battery=%04x operation=%04x mac_dsg=%u batt_dsg=%u mode=%s mode_valid=%u",
        static_cast<unsigned long>(sample.validMask),sample.gaugingStatus,sample.rawEcho16,
        !!(sample.validMask & mosaico_gauge_status::TxAck),!!(sample.validMask & mosaico_gauge_status::EchoRead),
        !!(sample.validMask & mosaico_gauge_status::DataRead),sample.batteryStatus,sample.operationStatus,
        !!(sample.gaugingStatus & 0x40),!!(sample.batteryStatus & 1),
        mosaico_gauge_status::modeName(sample.mode),!!(sample.validMask & mosaico_gauge_status::ModeValid));
    return ok && (sample.validMask & mosaico_gauge_status::ModeValid);
}

namespace {
bool gauge_official_apply_locked(uint16_t capacity, char* details, size_t size, bool strictFcc = false)
{
    using namespace mosaico_gauge;
    const auto official = mosaico_official_gauge_apply(gauge, capacity);
    const bool applied = official.success;
    uint16_t operation=0, dc=0, fcc=0;
    bool capacityRead = gauge_word(0x3c, dc) && gauge_word(0x12, fcc);
    const bool observed = gauge_word(0x3a, operation);
#if MOSAICO_GAUGE_USE_PREVIOUS_PROFILE
    const bool capacityValid = capacityRead && dc == 65 && reasonableFcc(fcc);
#else
    // Official skip deliberately preserves learned FCC; FCC_LIM bounds it to DC.
    const bool capacityValid = capacityRead && dc == capacity && fcc > 0 && fcc <= capacity;
#endif
    const bool verified = applied && observed && configExitAccepted(operation) &&
                          ((operation >> 1) & 3) == 3 && capacityValid && (!strictFcc || fcc == capacity);
    if (verified) {
        // Equivalent runtime selection only: do not write NVS or revive old gates.
        if (capacity == 65) normal_gauge_target = 65;
        return true;
    }
    // Official create failure may leave access open. Do not repeat profile apply,
    // use old cold/tx gates, or write SOC. Cleanup only when status proves need.
    bool cleanup = observed;
    if (observed && (operation & 0x0400)) {
        // A failed/unknown vendor exit ACK is still an attempt: never resend.
        cleanup = !official.exitAttempted && gauge_exit_config(true);
    }
    uint16_t cleanupOperation=0;
    const bool cleanupObserved = gauge_word(0x3a, cleanupOperation);
    cleanup = cleanup && cleanupObserved;
    if (cleanupObserved && (cleanupOperation & (0x0400 | 1))) cleanup = false;
    if (cleanupObserved && !(cleanupOperation & (0x0400 | 1)) && ((cleanupOperation >> 1) & 3) != 3 &&
        !official.sealAttempted) {
        const uint8_t seal[] = {0x00, 0x30, 0x00};
        const bool sent = i2c_master_transmit(gauge, seal, sizeof(seal), 100) == ESP_OK;
        gauge_delay_ms(2000);
        cleanup = sent && gauge_word(0x3a, cleanupOperation) && configExitAccepted(cleanupOperation) &&
                  ((cleanupOperation >> 1) & 3) == 3 && cleanup;
    }
    if (cleanupObserved && ((cleanupOperation >> 1) & 3) != 3) cleanup = false;

    std::snprintf(details, size, "boot_official_failed_no_retry apply=%u cleanup=%u op=%04x dc=%u fcc=%u exit_try=%u seal_try=%u",
                  applied, cleanup, cleanupOperation, dc, fcc, official.exitAttempted, official.sealAttempted);
    // Failure is visible in the reason, but must not gate UI or OTA recovery.
    return false;
}
struct CapacityTestState {
    bool possiblyChanged=false, restoreAttempted=false;
    std::atomic<bool> restorePending{false};
    int64_t deadlineUs=0;
    const char* outcome="idle";
} capacity_test;

esp_err_t capacity_arm_read(uint8_t& armed)
{
    armed=0;
    nvs_handle_t h=0;
    esp_err_t e=nvs_open("gaugecal",NVS_READONLY,&h);
    if(e==ESP_ERR_NVS_NOT_FOUND)return ESP_OK;
    if(e!=ESP_OK)return e;
    e=nvs_get_u8(h,"cap130_once",&armed);
    nvs_close(h);
    if(e==ESP_ERR_NVS_NOT_FOUND){armed=0;return ESP_OK;}
    return e==ESP_OK&&armed>1?ESP_FAIL:e;
}
bool capacity_arm_write(uint8_t armed)
{
    nvs_handle_t h=0;
    if(nvs_open("gaugecal",NVS_READWRITE,&h)!=ESP_OK)return false;
    esp_err_t e=nvs_set_u8(h,"cap130_once",armed);
    if(e==ESP_OK)e=nvs_commit(h);
    nvs_close(h);
    uint8_t observed=2;
    return e==ESP_OK&&capacity_arm_read(observed)==ESP_OK&&observed==armed;
}
// Consume once even on non-POWERON resets and read failures. Never dispatch an
// arm whose durable cancellation could not be confirmed.
bool capacity_consume_once()
{
    uint8_t armed=0;
    const bool read=capacity_arm_read(armed)==ESP_OK;
    // Missing/zero is already disarmed; avoid a flash commit on ordinary boots.
    if(read&&armed==0)return false;
    const bool cleared=capacity_arm_write(0);
    if(!read||!cleared){capacity_test.outcome="consume_failed_default65";return false;}
    const bool poweron=esp_reset_reason()==ESP_RST_POWERON;
    if(armed&&!poweron)capacity_test.outcome="arm_consumed_non_poweron_default65";
    if(armed&&poweron)capacity_test.outcome="arm_consumed_poweron";
    return armed==1&&poweron;
}
// Shared readonly admission: never send fresh access keys after an unknown exit,
// active CFG/CAL, unreadable security, or a prior unsealed/full-access state.
bool gauge_official_sealed_locked(uint16_t& operation)
{
    return gauge && gauge_word(0x3a,operation) && mosaico_gauge::configExitAccepted(operation) &&
        ((operation>>1)&3)==3;
}
bool capacity_safe_locked(uint16_t& dc,uint16_t& fcc)
{
    uint16_t op=0,mv=0,temp=0,current=0,average=0;
    return gauge_official_sealed_locked(op) && gauge_word(0x08,mv) && gauge_word(0x06,temp) &&
        gauge_word(0x0c,current) && gauge_word(0x14,average) && gauge_word(0x3c,dc) && gauge_word(0x12,fcc) &&
        ((op>>1)&3)==3 && mosaico_gauge::bootReloadPhysical(op,mv,temp,
            static_cast<int16_t>(current),static_cast<int16_t>(average));
}

// Caller owns storage lease and transaction. Admission failures never spend
// the one hardware attempt. Unknown CFG/exit state never receives fresh keys.
bool capacity_restore_locked(char* details,size_t size)
{
    if(capacity_test.restoreAttempted){std::snprintf(details,size,"restore_already_attempted_no_retry");return false;}
    std::lock_guard<std::mutex> battery(battery_mutex);
    uint16_t dc=0,fcc=0;
    if(!capacity_safe_locked(dc,fcc)) {
        capacity_test.outcome="restore_pending_unsafe_or_unknown_no_keys";
        std::snprintf(details,size,"%s",capacity_test.outcome);return false;
    }
    if(dc==65&&fcc==65) {
        capacity_test.restorePending=false;
        capacity_test.possiblyChanged=false;
        capacity_test.outcome="already65_sealed_no_write";
        normal_gauge_target=65;
        std::snprintf(details,size,"%s",capacity_test.outcome);return true;
    }
    capacity_test.restoreAttempted=true;
    const bool ok=gauge_official_apply_locked(65,details,size,true);
    capacity_test.restorePending=!ok;
    if(ok)capacity_test.possiblyChanged=false;
    capacity_test.outcome=ok?"restored65_verified":"restore65_failed_no_retry";
    if(ok)std::snprintf(details,size,"%s",capacity_test.outcome);
    return ok;
}
void capacity_test_poll()
{
    if(!capacity_test.restorePending.load(std::memory_order_acquire))return;
    NormalGaugeStorageLease lease;
    if(!lease.held)return;
    std::unique_lock<std::recursive_mutex> transaction(gauge_transaction_mutex,std::try_to_lock);
    if(!transaction.owns_lock()||!capacity_test.restorePending||capacity_test.restoreAttempted)return;
    if(esp_timer_get_time()<capacity_test.deadlineUs)return;
    // Old unknown trial ownership is never overridden, including explicit restore.
    if(GetHAL().gaugeScaleTrialActive())return;
    char details[128]{};
    (void)capacity_restore_locked(details,sizeof(details));
}
} // namespace

bool Hal::gaugeCapacityTest(const char* action,char* details,size_t size)
{
    using namespace mosaico_gauge;
    NormalGaugeStorageLease lease;
    std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    const bool status=!std::strcmp(action,"status");
    if(!lease.held){std::snprintf(details,size,"storage_lease_busy arm_canceled=0 hardware_restore=not_attempted");return false;}
    if(status) {
        uint8_t armed=0;const bool read=capacity_arm_read(armed)==ESP_OK;
        std::snprintf(details,size,"arm_read=%u armed=%u possibly_changed=%u restore_pending=%u restore_attempted=%u deadline_us=%lld outcome=%s physical_soc_trusted=0",
            read,armed,capacity_test.possiblyChanged,capacity_test.restorePending.load(),capacity_test.restoreAttempted,
            static_cast<long long>(capacity_test.deadlineUs),capacity_test.outcome);
        return read;
    }
    const bool restore=!std::strcmp(action,"restore65");
    if(restore) {
        // Cancellation is independent of any later hardware admission/failure.
        const bool canceled=capacity_arm_write(0);
        if(!canceled){std::snprintf(details,size,"arm_canceled=0 hardware_restore=not_attempted cancel_readback_failed");return false;}
        if(gaugeScaleTrialActive()){std::snprintf(details,size,"arm_canceled=1 hardware_restore=not_attempted old_trial_hold");return false;}
        char phase[128]{};
        const bool ok=capacity_restore_locked(phase,sizeof(phase));
        std::snprintf(details,size,"arm_canceled=1 hardware_restore=%s %s",ok?"verified65":"pending_or_failed",phase);
        return ok;
    }
    if(std::strcmp(action,"arm130")){std::snprintf(details,size,"unknown_action");return false;}
#if MOSAICO_GAUGE_USE_PREVIOUS_PROFILE
    std::snprintf(details,size,"previous_profile_refuses130");return false;
#else
    if(gaugeScaleTrialActive()||capacity_test.possiblyChanged||capacity_test.restorePending){std::snprintf(details,size,"old_trial_or_restore_pending");return false;}
    std::lock_guard<std::mutex> battery(battery_mutex);
    uint16_t dc=0,fcc=0;
    if(!gauge||!selftest()||!capacity_safe_locked(dc,fcc)||dc!=65||fcc!=65){std::snprintf(details,size,"arm_preflight_requires_safe_sealed65_pair");return false;}
    const bool ok=capacity_arm_write(1);
    std::snprintf(details,size,"arm_confirmed=%u bq_writes=0 requires=poweron",ok);return ok;
#endif
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
    NormalGaugeStorageLease lease;
    std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    const auto finish = [&](Status status, const char* why) {
        // Terminal admission refusals cancel this boot's one-shot experiment.
        // Only Deferred probes may retain it; never dispatch from this cleanup.
        if(status==Status::Skipped&&!gauge_boot_info.attempted)(void)capacity_consume_once();
        gauge_boot_info.status = status;
        std::snprintf(gauge_boot_info.reason, sizeof(gauge_boot_info.reason), "%s", why);
        if (reason && reasonSize) std::snprintf(reason, reasonSize, "%s", why);
        return status;
    };
    // Classify every old ledger before a lease/Original branch can short-circuit.
    // These loaders read metadata only; they do not replay an experiment.
    gauge_legacy_cache_init_locked();
    // Only Deferred readonly probes may be revisited by the caller's scheduler.
    if (gauge_boot_info.attempted || gauge_boot_info.status != Status::Deferred) {
        if (reason && reasonSize) std::snprintf(reason, reasonSize, "%s", gauge_boot_info.reason);
        return gauge_boot_info.status;
    }
    if(!lease.held)return finish(Status::Deferred,"boot_deferred_ota_storage_lease");
    // Capture only initialized pre-write evidence. Deferred retries may refresh
    // it while no reload has been attempted; diagnostic failure never gates reload.
    {
        std::lock_guard<std::mutex> battery(battery_mutex);
        uint16_t operation=0;
        if(!gauge_boot_info.attempted && gauge && gauge_word(0x3a,operation) && (operation & 0x20)) {
            gauge_diagnostic_locked(gauge_boot_before,mosaico_gauge_status::BootBefore,static_cast<uint8_t>(Status::Deferred));
            gauge_engine_locked(gauge_engine_before,mosaico_gauge_status::BootBefore,static_cast<uint8_t>(Status::Deferred));
        }
    }
    // Destroyed after inner battery locks, before outer transaction lock. Every
    // terminal path, including early skip/critical, has exactly one after record.
    struct DiagnosticFinish {
        ~DiagnosticFinish() {
            if(gauge_boot_info.status==Status::Deferred || gauge_boot_after.phase)return;
            std::lock_guard<std::mutex> battery(battery_mutex);
            if(!gauge_boot_before.phase)gauge_boot_before.phase=mosaico_gauge_status::BootBefore;
            gauge_boot_before.outcome=static_cast<uint8_t>(gauge_boot_info.status);
            if(!gauge_engine_before.phase)gauge_engine_before.phase=mosaico_gauge_status::BootBefore;
            gauge_engine_before.outcome=static_cast<uint8_t>(gauge_boot_info.status);
            gauge_engine_locked(gauge_engine_after,mosaico_gauge_status::BootAfter,static_cast<uint8_t>(gauge_boot_info.status));
            gauge_diagnostic_locked(gauge_boot_after,mosaico_gauge_status::BootAfter,
                                    static_cast<uint8_t>(gauge_boot_info.status));
        }
    } diagnosticFinish;
    // Terminal refusals also cancel the one-shot arm through finish().
    if (gaugeScaleTrialActive()) {
        return finish(Status::Skipped, "boot_official_active_or_unknown_experiment_no_write");
    }
    std::lock_guard<std::mutex> battery(battery_mutex);
    if (!gauge || !selftest()) return finish(Status::Skipped, "boot_official_failed_unknown_gauge_or_model");
    uint16_t admissionOperation=0;
    if(!gauge_word(0x3a,admissionOperation))
        return finish(Status::Deferred,"boot_official_deferred_operation_read");
    const unsigned security=(admissionOperation>>1)&3;
    if((security!=2&&security!=3)||(admissionOperation&0x0401))
        return finish(Status::Skipped,"boot_official_security_unknown_or_not_idle_no_keys");
    if(!(admissionOperation&0x0020))
        return finish(Status::Deferred,"boot_official_deferred_initcomp");
    if(security==2) {
        // Cold POR defaults to UNSEALED. Admit only the identified factory RAM
        // pair, never arbitrary open access or a partially configured profile.
        uint16_t dc=0,fcc=0,mv=0,temp=0,current=0,average=0;
        if(!gauge_word(0x3c,dc)||!gauge_word(0x12,fcc))
            return finish(Status::Deferred,"boot_official_deferred_factory_pair_read");
        if(dc!=FactoryMah||fcc!=FactoryMah)
            return finish(Status::Skipped,"boot_official_unsealed_nonfactory_no_keys");
        if(!gauge_word(0x08,mv)||!gauge_word(0x06,temp)||!gauge_word(0x0c,current)||!gauge_word(0x14,average)||
           !bootReloadPhysical(admissionOperation,mv,temp,static_cast<int16_t>(current),static_cast<int16_t>(average)))
            return finish(Status::Deferred,"boot_official_deferred_factory_physical");
        if(!gauge_identity())
            return finish(Status::Deferred,"boot_official_deferred_factory_identity");
    }
    // Deferred probes neither consume the experimental arm nor spend the one
    // hardware attempt. SEALED keeps its existing normal admission policy.
    const bool armed130=capacity_consume_once();
    uint16_t capacity=65;
    if(armed130) {
#if MOSAICO_GAUGE_USE_PREVIOUS_PROFILE
        capacity_test.outcome="previous_profile_refused130_default65";
#else
        uint16_t dc=0,fcc=0;
        // Cold gauge RAM may have returned to its known factory pair. The
        // arm command required65; cold admission accepts only these two pairs.
        if(capacity_safe_locked(dc,fcc)&&((dc==65&&fcc==65)||
           (dc==FactoryMah&&fcc==FactoryMah)))capacity=130;
        else capacity_test.outcome="arm_preflight_failed_default65";
#endif
    }
    if(capacity==130) {
        // Mark before the first potentially submitted write, including failures.
        capacity_test.possiblyChanged=true;
        capacity_test.restorePending=true;
        capacity_test.restoreAttempted=false;
        capacity_test.deadlineUs=esp_timer_get_time()+600000000LL;
    }
    char details[128]{};
    gauge_boot_info.attempted = true; // Latch before vendor can submit its first write; never replay failures.
    const bool verified=gauge_official_apply_locked(capacity,details,sizeof(details),capacity==130);
    if(capacity==130)capacity_test.outcome=verified?"applied130_restore_pending":"apply130_failed_restore_pending";
    return finish(verified?Status::Applied:Status::Skipped,
                  verified?(capacity==130?"boot_capacity130_verified_restore_pending":"boot_official_profile_verified_sealed_native_capacity"):details);
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
void Hal::ioe_init() {
    std::lock_guard<std::mutex> guard(haptic_mutex);
    haptic_init_locked();
    ESP_LOGI(Tag, "No M5 IO expander; GPIO8 motor ready=%d (Core V1.0/V1.2.1 mapping)", haptic_ready.load());
}
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
    // Keep the panel off until the complete native splash drains through tx_param.
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

void Hal::display_boot_splash()
{
    struct SplashTransfer {
        uint16_t* strip = nullptr;
        bool allocate() {
            strip = static_cast<uint16_t*>(heap_caps_malloc(
                mosaico_boot_splash::Width * mosaico_boot_splash::StripRows * sizeof(uint16_t),
                MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA));
            return strip != nullptr;
        }
        void render(int y) {
            for (int dy = 0; dy < mosaico_boot_splash::StripRows; ++dy)
                for (int x = 0; x < mosaico_boot_splash::Width; ++x)
                    strip[dy * mosaico_boot_splash::Width + x] = mosaico_boot_splash::pixel(x, y + dy);
            // Only black/white RGB565 pixels; byte swapping is immaterial.
        }
        void draw(int y) {
            ESP_ERROR_CHECK(esp_lcd_panel_draw_bitmap(panel, 0, y, mosaico_boot_splash::Width,
                y + mosaico_boot_splash::StripRows, strip));
        }
        void drainDark() {
            // Pinned CO5300 brightness -> SPI tx_param -> get_trans_result for
            // every in-flight color transaction. No callback takeover/private API.
            // Fail-stop on errors: never free/reuse a possibly in-flight strip.
            ESP_ERROR_CHECK(esp_lcd_panel_co5300_set_brightness(panel, 0));
        }
        void release() { heap_caps_free(strip); strip = nullptr; }
        void enable() {
            ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(panel, true));
            ESP_ERROR_CHECK(esp_lcd_panel_co5300_set_brightness(panel, mosaico_boot_splash::Brightness));
            panel_brightness = mosaico_boot_splash::Brightness;
            BootTimingMark(BootTiming::FirstFrame); // Commands completed, not optical proof.
        }
    } transfer;
    if (!mosaico_boot_splash::show(transfer)) {
        ESP_LOGW(Tag, "Boot splash allocation unavailable; panel remains dark");
        return;
    }
    ESP_LOGI(Tag, "Boot splash transfer complete; native 480x480 brightness=%d; gauge work next",
             mosaico_boot_splash::Brightness);
}



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
#if CONFIG_MOSAICO_CST_SLEEP_TRIAL && CONFIG_IDF_TARGET_ESP32S31
    i2c_device_config_t control{};control.dev_addr_length=I2C_ADDR_BIT_LEN_7;
    control.device_address=ESP_LCD_TOUCH_IO_I2C_CST9217_ADDRESS;control.scl_speed_hz=400000;
    // Registration failure leaves the capability disabled; don't disturb the existing touch driver.
    if(i2c_master_bus_add_device(_i2c_bus,&control,&touch_control)!=ESP_OK)touch_control=nullptr;
#endif
}

bool Hal::touch_ready() const { return touch && lvTouchpad; }
bool Hal::touchSleepAdapterReady() const {
#if CONFIG_MOSAICO_CST_SLEEP_TRIAL && CONFIG_IDF_TARGET_ESP32S31
 return touch_control && touch && port_ready.load(std::memory_order_acquire) && orientation_healthy.load() && touch_idle_polling.load();
#else
 return false;
#endif
}
int32_t Hal::touchSleepWrite(uint16_t command) {
#if CONFIG_MOSAICO_CST_SLEEP_TRIAL && CONFIG_IDF_TARGET_ESP32S31
 if(!touch_control || !TouchSleep::blocksTouch())return ESP_ERR_INVALID_STATE;
 if(command!=0xD11E && command!=0xD101 && command!=0xD105 && command!=0xD109)return ESP_ERR_INVALID_ARG;
 // Exact two-byte wire command. Never add guessed register values or resets.
 const uint8_t bytes[]={uint8_t(command>>8),uint8_t(command)};
 return i2c_master_transmit(touch_control,bytes,sizeof(bytes),25);
#else
 (void)command;return ESP_ERR_NOT_SUPPORTED;
#endif
}
int32_t Hal::touchSleepRead(uint16_t reg,uint8_t* bytes,std::size_t count) {
#if CONFIG_MOSAICO_CST_SLEEP_TRIAL && CONFIG_IDF_TARGET_ESP32S31
 if(!touch_control || !TouchSleep::blocksTouch() || !bytes || !count || count>10)return ESP_ERR_INVALID_STATE;
 if(!((reg==0x0002 && (count==2 || count==4)) || (reg==0xD000 && count==10) ||
     ((reg==0xD204 || reg==0xD1F8 || reg==0xD1FC) && count==4)))return ESP_ERR_INVALID_ARG;
 const uint8_t address[]={uint8_t(reg>>8),uint8_t(reg)};
 return i2c_master_transmit_receive(touch_control,address,sizeof(address),bytes,count,25);
#else
 (void)reg;(void)bytes;(void)count;return ESP_ERR_NOT_SUPPORTED;
#endif
}
int32_t Hal::touchSleepProbe() {
 uint8_t chip[4]{},resolution[4]{},check[4]{};
 int32_t rc=touchSleepRead(0xD204,chip,sizeof(chip));if(rc)return rc;
 rc=touchSleepRead(0xD1F8,resolution,sizeof(resolution));if(rc)return rc;
 rc=touchSleepRead(0xD1FC,check,sizeof(check));if(rc)return rc;
 const uint16_t type=uint16_t(chip[2]) | uint16_t(chip[3])<<8;
 const uint16_t x=uint16_t(resolution[0]) | uint16_t(resolution[1])<<8;
 const uint16_t y=uint16_t(resolution[2]) | uint16_t(resolution[3])<<8;
 return (type==0x9217 || type==0x9220) && x==Resolution && y==Resolution && check[2]==0xCA && check[3]==0xCA ? ESP_OK : ESP_ERR_INVALID_RESPONSE;
}
int32_t Hal::touchSleepReleaseProof(bool& released) {
 released=false;uint8_t data[10]{};
 const int32_t rc=touchSleepRead(0xD000,data,sizeof(data));if(rc)return rc;
 if(data[6]!=0xAB)return ESP_ERR_INVALID_RESPONSE;
 released=(data[5]&0x7F)==0;
 // Leave a proven release available for the original driver/latch to consume
 // immediately on resume. A held report is ACKed so a new release can arrive.
 if(released)return ESP_OK;
#if CONFIG_MOSAICO_CST_SLEEP_TRIAL && CONFIG_IDF_TARGET_ESP32S31
 // Same exact acknowledgment as the reference, one bounded packet.
 const uint8_t ack[]={0xD0,0x00,0xAB};
 return i2c_master_transmit(touch_control,ack,sizeof(ack),25);
#else
 return ESP_ERR_NOT_SUPPORTED;
#endif
}


void Hal::lvgl_init()
{
    // Hide the raw splash before any LVGL task/rotation/partial refresh exists.
    // First FULL redraw must drain before observe_refresh restores saved light.
    ESP_ERROR_CHECK(esp_lcd_panel_co5300_set_brightness(panel, 0));
    ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(panel, false));
    panel_brightness = 0;
    pending_boot_display = true;
    pending_wake_brightness = getBackLightBrightness(true);
    lvgl_port_cfg_t config{};
    config.task_priority = 3;
    config.task_stack = 7168;
#if CONFIG_IDF_TARGET_ESP32S31
    // APP-only mitigation: keep LVGL on CPU0 with the LCD SPI2 ISR installed
    // by main, avoiding cross-core acquiring_dev changes in SDK bg_exit_core.
    config.task_affinity = 0;
#else
    config.task_affinity = 1;
#endif
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
    lv_obj_invalidate(lv_screen_active()); // Full first LVGL redraw, including edges.
    lvglUnlock();
    if (xTaskCreate(motion_task, "motion-cache", 4096, _i2c_bus, 2, &motion_task_handle) != pdPASS) {
        portENTER_CRITICAL(&motion_mux); motion_snapshot.error = ESP_ERR_NO_MEM; portEXIT_CRITICAL(&motion_mux);
    }
    ESP_LOGI(Tag, "CO5300/CST9217 480x480; geometry requires physical acceptance");
#if CONFIG_MOSAICO_SLEEP_IO_RETENTION && CONFIG_IDF_TARGET_ESP32S31
    { std::lock_guard<std::mutex> motorGuard(haptic_mutex);
      std::lock_guard<std::mutex> guard(sleep_io_mutex);
      if (!haptic_active.load(std::memory_order_acquire)) refresh_sleep_io_locked(); }
#endif
}

bool Hal::lvglLock() { return port_ready && lvgl_port_lock(0); }
void Hal::lvglUnlock() { if (port_ready) lvgl_port_unlock(); }

void Hal::setTouchIdlePolling(bool idle)
{
    if(!idle && TouchSleep::blocksTouch()) {TouchSleep::wakeRequested();return;}
    {
        std::lock_guard<std::mutex> guard(haptic_mutex);
        haptic_blocked = idle;
        if (idle) haptic_off_locked(); // Also covers pre-port and repeated idle requests.
    }
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
    bool motorBusy=false;
    { std::lock_guard<std::mutex> motorGuard(haptic_mutex);
      std::lock_guard<std::mutex> guard(sleep_io_mutex);
      // GPIO8 intentionally routes PWM during a pulse. Do not poison the
      // GPIO-only baseline with a transient observation; LS is blocked below.
      motorBusy=haptic_active.load(std::memory_order_acquire);
      if (!motorBusy) refresh_sleep_io_locked();
      info=sleep_io_model.state; }
    if (motorBusy) info.ready=false;
    // No GPIO mutation in this report. A detected inconsistency does immediately
    // block LS through the existing recovery lock AFTER releasing our mutex.
    if(info.enabled && !info.ready)StandbySleep::cancelForActivity();
    return info;
}
bool Hal::sleepIoRetentionReady() const {
    if (haptic_active.load(std::memory_order_acquire)) return false;
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
    TouchPollingInfo info{idle, mosaico_touch_power::pollingPeriodMs(idle),
        unused_gates_off.load(std::memory_order_relaxed) && !haptic_active.load(std::memory_order_acquire)};
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
    if (gaugeScaleTrialActive()) return {}; // Cached consumer fence; no I2C or gauge mutex.
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

void Hal::audio_init() { ESP_LOGW(Tag, "Codec/microphone unvalidated and disabled"); }
bool Hal::audio_ready() const { return false; }
bool Hal::vibrator_ready() const { return haptic_ready.load(std::memory_order_acquire); }
bool Hal::audioSuspended() const { return true; }
// Software tone-generator format only; this does not claim a working codec.
int Hal::getAudioSampleRate() { return 44100; }
void Hal::audioPlay(std::vector<int16_t>&, bool) {}
void Hal::setMicrophoneMeterEnabled(bool) {}
bool Hal::isMicrophoneMeterEnabled() { return false; }
float Hal::getMicrophoneLevel() { return 0.0f; }
void Hal::vibrate(uint16_t durationMs, uint8_t strength) {
    // Recovery lock before haptic mutex: standby can query atomic readiness
    // without taking our mutex. No periodic task, I2C or codec activity.
    if (durationMs && strength) StandbySleep::cancelForActivity();
    std::lock_guard<std::mutex> guard(haptic_mutex);
    if (!durationMs || !strength) { haptic_off_locked(); return; }
    if (haptic_blocked || TouchSleep::blocksTouch()) { haptic_off_locked(); return; }
    if (!haptic_initialized) haptic_init_locked();
    if (!haptic_ready.load(std::memory_order_acquire)) { haptic_off_locked(); return; }
    const int64_t now = esp_timer_get_time();
    if (!haptic_model.begin(now, durationMs, strength)) return; // <=1 pulse / 60ms.
    haptic_active.store(true, std::memory_order_release); // Block sleep BEFORE attaching PWM.
    if (haptic_timer) esp_timer_stop(haptic_timer);
    esp_err_t result = haptic_attach_locked(); // Always attach with duty 0.
    // Arm OFF before enabling duty; queued callbacks serialize on this mutex.
    const int64_t remaining = haptic_model.remaining(esp_timer_get_time());
    if (result == ESP_OK) result = remaining > 0 ? esp_timer_start_once(haptic_timer, remaining) : ESP_ERR_TIMEOUT;
    if (result == ESP_OK) {
        haptic_pwm.resuming(); // A partial/failed resume still needs OFF/pause.
        result = ledc_timer_resume(LEDC_LOW_SPEED_MODE, LEDC_TIMER_1);
    }
    if (result == ESP_OK) result = ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_1, mosaico_haptic::Model::duty(strength));
    if (result == ESP_OK) result = ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_1);
    if (result != ESP_OK) {
        haptic_ready.store(false, std::memory_order_release);
        haptic_off_locked();
        ESP_LOGW(Tag, "Motor pulse rejected error=%ld", long(result));
    }
}
void Hal::stopVibrate() {
    std::lock_guard<std::mutex> guard(haptic_mutex);
    haptic_off_locked();
}

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
    if (snapshot.idle || motion_idle.load() || gaugeScaleTrialActive()) snapshot.valid = false;
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

// Unit/recovery ledger implementation shares the existing closed gauge transport.
#include "mosaico_gauge_scale_runtime.inc"
#include "mosaico_native_recovery.inc"
#include "mosaico_factory_audit.inc"
#include "mosaico_factory_scale_runtime.inc"
#include "mosaico_gauge_contrast_runtime.inc"
#include "mosaico_gauge_cold_runtime.inc"
#include "mosaico_maintenance_power.inc"

namespace {
// Read-only ledger classification. A stable Original is history, not an active
// capacity owner. Unknown/corrupt/Pending/Applied/Restoring never get ignored.
void gauge_legacy_cache_init_locked()
{
    (void)scale_load_locked();
    (void)factory_scale_load_locked();
    (void)contrast_load_locked();
}

bool normal_gauge_fence_locked()
{
    if(!MosaicoGaugeCold::fallbackAllowed()||MosaicoGaugeCold::protectedState())return false;
    const auto old=scale_load_locked();
    (void)factory_scale_load_locked();
    (void)contrast_load_locked();
    if(!factory_scale_maintenance_cached_safe_locked()||!contrast_maintenance_cached_safe_locked()||
       scale_mutation_in_progress||scale_safety_mismatch_locked())return false;
    if(old==scale_journal::Status::Missing)return true;
    if(old!=scale_journal::Status::Ready||!scale_runtime)return false;
    const auto& loaded=scale_runtime->journal.loaded;
    return loaded.selected>=0&&loaded.selected<=1&&
        loaded.records[size_t(loaded.selected)].snapshot.phase==scale_recovery::Phase::Original;
}
bool cold_poll_supervisor()
{
    if (!MosaicoGaugeCold::protectedState()) return false;
    // Preserve OTA -> gauge transaction -> battery order through the entire
    // dispatch, not just through the planner which requested it.
    ContrastStorageLease lease;
    if (!lease.held) return true;
    std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    MosaicoGaugeCold::RestoreRequest request{};
    const bool held = MosaicoGaugeCold::poll(&request);
    if (request.requested && MosaicoGaugeCold::ownsOperation()) {
        if (!contrast_restore_locked()) MosaicoGaugeCold::onRestoreFailed();
    }
    return held;
}
}

Hal::PhysicalBatterySnapshot Hal::physicalBatteryTelemetry(bool refresh)
{
    std::unique_lock<std::recursive_mutex> transaction(gauge_transaction_mutex,std::try_to_lock);
    if(!transaction.owns_lock())return {}; // No UI wait behind quiet diagnostics.
    const bool allowed=normal_gauge_fence_locked();
    std::lock_guard<std::mutex> battery(battery_mutex);
    auto context=scale_unit_context_locked();
    const bool changed=battery_telemetry.unitFactor!=context.factor||
        battery_telemetry.unitEpoch!=context.unitEpoch||battery_telemetry.unitGeneration!=context.generation;
    sample_battery_locked(refresh||changed);
    const auto& t=battery_telemetry;
    if(!allowed&&!(context.verified&&context.phase==mosaico_gauge_physical::Phase::Applied))context.verified=false;
    mosaico_gauge_physical::SampleMeta meta{t.unitFactor,t.unitEpoch,t.unitGeneration,
        t.valid&&t.capacityValid&&t.unitVerified&&!t.knownSafetyCalibrationMismatch};
    mosaico_gauge_physical::NativeQuantities quantities{t.currentMa,t.averageCurrentMa,
        t.remainingMah,t.fullMah,t.designMah};
    return mosaico_gauge_physical::makeSnapshot(t,quantities,meta,context);
}

Hal::BatteryTelemetry Hal::batteryPolicyTelemetry(bool refresh)
{
    std::unique_lock<std::recursive_mutex> transaction(gauge_transaction_mutex,std::try_to_lock);
    if(!transaction.owns_lock())return {};
    const auto physical=physicalBatteryTelemetry(refresh);
    auto policy=physical.native;
    if(!physical.valid){
        // Native normal65 policy, NOT a new verified physical-unit context.
        // Stable Original history must not invalidate the user's persisted
        // normal target. Unknown or active experiments never get this fallback.
        const bool legacy=normal_gauge_fence_locked()&&
            (normal_gauge_target==mosaico_gauge::NominalMah||scale_journal_missing_locked())&&
            !physical.context.verified&&policy.nominalConfigured&&!policy.knownSafetyCalibrationMismatch;
        if(!legacy){policy.valid=false;policy.capacityValid=false;policy.nominalConfigured=false;}
        return policy;
    }
    const auto away=[](int32_t ua)->int16_t {
        return static_cast<int16_t>(ua>=0?(ua+999)/1000:(ua-999)/1000);
    };
    policy.currentMa=away(physical.currentMicroamp);
    policy.averageCurrentMa=away(physical.averageCurrentMicroamp);
    policy.remainingMah=static_cast<uint16_t>(physical.remainingCapacityMicroampHour/1000);
    policy.fullMah=static_cast<uint16_t>((physical.fullChargeCapacityMicroampHour+999)/1000);
    policy.designMah=static_cast<uint16_t>((physical.designCapacityMicroampHour+999)/1000);
    policy.nominalConfigured=physical.designCapacityMicroampHour==65000&&
        physical.fullChargeCapacityMicroampHour>0&&
        physical.fullChargeCapacityMicroampHour<=mosaico_gauge::MaxReasonableFcc*1000U&&
        physical.remainingCapacityMicroampHour<=physical.fullChargeCapacityMicroampHour&&
        mosaico_gauge::configExitAccepted(policy.operationStatus)&&((policy.operationStatus>>1)&3)==3;
    policy.valid=policy.valid&&policy.nominalConfigured&&!policy.knownSafetyCalibrationMismatch;
    return policy;
}
