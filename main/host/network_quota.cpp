#include "network_quota.h"
#include "host_bridge.h"
#include "tailscale_transport.h"
#include "token_history.h"
#ifdef MOSAICO_BOARD
#include "quota_monitor.h"
#include "mosaico_display_settings.h"
#include "mosaico_session_monitor.h"
#include "system_clock.h"
#include <ota/panic_capture.h>
#include <hal/hal.h>
#include <esp_heap_caps.h>
#include <esp_memory_utils.h>
#include <esp_ota_ops.h>
#include <esp_system.h>
#endif
#include <memory>
#include <algorithm>
#include <new>
#include <cJSON.h>
#include <cmath>
#include <cstring>
#include <cstdio>
#include <esp_crt_bundle.h>
#include <esp_event.h>
#include <esp_http_client.h>
#include <esp_netif.h>
#include <esp_netif_sntp.h>
#include <ctime>
#include <esp_timer.h>
#include <esp_pm.h>
#include <esp_private/esp_clk.h>
#include <esp_log.h>
#include <hal/ble/codex_micro_ble.h>
#include <esp_wifi.h>
#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT
#include <esp_wifi_he.h>
#endif
#include <freertos/task.h>
#include <mbedtls/base64.h>
#include <nvs.h>

namespace {
NetworkQuota instance;
#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT
bool twtOtaBlocked()
{
    if (MosaicoOta::busy() || MosaicoOta::healthPending()) return true;
    MosaicoOta::UiSnapshot ota{};
    if (!MosaicoOta::copyUiSnapshot(ota)) return true;
    return (ota.imageVerified && ota.stage != MosaicoOta::UiStage::Complete) ||
        ota.stage == MosaicoOta::UiStage::ReadyReboot || ota.stage == MosaicoOta::UiStage::BootChecking;
}
#endif
bool copyString(cJSON* root, const char* key, char* out, size_t capacity, bool empty = false)
{
    const cJSON* value = cJSON_GetObjectItemCaseSensitive(root, key);
    if (!cJSON_IsString(value) || !value->valuestring) return false;
    const size_t length = std::strlen(value->valuestring);
    if (length >= capacity || (!empty && length == 0)) return false;
    for (size_t i = 0; i < length; ++i)
        if (static_cast<unsigned char>(value->valuestring[i]) < 32) return false;
    std::memcpy(out, value->valuestring, length + 1);
    return true;
}
bool number(cJSON* root, const char* key, double low, double high, uint32_t& out)
{
    const cJSON* value = cJSON_GetObjectItemCaseSensitive(root, key);
    if (!cJSON_IsNumber(value) || !std::isfinite(value->valuedouble) || value->valuedouble < low ||
        value->valuedouble > high || std::floor(value->valuedouble) != value->valuedouble)
        return false;
    out = static_cast<uint32_t>(value->valuedouble);
    return true;
}
}  // namespace
NetworkQuota& GetNetworkQuota()
{
    return instance;
}
void NetworkQuota::setLocked(bool locked)
{
    const bool changed = _locked.exchange(locked) != locked;
#ifdef MOSAICO_BOARD
    // Boost synchronously BEFORE the view starts its full wake redraw, rather
    // than racing the network worker's eventual wake notification.
    if (!locked) setCpu(CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ);
#endif
    if (changed && _task_handle) xTaskNotifyGive(_task_handle);
#ifndef MOSAICO_BOARD
    GetCodexMicroBle().requestRadioIdle(idleLocked());
#endif
}
void NetworkQuota::setPowerProfile(uint8_t profile)
{
    if (profile > 2) return;
    _power_profile = profile;
#ifndef MOSAICO_BOARD
    GetCodexMicroBle().requestRadioIdle(idleLocked());
#endif
    if (_task_handle) xTaskNotifyGive(_task_handle);
}
void NetworkQuota::refreshWhileLocked()
{
    if (!idleLocked()) return;
    _force_refresh = true;
    if (_task_handle) xTaskNotifyGive(_task_handle);
}
void NetworkQuota::wait(uint32_t milliseconds)
{
#ifdef MOSAICO_BOARD
    MosaicoDisplay::Snapshot display;
    if (MosaicoDisplay::snapshot(display) && display.pending && milliseconds > 250) milliseconds = 250;
    WifiSettingsSnapshot wifi;
    if (wifiSettingsSnapshot(wifi) && (wifi.pending || wifi.restartPending) && milliseconds > 250) milliseconds=250;
#endif
#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT
    if (_twt.live()) {
        const int64_t now = esp_timer_get_time();
        const int64_t deadline = _twt.state.setupDeadlineUs ? std::min(_twt.state.setupDeadlineUs, _twt.state.expiryUs) : _twt.state.expiryUs;
        const uint32_t remaining = static_cast<uint32_t>(std::max<int64_t>(1, (deadline - now) / 1000));
        milliseconds = std::min(milliseconds, std::min<uint32_t>(remaining, 1000));
    }
#endif
    ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(milliseconds));
}
void NetworkQuota::setCpu(uint32_t mhz)
{
#ifdef MOSAICO_BOARD
    std::lock_guard<std::mutex> clockLock(_cpu_mutex);
    // Repeated corruption is correlated with the 80MHz standby/transition
    // configuration. Keep CPU/SYS/MEM clock domains stable as a conservative
    // guard, NOT a proven display root-cause fix. Radio duty cycling is retained.
    if (idleLocked() && mhz == 80)
        mhz = _diagnostic_override.load() ? _diagnostic_idle_mhz.load() : _idle_cpu_mhz.load();
    else mhz = CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ;
#endif
    if (_cpu_target == mhz) return;
    esp_pm_config_t config{};
    config.max_freq_mhz       = mhz;
    config.min_freq_mhz       = mhz;
    config.light_sleep_enable = false;
    const esp_err_t result    = esp_pm_configure(&config);
    _clock_error              = result;
    if (result == ESP_OK) _cpu_target = mhz;
}
#ifdef MOSAICO_BOARD
bool NetworkQuota::wifiSettingsSnapshot(WifiSettingsSnapshot& out)
{
    std::unique_lock<std::mutex> guard(_wifi_settings_mutex, std::try_to_lock);
    if (!guard.owns_lock()) return false;
    out = _wifi_settings; return true;
}
bool NetworkQuota::requestWifiCredentials(const char* ssid, const char* password)
{
    std::unique_lock<std::mutex> guard(_wifi_settings_mutex, std::try_to_lock);
    if (!guard.owns_lock()) return false;
    if (!_wifi_settings.available) { _wifi_settings.error=ESP_ERR_INVALID_STATE; return false; }
    if (_wifi_settings.pending || _wifi_settings.restartPending) return false;
    if (!MosaicoWifi::validCredentials(ssid,password)) { _wifi_settings.error=ESP_ERR_INVALID_ARG; return false; }
    std::strcpy(_pending_ssid,ssid); std::strcpy(_pending_password,password);
    _wifi_settings.pending=true; _wifi_settings.error=0;
    xTaskNotifyGive(_task_handle); return true;
}
bool NetworkQuota::requestWifiRestart()
{
    std::unique_lock<std::mutex> guard(_wifi_settings_mutex, std::try_to_lock);
    if (!guard.owns_lock()) return false;
    if (!_wifi_settings.available || _wifi_settings.pending || _wifi_settings.error || !_wifi_settings.rebootRequired) return false;
    _wifi_settings.restartPending=true; _wifi_settings.restartError=0; xTaskNotifyGive(_task_handle); return true;
}
void NetworkQuota::serviceWifiSettings()
{
    // Same sole-owner safety gate as display persistence. Never hold the GUI
    // cache mutex over NVS operations or restart, never rebuild live netifs.
    if (MosaicoOta::busy() || MosaicoOta::healthPending()) return;
    MosaicoOta::UiSnapshot ota{};
    if (!MosaicoOta::copyUiSnapshot(ota) ||
        (ota.imageVerified && ota.stage != MosaicoOta::UiStage::Complete) ||
        ota.stage==MosaicoOta::UiStage::ReadyReboot || ota.stage==MosaicoOta::UiStage::BootChecking) return;
    const esp_partition_t* running=esp_ota_get_running_partition();
    esp_ota_img_states_t state=ESP_OTA_IMG_UNDEFINED;
    if (!running || esp_ota_get_state_partition(running,&state)!=ESP_OK || state!=ESP_OTA_IMG_VALID) return;
    char ssid[33]{}, password[65]{};
    bool restart=false;
    {
        std::unique_lock<std::mutex> guard(_wifi_settings_mutex,std::try_to_lock);
        if (!guard.owns_lock()) return;
        restart=_wifi_settings.restartPending && !_wifi_settings.pending && !_wifi_settings.error;
        if (!restart && !_wifi_settings.pending) return;
        if (!restart) { std::memcpy(ssid,_pending_ssid,sizeof(ssid)); std::memcpy(password,_pending_password,sizeof(password)); }
    }
    if (restart) {
        MosaicoDisplay::Snapshot display;
        if (!MosaicoDisplay::snapshot(display)) return;
        if (display.error) {
            std::lock_guard<std::mutex> guard(_wifi_settings_mutex);
            _wifi_settings.restartPending=false;
            _wifi_settings.restartError=display.error;
            return;
        }
        if (!MosaicoDisplay::rebootSaveReady(display)) return;
        esp_restart(); return;
    }
    nvs_handle_t h=0;
    esp_err_t error=nvs_open("quota_net",NVS_READWRITE,&h);
    if (error==ESP_OK) {
        MosaicoWifi::Blob blob=MosaicoWifi::encode(ssid,password);
        error=nvs_set_blob(h,"wifi_ui",blob.data(),blob.size());
        MosaicoWifi::scrub(blob.data(),blob.size());
        if (error==ESP_OK) error=nvs_commit(h);
        nvs_close(h);
    }
    MosaicoWifi::scrub(password,sizeof(password));
    std::lock_guard<std::mutex> guard(_wifi_settings_mutex);
    MosaicoWifi::scrub(_pending_password,sizeof(_pending_password));
    MosaicoWifi::scrub(_pending_ssid,sizeof(_pending_ssid));
    _wifi_settings.pending=false; _wifi_settings.error=error;
    if (!error) { std::memcpy(_wifi_settings.ssid,ssid,sizeof(ssid)); _wifi_settings.rebootRequired=true; }
}
#if SOC_WIFI_HE_SUPPORT
bool NetworkQuota::requestTwtTrial(MosaicoTwt::Mode mode)
{
    if (mode != MosaicoTwt::Mode::Off && mode != MosaicoTwt::Mode::Baseline && mode != MosaicoTwt::Mode::On) return false;
    if (!_task_handle) return false;
    // Off always wins over a queued command; no Wi-Fi API on serial/GUI threads.
    if (mode == MosaicoTwt::Mode::Off) _twt_request.store(0);
    else { int empty = -1; if (!_twt_request.compare_exchange_strong(empty, static_cast<int>(mode))) return false; }
    xTaskNotifyGive(_task_handle);
    return true;
}
MosaicoTwt::Snapshot NetworkQuota::twtSnapshot() const
{
    portENTER_CRITICAL(&_twt_mux);
    auto out = _twt_cache;
    portEXIT_CRITICAL(&_twt_mux);
    return out;
}
void NetworkQuota::publishTwt()
{
    portENTER_CRITICAL(&_twt_mux);
    _twt_cache = _twt.state;
    portEXIT_CRITICAL(&_twt_mux);
}
void NetworkQuota::twtEvent(void* arg, const char*, int32_t event, void* data)
{
    auto& owner = *static_cast<NetworkQuota*>(arg);
    TwtEvent copied{};
    if (event == WIFI_EVENT_ITWT_SETUP && data) {
        const auto& e = *static_cast<const wifi_event_sta_itwt_setup_t*>(data);
        copied.kind = 1;
        copied.setup.id = e.config.twt_id; copied.setup.flow = e.config.flow_id;
        copied.setup.mantissa = e.config.wake_invl_mant; copied.setup.exponent = e.config.wake_invl_expn;
        copied.setup.duration = e.config.min_wake_dura; copied.setup.unit = e.config.wake_duration_unit;
        copied.setup.status = e.status; copied.setup.reason = e.reason; copied.setup.targetWakeUs = e.target_wake_time;
    } else if (event == WIFI_EVENT_STA_DISCONNECTED) copied.kind = 2;
    else if (event == WIFI_EVENT_ITWT_TEARDOWN && data) {
        const auto& e = *static_cast<const wifi_event_sta_itwt_teardown_t*>(data);
        if (e.status != ITWT_TEARDOWN_SUCCESS) return;
        copied.kind = 3; copied.flow = e.flow_id;
    } else return; // No per-wake logging, APIs, allocations, NVS or HAL calls.
    portENTER_CRITICAL(&owner._twt_mux);
    const uint8_t next = (owner._twt_event_write + 1) % 8;
    if (next == owner._twt_event_read) owner._twt_event_overflow = true;
    else { owner._twt_events[owner._twt_event_write] = copied; owner._twt_event_write = next; }
    portEXIT_CRITICAL(&owner._twt_mux);
    if (owner._task_handle) xTaskNotifyGive(owner._task_handle);
}
void NetworkQuota::cancelTwtTrial(MosaicoTwt::Stop reason)
{
    _twt.end(reason);
    cleanupTwtTrial();
    publishTwt();
}
void NetworkQuota::cleanupTwtTrial()
{
    // Submission success is not teardown completion. Prove the local bitmap
    // empty, or stop the STA radio. Keep restoration debt on any driver error.
    if (_twt_cleanup_failed) return;
    if (!_twt_submitted && !_twt_ps_saved) return;
    ++_twt_cleanup_attempts;
    // A confirmed stop invalidates that Wi-Fi epoch, even if its queued setup
    // result is delivered afterwards. No driver query on an already-stopped STA.
    if (!_wifi_running) _twt_submitted = _twt_negotiation_pending = false;
    if (_twt_submitted) {
        _twt.state.teardownError = esp_wifi_sta_itwt_teardown(FLOW_ID_ALL);
        int bitmap = -1;
        const esp_err_t queried = esp_wifi_sta_itwt_get_flow_id_status(&bitmap);
        if (!_twt_negotiation_pending && queried == ESP_OK && bitmap == 0) _twt_submitted = false;
        else {
            esp_wifi_disconnect();
            if (esp_wifi_stop() == ESP_OK) {
                _twt_submitted = false;
                _twt_negotiation_pending = false;
                recordWifiRunning(false);
                _connected = false;
                _twt.state.associated = false;
            }
        }
    }
    if (_twt_ps_saved) {
        _twt.state.restoreError = esp_wifi_set_ps(static_cast<wifi_ps_type_t>(_twt_saved_ps));
        if (_twt.state.restoreError == ESP_OK) _twt_ps_saved = false;
        else if (_wifi_running && esp_wifi_stop() == ESP_OK) {
            recordWifiRunning(false);
            _connected = false;
            _twt.state.associated = false;
            _twt_submitted = false;
            _twt_negotiation_pending = false;
        }
    }
    _twt.state.cleanupPending = _twt_submitted || _twt_ps_saved;
    if (_twt.state.cleanupPending && _twt_cleanup_attempts >= 2)
        _twt_cleanup_failed = _twt.state.cleanupFailed = true;
    if (!_twt.state.cleanupPending) _twt_cleanup_attempts = 0;
}
void NetworkQuota::serviceTwtTrial(int64_t now, bool locked)
{
    using namespace MosaicoTwt;
    if (_twt.state.cleanupPending) cleanupTwtTrial();
    // Expiry/OTA/unlock wins over an already queued setup success.
    if (_twt.live() && !_twt.check(now, twtOtaBlocked(), locked))
        cancelTwtTrial(_twt.state.stop);
    bool overflow = false;
    portENTER_CRITICAL(&_twt_mux);
    overflow = _twt_event_overflow; _twt_event_overflow = false;
    portEXIT_CRITICAL(&_twt_mux);
    if (overflow) { _twt_submitted = _twt_negotiation_pending = true; cancelTwtTrial(Stop::Lost); }
    while (true) {
        TwtEvent e{};
        portENTER_CRITICAL(&_twt_mux);
        const bool available = _twt_event_read != _twt_event_write;
        if (available) { e = _twt_events[_twt_event_read]; _twt_event_read = (_twt_event_read + 1) % 8; }
        portEXIT_CRITICAL(&_twt_mux);
        if (!available) break;
        if (e.kind == 1) {
            const bool current = _twt.state.stage == Stage::Negotiating && e.setup.id == _twt.state.id;
            if (current) _twt_negotiation_pending = false;
            const bool accepted = _twt.accept(e.setup);
            if (!accepted && current) cancelTwtTrial(_twt.state.stop);
            if (!current && e.setup.status == 1) {
                // SDK carries no request generation except echoed twt_id. Never stamp current generation.
                // A late accepted flow colliding with a live trial invalidates that trial too.
                _twt_submitted = true;
                cancelTwtTrial(Stop::Lost);
            }
        } else if (e.kind == 2 && _twt.live()) {
            ++_twt.state.losses; cancelTwtTrial(Stop::Lost);
        } else if (e.kind == 3 && _twt.state.stage == Stage::Active && e.flow == _twt.state.actual.flow) {
            ++_twt.state.losses; cancelTwtTrial(Stop::Lost);
        }
    }
    const int request = _twt_request.exchange(-1);
    if (request >= 0) {
        cancelTwtTrial(Stop::Explicit);
        const auto mode = static_cast<Mode>(request);
        if (_twt.state.cleanupPending || _twt_cleanup_failed) {
            _twt.state.error = ESP_ERR_INVALID_STATE;
            _twt.end(Stop::Driver);
        } else if (!_twt.start(mode, now)) { _twt.state.error = ESP_ERR_INVALID_STATE; _twt.end(Stop::Driver); }
        if (_twt.live() && !locked) cancelTwtTrial(Stop::Awake);
        if (mode != Mode::Off && (!_twt_handler_ready || twtOtaBlocked()))
            cancelTwtTrial(_twt_handler_ready ? Stop::Ota : Stop::Driver);
    }
    if (_twt.live() && _wifi_running) {
        wifi_ap_record_t ap{};
        const bool associated = esp_wifi_sta_get_ap_info(&ap) == ESP_OK;
        _twt.state.associated = associated;
        if (!associated && (_twt.state.stage == Stage::Active || _twt.state.stage == Stage::Baseline)) {
            ++_twt.state.losses; cancelTwtTrial(Stop::Lost);
        } else if (associated && locked && _twt.state.stage == Stage::Armed) {
            wifi_phy_mode_t phy{};
            _twt.state.apAx = ap.phy_11ax;
            const esp_err_t phyError = esp_wifi_sta_get_negotiated_phymode(&phy);
            _twt.state.phy = phyError == ESP_OK ? static_cast<int>(phy) : -1;
            if (_twt.state.requested == Mode::On && (phyError != ESP_OK || !ap.phy_11ax || phy != WIFI_PHY_MODE_HE20)) {
                _twt.state.error = phyError; cancelTwtTrial(Stop::Unsupported);
            } else {
                wifi_ps_type_t ps{};
                _twt.state.error = esp_wifi_get_ps(&ps);
                if (_twt.state.error == ESP_OK) {
                    _twt_saved_ps = static_cast<int>(ps); _twt_ps_saved = true;
                    _twt.state.error = esp_wifi_set_ps(WIFI_PS_MIN_MODEM);
                }
                if (_twt.state.error != ESP_OK) cancelTwtTrial(Stop::Driver);
                else if (_twt.state.requested == Mode::Baseline) {
                    _twt.state.stage = Stage::Baseline; _twt.state.setupDeadlineUs = 0;
                } else {
                    wifi_itwt_setup_config_t config{};
                    config.setup_cmd = TWT_SUGGEST;
                    config.trigger = 0; config.flow_type = 1; config.flow_id = 0;
                    config.wake_invl_expn = 11; config.wake_invl_mant = 512;
                    config.min_wake_dura = 64; config.wake_duration_unit = 0;
                    config.twt_id = _twt.state.id; config.timeout_time_ms = 5000;
                    _twt.state.stage = Stage::Negotiating;
                    _twt_submitted = true;
                    _twt_negotiation_pending = true;
                    _twt.state.error = esp_wifi_sta_itwt_setup(&config);
                    if (_twt.state.error != ESP_OK) {
                        _twt_negotiation_pending = false;
                        cancelTwtTrial(Stop::Driver);
                    }
                }
            }
        }
    }
    publishTwt();
}
#endif
void NetworkQuota::setLowClockDiagnostic(bool enabled)
{
    if (enabled) _diagnostic_idle_mhz = 80; // legacy on/off means the original 80MHz experiment.
    _diagnostic_low_clock = enabled;
    _diagnostic_override = enabled;
    ESP_LOGW("DisplayClock", "diagnostic_low_clock=%d configured_idle_mhz=%lu", enabled,
             static_cast<unsigned long>(_idle_cpu_mhz.load()));
    if (_task_handle) xTaskNotifyGive(_task_handle);
}
void NetworkQuota::setDiagnosticIdleFrequency(uint32_t mhz)
{
    if (mhz != 80 && mhz != 160 && mhz != CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ) return;
    _diagnostic_idle_mhz = mhz;
    _diagnostic_low_clock = mhz != CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ;
    _diagnostic_override = true; // includes an explicit temporary 320MHz override.
    ESP_LOGW("DisplayClock", "diagnostic_idle_override_mhz=%lu", static_cast<unsigned long>(mhz));
    if (_task_handle) xTaskNotifyGive(_task_handle);
}
bool NetworkQuota::setIdleCpuFrequency(uint32_t mhz)
{
    if (mhz != 160 && mhz != 320) return false; // never persist the suspect 80MHz path.
    nvs_handle_t handle = 0;
    if (nvs_open("display_pw", NVS_READWRITE, &handle) != ESP_OK) return false;
    esp_err_t result = nvs_set_u32(handle, "idle_mhz", mhz);
    if (result == ESP_OK) result = nvs_commit(handle);
    nvs_close(handle);
    if (result != ESP_OK || nvs_open("display_pw", NVS_READONLY, &handle) != ESP_OK) return false;
    uint32_t observed = 0;
    result = nvs_get_u32(handle, "idle_mhz", &observed);
    nvs_close(handle);
    if (result != ESP_OK || observed != mhz) return false;
    _idle_cpu_mhz = mhz;
    _diagnostic_low_clock = false;
    _diagnostic_override = false;
    if (_task_handle) xTaskNotifyGive(_task_handle);
    return true;
}
#endif
void NetworkQuota::recordWifiRunning(bool running)
{
    const uint64_t now = esp_timer_get_time();
    portENTER_CRITICAL(&_power_mux);
    if (!running && _wifi_running) _off_since_us = now;
    if (running && !_wifi_running && _off_since_us) {
        _off_completed_us += now - _off_since_us;
        _off_since_us = 0;
    }
    _wifi_running = running;
    portEXIT_CRITICAL(&_power_mux);
}
IdlePowerStats NetworkQuota::powerStats() const
{
    IdlePowerStats stats;
    stats.locked     = _locked;
    stats.profile    = _power_profile;
    stats.phase      = _power_phase;
    stats.cpuMHz     = esp_clk_cpu_freq() / 1000000;
    stats.cycles     = _power_cycles;
    stats.clockError = _clock_error;
    portENTER_CRITICAL(&_power_mux);
    stats.wifiRunning = _wifi_running;
    stats.offMs       = (_off_completed_us + (_off_since_us ? esp_timer_get_time() - _off_since_us : 0)) / 1000;
    portEXIT_CRITICAL(&_power_mux);
    return stats;
}
void NetworkQuota::setPhase(uint8_t phase)
{
    if (_power_phase.exchange(phase) != phase) {
        const auto stats = powerStats();
        ESP_LOGI("IdlePower", "phase=%u cpu_mhz=%lu wifi_running=%d cycles=%lu", phase,
                 static_cast<unsigned long>(stats.cpuMHz), stats.wifiRunning, static_cast<unsigned long>(stats.cycles));
    }
}

bool NetworkQuota::configure(const char* encoded)
{
    // USB-only provisioning: never print the input or credentials.
    unsigned char decoded[1024]{};
    size_t length = 0;
    if (!encoded || mbedtls_base64_decode(decoded, sizeof(decoded) - 1, &length,
                                          reinterpret_cast<const unsigned char*>(encoded), std::strlen(encoded)) != 0)
        return false;
    cJSON* root = cJSON_ParseWithLength(reinterpret_cast<char*>(decoded), length);
    char ssid[33]{}, password[65]{}, url[257]{}, token[129]{};
    const bool valid = root && copyString(root, "ssid", ssid, sizeof(ssid)) &&
                       copyString(root, "password", password, sizeof(password), true) &&
                       copyString(root, "url", url, sizeof(url)) && copyString(root, "token", token, sizeof(token)) &&
                       (std::strncmp(url, "http://", 7) == 0 || std::strncmp(url, "https://", 8) == 0);
    cJSON_Delete(root);
    std::memset(decoded, 0, sizeof(decoded));
    if (!valid) return false;
    nvs_handle_t handle;
    if (nvs_open("quota_net", NVS_READWRITE, &handle) != ESP_OK) return false;
    bool ok = nvs_set_str(handle, "ssid", ssid) == ESP_OK && nvs_set_str(handle, "password", password) == ESP_OK &&
              nvs_set_str(handle, "url", url) == ESP_OK && nvs_set_str(handle, "token", token) == ESP_OK;
#ifdef MOSAICO_BOARD
    if (ok) {
        auto blob=MosaicoWifi::encode(ssid,password);
        ok=nvs_set_blob(handle,"wifi_ui",blob.data(),blob.size())==ESP_OK;
        MosaicoWifi::scrub(blob.data(),blob.size());
    }
#endif
    if (ok) ok=nvs_commit(handle)==ESP_OK;
    nvs_close(handle);
    std::memset(password, 0, sizeof(password));
    std::memset(token, 0, sizeof(token));
    return ok;
}

void NetworkQuota::begin()
{
    InitTokenHistory();
#ifdef MOSAICO_BOARD
    InitQuotaMonitor();
    // Loading this setting cannot enable an unsupported frequency. Diagnostics
    // are still RAM-only; choosing 160 requires the separate explicit command.
    nvs_handle_t displayHandle = 0;
    uint32_t savedIdle = 320;
    if (nvs_open("display_pw", NVS_READONLY, &displayHandle) == ESP_OK) {
        if (nvs_get_u32(displayHandle, "idle_mhz", &savedIdle) != ESP_OK ||
            (savedIdle != 160 && savedIdle != 320)) savedIdle = 320;
        nvs_close(displayHandle);
    }
    _idle_cpu_mhz = savedIdle;
#endif
    GetTailnetQuota().load();
    nvs_handle_t handle;
    if (nvs_open("quota_net", NVS_READONLY, &handle) != ESP_OK) {
#ifdef MOSAICO_BOARD
        MosaicoDisplay::startFallbackOwner();
#endif
        return;
    }
    size_t a = sizeof(_ssid), b = sizeof(_password), c = sizeof(_url), d = sizeof(_token);
    bool ok = nvs_get_str(handle, "ssid", _ssid, &a) == ESP_OK &&
              nvs_get_str(handle, "password", _password, &b) == ESP_OK &&
              nvs_get_str(handle, "url", _url, &c) == ESP_OK && nvs_get_str(handle, "token", _token, &d) == ESP_OK;
    #ifdef MOSAICO_BOARD
    MosaicoWifi::Blob wifiBlob{}; size_t wifiSize=wifiBlob.size();
    const esp_err_t wifiError=nvs_get_blob(handle,"wifi_ui",wifiBlob.data(),&wifiSize);
    if (wifiError==ESP_OK) {
        // Corruption falls back to the legacy pair; never rewrite automatically.
        if (wifiSize==wifiBlob.size()) MosaicoWifi::decode(wifiBlob,_ssid,_password);
    }
    MosaicoWifi::scrub(wifiBlob.data(),wifiBlob.size());
#endif
    nvs_close(handle);
#ifdef MOSAICO_BOARD
    { std::lock_guard<std::mutex> guard(_wifi_settings_mutex); std::memcpy(_wifi_settings.ssid,_ssid,sizeof(_ssid)); }
#endif
    if (!ok || !_ssid[0] || !_url[0] || !_token[0]) {
#ifdef MOSAICO_BOARD
        MosaicoDisplay::startFallbackOwner();
#endif
        return;
    }
    _configured = true;
    if (xTaskCreate(task, "quota_wifi", 8192, this, 2, &_task_handle) != pdPASS) {
        _configured = false;
        ++_failures;
#ifdef MOSAICO_BOARD
        MosaicoDisplay::startFallbackOwner();
#endif
    }
}
void NetworkQuota::task(void* arg)
{
    static_cast<NetworkQuota*>(arg)->run();
    vTaskDelete(nullptr);
}
void NetworkQuota::run()
{
    setCpu(CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ);
    if (esp_netif_init() != ESP_OK) {
        ++_failures;
#ifdef MOSAICO_BOARD
        MosaicoDisplay::startFallbackOwner();
#endif
        return;
    }
    esp_err_t err = esp_event_loop_create_default();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        ++_failures;
#ifdef MOSAICO_BOARD
        MosaicoDisplay::startFallbackOwner();
#endif
        return;
    }
    if (!esp_netif_create_default_wifi_sta()) {
        ++_failures;
#ifdef MOSAICO_BOARD
        MosaicoDisplay::startFallbackOwner();
#endif
        return;
    }
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
#ifdef MOSAICO_BOARD
    // Credentials/configuration are owned by the application's NVS namespace.
    // WIFI_STORAGE_RAM is set only after init and cannot prevent init-time
    // wifi_nvs_load. Avoid that unused persistent Wi-Fi path entirely.
    init.nvs_enable = 0;
#endif
    // Quota traffic is small and infrequent; reserve less scarce DMA SRAM.
    init.static_tx_buf_num = 4;
    init.cache_tx_buf_num  = 8;
    wifi_config_t config{};
#ifdef MOSAICO_BOARD
    config.sta.sae_pwe_h2e = WPA3_SAE_PWE_BOTH;
    config.sta.pmf_cfg.capable = true;
#endif
    std::memcpy(config.sta.ssid, _ssid, std::strlen(_ssid));
    std::memcpy(config.sta.password, _password, std::strlen(_password));
    if (esp_wifi_init(&init) != ESP_OK || esp_wifi_set_storage(WIFI_STORAGE_RAM) != ESP_OK ||
        esp_wifi_set_mode(WIFI_MODE_STA) != ESP_OK || esp_wifi_set_config(WIFI_IF_STA, &config) != ESP_OK ||
        esp_wifi_start() != ESP_OK) {
        ++_failures;
#ifdef MOSAICO_BOARD
        MosaicoDisplay::startFallbackOwner();
#endif
        return;
    }
#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT
    _twt_handler_ready = esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &NetworkQuota::twtEvent, this) == ESP_OK;
#endif
    recordWifiRunning(true);
    esp_sntp_config_t timeConfig = ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org");
#ifdef MOSAICO_BOARD
    timeConfig.sync_cb = MosaicoClock::onSntpTime;
#endif
    const esp_err_t timeInit = esp_netif_sntp_init(&timeConfig);
    if (timeInit != ESP_OK) ESP_LOGW("SystemClock", "SNTP init failed: %s", esp_err_to_name(timeInit));
    bool hadConnection = false;
    bool wasLocked = false, updateWindow = false;
    int64_t nextRefresh = 0, windowDeadline = 0;
    constexpr int64_t RefreshIntervalUs = 300LL * 1000000;
    constexpr int64_t UpdateWindowUs    = 90LL * 1000000;
#ifdef MOSAICO_BOARD
    const bool ownsDisplaySettings = MosaicoDisplay::claimNetworkOwner();
    { std::lock_guard<std::mutex> guard(_wifi_settings_mutex);
      std::memcpy(_wifi_settings.ssid,_ssid,sizeof(_ssid)); _wifi_settings.available=true; }
    uint32_t refreshIntervalMs=300000;
    int64_t refreshBaseUs=0;
    int64_t nextGaugeCheckUs = 0;

#endif
    while (true) {
#ifdef MOSAICO_BOARD
#if SOC_WIFI_HE_SUPPORT
        serviceTwtTrial(esp_timer_get_time(), idleLocked());
#endif
        if (ownsDisplaySettings) MosaicoDisplay::service();
        serviceWifiSettings();
#if SOC_WIFI_HE_SUPPORT
        if (twtOtaBlocked() && _twt.live()) cancelTwtTrial(MosaicoTwt::Stop::Ota);
        if (_twt.state.cleanupPending || _twt_cleanup_failed) {
            if (MosaicoOta::busy()) MosaicoOta::fail("twt_cleanup_not_ready");
        } else
#endif
            MosaicoOta::processLocalRequests(); // INSTALL/REBOOT do not require network readiness.
        if (MosaicoOta::busy() && MosaicoOta::requestAgeMs() > 120000)
            MosaicoOta::fail("network_ready_timeout");
#endif
        const bool locked = idleLocked();
        const int64_t now = esp_timer_get_time();
        int64_t refreshIntervalUs=RefreshIntervalUs;
        int64_t updateWindowUs=UpdateWindowUs;
#ifdef MOSAICO_BOARD
        MosaicoDisplay::Snapshot display;
        if (MosaicoDisplay::snapshot(display)) {
            const uint32_t interval=MosaicoDisplay::lockIntervalMs(display.config.lockWifiMinutes,5);
            if (interval!=refreshIntervalMs) { refreshIntervalMs=interval; nextRefresh=refreshBaseUs+int64_t(interval)*1000; }
        }
        refreshIntervalUs=int64_t(refreshIntervalMs)*1000;
        updateWindowUs=std::min<int64_t>(UpdateWindowUs, refreshIntervalUs*3/4);
        if (updateWindow) windowDeadline=std::min(windowDeadline,refreshBaseUs+updateWindowUs);
        // Network/OTA can close the lease, never reopen a stale GUI request.
        // Main-loop Sessions service is the sole advertising-enable owner.
        if (!MosaicoSessions::enabled() || MosaicoOta::busy() || MosaicoOta::healthPending())
            GetCodexMicroBle().requestRadioIdle(true);
#else
        GetCodexMicroBle().requestRadioIdle(locked);
#endif
        if (locked && !wasLocked) {
            nextRefresh  = now + refreshIntervalUs;
#ifdef MOSAICO_BOARD
            refreshBaseUs=now;
#endif
            updateWindow = false;
        }
        wasLocked = locked;
        if (locked && !updateWindow && (_force_refresh.exchange(false) || now >= nextRefresh)) {
            updateWindow   = true;
            windowDeadline = now + updateWindowUs;
            nextRefresh    = now + refreshIntervalUs;
#ifdef MOSAICO_BOARD
            refreshBaseUs=now;
#endif
        }
        if (locked && updateWindow && now >= windowDeadline) updateWindow = false;
        if (locked && !updateWindow) {
#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT
            // Hold association/tailnet, but never fetch outside the existing refresh window.
            // Armed state may start/reconnect via the normal owner path, bounded by 10s.
            const bool trialHold = _twt.live();
            if (trialHold && _wifi_running && _twt.state.associated && _connected &&
                (!GetTailnetQuota().enabled() || GetTailnetQuota().ready())) {
                setCpu(_power_profile == 2 ? 80 : CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ);
                setPhase(2);
                wait(static_cast<uint32_t>(std::max<int64_t>(1, (nextRefresh - esp_timer_get_time()) / 1000)));
                continue;
            }
            if (!trialHold) {
#endif
            if (_wifi_running) {
                setPhase(1);
                if (!GetTailnetQuota().pause()) {
                    setPhase(4);
                    wait(1000);
                    continue;
                }
                if (!idleLocked()) continue;
                esp_wifi_disconnect();
                if (esp_wifi_stop() != ESP_OK) {
                    setPhase(4);
                    wait(1000);
                    continue;
                }
                recordWifiRunning(false);
                _connected    = false;
                hadConnection = false;
            }
            if (!idleLocked()) continue;
            const auto ble = GetCodexMicroBle().diagnostics();
            if (GetCodexMicroBle().connected() || ble.advertising) {
                setPhase(1);
                wait(100);
                continue;
            }
            setCpu(_power_profile == 2 ? 80 : CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ);
            setPhase(2);
#ifdef MOSAICO_BOARD
            // Deferred preconditions get read-only checks, at most once/minute,
            // while radios are off. A real attempt is latched by the HAL; failed
            // access/configuration must never become an automatic retry loop.
            if (GetHAL().gaugeBootReloadInfo().status == Hal::GaugeBootReloadStatus::Deferred &&
                esp_timer_get_time() >= nextGaugeCheckUs) {
                char reason[128]{};
                const auto status = GetHAL().gaugeBootReload(reason, sizeof(reason));
                ESP_LOGI("GaugeBoot", "idle status=%u reason=%s", static_cast<unsigned>(status), reason);
                nextGaugeCheckUs = esp_timer_get_time() + 60LL * 1000000;
            }
#endif
            const int64_t remaining = (nextRefresh - esp_timer_get_time()) / 1000;
            uint32_t waitMs = static_cast<uint32_t>(remaining > 0 ? remaining : 1);
#ifdef MOSAICO_BOARD
            if (GetHAL().gaugeBootReloadInfo().status == Hal::GaugeBootReloadStatus::Deferred)
                waitMs = std::min<uint32_t>(waitMs, 60000);
#endif
            wait(waitMs);
            continue;
#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT
            }
#endif
        }
        setCpu(CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ);
        if (!_wifi_running) {
            if (esp_wifi_start() != ESP_OK) {
                setPhase(4);
                wait(1000);
                continue;
            }
            recordWifiRunning(true);
#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT
            hadConnection = false; // Cleanup may have stopped Wi-Fi before the usual pause path.
#endif
        }
        setPhase(locked ? 3 : 0);
        if (!locked) updateWindow = false;
        wifi_ap_record_t ap{};
        esp_netif_ip_info_t ip{};
        esp_netif_t* netif = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
        _connected = esp_wifi_sta_get_ap_info(&ap) == ESP_OK && netif && esp_netif_get_ip_info(netif, &ip) == ESP_OK &&
                     ip.ip.addr != 0;
        if (!_connected) {
            hadConnection = false;
            esp_wifi_connect();
            wait(5000);
            continue;
        }
#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT
        // No blocking HTTP, clock wait or tailnet initialization while the
        // asynchronous setup response/deadline must be serviced.
        if (_twt.state.stage == MosaicoTwt::Stage::Armed || _twt.state.stage == MosaicoTwt::Stage::Negotiating) {
            wait(50); continue;
        }
#endif
        if (GetTailnetQuota().enabled()) {
            // A real clock is required before certificate verification.
            if (std::time(nullptr) < 1735689600) {
                wait(5000);
                continue;
            }
            GetTailnetQuota().start();
            // start() resumes a paused context; rebind is only for live link loss.
            if (!hadConnection && GetTailnetQuota().ready()) GetTailnetQuota().rebind();
        }
        hadConnection = true;
        if (GetTailnetQuota().enabled() && !GetTailnetQuota().ready()) {
            wait(5000);
            continue;
        }
#ifdef MOSAICO_BOARD
        // Reuse an already-online quota window. Do not wake radios just for OTA.
        if (MosaicoOta::takeCheckRequest() || MosaicoOta::automaticCheckDue()) {
#if SOC_WIFI_HE_SUPPORT
            cancelTwtTrial(MosaicoTwt::Stop::Ota);
            if (_twt.state.cleanupPending || _twt_cleanup_failed || !_wifi_running || !_connected) {
                MosaicoOta::finishCheck(false);
                wait(1000); continue;
            }
#endif
            std::unique_ptr<char[]> manifest(new (std::nothrow) char[2048]);
            int manifestSize = 0;
            const bool downloaded = manifest && requestJson("/v1/ota/manifest", manifest.get(), 2048, manifestSize);
            if (downloaded) MosaicoOta::discoverManifest(manifest.get());
            MosaicoOta::finishCheck(downloaded);
        }
        if (MosaicoOta::takeRequest()) {
            updateFirmware();

            continue;
        }
#endif
#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT
        if (locked && !updateWindow && _twt.live()) continue;
        const int64_t twtFetchStart = esp_timer_get_time();
        const bool twtMeasure = _twt.live();
        const uint16_t twtFetchId = _twt.state.id;
#endif
        const bool quotaOk = fetch();
        if (quotaOk)
            ++_accepted;
        else
            ++_failures;
#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT
        // Process queued off/unlock/OTA/expiry between bounded requests, not
        // only after both quota and history transport timeouts have elapsed.
        serviceTwtTrial(esp_timer_get_time(), idleLocked());
        const bool twtHistoryAllowed = _wifi_running && !_twt.state.cleanupPending && !_twt_cleanup_failed &&
            _twt.state.stage != MosaicoTwt::Stage::Armed && _twt.state.stage != MosaicoTwt::Stage::Negotiating &&
            (!twtMeasure || (_twt.live() && _twt.state.id == twtFetchId));
#endif
        bool historyOk = false;
        if ((!GetTailnetQuota().enabled() || GetTailnetQuota().ready())
#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT
            && twtHistoryAllowed
#endif
            ) {
            historyOk = fetchHistory();
            if (historyOk)
                ++_history_accepted;
            else
                ++_history_failures;
        }
#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT
        serviceTwtTrial(esp_timer_get_time(), idleLocked()); // Also before the next wait/idle transition.
        if (twtMeasure && _twt.state.id == twtFetchId) {
            ++_twt.state.fetchAttempts;
            if (quotaOk && historyOk) ++_twt.state.fetchOk;
            _twt.state.fetchUs += static_cast<uint64_t>(esp_timer_get_time() - twtFetchStart);
            publishTwt();
        }
#endif
        if (locked && quotaOk && historyOk) {
            ++_power_cycles;
            updateWindow = false;
            continue;
        }
        wait(locked ? 5000 : 60000);
    }
}
bool NetworkQuota::requestJson(const char* path, char* body, size_t capacity, int& used)
{
    used = 0;
    if (!body || capacity < 2) return false;
    body[0] = 0;
    bool ok = false;
    if (GetTailnetQuota().enabled()) {
        // Fail closed: an enabled tailnet never falls back to LAN cleartext.
        ok = GetTailnetQuota().fetch(_token, body, capacity, used, path);
    } else {
        char requestUrl[288]{};
        std::strcpy(requestUrl, _url);
        if (path) {
            const char* scheme = std::strstr(_url, "://");
            if (!scheme) return false;
            const char* slash   = std::strchr(scheme + 3, '/');
            const size_t origin = slash ? static_cast<size_t>(slash - _url) : std::strlen(_url);
            if (origin + std::strlen(path) >= sizeof(requestUrl)) return false;
            requestUrl[origin] = 0;
            std::strcat(requestUrl, path);
        }
        esp_http_client_config_t config{};
        config.url                      = requestUrl;
        config.timeout_ms               = 7000;
        config.crt_bundle_attach        = esp_crt_bundle_attach;
        config.disable_auto_redirect    = true;
        esp_http_client_handle_t client = esp_http_client_init(&config);
        if (!client) return false;
        char authorization[136] = "Bearer ";
        std::strcat(authorization, _token);
        esp_http_client_set_header(client, "Authorization", authorization);
        esp_http_client_set_header(client, "Accept", "application/json");
        ok = esp_http_client_open(client, 0) == ESP_OK;
        if (ok) {
            esp_http_client_fetch_headers(client);
            ok = esp_http_client_get_status_code(client) == 200;
        }
        while (ok && used < static_cast<int>(capacity) - 1) {
            int count = esp_http_client_read(client, body + used, capacity - 1 - used);
            if (count < 0) {
                ok = false;
                break;
            }
            if (!count) break;
            used += count;
        }
        ok = ok && esp_http_client_is_complete_data_received(client);
        esp_http_client_close(client);
        esp_http_client_cleanup(client);
        std::memset(authorization, 0, sizeof(authorization));
    }
    body[used] = 0;
    return ok;
}
bool NetworkQuota::fetchHistory()
{
    std::unique_ptr<char[]> body(new (std::nothrow) char[32769]);
    int used = 0;
#ifdef MOSAICO_BOARD
    const char* path = "/v2/history";
#else
    const char* path = "/v1/history";
#endif
    return body && requestJson(path, body.get(), 32769, used) && ApplyTokenHistory(body.get(), used);
}
bool NetworkQuota::fetch()
{
#ifdef MOSAICO_BOARD
    std::unique_ptr<char[]> body(new (std::nothrow) char[8193]);
    int used = 0;
    const uint32_t now = static_cast<uint32_t>(esp_timer_get_time() / 1000);
    if (!body || !requestJson("/v2/status", body.get(), 8193, used) ||
        !ApplyQuotaMonitor(body.get(), used, now)) return false;
    // Keep legacy diagnostics populated from the canonical bucket, without
    // collapsing the independently stored monitor's buckets/windows.
    std::unique_ptr<QuotaMonitorSnapshot> snapshot(new (std::nothrow) QuotaMonitorSnapshot());
    if (!snapshot || !CopyQuotaMonitor(*snapshot, now)) return false;
    for (std::size_t i = 0; i < snapshot->bucketCount; ++i) {
        const auto& bucket = snapshot->buckets[i];
        if (std::strcmp(bucket.id, "codex")) continue;
        const QuotaMonitorWindow* selected = nullptr;
        for (const auto& window : bucket.windows)
            if (window.available && (!selected || window.remainingBasisPoints < selected->remainingBasisPoints ||
                (window.remainingBasisPoints == selected->remainingBasisPoints && window.durationMinutes > selected->durationMinutes)))
                selected = &window;
        if (selected) GetHostBridge().applyNetworkUsage(selected->remainingBasisPoints, selected->resetEpoch,
            snapshot->capturedEpoch, static_cast<uint8_t>(std::min<uint16_t>(snapshot->resetCredits, 99)),
            now - snapshot->ageSecondsAtReceipt * 1000U);
        break;
    }
    return true;
#else
    char body[2048]{};
    int used = 0;
    if (!requestJson(nullptr, body, sizeof(body), used) || !JsonNestingValid(body, used)) return false;
    cJSON* root      = cJSON_ParseWithLength(body, used);
    uint32_t version = 0, remaining = 0, reset = 0, captured = 0, credits = 0, age = 0;
    bool ok = root && cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(root, "available")) &&
              number(root, "version", 1, 1, version) && number(root, "remaining_bp", 0, 10000, remaining) &&
              number(root, "reset_epoch", 0, UINT32_MAX, reset) &&
              number(root, "captured_epoch", 1, UINT32_MAX, captured) &&
              number(root, "reset_credits", 0, 99, credits) && number(root, "age_seconds", 0, 120, age);
    cJSON_Delete(root);
    if (!ok) return false;
    return GetHostBridge().applyNetworkUsage(remaining, reset, captured, credits,
                                             static_cast<uint32_t>(esp_timer_get_time() / 1000) - age * 1000);
#endif
}

#ifdef MOSAICO_BOARD
void NetworkQuota::updateFirmware()
{
#if SOC_WIFI_HE_SUPPORT
    cancelTwtTrial(MosaicoTwt::Stop::Ota); // Before any manifest/chunk transfer.
    if (_twt.state.cleanupPending || _twt_cleanup_failed || !_wifi_running || !_connected) {
        MosaicoOta::fail("twt_cleanup_not_ready"); return;
    }
#endif
    struct TraceScope { ~TraceScope() { MosaicoOtaBreadcrumb(0, 0); } } traceScope;
    MosaicoOtaBreadcrumb(1, 0); // allocation and initial heap probe
    // Probe only during OTA, not normal/locked operation or every GUI frame.
    if (!heap_caps_check_integrity_all(true)) { MosaicoOta::fail("heap_before_ota"); return; }
    // Only the network owner runs this path: no parallel quota HTTP, tailnet
    // pause, idle downclock or deferred gauge reload can race the transfer.
    constexpr size_t BodyCapacity = 8192, ChunkCapacity = 4096;
    std::unique_ptr<char[]> body(new (std::nothrow) char[BodyCapacity]);
    // Do not route NOR writes through cache-backed PSRAM. SDK fallback copies
    // external input in 32-byte batches, multiplying cache/bus transitions.
    // One reusable 4KiB internal chunk bounds SRAM use and gives the flash
    // driver a directly readable source even while caches are suspended.
    std::unique_ptr<uint8_t, decltype(&heap_caps_free)> chunk(
        static_cast<uint8_t*>(heap_caps_malloc(ChunkCapacity, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)),
        &heap_caps_free);
    if (!body || !chunk) { MosaicoOta::fail("ota_buffer_allocation"); return; }
    if (!esp_ptr_in_dram(chunk.get()) || !esp_ptr_in_dram(chunk.get() + ChunkCapacity - 1)) {
        MosaicoOta::fail("ota_buffer_not_internal"); return;
    }
    ESP_LOGI("NetworkOTA", "flash source internal=1 capacity=%u", static_cast<unsigned>(ChunkCapacity));
    int used = 0;
    MosaicoOtaBreadcrumb(2, 0); // manifest HTTP/parse
    if (!requestJson("/v1/ota/manifest", body.get(), BodyCapacity, used)) {
        MosaicoOta::fail("manifest_download"); return;
    }
    cJSON* manifest = cJSON_ParseWithLength(body.get(), static_cast<size_t>(used));
    const cJSON* sizeValue = manifest ? cJSON_GetObjectItemCaseSensitive(manifest, "size") : nullptr;
    char imageHash[65]{};
    const bool fieldsOk = manifest && cJSON_IsNumber(sizeValue) &&
        sizeValue->valuedouble > 0 && sizeValue->valuedouble <= 0x3E0000 &&
        std::floor(sizeValue->valuedouble) == sizeValue->valuedouble &&
        copyString(manifest, "sha256", imageHash, sizeof(imageHash));
    const uint32_t size = fieldsOk ? static_cast<uint32_t>(sizeValue->valuedouble) : 0;
    cJSON_Delete(manifest);
    if (!fieldsOk) { MosaicoOta::fail("manifest_metadata"); return; }
    MosaicoOtaBreadcrumb(3, 0); // validation + inactive-slot erase
    if (!MosaicoOta::beginManifest(body.get())) return;
    const int64_t deadline = esp_timer_get_time() + 600LL * 1000000;
    for (uint32_t offset = 0; offset < size;) {
        MosaicoOtaBreadcrumb(4, offset); // probe before allocating HTTP/JSON
        if ((offset % 65536U) == 0 && !heap_caps_check_integrity_all(true)) {
            MosaicoOta::fail("heap_during_ota"); return;
        }
        if (esp_timer_get_time() >= deadline) { MosaicoOta::fail("download_timeout"); return; }
        char path[160]{};
        const int n = std::snprintf(path, sizeof(path), "/v1/ota/chunk?sha256=%s&offset=%lu",
                                  imageHash, static_cast<unsigned long>(offset));
        if (n <= 0 || n >= static_cast<int>(sizeof(path))) {
            MosaicoOta::fail("chunk_path"); return;
        }
        bool downloaded = false;
        MosaicoOtaBreadcrumb(5, offset); // chunk HTTP
        // A failed HTTP read never advances offset or writes bytes. Retrying
        // this hash-bound chunk cannot mix releases or duplicate flash writes.
        for (unsigned attempt = 0; attempt < 3 && esp_timer_get_time() < deadline; ++attempt) {
            if (requestJson(path, body.get(), BodyCapacity, used)) { downloaded = true; break; }
            if (attempt < 2) vTaskDelay(pdMS_TO_TICKS(300U << attempt));
        }
        if (!downloaded) { MosaicoOta::fail("chunk_download"); return; }
        MosaicoOtaBreadcrumb(6, offset); // JSON allocation / decode / free
        cJSON* root = cJSON_ParseWithLength(body.get(), static_cast<size_t>(used));
        const cJSON* position = root ? cJSON_GetObjectItemCaseSensitive(root, "offset") : nullptr;
        const cJSON* data = root ? cJSON_GetObjectItemCaseSensitive(root, "data") : nullptr;
        size_t count = 0;
        const bool ok = cJSON_IsNumber(position) && position->valuedouble == offset &&
            cJSON_IsString(data) && data->valuestring &&
            mbedtls_base64_decode(chunk.get(), ChunkCapacity, &count,
                reinterpret_cast<const unsigned char*>(data->valuestring),
                std::strlen(data->valuestring)) == 0 &&
            count == std::min<uint32_t>(ChunkCapacity, size - offset);
        cJSON_Delete(root);
        if (!ok) { MosaicoOta::fail("chunk_format"); return; }
        MosaicoOtaBreadcrumb(7, offset); // SDK write + incremental hash
        if (!MosaicoOta::writeChunk(offset, chunk.get(), count)) return;
        offset += static_cast<uint32_t>(count);
        vTaskDelay(pdMS_TO_TICKS(1));
    }
    MosaicoOtaBreadcrumb(8, size); // final hash/SDK verification
    if (!heap_caps_check_integrity_all(true)) { MosaicoOta::fail("heap_after_download"); return; }
    if (!MosaicoOta::finishDownload()) return;
    MosaicoOta::processLocalRequests(); // Full pipeline only when explicitly enabled/bypassed.

}
#endif
#ifdef MOSAICO_BOARD
void NetworkQuota::wakeForFirmwareUpdate()
{
    setCpu(CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ);
    if (_task_handle) xTaskNotifyGive(_task_handle);
}
#endif
