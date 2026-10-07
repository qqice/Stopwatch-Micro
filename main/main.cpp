#include <debug/boot_trace.h>
/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#include <smooth_ui_toolkit.hpp>
#include <uitk/short_namespace.hpp>
#include <mooncake_log.h>
#include <mooncake.h>
#include <apps/app_codex_micro/app_codex_micro.h>
#include <hal/hal.h>
#include <hal/ble/codex_micro_ble.h>
#include <host/network_quota.h>
#include <memory>
#include <new>
#include <utility>
#include <freertos/task.h>

using namespace mooncake;
using namespace smooth_ui_toolkit;

#ifdef MOSAICO_BOARD
#include <ota/mosaico_ota.h>
#include <host/mosaico_display_settings.h>
#include <host/mosaico_session_monitor.h>
#include <host/standby_sleep.h>
#include "main_idle_wait.h"
#include <tusb.h>
#if CONFIG_IDF_TARGET_ESP32S31 && CONFIG_IDF_TARGET_ARCH_RISCV
#include <ota/panic_capture.h>
#endif
extern "C" void mosaico_console_init(void);
#endif
extern "C" void app_main(void)
{
#ifdef MOSAICO_BOARD
    MosaicoOta::healthPoll(false);
    mosaico_console_init();
#if CONFIG_IDF_TARGET_ESP32S31 && CONFIG_IDF_TARGET_ARCH_RISCV
    MosaicoPanicReport();
#endif
#endif
    BootTraceBegin();
    // Setup logger
    mclog::set_level(mclog::level_info);
    mclog::set_time_format(mclog::time_format_unix_milliseconds);

    // HAL init
    GetHAL().init();
#ifdef MOSAICO_BOARD
    MosaicoDisplay::init();
    MainIdleWait::Lifetime idleWaitLifetime;
#endif
    BootTraceStage(6);

    // BLE monitor startup is synchronous, before the network/OTA owner exists.
#ifdef MOSAICO_BOARD
    MosaicoSessions::init();
#else
    // BLE is a system service and remains available for the device lifetime.
    if (!GetCodexMicroBle().begin()) {
        mclog::tagError("Codex Micro", "BLE initialization failed; restarting");
        GetHAL().delay(1000);
        GetHAL().reboot();
        return;
    }
    const auto battery = GetHAL().getBatteryLevel();
    if (GetHAL().isBatteryLevelValid()) GetCodexMicroBle().setBattery(battery, GetHAL().isBatteryCharging());
#endif
    GetNetworkQuota().begin();
#ifndef MOSAICO_BOARD
    uint32_t last_codex_battery_update = GetHAL().millis();
#endif

    // Setup ui hal
    ui_hal::on_delay([](uint32_t ms) { GetHAL().delay(ms); });
    ui_hal::on_get_tick([]() { return GetHAL().millis(); });

    // This dedicated firmware has one system UI and no launcher.
    std::unique_ptr<AppCodexMicro> system_app(new (std::nothrow) AppCodexMicro());
    if (system_app == nullptr) {
        mclog::tagError("Codex Micro", "failed to allocate system app");
        return;
    }
#ifdef MOSAICO_BOARD
    AppCodexMicro* const ota_app = system_app.get(); // Mooncake owns it for the installed app lifetime.
#endif
    const int app_id = GetMooncake().installApp(std::move(system_app));
    if (app_id < 0 || !GetMooncake().openApp(app_id)) {
        mclog::tagError("Codex Micro", "failed to start system app");
        return;
    }

    BootTraceStage(7);
    // Main loop
    while (true) {
        GetHAL().feedTheDog();
        GetMooncake().update();
#ifndef MOSAICO_BOARD
        GetCodexMicroBle().poll();
        const uint32_t now = GetHAL().millis();
        if (now - last_codex_battery_update >= 30000) {
            last_codex_battery_update = now;
            const auto battery = GetHAL().getBatteryLevel();
            if (GetHAL().isBatteryLevelValid()) GetCodexMicroBle().setBattery(battery, GetHAL().isBatteryCharging());
        }
#endif
#ifdef MOSAICO_BOARD
        MosaicoSessions::service(GetNetworkQuota().displayLocked(), MosaicoOta::busy() || MosaicoOta::healthPending());
        MosaicoOta::healthPoll(ota_app->otaReady());
        const auto sleepBle=GetCodexMicroBle().diagnostics();
        StandbySleep::service(MosaicoOta::busy() || MosaicoOta::healthPending(), sleepBle.advertising || sleepBle.connected);
        GetNetworkQuota().serviceStandbySleep();
        // The monitor has no low-latency remote-control path. Keep its 10Hz
        // motion/100Hz touch responsive without a 1kHz application update loop.
        // Function remains the sole physical wake input. The event candidate
        // retains 20 ms button/release sampling; it never polls touch I2C.
        MainIdleWait::wait(GetNetworkQuota().idleLocked(), MosaicoOta::busy() || MosaicoOta::healthPending(),
            tud_mounted(), GetNetworkQuota().powerStats().wifiRunning, sleepBle.advertising || sleepBle.connected);
#else
        // Preserve StopWatch HID/control latency and scheduling.
        vTaskDelay(pdMS_TO_TICKS(GetNetworkQuota().idleLocked() ? 100 : 1));
#endif
    }
}
