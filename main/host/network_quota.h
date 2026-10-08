#pragma once
#include <atomic>
#include <cstddef>
#include <cstdint>
#ifdef MOSAICO_BOARD
#include <mutex>
#include "mosaico_wifi_settings_model.h"
#include "mosaico_wifi_profiles_model.h"
#include "mosaico_twt_model.h"
#include <soc/soc_caps.h>
#include <ota/mosaico_ota.h>
#endif
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

struct IdlePowerStats {
    bool locked = false, wifiRunning = false;
    uint8_t profile = 2, phase = 0;
    uint32_t cpuMHz = 240, cycles = 0;
    uint64_t offMs = 0;
    int clockError = 0;
};

class NetworkQuota {
public:
    void begin();
    bool configure(const char* base64);
    void setLocked(bool locked);
    bool displayLocked() const { return _locked.load(); } // Actual UI state, independent of radio power profile.
    bool idleLocked() const
    {
        return _locked.load() && _power_profile.load() != 0
#ifdef MOSAICO_BOARD
               && !MosaicoOta::busy() && !MosaicoOta::healthPending()
#endif
               ;
    }
    void setPowerProfile(uint8_t profile);
    void refreshWhileLocked();
#ifdef MOSAICO_BOARD
    void wakeForFirmwareUpdate();
    void serviceStandbySleep(); // Existing main loop; preserves the current CPU/RF target.
#if SOC_WIFI_HE_SUPPORT
    bool requestTwtTrial(MosaicoTwt::Mode mode, uint32_t leaseSeconds = 600);
    void requestTwtObserve(bool enabled);
    bool popTwtCycle(MosaicoTwt::Cycle& cycle);
    MosaicoTwt::Snapshot twtSnapshot() const;
#endif
    bool requestWifiCredentials(const char* ssid, const char* password);
    bool requestWifiForget(const char* ssid);
    bool wifiSettingsSnapshot(WifiSettingsSnapshot& out);
    bool requestWifiRestart();
    uint32_t wifiConnectAttempts() const { return _wifi_connect_attempts.load(); }
    uint32_t wifiBudgetClosures() const { return _wifi_budget_closures.load(); }
#endif
    IdlePowerStats powerStats() const;
#ifdef MOSAICO_BOARD
    // Explicit serial diagnostics only; RAM state resets to the protective
    // fixed-frequency default on every boot. Never persist this in NVS.
    void setLowClockDiagnostic(bool enabled);
    void setDiagnosticIdleFrequency(uint32_t mhz);
    uint32_t diagnosticIdleFrequency() const { return _diagnostic_idle_mhz.load(); }
    bool lowClockDiagnosticEnabled() const { return _diagnostic_low_clock.load(); }
    // Explicit reversible opt-in after optical validation. Only 160/320MHz;
    // factory/missing/corrupt settings keep the protective 320MHz default.
    bool setIdleCpuFrequency(uint32_t mhz);
    uint32_t idleCpuFrequency() const { return _idle_cpu_mhz.load(); }
#endif
    bool configured() const
    {
        return _configured;
    }
    bool connected() const
    {
        return _connected;
    }
    uint32_t accepted() const
    {
        return _accepted;
    }
    uint32_t failures() const
    {
        return _failures;
    }
    uint32_t historyAccepted() const
    {
        return _history_accepted;
    }
    uint32_t historyFailures() const
    {
        return _history_failures;
    }

private:
    static void task(void* arg);
    void run();
    bool fetch();
    bool requestJson(const char* path, char* body, size_t capacity, int& used);
    bool fetchHistory();
#ifdef MOSAICO_BOARD
    void updateFirmware();
    void serviceWifiSettings();
    void publishWifiProfiles();
    static void scanEvent(void* arg, const char*, int32_t, void*);
    bool selectWifiCandidate(bool locked, int64_t now);
    void cancelWifiScan();
    void drainWifiScan();
    MosaicoWifiProfiles::Model _wifi_profiles;
    MosaicoWifiProfiles::Candidates _wifi_candidates;
    std::atomic<bool> _wifi_scan_done{false};
    std::atomic<int> _wifi_scan_id{-1};
    std::atomic<uint32_t> _wifi_scan_status{1};
    bool _wifi_scan_cancelled=false, _wifi_scan_fault=false;
    bool _wifi_scan_complete=false, _wifi_scan_handler=false;
    std::atomic<bool> _wifi_scan_started{false};
    int64_t _wifi_scan_deadline=0;
    bool _pending_forget=false, _wifi_profiles_persisted=false;
#if SOC_WIFI_HE_SUPPORT
    static void twtEvent(void* arg, const char* base, int32_t event, void* data);
    void serviceTwtTrial(int64_t now, bool locked);
    void cancelTwtTrial(MosaicoTwt::Stop reason);
    void cleanupTwtTrial();
    void publishTwt();
    void reconcileTwtCycleIdentity();
    void recordTwtCycle(MosaicoTwt::CyclePhase phase, MosaicoTwt::CycleReason reason = MosaicoTwt::CycleReason::None, uint8_t flags = 0);
    MosaicoTwt::CycleRing _twt_cycles;
    uint32_t _twt_cycle_seq = 0;
    uint16_t _twt_cycle_trial_id = 0;
    bool _twt_cycle_open = false;
    std::atomic<bool> _twt_observe{false};
    MosaicoTwt::Model _twt;
    MosaicoTwt::Snapshot _twt_cache;
    struct TwtEvent { uint8_t kind = 0, flow = 0; MosaicoTwt::Result setup{}; };
    TwtEvent _twt_events[8]{};
    uint8_t _twt_event_read = 0, _twt_event_write = 0;
    bool _twt_event_overflow = false, _twt_handler_ready = false;
    mutable portMUX_TYPE _twt_mux = portMUX_INITIALIZER_UNLOCKED;
    std::atomic<int> _twt_request{-1};
    int _twt_saved_ps = 0;
    bool _twt_ps_saved = false, _twt_submitted = false, _twt_negotiation_pending = false;
    uint8_t _twt_cleanup_attempts = 0;
    bool _twt_cleanup_failed = false, _twt_teardown_sent = false, _twt_teardown_ack = false;
    bool _twt_cleanup_barrier_needed = false;
#endif
#endif
    void wait(uint32_t milliseconds);
    void setCpu(uint32_t mhz);
    void applyCpuConfig(uint32_t mhz, bool lightSleep);
    void recordWifiRunning(bool running);
    void setPhase(uint8_t phase);
    TaskHandle_t _task_handle = nullptr;
    std::atomic<bool> _locked{false}, _wifi_running{false}, _force_refresh{false};
    std::atomic<uint8_t> _power_profile{2}, _power_phase{0};
#ifdef MOSAICO_BOARD
    std::atomic<bool> _diagnostic_low_clock{false};
    std::atomic<bool> _diagnostic_override{false};
    std::atomic<uint32_t> _diagnostic_idle_mhz{80};
    std::atomic<uint32_t> _idle_cpu_mhz{320};
    std::mutex _cpu_mutex;
    bool _cpu_light_sleep=false, _sleep_pm_fault=false;
    std::mutex _wifi_settings_mutex;
    WifiSettingsSnapshot _wifi_settings;
    std::atomic<uint32_t> _wifi_connect_attempts{0}, _wifi_budget_closures{0};
    char _pending_ssid[33]{}, _pending_password[65]{};
#endif
    std::atomic<uint32_t> _power_cycles{0};
    std::atomic<int> _clock_error{0};
    uint32_t _cpu_target       = 0;
    uint64_t _off_completed_us = 0, _off_since_us = 0;
    mutable portMUX_TYPE _power_mux = portMUX_INITIALIZER_UNLOCKED;
    std::atomic<bool> _configured{false}, _connected{false};
    std::atomic<uint32_t> _accepted{0}, _failures{0};
    std::atomic<uint32_t> _history_accepted{0}, _history_failures{0};
    char _ssid[33]{}, _password[65]{}, _url[257]{}, _token[129]{};
};
NetworkQuota& GetNetworkQuota();
