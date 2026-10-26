#pragma once
#include <atomic>
#include <cstddef>
#include <cstdint>
#ifdef MOSAICO_BOARD
#include <mutex>
#include "mosaico_wifi_settings_model.h"
#include "mosaico_wifi_profiles_model.h"
#include "mosaico_twt_model.h"
#include "mosaico_twt_keepalive.h"
#include <soc/soc_caps.h>
#if CONFIG_IDF_TARGET_ESP32S31
#include "mosaico_hw_rx_snapshot.h"
#include "mosaico_tsf_probe.h"
#include "mosaico_lan_tcp_probe.h"
#include <esp_timer.h>
#endif
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
    void notifyOtaQueued(); // Wake owner only; no radio, refresh-window or lock-state changes.
#if CONFIG_IDF_TARGET_ESP32S31
    bool requestLanTcp(const MosaicoLanTcp::Request& request);
    void cancelLanTcp() { _lan_tcp.cancel(); }
    MosaicoLanTcp::Snapshot lanTcpSnapshot() const { return _lan_tcp.snapshot(); }
    bool requestTsfProbe(uint32_t timeoutMs, uint64_t& requestId);
    MosaicoTsfProbe::Snapshot tsfProbeSnapshot() const;
    bool requestHwRxSnapshot(uint64_t& requestId);
    MosaicoHwRx::Snapshot hwRxSnapshot() const;
#endif
    bool requestRxIngress(bool on, uint32_t src=0, uint16_t port=0, uint32_t ttlSeconds=0);
    bool requestWifiRxObservation(bool on, uint32_t ttlSeconds = 120);
    bool requestRxObservation(bool on, uint32_t ttlSeconds = 1200, bool controlOnly = false,
                              uint32_t filterSrcIp = 0, uint16_t filterSrcPort = 0);
    // Startup owner only, after HAL and before BLE/Wi-Fi; one attempt per boot.
    bool prepareOtaBuffers();
    void wakeForFirmwareUpdate();
    void serviceStandbySleep(); // Existing main loop; preserves the current CPU/RF target.
#if SOC_WIFI_HE_SUPPORT
    bool requestTwtTrial(MosaicoTwt::Mode mode, uint32_t leaseSeconds = 600, MosaicoTwt::Profile profile = MosaicoTwt::Profile::Default);
    bool requestTwtKeepalive(bool enabled); // RAM only; never notify or call Wi-Fi.
    MosaicoTwtKeepalive::State twtKeepaliveSnapshot() const;
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
#if CONFIG_IDF_TARGET_ESP32S31
    void serviceLanTcp();
    MosaicoLanTcp::Probe _lan_tcp;
    void serviceTsfProbe(); // Natural network loop only; never wake it for a probe.
    void captureTsfProbeEvent(int status, uint8_t reason, int64_t localUs);
    static void tsfProbeTimerSentinel(void* arg);
    std::atomic<bool> _tsf_probe_timer_seen{false};
    esp_timer_handle_t _tsf_probe_timer = nullptr;
    std::atomic<bool> _tsf_probe_pending{false};
    MosaicoTsfProbe::Snapshot _tsf_probe_cache;
    struct TsfProbeEvent { bool seen = false; int status = 0; uint8_t reason = 0; int64_t localUs = 0; };
    TsfProbeEvent _tsf_probe_event;
    bool _tsf_probe_consumed = false, _tsf_probe_accept_event = false; // _twt_mux protected.
    void serviceHwRxSnapshot(); // Network owner only, explicit one-shot request.
    std::atomic<bool> _hw_rx_pending{false}, _hw_rx_owner_ready{false};
    mutable portMUX_TYPE _hw_rx_mux = portMUX_INITIALIZER_UNLOCKED;
    MosaicoHwRx::Snapshot _hw_rx_cache;
#endif
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
    bool serviceTwtBootstrap(); // Owner-only, one quota data request per trial.
    uint16_t _twt_bootstrap_id = 0;
    bool _twt_bootstrap_attempted = false, _twt_bootstrap_ok = false;
    void cancelTwtTrial(MosaicoTwt::Stop reason);
    void cleanupTwtTrial();
    bool serviceTwtKeepaliveConfig(); // Owner only, public config before setup.
    void publishTwt();
    void reconcileTwtCycleIdentity();
    void recordTwtCycle(MosaicoTwt::CyclePhase phase, MosaicoTwt::CycleReason reason = MosaicoTwt::CycleReason::None, uint8_t flags = 0);
    MosaicoTwt::CycleRing _twt_cycles;
    uint32_t _twt_cycle_seq = 0;
    uint16_t _twt_cycle_trial_id = 0;
    bool _twt_cycle_open = false;
    std::atomic<bool> _twt_observe{false};
    MosaicoTwtKeepalive::State _twt_keepalive, _twt_keepalive_cache;
    bool _twt_keepalive_owner_processing = false; // _twt_mux; whole owner service, including cancel/publish.
    bool _twt_keepalive_busy = false; // _twt_mux: queued/owner/live/cleanup exclusion.
    MosaicoTwt::Model _twt;
    MosaicoTwt::Snapshot _twt_cache;
    struct TwtEvent {
        uint8_t kind = 0, flow = 0;
        MosaicoTwt::Result setup{};
        uint16_t staReason = 0;
        int64_t eventUs = 0;
        bool staReasonValid = false;
    };
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
#ifdef MOSAICO_BOARD
    std::atomic<bool> _ota_owner_ready{false}; // Published only by the permanent owner loop.
#endif
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
#if CONFIG_IDF_TARGET_ESP32S31
    uint64_t _wifi_settings_generation=0; // Settings mutex; identity for the LAN close gate.
#endif
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
