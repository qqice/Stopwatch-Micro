/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#pragma once
#include "mosaico/mosaico_sleep_io_model.h"
#include "mosaico/mosaico_gauge_status_model.h"
#include "mosaico/mosaico_gauge_engine_model.h"
#include "mosaico/mosaico_gauge_physical_model.h"
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
        uint16_t temperatureDeciK = 0;
        bool temperatureValid = false;
        uint16_t rawCoulombMah = 0;
        bool rawCoulombValid = false;
        // Context captured with this native sample, never inferred by readers.
        int64_t unitFactor = 0;
        uint64_t unitEpoch = 0, unitGeneration = 0;
        bool unitVerified = false;
        bool knownSafetyCalibrationMismatch = false; // Detected anomaly, not a claim of absolute calibration.
    };
    using PhysicalBatterySnapshot = mosaico_gauge_physical::Snapshot<BatteryTelemetry>;
    // Raw-only power evidence for explicitly authorized USB maintenance OTA.
    // No SOC/capacity calibration claim; never clears a gauge failure latch.
    // Per-call classification: only failed mutex acquisition is retryable.
    enum class MaintenancePowerResult { Rejected, Busy, Safe };
    struct MaintenancePowerSnapshot {
        bool valid = false;
        // Exact MAC-bound Failed evidence; never a persistent waiver.
        bool historicalCritical = false;
        uint32_t historicalFailedCrc = 0;
        uint16_t voltageMv = 0, temperatureDeciK = 0, operationStatus = 0;
        uint16_t designMah = 0, fullMah = 0;
        int16_t currentMa = 0, averageCurrentMa = 0;
        int64_t sampledUs = 0;
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
    using GaugeDiagnosticSnapshot = mosaico_gauge_status::Snapshot;
    using GaugeEngineSnapshot = mosaico_gauge_engine::Snapshot;
    enum class GaugeBootReloadStatus { Deferred, Skipped, Applied, Critical };
    struct GaugeBootReloadInfo {
        GaugeBootReloadStatus status = GaugeBootReloadStatus::Deferred;
        bool attempted = false;
        char reason[128] = "boot_reload_not_checked";
    };
#ifdef MOSAICO_BOARD
    // Explicit diagnostic I2C only; not part of UI telemetry refresh.
    bool gaugeDiagnosticSample(GaugeDiagnosticSnapshot& out);
    bool gaugeEngineSample(GaugeEngineSnapshot& out);
    void gaugeBootEngine(GaugeEngineSnapshot& before, GaugeEngineSnapshot& after) const;
    // First successful ColdFactory exit only, pre-seal RAM evidence (phase 4).
    bool gaugeBootPostExit(GaugeDiagnosticSnapshot& standard, GaugeEngineSnapshot& engine) const;
    bool gaugeModeAudit(char* out, size_t len);
    // RAM-only copy; phase/validMask distinguish unavailable boot observations.
    void gaugeBootDiagnostic(GaugeDiagnosticSnapshot& before, GaugeDiagnosticSnapshot& after) const;
    BatteryTelemetry batteryTelemetry(bool refresh = false);
    PhysicalBatterySnapshot physicalBatteryTelemetry(bool refresh = false);
    // Dedicated UI/OTA copy. Raw telemetry/history are NEVER scaled in place.
    BatteryTelemetry batteryPolicyTelemetry(bool refresh = false);
    bool gaugeScalePrepare(char* details,size_t size);
    bool gaugeContrastStatus(char*,size_t);
    bool gaugeContrastImages(const char*,char*,size_t);
    bool gaugeContrastPrepare(const char*,char*,size_t);
    bool gaugeContrastApply(char*,size_t);
    bool gaugeContrastRestore(char*,size_t);
    bool gaugeContrastObservedPage(size_t,char*,size_t);
    bool gaugeFactoryScaleStatus(char* details,size_t size);
    bool gaugeFactoryScalePrepare(char* details,size_t size);
    bool gaugeFactoryScaleImages(const char* proof,char* details,size_t size);
    bool gaugeFactoryScaleApply(char* details,size_t size);
    bool gaugeFactoryScaleRestore(char* details,size_t size);
    bool gaugeFactoryScaleObservedField(size_t index,char* details,size_t size);
    bool gaugeFactoryScaleObservedPage(size_t page,char* details,size_t size);
    bool gaugeNativeRecoveryEvidence(char* details,size_t size);
    bool gaugeNativeRecoveryResolve(uint32_t expectedEvidenceCrc,char* details,size_t size);
    bool gaugeFactoryAuditRevalidate(char* details,size_t size);
    bool gaugeFactoryAuditRevalidationStatus(char* details,size_t size);
    bool gaugeFactoryAuditRevalidationField(size_t index,char* details,size_t size);
    bool gaugeFactoryAuditCapture(char* details,size_t size);
    bool gaugeFactoryAuditStatus(char* details,size_t size);
    bool gaugeFactoryAuditField(size_t index,char* details,size_t size);
    bool tryMaintenancePowerSnapshot(MaintenancePowerSnapshot& out, MaintenancePowerResult* result = nullptr);
    bool maintenancePowerStatus(char* details,size_t size) const;
    bool gaugeScaleStatus(char* details,size_t size);
    bool gaugeScaleRestorePlan(char* details,size_t size);
    bool gaugeScaleObservedField(size_t index,char* details,size_t size);
    bool gaugeScaleImages(const char* proof,char* details,size_t size);
    bool gaugeScaleApply(char* details,size_t size);
    bool gaugeScaleRestore(char* details,size_t size);
    void gaugeScalePoll();
    bool gaugeScaleTrialActive() const;
    // Nonblocking recorder sample. False means no sample (lock/OTA/gauge gate);
    // true may contain valid=false/temperatureValid=false for an I2C failure.
    bool gaugeTraceTrySample(BatteryTelemetry& sample);
    // Explicit RAM nominal setup/restore only. For APPLY expectedOld must match
    // current standard + DM design capacity. For RESTORE it must match current
    // authenticated DM_DC, since standard mirrors may lag during interrupted
    // CFGUPDATE; mismatch reason reports observed DM_DC and standard DC.
    // true is NOT learned FCC/cell calibration. reason uses blocked_/critical_ IDs.
    bool gaugeSetNominalCapacity(uint16_t expectedOld, uint16_t target65, bool restore,
                                 char* reason, size_t reasonSize);
    // Closed, read-only standard + authenticated DM profile evidence; no cache updates.
    bool gaugeProfileAudit(char* details, size_t detailsSize);
    // Closed standard charge-register diagnostic only, without cache updates.
    bool gaugeChargeAudit(char* details, size_t detailsSize);
    // Explicit CDC-only manual OCV diagnostic, one RAM-latched attempt per boot.
    bool gaugeOcvMeasure(char* details, size_t detailsSize);
    bool gaugeThresholdAudit(bool privileged,char* details,size_t detailsSize);
    // RAM snapshot only, published after a complete verified sealed privileged audit.
    bool gaugeThresholdAuditField(size_t index,char* details,size_t detailsSize);
    bool gaugePublicExtraField(size_t index,char* details,size_t detailsSize);
    // Explicit confirmed runtime action only; no boot/replay caller.
    bool gaugeFccLimitRepair(char* details, size_t detailsSize);
    // NVS read-only, with explicit absent/present/invalid record distinction.
    bool gaugeFccLimitStatus(char* details, size_t detailsSize);
    // Pure safety-model checks; no I2C/NVS writes or hardware operations.
    bool gaugeSafetySelfTest() const;
    // Explicit access-mode transaction only, never an automatic boot action.
    // OPEN supports SEALED->verified default-key->FULL_ACCESS or a no-op prior
    // FULL_ACCESS. Unknown-key prior UNSEALED is refused to preserve a return path.
    // RESTORE uses a separate unit/CRC-bound prior-security journal.
    bool gaugeAccess(GaugeAccessAction action, char* reason, size_t reasonSize);
    bool gaugeNearFull(const char* action, uint16_t expected, uint16_t target, char* details, size_t size);
    bool gaugeNearFullField(bool after, size_t index, char* details, size_t size);
    // Explicit read-only gauge audit + NVS-only journal reconciliation. Never
    // sends access keys, CFG controls or capacity/configuration data.
    bool gaugeReconcileNominal(char* reason, size_t reasonSize);
    // Caller owns startup/radio-off scheduling. No task/timer is created here.
    bool gaugeCapacityTest(const char* action, char* details, size_t size);
    GaugeBootReloadStatus gaugeBootReload(char* reason, size_t reasonSize);
    // Snapshot only, no I2C/NVS. Waits for any transaction to finish before a
    // caller uses it to permit a runtime restart.
    GaugeBootReloadInfo gaugeBootReloadInfo() const;
    MosaicoClockDiagnostics displayClockDiagnostics() const;
    // Caller must exclude concurrent flash/NVS operations (esp_cache_msync rule).
    bool displayRamProbe(uint32_t& errors);
#else
    bool gaugeDiagnosticSample(GaugeDiagnosticSnapshot& out) { out={}; return false; }
    bool gaugeEngineSample(GaugeEngineSnapshot& out) { out={}; return false; }
    void gaugeBootEngine(GaugeEngineSnapshot& before, GaugeEngineSnapshot& after) const { before={}; after={}; }
    bool gaugeBootPostExit(GaugeDiagnosticSnapshot& standard, GaugeEngineSnapshot& engine) const { standard={}; engine={}; return false; }
    bool gaugeModeAudit(char* out, size_t len) {
        if(out && len)std::snprintf(out,len,"readonly=1 reason=unsupported_not_mosaico");
        return false;
    }
    void gaugeBootDiagnostic(GaugeDiagnosticSnapshot& before, GaugeDiagnosticSnapshot& after) const { before={}; after={}; }
    BatteryTelemetry batteryTelemetry(bool = false) { return {}; }
    PhysicalBatterySnapshot physicalBatteryTelemetry(bool = false) { return {}; }
    BatteryTelemetry batteryPolicyTelemetry(bool = false) { return {}; }
    bool gaugeScalePrepare(char*,size_t) { return false; }
    bool gaugeNativeRecoveryEvidence(char*,size_t) { return false; }
    bool gaugeNativeRecoveryResolve(uint32_t,char*,size_t) { return false; }
    bool gaugeFactoryAuditRevalidate(char*,size_t) { return false; }
    bool gaugeFactoryAuditRevalidationStatus(char*,size_t) { return false; }
    bool gaugeFactoryAuditRevalidationField(size_t,char*,size_t) { return false; }
    bool gaugeFactoryAuditCapture(char*,size_t) { return false; }
    bool gaugeContrastStatus(char*,size_t) { return false; }
    bool gaugeContrastImages(const char*,char*,size_t) { return false; }
    bool gaugeContrastPrepare(const char*,char*,size_t) { return false; }
    bool gaugeContrastApply(char*,size_t) { return false; }
    bool gaugeContrastRestore(char*,size_t) { return false; }
    bool gaugeContrastObservedPage(size_t,char*,size_t) { return false; }
    bool gaugeFactoryScaleStatus(char*,size_t) { return false; }
    bool gaugeFactoryScalePrepare(char*,size_t) { return false; }
    bool gaugeFactoryScaleImages(const char*,char*,size_t) { return false; }
    bool gaugeFactoryScaleApply(char*,size_t) { return false; }
    bool gaugeFactoryScaleRestore(char*,size_t) { return false; }
    bool gaugeFactoryScaleObservedField(size_t,char*,size_t) { return false; }
    bool gaugeFactoryScaleObservedPage(size_t,char*,size_t) { return false; }
    bool gaugeFactoryAuditStatus(char*,size_t) { return false; }
    bool gaugeFactoryAuditField(size_t,char*,size_t) { return false; }
    bool tryMaintenancePowerSnapshot(MaintenancePowerSnapshot& out, MaintenancePowerResult* result = nullptr) { out={};if(result)*result=MaintenancePowerResult::Rejected;return false; }
    bool maintenancePowerStatus(char*,size_t) const { return false; }
    bool gaugeScaleStatus(char*,size_t) { return false; }
    bool gaugeScaleRestorePlan(char*,size_t) { return false; }
    bool gaugeScaleObservedField(size_t,char*,size_t) { return false; }
    bool gaugeScaleImages(const char*,char*,size_t) { return false; }
    bool gaugeScaleApply(char*,size_t) { return false; }
    bool gaugeScaleRestore(char*,size_t) { return false; }
    void gaugeScalePoll() {}
    bool gaugeScaleTrialActive() const { return false; }
    bool gaugeTraceTrySample(BatteryTelemetry&) { return false; }
    bool gaugeSetNominalCapacity(uint16_t, uint16_t, bool, char* reason, size_t reasonSize)
    {
        if (reason && reasonSize) std::snprintf(reason, reasonSize, "unsupported: not Mosaico");
        return false;
    }
    MosaicoClockDiagnostics displayClockDiagnostics() const { return {}; }
    bool gaugeFccLimitStatus(char* details, size_t detailsSize)
    {
        if (details && detailsSize) std::snprintf(details, detailsSize, "readonly=1 runtime_record=absent cold_record=absent reason=unsupported_not_mosaico");
        return false;
    }
    bool gaugeFccLimitRepair(char* details, size_t detailsSize)
    {
        if (details && detailsSize) std::snprintf(details, detailsSize, "reason=unsupported_not_mosaico");
        return false;
    }
    bool gaugePublicExtraField(size_t,char* details,size_t detailsSize)
    {
        if(details&&detailsSize)std::snprintf(details,detailsSize,"cached=0 complete=0 reason=unsupported_not_mosaico");
        return false;
    }
    bool gaugeThresholdAuditField(size_t,char* details,size_t detailsSize)
    {
        if(details&&detailsSize)std::snprintf(details,detailsSize,"cached=0 reason=unsupported_not_mosaico");
        return false;
    }
    bool gaugeThresholdAudit(bool,char* details,size_t detailsSize)
    {
        if(details&&detailsSize)std::snprintf(details,detailsSize,"readable=0 reason=unsupported_not_mosaico");
        return false;
    }
    bool gaugeOcvMeasure(char* details, size_t detailsSize)
    {
        if (details && detailsSize) std::snprintf(details, detailsSize, "result=REJECTED_UNSUPPORTED attempted=0 sent=0");
        return false;
    }
    bool gaugeChargeAudit(char* details, size_t detailsSize)
    {
        if (details && detailsSize) std::snprintf(details, detailsSize, "valid=0 readonly=1 reason=unsupported_not_mosaico");
        return false;
    }
    bool gaugeProfileAudit(char* details, size_t detailsSize)
    {
        if (details && detailsSize) std::snprintf(details, detailsSize,
            "readonly=1 readable=0 stable=0 reason=unsupported_not_mosaico");
        return false;
    }
    bool gaugeSafetySelfTest() const { return false; }
    bool gaugeNearFull(const char*, uint16_t, uint16_t, char* details, size_t size)
    { if(details&&size)std::snprintf(details,size,"unsupported_not_mosaico");return false; }
    bool gaugeNearFullField(bool, size_t, char* details, size_t size)
    { if(details&&size)std::snprintf(details,size,"unsupported_not_mosaico");return false; }
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
    bool gaugeCapacityTest(const char*, char*, size_t) { return false; }
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
        uint32_t periodMs = 0; // Mosaico idle: paused (0), awake: 10 ms.
        bool unusedGatesOff = false; // Output setup succeeded, NOT measured voltage/current.
        uint32_t lvglTimerPeriodMs = 0; // Actual configured port timer, not tick resolution.
        bool monotonicTick = false; // Published only after the public callback is installed.
    };
#ifdef MOSAICO_BOARD
    // idle=true pauses touch reads; wake is Function/explicit caller only.
    void setTouchIdlePolling(bool idle);
    TouchPollingInfo touchPollingInfo() const;
    bool touchSleepAdapterReady() const;
    int32_t touchSleepWrite(uint16_t command);
    int32_t touchSleepRead(uint16_t reg,uint8_t* bytes,std::size_t count);
    int32_t touchSleepProbe();
    int32_t touchSleepReleaseProof(bool& released);
#else
    void setTouchIdlePolling(bool) {}
    TouchPollingInfo touchPollingInfo() const { return {}; }
#endif

    using SleepIoInfo=mosaico_sleep_io::Snapshot;
#ifdef MOSAICO_BOARD
    SleepIoInfo sleepIoRetentionInfo() const; // Reads registered role pins only; no GPIO writes; inconsistency synchronously blocks LS.
    bool sleepIoRetentionReady() const; // Cached; no driver calls in the PM admission path.
#else
    SleepIoInfo sleepIoRetentionInfo() const { return {}; }
    bool sleepIoRetentionReady() const { return true; }
#endif

    struct MotionOrientation {
        bool available = false, idle = false, valid = false;
        uint8_t chipId = 0, initStage = 0;
        int32_t error = 0;
        float ax = 0, ay = 0, az = 0;
        uint16_t degrees = 0;
        uint32_t generation = 0, samples = 0, readErrors = 0;
        int64_t sampleUs = 0;
    };
#ifdef MOSAICO_BOARD
    MotionOrientation motionOrientation() const; // Cached only; no bus access.
    void setMotionIdle(bool idle); // Task owns sensor-only PWR_CTRL, never rails.
    bool setDisplayOrientation(uint16_t degrees); // Caller owns LVGL mutex.
    uint16_t getDisplayOrientation() const;
    bool isDisplayOrientationHealthy() const; // False: hardware/logical transform uncertain; block touch.
#else
    MotionOrientation motionOrientation() const { return {}; }
    void setMotionIdle(bool) {}
    bool setDisplayOrientation(uint16_t) { return false; }
    uint16_t getDisplayOrientation() const { return 0; }
    bool isDisplayOrientationHealthy() const { return true; }
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
#ifdef MOSAICO_BOARD
    void display_boot_splash();
#endif
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
