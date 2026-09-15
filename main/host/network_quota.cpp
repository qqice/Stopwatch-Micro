#include "network_quota.h"
#include "host_bridge.h"
#include <cJSON.h>
#include <cmath>
#include <cstring>
#include <esp_crt_bundle.h>
#include <esp_event.h>
#include <esp_http_client.h>
#include <esp_netif.h>
#include <esp_timer.h>
#include <esp_wifi.h>
#include <freertos/task.h>
#include <mbedtls/base64.h>
#include <nvs.h>

namespace {
NetworkQuota instance;
bool copyString(cJSON* root, const char* key, char* out, size_t capacity, bool empty = false) {
    const cJSON* value = cJSON_GetObjectItemCaseSensitive(root, key);
    if (!cJSON_IsString(value) || !value->valuestring) return false;
    const size_t length = std::strlen(value->valuestring);
    if (length >= capacity || (!empty && length == 0)) return false;
    for (size_t i = 0; i < length; ++i) if (static_cast<unsigned char>(value->valuestring[i]) < 32) return false;
    std::memcpy(out, value->valuestring, length + 1);
    return true;
}
bool number(cJSON* root, const char* key, double low, double high, uint32_t& out) {
    const cJSON* value = cJSON_GetObjectItemCaseSensitive(root, key);
    if (!cJSON_IsNumber(value) || !std::isfinite(value->valuedouble) || value->valuedouble < low ||
        value->valuedouble > high || std::floor(value->valuedouble) != value->valuedouble) return false;
    out = static_cast<uint32_t>(value->valuedouble);
    return true;
}
}
NetworkQuota& GetNetworkQuota() { return instance; }

bool NetworkQuota::configure(const char* encoded) {
    // USB-only provisioning: never print the input or credentials.
    unsigned char decoded[1024]{};
    size_t length = 0;
    if (!encoded || mbedtls_base64_decode(decoded, sizeof(decoded) - 1, &length,
        reinterpret_cast<const unsigned char*>(encoded), std::strlen(encoded)) != 0) return false;
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
        nvs_set_str(handle, "url", url) == ESP_OK && nvs_set_str(handle, "token", token) == ESP_OK && nvs_commit(handle) == ESP_OK;
    nvs_close(handle);
    std::memset(password, 0, sizeof(password)); std::memset(token, 0, sizeof(token));
    return ok;
}

void NetworkQuota::begin() {
    nvs_handle_t handle;
    if (nvs_open("quota_net", NVS_READONLY, &handle) != ESP_OK) return;
    size_t a=sizeof(_ssid), b=sizeof(_password), c=sizeof(_url), d=sizeof(_token);
    bool ok = nvs_get_str(handle,"ssid",_ssid,&a)==ESP_OK && nvs_get_str(handle,"password",_password,&b)==ESP_OK &&
        nvs_get_str(handle,"url",_url,&c)==ESP_OK && nvs_get_str(handle,"token",_token,&d)==ESP_OK;
    nvs_close(handle);
    if (!ok || !_ssid[0] || !_url[0] || !_token[0]) return;
    _configured = true;
    if (xTaskCreate(task, "quota_wifi", 8192, this, 2, nullptr) != pdPASS) { _configured=false; ++_failures; }
}
void NetworkQuota::task(void* arg) { static_cast<NetworkQuota*>(arg)->run(); vTaskDelete(nullptr); }
void NetworkQuota::run() {
    if (esp_netif_init()!=ESP_OK) { ++_failures; return; }
    esp_err_t err=esp_event_loop_create_default();
    if (err!=ESP_OK && err!=ESP_ERR_INVALID_STATE) { ++_failures; return; }
    if (!esp_netif_create_default_wifi_sta()) { ++_failures; return; }
    wifi_init_config_t init=WIFI_INIT_CONFIG_DEFAULT();
    // Quota traffic is small and infrequent; reserve less scarce DMA SRAM.
    init.static_tx_buf_num=4;
    init.cache_tx_buf_num=8;
    wifi_config_t config{};
    std::memcpy(config.sta.ssid,_ssid,std::strlen(_ssid));
    std::memcpy(config.sta.password,_password,std::strlen(_password));
    if (esp_wifi_init(&init)!=ESP_OK || esp_wifi_set_storage(WIFI_STORAGE_RAM)!=ESP_OK ||
        esp_wifi_set_mode(WIFI_MODE_STA)!=ESP_OK || esp_wifi_set_config(WIFI_IF_STA,&config)!=ESP_OK ||
        esp_wifi_start()!=ESP_OK) { ++_failures; return; }
    while (true) {
        wifi_ap_record_t ap{};
        esp_netif_ip_info_t ip{};
        esp_netif_t* netif=esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
        _connected=esp_wifi_sta_get_ap_info(&ap)==ESP_OK && netif &&
            esp_netif_get_ip_info(netif,&ip)==ESP_OK && ip.ip.addr!=0;
        if (!_connected) { esp_wifi_connect(); vTaskDelay(pdMS_TO_TICKS(10000)); continue; }
        if (fetch()) ++_accepted; else ++_failures;
        vTaskDelay(pdMS_TO_TICKS(60000));
    }
}
bool NetworkQuota::fetch() {
    esp_http_client_config_t config{};
    config.url=_url; config.timeout_ms=7000; config.crt_bundle_attach=esp_crt_bundle_attach;
    config.disable_auto_redirect=true;
    esp_http_client_handle_t client=esp_http_client_init(&config);
    if (!client) return false;
    char authorization[136]="Bearer "; std::strcat(authorization,_token);
    esp_http_client_set_header(client,"Authorization",authorization);
    esp_http_client_set_header(client,"Accept","application/json");
    char body[2048]{}; int used=0;
    bool ok=esp_http_client_open(client,0)==ESP_OK;
    if (ok) { esp_http_client_fetch_headers(client); ok=esp_http_client_get_status_code(client)==200; }
    while (ok && used < static_cast<int>(sizeof(body))-1) {
        int count=esp_http_client_read(client,body+used,sizeof(body)-1-used);
        if (count<0) { ok=false; break; }
        if (!count) break;
        used+=count;
    }
    ok=ok && esp_http_client_is_complete_data_received(client);
    esp_http_client_close(client); esp_http_client_cleanup(client);
    std::memset(authorization,0,sizeof(authorization));
    if (!ok) return false;
    cJSON* root=cJSON_ParseWithLength(body,used);
    uint32_t version=0, remaining=0, reset=0, captured=0, credits=0, age=0;
    ok=root && cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(root,"available")) &&
        number(root,"version",1,1,version) && number(root,"remaining_bp",0,10000,remaining) &&
        number(root,"reset_epoch",0,UINT32_MAX,reset) && number(root,"captured_epoch",1,UINT32_MAX,captured) &&
        number(root,"reset_credits",0,99,credits) && number(root,"age_seconds",0,120,age);
    cJSON_Delete(root);
    if (!ok) return false;
    return GetHostBridge().applyNetworkUsage(remaining,reset,captured,credits,
        static_cast<uint32_t>(esp_timer_get_time()/1000)-age*1000);
}
