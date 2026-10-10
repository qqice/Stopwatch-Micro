#include "tailscale_transport.h"
#include <microlink.h>
#include <cJSON.h>
#include <mbedtls/base64.h>
#include <nvs.h>
#include <esp_heap_caps.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <strings.h>
#include <cctype>
#include <initializer_list>
extern "C" esp_err_t ml_noise_selftest(void);

namespace {
TailnetQuota instance;
portMUX_TYPE fetchDiagnosticsMux = portMUX_INITIALIZER_UNLOCKED;
bool parseUrl(const char* url, char* host, uint16_t& port, uint32_t& peer, char* path)
{
    unsigned a, b, c, d, p;
    int end = 0;
    if (!url || std::sscanf(url, "http://%u.%u.%u.%u:%u%191s%n", &a, &b, &c, &d, &p, path, &end) != 6 || url[end] ||
        a != 100 || b < 64 || b > 127 || c > 255 || d > 255 || p == 0 || p > 65535 || path[0] != '/')
        return false;
    for (const char* ch = path; *ch; ++ch)
        if (static_cast<unsigned char>(*ch) <= 32) return false;
    std::snprintf(host, 16, "%u.%u.%u.%u", a, b, c, d);
    port = p;
    peer = (a << 24) | (b << 16) | (c << 8) | d;
    return true;
}
}  // namespace
TailnetQuota& GetTailnetQuota()
{
    return instance;
}
wireguardif_rx_stats_t TailnetQuota::rxDiagnostics() const
{
    return wireguardif_rx_stats();
}
bool TailnetQuota::setDerpTxRetryBudgetMs(uint32_t milliseconds)
{
    return microlink_set_derp_tx_retry_budget_ms(_client.load(), milliseconds) == ESP_OK;
}
uint32_t TailnetQuota::derpTxRetryBudgetMs() const
{
    return microlink_get_derp_tx_retry_budget_ms(_client.load());
}
bool TailnetQuota::configure(const char* encoded)
{
    unsigned char decoded[768]{};
    size_t length = 0;
    if (!encoded || mbedtls_base64_decode(decoded, sizeof(decoded) - 1, &length,
                                          reinterpret_cast<const unsigned char*>(encoded), std::strlen(encoded)) != 0)
        return false;
    cJSON* root     = cJSON_ParseWithLength(reinterpret_cast<char*>(decoded), length);
    const auto* key = cJSON_GetObjectItemCaseSensitive(root, "auth_key");
    const auto* url = cJSON_GetObjectItemCaseSensitive(root, "url");
    char host[16]{}, path[192]{};
    uint16_t port = 0;
    uint32_t peer = 0;
    bool ok       = cJSON_IsString(key) && cJSON_IsString(url) && std::strlen(key->valuestring) < 257 &&
                    std::strncmp(key->valuestring, "tskey-auth-", 11) == 0 &&
                    parseUrl(url->valuestring, host, port, peer, path);
    if (ok)
        for (const unsigned char* ch = reinterpret_cast<const unsigned char*>(key->valuestring); *ch; ++ch)
            if (!std::isalnum(*ch) && *ch != '-' && *ch != '_') {
                ok = false;
                break;
            }
    if (ok) {
        nvs_handle_t handle;
        ok = nvs_open("quota_tail", NVS_READWRITE, &handle) == ESP_OK;
        if (ok) {
            ok = nvs_set_str(handle, "key", key->valuestring) == ESP_OK &&
                 nvs_set_str(handle, "url", url->valuestring) == ESP_OK && nvs_set_u8(handle, "mode", 1) == ESP_OK &&
                 nvs_commit(handle) == ESP_OK;
            nvs_close(handle);
        }
    }
    cJSON_Delete(root);
    std::memset(decoded, 0, sizeof(decoded));
    return ok;
}
void TailnetQuota::load()
{
    nvs_handle_t handle;
    const esp_err_t opened = nvs_open("quota_tail", NVS_READONLY, &handle);
    // Only a never-configured namespace permits LAN mode. A broken/partial
    // tailnet configuration fails closed instead of sending the token on LAN.
    if (opened == ESP_ERR_NVS_NOT_FOUND) return;
    _enabled = true;
    if (opened != ESP_OK) return;
    char url[256]{};
    size_t a = sizeof(_key), b = sizeof(url);
    bool ok = nvs_get_str(handle, "key", _key, &a) == ESP_OK && nvs_get_str(handle, "url", url, &b) == ESP_OK;
    nvs_close(handle);
    _valid = ok && parseUrl(url, _host, _port, _peer, _path) && std::strncmp(_key, "tskey-auth-", 11) == 0;
}
void TailnetQuota::start()
{
    if (!_enabled || !_valid) return;
    if (_client) {
        if (_pause_requested) {
            if (microlink_stop(_client) != ESP_OK) return;
            if (microlink_start(_client) == ESP_OK) _pause_requested = false;
        }
        return;
    }
    // Keep room for MicroLink's task stacks and the existing BLE/UI workers.
    if (heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT) < 80 * 1024 ||
        heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT) < 16 * 1024)
        return;
    if (ml_noise_selftest() != ESP_OK) return;
    // Avoid per-packet USB logging while the display is idle; errors remain visible.
    for (const char* tag : {"ml_coord", "ml_derp", "ml_net_io", "ml_wg_mgr", "ml_stun", "ml_tcp", "ml_noise"}) {
        esp_log_level_set(tag, ESP_LOG_ERROR);
    }
    microlink_config_t config{};
    config.auth_key         = _key;
#ifdef MOSAICO_BOARD
    config.device_name      = "mosaico-micro";
#else
    config.device_name      = "stopwatch-micro";
#endif
    config.enable_derp      = true;
    config.enable_stun      = true;
    config.enable_disco     = true;
    config.max_peers        = 4;
    config.priority_peer_ip = _peer;
    _client                 = microlink_init(&config);
    if (_client) _pause_requested = microlink_start(_client) != ESP_OK;
}
bool TailnetQuota::pause()
{
    _pause_requested = true;
    return !_client || microlink_stop(_client) == ESP_OK;
}
void TailnetQuota::rebind()
{
    if (_client && !_pause_requested) microlink_rebind(_client);
}
int TailnetQuota::state() const
{
    return _client ? microlink_get_state(_client) : (_enabled && !_valid ? -3 : -1);
}
bool TailnetQuota::ready() const
{
    return !_pause_requested && _client && microlink_is_connected(_client);
}
uint32_t TailnetQuota::ip() const
{
    return _client ? microlink_get_vpn_ip(_client) : 0;
}
TailnetQuota::FetchDiagnostics TailnetQuota::fetchDiagnostics(FetchPath path) const
{
    const unsigned index = static_cast<unsigned>(path);
    if (index >= 4) return {};
    portENTER_CRITICAL(&fetchDiagnosticsMux);
    const auto value = _fetch_diagnostics[index];
    portEXIT_CRITICAL(&fetchDiagnosticsMux);
    return value;
}
void TailnetQuota::publishFetchDiagnostics(FetchPath path, const FetchDiagnostics& value)
{
    portENTER_CRITICAL(&fetchDiagnosticsMux);
    _fetch_diagnostics[static_cast<unsigned>(path)] = value;
    portEXIT_CRITICAL(&fetchDiagnosticsMux);
}
bool TailnetQuota::fetch(const char* token, char* body, size_t capacity, int& length, const char* pathOverride)
{
    const int64_t started = esp_timer_get_time();
    const char* path = pathOverride ? pathOverride : _path;
    const FetchPath kind = !std::strcmp(path, "/v1/history") || !std::strcmp(path, "/v2/history") ? FetchPath::History :
        !std::strncmp(path, "/v1/ota/", 8) ? FetchPath::Ota :
        !pathOverride || !std::strcmp(path, "/v2/status") ? FetchPath::Quota : FetchPath::Other;
    FetchDiagnostics diagnostic{};
    auto finish = [&](FetchStage stage, bool result) {
        diagnostic.stage = stage;
        diagnostic.result = result;
        const int64_t ms = (esp_timer_get_time() - started) / 1000;
        diagnostic.elapsedMs = ms <= 0 ? 0 : ms > UINT32_MAX ? UINT32_MAX : static_cast<uint32_t>(ms);
        publishFetchDiagnostics(kind, diagnostic);
        return result;
    };
    length = 0;
    if (_pause_requested || !_client || !microlink_is_connected(_client)) return finish(FetchStage::NotReady, false);
    if (!body || capacity < 2) return finish(FetchStage::Input, false);
    auto* socket = microlink_tcp_connect(_client, _peer, _port, 7000);
    if (!socket) return finish(FetchStage::Connect, false);
    char request[640]{};
    const int requestLength =
        std::snprintf(request, sizeof(request),
                      "GET %s HTTP/1.1\r\nHost: %s:%u\r\nAuthorization: Bearer %s\r\nConnection: close\r\n\r\n",
                      pathOverride ? pathOverride : _path, _host, _port, token);
    bool ok = requestLength > 0 && requestLength < static_cast<int>(sizeof(request)) &&
              microlink_tcp_send(socket, request, requestLength) == ESP_OK;
    std::memset(request, 0, sizeof(request));
    char* response         = static_cast<char*>(heap_caps_calloc(1, capacity + 2048, MALLOC_CAP_SPIRAM));
    FetchStage failure = !ok ? FetchStage::Send : !response ? FetchStage::Allocation : FetchStage::None;
    size_t used            = 0;
    char* payload          = nullptr;
    size_t contentLength   = 0;
    const int64_t deadline = esp_timer_get_time() + 10000000;
    while (ok && response && used < capacity + 2047 && esp_timer_get_time() < deadline) {
        const int count = microlink_tcp_recv(socket, response + used, capacity + 2047 - used, 2000);
        if (count < 0) { failure = FetchStage::Receive; break; }
        if (!count) continue;
        used += count;
        diagnostic.received = static_cast<uint32_t>(used);
        response[used] = 0;
        if (!payload) {
            if (std::strstr(response, "\r\n")) std::sscanf(response, "HTTP/%*u.%*u %3d", &diagnostic.httpStatus);
            char* split = std::strstr(response, "\r\n\r\n");
            if (!split) {
                if (used >= 2048) {
                    failure = FetchStage::Header;
                    ok = false;
                    break;
                }
                continue;
            }
            payload = split + 4;
            *split  = 0;
            const char* header = strcasestr(response, "\r\nContent-Length:");
            if (header) {
                const unsigned long n = std::strtoul(header + 17, nullptr, 10);
                diagnostic.contentLength = n > UINT32_MAX ? UINT32_MAX : static_cast<uint32_t>(n);
            }
            if (std::strncmp(response, "HTTP/1.1 200 ", 13) != 0 && std::strncmp(response, "HTTP/1.0 200 ", 13) != 0) {
                failure = FetchStage::HttpStatus;
                ok = false;
                break;
            }
            if (!header || strcasestr(response, "\r\nTransfer-Encoding:")) {
                failure = FetchStage::Header;
                ok = false;
                break;
            }
            char* end       = nullptr;
            unsigned long n = std::strtoul(header + 17, &end, 10);
            if (!end || (*end != '\r' && *end != 0) || !n || n >= capacity) {
                failure = end && (*end == '\r' || *end == 0) && n >= capacity ? FetchStage::Oversize : FetchStage::Header;
                ok = false;
                break;
            }
            contentLength = n;
        }
        if (payload && used - static_cast<size_t>(payload - response) >= contentLength) break;
    }
    ok = ok && response && payload && contentLength > 0 &&
         used - static_cast<size_t>(payload - response) >= contentLength;
    if (ok) {
        std::memcpy(body, payload, contentLength);
        body[contentLength] = 0;
        length              = contentLength;
    }
    free(response);
    microlink_tcp_close(socket);
    if (!ok && failure == FetchStage::None)
        failure = used >= capacity + 2047 ? FetchStage::Oversize :
            esp_timer_get_time() >= deadline ? FetchStage::Deadline : FetchStage::Incomplete;
    return finish(ok ? FetchStage::Success : failure, ok);
}
