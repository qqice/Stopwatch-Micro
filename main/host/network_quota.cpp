#include "network_quota.h"
#include "host_bridge.h"
#include "tailscale_transport.h"
#include "token_history.h"
#include <memory>
#include <new>
#include <cJSON.h>
#include <cmath>
#include <cstring>
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
#include <freertos/task.h>
#include <mbedtls/base64.h>
#include <nvs.h>

namespace {
NetworkQuota instance;
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
    if (_locked.exchange(locked) != locked && _task_handle) xTaskNotifyGive(_task_handle);
    GetCodexMicroBle().requestRadioIdle(idleLocked());
}
void NetworkQuota::setPowerProfile(uint8_t profile)
{
    if (profile > 2) return;
    _power_profile = profile;
    GetCodexMicroBle().requestRadioIdle(idleLocked());
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
    ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(milliseconds));
}
void NetworkQuota::setCpu(uint32_t mhz)
{
    if (_cpu_target == mhz) return;
    esp_pm_config_t config{};
    config.max_freq_mhz       = mhz;
    config.min_freq_mhz       = mhz;
    config.light_sleep_enable = false;
    const esp_err_t result    = esp_pm_configure(&config);
    _clock_error              = result;
    if (result == ESP_OK) _cpu_target = mhz;
}
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
              nvs_set_str(handle, "url", url) == ESP_OK && nvs_set_str(handle, "token", token) == ESP_OK &&
              nvs_commit(handle) == ESP_OK;
    nvs_close(handle);
    std::memset(password, 0, sizeof(password));
    std::memset(token, 0, sizeof(token));
    return ok;
}

void NetworkQuota::begin()
{
    InitTokenHistory();
    GetTailnetQuota().load();
    nvs_handle_t handle;
    if (nvs_open("quota_net", NVS_READONLY, &handle) != ESP_OK) return;
    size_t a = sizeof(_ssid), b = sizeof(_password), c = sizeof(_url), d = sizeof(_token);
    bool ok = nvs_get_str(handle, "ssid", _ssid, &a) == ESP_OK &&
              nvs_get_str(handle, "password", _password, &b) == ESP_OK &&
              nvs_get_str(handle, "url", _url, &c) == ESP_OK && nvs_get_str(handle, "token", _token, &d) == ESP_OK;
    nvs_close(handle);
    if (!ok || !_ssid[0] || !_url[0] || !_token[0]) return;
    _configured = true;
    if (xTaskCreate(task, "quota_wifi", 8192, this, 2, &_task_handle) != pdPASS) {
        _configured = false;
        ++_failures;
    }
}
void NetworkQuota::task(void* arg)
{
    static_cast<NetworkQuota*>(arg)->run();
    vTaskDelete(nullptr);
}
void NetworkQuota::run()
{
    setCpu(240);
    if (esp_netif_init() != ESP_OK) {
        ++_failures;
        return;
    }
    esp_err_t err = esp_event_loop_create_default();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        ++_failures;
        return;
    }
    if (!esp_netif_create_default_wifi_sta()) {
        ++_failures;
        return;
    }
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    // Quota traffic is small and infrequent; reserve less scarce DMA SRAM.
    init.static_tx_buf_num = 4;
    init.cache_tx_buf_num  = 8;
    wifi_config_t config{};
    std::memcpy(config.sta.ssid, _ssid, std::strlen(_ssid));
    std::memcpy(config.sta.password, _password, std::strlen(_password));
    if (esp_wifi_init(&init) != ESP_OK || esp_wifi_set_storage(WIFI_STORAGE_RAM) != ESP_OK ||
        esp_wifi_set_mode(WIFI_MODE_STA) != ESP_OK || esp_wifi_set_config(WIFI_IF_STA, &config) != ESP_OK ||
        esp_wifi_start() != ESP_OK) {
        ++_failures;
        return;
    }
    recordWifiRunning(true);
    esp_sntp_config_t timeConfig = ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org");
    esp_netif_sntp_init(&timeConfig);
    bool hadConnection = false;
    bool wasLocked = false, updateWindow = false;
    int64_t nextRefresh = 0, windowDeadline = 0;
    constexpr int64_t RefreshIntervalUs = 300LL * 1000000;
    constexpr int64_t UpdateWindowUs    = 90LL * 1000000;
    while (true) {
        const bool locked = idleLocked();
        const int64_t now = esp_timer_get_time();
        GetCodexMicroBle().requestRadioIdle(locked);
        if (locked && !wasLocked) {
            nextRefresh  = now + RefreshIntervalUs;
            updateWindow = false;
        }
        wasLocked = locked;
        if (locked && (_force_refresh.exchange(false) || now >= nextRefresh)) {
            updateWindow   = true;
            windowDeadline = now + UpdateWindowUs;
            nextRefresh    = now + RefreshIntervalUs;
        }
        if (locked && updateWindow && now >= windowDeadline) updateWindow = false;
        if (locked && !updateWindow) {
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
            setCpu(_power_profile == 2 ? 80 : 240);
            setPhase(2);
            const int64_t remaining = (nextRefresh - esp_timer_get_time()) / 1000;
            wait(static_cast<uint32_t>(remaining > 0 ? remaining : 1));
            continue;
        }
        setCpu(240);
        if (!_wifi_running) {
            if (esp_wifi_start() != ESP_OK) {
                setPhase(4);
                wait(1000);
                continue;
            }
            recordWifiRunning(true);
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
        const bool quotaOk = fetch();
        if (quotaOk)
            ++_accepted;
        else
            ++_failures;
        if (!GetTailnetQuota().enabled() || GetTailnetQuota().ready()) {
            if (fetchHistory())
                ++_history_accepted;
            else
                ++_history_failures;
        }
        if (locked && quotaOk) {
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
    return body && requestJson("/v1/history", body.get(), 32769, used) && ApplyTokenHistory(body.get(), used);
}
bool NetworkQuota::fetch()
{
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
}
