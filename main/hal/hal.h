/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#pragma once
#include "utils/button/Button_Class.hpp"
#include <system_config.h>
#include <memory>
#include <cstdint>
#include <string>
#include <lvgl.h>
#include <smooth_ui_toolkit.hpp>
#include <uitk/short_namespace.hpp>
#include <smooth_lvgl.hpp>
#ifdef MOSAICO_BOARD
#include <driver/i2c_master.h>
using i2c_bus_handle_t = i2c_master_bus_handle_t;
#else
#include <i2c_bus.h>
#endif
#include <string_view>
#include <array>
#include <vector>
#include <cstddef>
#include <cstdio>
/** Startup screen shown while the system UI is being created. */
class BootLogo {
public:
    BootLogo()
    {
        _panel = std::make_unique<uitk::lvgl_cpp::Container>(lv_screen_active());
        _panel->setSize(466, 466);
        _panel->setAlign(LV_ALIGN_CENTER);
        _panel->setBorderWidth(0);
        _panel->setBgOpa(0);
        _panel->setPaddingAll(0);
        _panel->setBgColor(lv_color_black());

        _label_logo = std::make_unique<uitk::lvgl_cpp::Label>(_panel->get());
        _label_logo->setTextFont(&lv_font_montserrat_28);
        _label_logo->setTextColor(lv_color_hex(0xFFFFFF));
        _label_logo->align(LV_ALIGN_CENTER, 0, -14);
        _label_logo->setText(system_config::ProductName);

        _label_msg = std::make_unique<uitk::lvgl_cpp::Label>(_panel->get());
        _label_msg->setTextFont(&lv_font_montserrat_16);
        _label_msg->setTextColor(lv_color_hex(0xBFBFBF));
        _label_msg->align(LV_ALIGN_CENTER, 0, 14);
        _label_msg->setText("Starting up ...");

        _label_version = std::make_unique<uitk::lvgl_cpp::Label>(_panel->get());
        _label_version->setTextFont(&lv_font_montserrat_14);
        _label_version->setTextColor(lv_color_hex(0x8B8B8B));
        _label_version->align(LV_ALIGN_BOTTOM_MID, 0, -12);
        _label_version->setText(system_config::FirmwareVersion);
    }

private:
    std::unique_ptr<uitk::lvgl_cpp::Container> _panel;
    std::unique_ptr<uitk::lvgl_cpp::Label> _label_logo;
    std::unique_ptr<uitk::lvgl_cpp::Label> _label_msg;
    std::unique_ptr<uitk::lvgl_cpp::Label> _label_version;
};

/** Board services required by the Stopwatch Micro system firmware. */
class Hal {
public:
    struct Diagnostics {
        bool i2c        = false;
        bool pmic       = false;
        bool ioExpander = false;
        bool display    = false;
        bool touch      = false;
        bool audio      = false;
        bool vibrator   = false;
        bool buttons    = false;
    };

    struct PerformanceDiagnostics {
        uint32_t lvglHandlerCalls = 0;
        uint32_t lvglHandlerMaxUs = 0;
        uint32_t touchReads       = 0;
        uint32_t touchMaxGapUs    = 0;
        int8_t lvglTaskCore       = -1;
    };

    void init();
    Diagnostics diagnostics() const;
    PerformanceDiagnostics performanceDiagnostics() const;
    void resetPerformanceDiagnostics();

    /* --------------------------------- System --------------------------------- */
    void delay(std::uint32_t ms);
    std::uint32_t millis();
    void feedTheDog();
    std::array<uint8_t, 6> getFactoryMac();
    std::string getFactoryMacString(std::string divider = "");
    void reboot();
    void factoryReset();

    /* ---------------------------------- Power --------------------------------- */
    uint8_t getBatteryLevel();
#ifdef MOSAICO_BOARD
    bool isBatteryLevelValid() const;
#else
    bool isBatteryLevelValid() const { return pmic_ready(); }
#endif
    bool isBatteryCharging(bool strict = false);

    /** Raw standard BQ27220 measurements; no virtual-capacity scaling.
     * nominalConfigured is a consistency check, not capacity calibration or
     * evidence that a qualified discharge/FCC learning cycle has occurred. */
    struct BatteryTelemetry {
        bool valid = false;
        bool capacityValid = false;
        bool nominalConfigured = false;
        uint8_t reportedSoc = 0;
        uint16_t voltageMv = 0;
        int16_t currentMa = 0;
        int16_t averageCurrentMa = 0;
        uint16_t remainingMah = 0;
        uint16_t fullMah = 0;
        uint16_t designMah = 0;
        uint16_t operationStatus = 0;
        uint16_t batteryStatus = 0;
        uint16_t stateOfHealth = 0;
        uint16_t cycleCount = 0;
    };

    struct MosaicoClockDiagnostics {
        uint32_t cpuHz = 0;
        uint32_t sysHz = 0;
        uint32_t apbHz = 0;
        uint32_t memBusDerivedHz = 0; // CPU/MEM divider, NOT physical PSRAM clock.
        int32_t clockErrors[3] = {-1, -1, -1}; // CPU/SYS/APB esp_err_t values.
        bool directDmaTrue = false;
    };
    enum class GaugeAccessAction { Open, Restore };
    enum class GaugeBootReloadStatus { Deferred, Skipped, Applied, Critical };
    struct GaugeBootReloadInfo {
        GaugeBootReloadStatus status = GaugeBootReloadStatus::Deferred;
        bool attempted = false;
        char reason[128] = "boot_reload_not_checked";
    };
#ifdef MOSAICO_BOARD
    BatteryTelemetry batteryTelemetry(bool refresh = false);
    // Explicit RAM nominal setup/restore only. For APPLY expectedOld must match
    // current standard + DM design capacity. For RESTORE it must match current
    // authenticated DM_DC, since standard mirrors may lag during interrupted
    // CFGUPDATE; mismatch reason reports observed DM_DC and standard DC.
    // true is NOT learned FCC/cell calibration. reason uses blocked_/critical_ IDs.
    bool gaugeSetNominalCapacity(uint16_t expectedOld, uint16_t target65, bool restore,
                                 char* reason, size_t reasonSize);
    // Pure safety-model checks; no I2C/NVS writes or hardware operations.
    bool gaugeSafetySelfTest() const;
    // Explicit access-mode transaction only, never an automatic boot action.
    // OPEN supports SEALED->verified default-key->FULL_ACCESS or a no-op prior
    // FULL_ACCESS. Unknown-key prior UNSEALED is refused to preserve a return path.
    // RESTORE uses a separate unit/CRC-bound prior-security journal.
    bool gaugeAccess(GaugeAccessAction action, char* reason, size_t reasonSize);
    // Explicit read-only gauge audit + NVS-only journal reconciliation. Never
    // sends access keys, CFG controls or capacity/configuration data.
    bool gaugeReconcileNominal(char* reason, size_t reasonSize);
    // Caller owns startup/radio-off scheduling. No task/timer is created here.
    GaugeBootReloadStatus gaugeBootReload(char* reason, size_t reasonSize);
    // Snapshot only, no I2C/NVS. Waits for any transaction to finish before a
    // caller uses it to permit a runtime restart.
    GaugeBootReloadInfo gaugeBootReloadInfo() const;
    MosaicoClockDiagnostics displayClockDiagnostics() const;
    // Caller must exclude concurrent flash/NVS operations (esp_cache_msync rule).
    bool displayRamProbe(uint32_t& errors);
#else
    BatteryTelemetry batteryTelemetry(bool = false) { return {}; }
    bool gaugeSetNominalCapacity(uint16_t, uint16_t, bool, char* reason, size_t reasonSize)
    {
        if (reason && reasonSize) std::snprintf(reason, reasonSize, "unsupported: not Mosaico");
        return false;
    }
    MosaicoClockDiagnostics displayClockDiagnostics() const { return {}; }
    bool gaugeSafetySelfTest() const { return false; }
    bool gaugeAccess(GaugeAccessAction, char* reason, size_t reasonSize)
    {
        if (reason && reasonSize) std::snprintf(reason, reasonSize, "unsupported: not Mosaico");
        return false;
    }
    bool gaugeReconcileNominal(char* reason, size_t reasonSize)
    {
        if (reason && reasonSize) std::snprintf(reason, reasonSize, "unsupported: not Mosaico");
        return false;
    }
    GaugeBootReloadStatus gaugeBootReload(char* reason, size_t reasonSize)
    {
        if (reason && reasonSize) std::snprintf(reason, reasonSize, "unsupported: not Mosaico");
        return GaugeBootReloadStatus::Skipped;
    }
    GaugeBootReloadInfo gaugeBootReloadInfo() const
    {
        GaugeBootReloadInfo info;
        info.status = GaugeBootReloadStatus::Skipped;
        std::snprintf(info.reason, sizeof(info.reason), "unsupported: not Mosaico");
        return info;
    }
    bool displayRamProbe(uint32_t& errors) { errors = 0; return false; }
#endif

    /* --------------------------------- Display -------------------------------- */
    void setBackLightBrightness(int brightness, bool saveToSettings = false);
    int getBackLightBrightness(bool loadFromSettings = false);

    // Lvgl
    lv_indev_t* lvTouchpad = nullptr;
    std::unique_ptr<BootLogo> bootLogo;
    bool lvglLock();
    void lvglUnlock();
    void startLvglUpdate();
    void stopLvglUpdate();
    struct TouchPollingInfo {
        bool idle = false;
        uint32_t periodMs = 0;
        bool unusedGatesOff = false; // Output setup succeeded, NOT measured voltage/current.
    };
#ifdef MOSAICO_BOARD
    void setTouchIdlePolling(bool idle);
    TouchPollingInfo touchPollingInfo() const;
#else
    void setTouchIdlePolling(bool) {}
    TouchPollingInfo touchPollingInfo() const { return {}; }
#endif

    /* ---------------------------------- Touch --------------------------------- */
    struct TouchPoint {
        int num = 0;
        int x   = -1;
        int y   = -1;
    };
    TouchPoint getTouchPoint();

    /* ---------------------------------- Audio --------------------------------- */
    void setSpeakerVolume(int volume, bool saveToSettings = false);
    int getSpeakerVolume(bool loadFromSettings = false);
    int getAudioSampleRate();
    bool audioSuspended() const;
    void audioPlay(std::vector<int16_t>& data, bool async = true);
    void setMicrophoneMeterEnabled(bool enabled);
    bool isMicrophoneMeterEnabled();
    float getMicrophoneLevel();

    /* ----------------------------- Vibrator Motor ----------------------------- */
    void vibrate(uint16_t durationMs, uint8_t strength = 100);
    void stopVibrate();

    /* --------------------------------- Button --------------------------------- */
    m5::Button_Class btnA;
    m5::Button_Class btnB;
    m5::Button_Class btnPwr;

    struct ButtonConfig {
        bool sfxEnabled     = false;
        bool vibrateEnabled = true;
    };

    void updateButtonStates();
    void setButtonConfig(ButtonConfig config, bool saveToSettings = false);
    const ButtonConfig& getButtonConfig(bool loadFromSettings = false);

private:
    static constexpr std::string_view SettingsNs = "system";

    i2c_bus_handle_t _i2c_bus = nullptr;
    ButtonConfig _btn_config;
    int _bl_brightness  = 80;
    int _spk_volume     = 80;
    bool _buttons_ready = false;

    void i2c_init();
    void i2c_detect();
    void pmic_init();
    bool pmic_get_pwr_btn_state();
    void ioe_init();
    void ioe_tp_reset();
    void ioe_speaker_enable(bool enable);
    void display_init();
    void touchpad_init();
    void lvgl_init();
    void audio_init();
    void button_init();
    bool pmic_ready() const;
    bool ioe_ready() const;
    bool display_ready() const;
    bool touch_ready() const;
    bool audio_ready() const;
    bool vibrator_ready() const;
};

Hal& GetHAL();
uint32_t GetDisplayFrameCount();

/** Scoped serialization for LVGL access from the Mooncake task. */
class LvglLockGuard {
public:
    LvglLockGuard() : _locked(GetHAL().lvglLock())
    {
    }
    ~LvglLockGuard()
    {
        if (_locked) {
            GetHAL().lvglUnlock();
        }
    }

    LvglLockGuard(const LvglLockGuard&)            = delete;
    LvglLockGuard& operator=(const LvglLockGuard&) = delete;

private:
    bool _locked;
};
