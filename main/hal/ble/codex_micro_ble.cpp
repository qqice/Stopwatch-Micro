/*
 * SPDX-License-Identifier: MIT
 *
 * Codex Micro compatibility protocol adapted from imliubo/codex-micro-4-core2.
 * Copyright (c) 2026 imliubo.
 */
#include "codex_micro_ble.h"

#include <algorithm>
#include <array>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <new>

#include <cJSON.h>
#include <esp_bt.h>
#include <esp_bt_main.h>
#include <esp_gatts_api.h>
#include <esp_hid_common.h>
#include <esp_hidd_gatts.h>
#include <esp_log.h>
#include <esp_system.h>
#include <esp_timer.h>
#include <freertos/task.h>
#include <system_config.h>

extern "C" void codex_micro_hid_gatt_compat_link_anchor();
extern "C" void codex_micro_hid_gatt_compat_snapshot_bonds();
extern "C" void codex_micro_hid_gatt_compat_gatts_event(esp_gatts_cb_event_t event, esp_gatt_if_t gatts_if,
                                                        esp_ble_gatts_cb_param_t* param);
extern "C" void codex_micro_hid_gatt_compat_authenticated(const uint8_t* address);

namespace {

constexpr const char* Tag               = "CodexMicro-BLE";
constexpr const char* DeviceName        = system_config::ProductName;
constexpr const char* Manufacturer      = "Work Louder";
constexpr const char* FirmwareVersion   = system_config::FirmwareVersion;
constexpr uint16_t VendorId             = 0x303A;
constexpr uint16_t ProductId            = 0x8360;
constexpr uint16_t ProductVersion       = 0x0101;
constexpr BaseType_t InputTaskCore      = 0;
constexpr UBaseType_t InputTaskPriority = 1;

void updateAtomicMax(std::atomic_uint32_t& destination, uint32_t value)
{
    uint32_t previous = destination.load(std::memory_order_relaxed);
    while (previous < value && !destination.compare_exchange_weak(previous, value, std::memory_order_relaxed)) {
    }
}

const uint8_t ReportMap[] = {
    0x06, 0x00, 0xFF,  // Usage Page (Vendor Defined 0xFF00)
    0x09, 0x01,        // Usage (1)
    0xA1, 0x01,        // Collection (Application)
    0x85, 0x06,        // Report ID (6)
    0x15, 0x00,        // Logical Minimum (0)
    0x26, 0xFF, 0x00,  // Logical Maximum (255)
    0x75, 0x08,        // Report Size (8)
    0x95, 0x3F,        // Report Count (63)
    0x09, 0x01,        // Usage (1)
    0x81, 0x02,        // Input (Data, Variable, Absolute)
    0x95, 0x3F,        // Report Count (63)
    0x09, 0x02,        // Usage (2)
    0x91, 0x02,        // Output (Data, Variable, Absolute)
    0xC0               // End Collection
};

esp_hid_raw_report_map_t ReportMaps[] = {
    {
        .data = ReportMap,
        .len  = sizeof(ReportMap),
    },
};

esp_hid_device_config_t HidConfig = {
    .vendor_id         = VendorId,
    .product_id        = ProductId,
    .version           = ProductVersion,
    .device_name       = DeviceName,
    .manufacturer_name = Manufacturer,
    .serial_number     = nullptr,
    .report_maps       = ReportMaps,
    .report_maps_len   = 1,
};

const uint8_t HidServiceUuid128[] = {
    0xfb, 0x34, 0x9b, 0x5f, 0x80, 0x00, 0x00, 0x80, 0x00, 0x10, 0x00, 0x00, 0x12, 0x18, 0x00, 0x00,
};

esp_ble_adv_params_t AdvertisingParams = {
    .adv_int_min       = 0x20,
    .adv_int_max       = 0x30,
    .adv_type          = ADV_TYPE_IND,
    .own_addr_type     = BLE_ADDR_TYPE_PUBLIC,
    .peer_addr         = {},
    .peer_addr_type    = BLE_ADDR_TYPE_PUBLIC,
    .channel_map       = ADV_CHNL_ALL,
    .adv_filter_policy = ADV_FILTER_ALLOW_SCAN_ANY_CON_ANY,
};

bool logStepError(const char* step, esp_err_t error)
{
    if (error == ESP_OK) {
        return false;
    }
    ESP_LOGE(Tag, "%s failed: %s", step, esp_err_to_name(error));
    return true;
}

const cJSON* objectItem(const cJSON* object, const char* name)
{
    return object == nullptr ? nullptr : cJSON_GetObjectItemCaseSensitive(object, name);
}

bool sameLight(const CodexMicroLight& lhs, const CodexMicroLight& rhs)
{
    return lhs.color == rhs.color && lhs.brightness == rhs.brightness && lhs.effect == rhs.effect &&
           lhs.speed == rhs.speed && lhs.magic == rhs.magic;
}

CodexMicroLightEffect parseLightEffect(const cJSON* value, CodexMicroLightEffect fallback)
{
    if (cJSON_IsNumber(value)) {
        const int effect = value->valueint;
        if (effect >= static_cast<int>(CodexMicroLightEffect::Off) &&
            effect <= static_cast<int>(CodexMicroLightEffect::ShallowBreath)) {
            return static_cast<CodexMicroLightEffect>(effect);
        }
        return fallback;
    }
    if (!cJSON_IsString(value) || value->valuestring == nullptr) {
        return fallback;
    }

    struct EffectName {
        const char* name;
        CodexMicroLightEffect effect;
    };
    constexpr EffectName names[] = {
        {"off", CodexMicroLightEffect::Off},
        {"solid", CodexMicroLightEffect::Solid},
        {"snake", CodexMicroLightEffect::Snake},
        {"rainbow", CodexMicroLightEffect::Rainbow},
        {"breath", CodexMicroLightEffect::Breath},
        {"gradient", CodexMicroLightEffect::Gradient},
        {"shallowBreath", CodexMicroLightEffect::ShallowBreath},
    };
    for (const EffectName& candidate : names) {
        if (std::strcmp(value->valuestring, candidate.name) == 0) {
            return candidate.effect;
        }
    }
    return fallback;
}

bool jsonFlag(const cJSON* value)
{
    return cJSON_IsTrue(value) || (cJSON_IsNumber(value) && value->valueint != 0);
}

enum class RpcObjectState : uint8_t {
    Incomplete,
    Complete,
    Invalid,
};

RpcObjectState rpcObjectState(const std::string& json)
{
    constexpr std::size_t MaxNestingDepth     = 32;
    std::array<char, MaxNestingDepth> closers = {};
    bool started                              = false;
    bool in_string                            = false;
    bool escaped                              = false;
    std::size_t depth                         = 0;
    for (std::size_t index = 0; index < json.size(); ++index) {
        const char character = json[index];
        if (!started) {
            if (character == ' ' || character == '\t' || character == '\r' || character == '\n') {
                continue;
            }
            if (character != '{') {
                return RpcObjectState::Invalid;
            }
            started    = true;
            closers[0] = '}';
            depth      = 1;
            continue;
        }
        if (in_string) {
            if (escaped) {
                escaped = false;
            } else if (character == '\\') {
                escaped = true;
            } else if (character == '"') {
                in_string = false;
            }
            continue;
        }
        if (character == '"') {
            in_string = true;
        } else if (character == '{' || character == '[') {
            if (depth == closers.size()) {
                return RpcObjectState::Invalid;
            }
            closers[depth] = character == '{' ? '}' : ']';
            ++depth;
        } else if (character == '}' || character == ']') {
            if (depth == 0 || closers[depth - 1] != character) {
                return RpcObjectState::Invalid;
            }
            --depth;
            if (depth == 0) {
                for (++index; index < json.size(); ++index) {
                    const char trailing = json[index];
                    if (trailing != ' ' && trailing != '\t' && trailing != '\r' && trailing != '\n' &&
                        trailing != '\0') {
                        return RpcObjectState::Invalid;
                    }
                }
                return RpcObjectState::Complete;
            }
        }
    }
    return RpcObjectState::Incomplete;
}

void addResponseId(cJSON* response, const cJSON* id)
{
    if (id == nullptr) {
        cJSON_AddNullToObject(response, "id");
        return;
    }
    cJSON* copy = cJSON_Duplicate(id, true);
    if (copy != nullptr) {
        cJSON_AddItemToObject(response, "id", copy);
    } else {
        cJSON_AddNullToObject(response, "id");
    }
}

void restartAfterPairingReset(void*)
{
    vTaskDelay(pdMS_TO_TICKS(250));
    esp_restart();
}

}  // namespace

CodexMicroBle& GetCodexMicroBle()
{
    static CodexMicroBle instance;
    return instance;
}

bool CodexMicroBle::begin()
{
    codex_micro_hid_gatt_compat_link_anchor();
    bool expected = false;
    if (!_initialized.compare_exchange_strong(expected, true)) {
        return _hid_device != nullptr;
    }

    _state_mutex = xSemaphoreCreateMutex();
    _tx_mutex    = xSemaphoreCreateMutex();
    if (_state_mutex == nullptr || _tx_mutex == nullptr) {
        ESP_LOGE(Tag, "failed to create protocol mutexes");
        return false;
    }

    if (!initializeController()) {
        return false;
    }
    codex_micro_hid_gatt_compat_snapshot_bonds();
    if (!configureAdvertising()) {
        return false;
    }

    if (logStepError("esp_ble_gatts_register_callback", esp_ble_gatts_register_callback(gattsEventCallback))) {
        return false;
    }

    if (logStepError("esp_hidd_dev_init",
                     esp_hidd_dev_init(&HidConfig, ESP_HID_TRANSPORT_BLE, hidEventCallback, &_hid_device))) {
        return false;
    }

    _input_queue          = xQueueCreate(NormalInputQueueDepth, sizeof(InputEvent));
    _critical_input_queue = xQueueCreate(CriticalInputQueueDepth, sizeof(InputEvent));
    _joystick_queue       = xQueueCreate(1, sizeof(InputEvent));
    _rpc_queue            = xQueueCreate(RpcQueueDepth, sizeof(RpcRequest*));
    if (_input_queue == nullptr || _critical_input_queue == nullptr || _joystick_queue == nullptr ||
        _rpc_queue == nullptr ||
        xTaskCreatePinnedToCore(inputTaskEntry, "codex_input_tx", 6144, this, InputTaskPriority, &_input_task,
                                InputTaskCore) != pdPASS) {
        ESP_LOGE(Tag, "unable to allocate required BLE transport queues or worker");
        if (_input_queue != nullptr) {
            vQueueDelete(_input_queue);
            _input_queue = nullptr;
        }
        if (_critical_input_queue != nullptr) {
            vQueueDelete(_critical_input_queue);
            _critical_input_queue = nullptr;
        }
        if (_joystick_queue != nullptr) {
            vQueueDelete(_joystick_queue);
            _joystick_queue = nullptr;
        }
        if (_rpc_queue != nullptr) {
            vQueueDelete(_rpc_queue);
            _rpc_queue = nullptr;
        }
        _input_task = nullptr;
        return false;
    }

    ESP_LOGI(Tag, "initializing vendor HID VID=%04X PID=%04X usage=FF00 report=%u", VendorId, ProductId, ReportId);
    return true;
}

void CodexMicroBle::poll()
{
    if (!connected()) {
        return;
    }

    const uint32_t now                 = static_cast<uint32_t>(esp_timer_get_time() / 1000);
    const ProtocolLinkState link_state = _link_state.load(std::memory_order_acquire);
    if (link_state == ProtocolLinkState::Ready || link_state == ProtocolLinkState::Disconnected) {
        return;
    }
    if (link_state == ProtocolLinkState::Recovering) {
        const uint32_t deadline = _half_open_recovery_deadline_ms.load(std::memory_order_relaxed);
        if (deadline != 0 && static_cast<int32_t>(now - deadline) >= 0) {
            ESP_LOGE(Tag, "BLE disconnect watchdog expired; restarting transport");
            esp_restart();
        }
        return;
    }

    const uint32_t connected_since = _connected_since_ms.load(std::memory_order_relaxed);
    if (connected_since != 0 && now - connected_since >= ProtocolHandshakeTimeoutMs) {
        recoverHalfOpenConnection();
    }
}

bool CodexMicroBle::initializeController()
{
    esp_err_t error = esp_bt_controller_mem_release(ESP_BT_MODE_CLASSIC_BT);
    if (logStepError("esp_bt_controller_mem_release", error)) {
        return false;
    }

    esp_bt_controller_config_t config = BT_CONTROLLER_INIT_CONFIG_DEFAULT();
    if (logStepError("esp_bt_controller_init", esp_bt_controller_init(&config))) {
        return false;
    }
    if (logStepError("esp_bt_controller_enable", esp_bt_controller_enable(ESP_BT_MODE_BLE))) {
        return false;
    }

    esp_bluedroid_config_t bluedroid_config = BT_BLUEDROID_INIT_CONFIG_DEFAULT();
    bluedroid_config.ssp_en                 = false;
    if (logStepError("esp_bluedroid_init", esp_bluedroid_init_with_cfg(&bluedroid_config))) {
        return false;
    }
    if (logStepError("esp_bluedroid_enable", esp_bluedroid_enable())) {
        return false;
    }
    if (logStepError("esp_ble_gap_register_callback", esp_ble_gap_register_callback(gapEventCallback))) {
        return false;
    }
    return true;
}

bool CodexMicroBle::configureAdvertising()
{
    esp_ble_auth_req_t auth_request = ESP_LE_AUTH_BOND;
    esp_ble_io_cap_t io_capability  = ESP_IO_CAP_NONE;
    uint8_t init_key                = ESP_BLE_ENC_KEY_MASK | ESP_BLE_ID_KEY_MASK;
    uint8_t response_key            = ESP_BLE_ENC_KEY_MASK | ESP_BLE_ID_KEY_MASK;
    uint8_t key_size                = 16;

    if (logStepError("set auth mode",
                     esp_ble_gap_set_security_param(ESP_BLE_SM_AUTHEN_REQ_MODE, &auth_request, sizeof(auth_request))) ||
        logStepError("set IO capability",
                     esp_ble_gap_set_security_param(ESP_BLE_SM_IOCAP_MODE, &io_capability, sizeof(io_capability))) ||
        logStepError("set initiator keys",
                     esp_ble_gap_set_security_param(ESP_BLE_SM_SET_INIT_KEY, &init_key, sizeof(init_key))) ||
        logStepError("set responder keys",
                     esp_ble_gap_set_security_param(ESP_BLE_SM_SET_RSP_KEY, &response_key, sizeof(response_key))) ||
        logStepError("set key size",
                     esp_ble_gap_set_security_param(ESP_BLE_SM_MAX_KEY_SIZE, &key_size, sizeof(key_size))) ||
        logStepError("set device name", esp_ble_gap_set_device_name(DeviceName))) {
        return false;
    }

    esp_ble_adv_data_t advertising_data = {
        .set_scan_rsp = false,
        // The 128-bit HID service UUID leaves no room for the full name in
        // the 31-byte advertising packet, so the name is sent separately in
        // the scan response.
        .include_name        = false,
        .include_txpower     = true,
        .min_interval        = 0x0006,
        .max_interval        = 0x0012,
        .appearance          = ESP_HID_APPEARANCE_GENERIC,
        .manufacturer_len    = 0,
        .p_manufacturer_data = nullptr,
        .service_data_len    = 0,
        .p_service_data      = nullptr,
        .service_uuid_len    = sizeof(HidServiceUuid128),
        .p_service_uuid      = const_cast<uint8_t*>(HidServiceUuid128),
        .flag                = ESP_BLE_ADV_FLAG_GEN_DISC | ESP_BLE_ADV_FLAG_BREDR_NOT_SPT,
    };

    esp_ble_adv_data_t scan_response_data = {
        .set_scan_rsp        = true,
        .include_name        = true,
        .include_txpower     = false,
        .min_interval        = 0,
        .max_interval        = 0,
        .appearance          = 0,
        .manufacturer_len    = 0,
        .p_manufacturer_data = nullptr,
        .service_data_len    = 0,
        .p_service_data      = nullptr,
        .service_uuid_len    = 0,
        .p_service_uuid      = nullptr,
        .flag                = 0,
    };

    return !logStepError("esp_ble_gap_config_adv_data", esp_ble_gap_config_adv_data(&advertising_data)) &&
           !logStepError("esp_ble_gap_config_scan_rsp_data", esp_ble_gap_config_adv_data(&scan_response_data));
}

void CodexMicroBle::gapEventCallback(esp_gap_ble_cb_event_t event, esp_ble_gap_cb_param_t* param)
{
    auto& owner = GetCodexMicroBle();
    switch (event) {
        case ESP_GAP_BLE_ADV_DATA_SET_COMPLETE_EVT:
            owner._adv_data_ready.store(true);
            owner.maybeStartAdvertising();
            break;
        case ESP_GAP_BLE_SCAN_RSP_DATA_SET_COMPLETE_EVT:
            owner._scan_rsp_ready.store(true);
            owner.maybeStartAdvertising();
            break;
        case ESP_GAP_BLE_ADV_START_COMPLETE_EVT:
            if (param->adv_start_cmpl.status == ESP_BT_STATUS_SUCCESS) {
                ESP_LOGI(Tag, "advertising as %s", DeviceName);
            } else {
                owner._advertising.store(false);
                ESP_LOGE(Tag, "advertising start failed: %d", param->adv_start_cmpl.status);
            }
            break;
        case ESP_GAP_BLE_ADV_STOP_COMPLETE_EVT:
            owner._advertising.store(false);
            break;
        case ESP_GAP_BLE_SEC_REQ_EVT:
            esp_ble_gap_security_rsp(param->ble_security.ble_req.bd_addr, true);
            break;
        case ESP_GAP_BLE_AUTH_CMPL_EVT:
            if (param->ble_security.auth_cmpl.success) {
                codex_micro_hid_gatt_compat_authenticated(param->ble_security.auth_cmpl.bd_addr);
                ESP_LOGI(Tag, "pairing complete");
            } else {
                ESP_LOGE(Tag, "pairing failed: 0x%02x", param->ble_security.auth_cmpl.fail_reason);
            }
            break;
        case ESP_GAP_BLE_REMOVE_BOND_DEV_COMPLETE_EVT:
            if (!owner._pairing_reset_requested.load()) {
                break;
            }
            if (param->remove_bond_dev_cmpl.status != ESP_BT_STATUS_SUCCESS) {
                ESP_LOGE(Tag, "BLE bond removal completion failed: %d", param->remove_bond_dev_cmpl.status);
                owner._pairing_bonds_pending.store(0);
                owner._pairing_reset_requested.store(false);
                break;
            }
            if (owner._pairing_bonds_pending.fetch_sub(1) == 1) {
                ESP_LOGW(Tag, "all BLE bonds removed; restarting in pairing mode");
                owner.schedulePairingRestart();
            }
            break;
        default:
            break;
    }
}

void CodexMicroBle::gattsEventCallback(esp_gatts_cb_event_t event, esp_gatt_if_t gattsIf,
                                       esp_ble_gatts_cb_param_t* param)
{
    auto& owner = GetCodexMicroBle();
    if (owner._state_mutex != nullptr &&
        ((param != nullptr && event == ESP_GATTS_CONNECT_EVT) || event == ESP_GATTS_DISCONNECT_EVT)) {
        xSemaphoreTake(owner._state_mutex, portMAX_DELAY);
        if (param != nullptr && event == ESP_GATTS_CONNECT_EVT) {
            std::memcpy(owner._peer_address.data(), param->connect.remote_bda, ESP_BD_ADDR_LEN);
            owner._peer_address_valid.store(true, std::memory_order_release);
        } else {
            owner._peer_address_valid.store(false, std::memory_order_release);
        }
        xSemaphoreGive(owner._state_mutex);
    }
    codex_micro_hid_gatt_compat_gatts_event(event, gattsIf, param);
    esp_hidd_gatts_event_handler(event, gattsIf, param);
}

void CodexMicroBle::hidEventCallback(void*, esp_event_base_t, int32_t id, void* eventData)
{
    auto& owner = GetCodexMicroBle();
    auto event  = static_cast<esp_hidd_event_t>(id);
    auto* data  = static_cast<esp_hidd_event_data_t*>(eventData);

    switch (event) {
        case ESP_HIDD_START_EVENT:
            owner._hid_ready.store(true);
            owner.setReady(true);
            ESP_LOGI(Tag, "CODEX_MICRO_BLE_READY");
            owner.maybeStartAdvertising();
            break;
        case ESP_HIDD_CONNECT_EVENT:
            owner._advertising.store(false);
            owner.onConnected(true);
            ESP_LOGI(Tag, "host connected");
            break;
        case ESP_HIDD_OUTPUT_EVENT:
            if (data != nullptr && data->output.report_id == ReportId) {
                owner.onOutput(data->output.data, data->output.length);
            }
            break;
        case ESP_HIDD_DISCONNECT_EVENT:
            owner.onConnected(false);
            owner._advertising.store(false);
            ESP_LOGI(Tag, "host disconnected reason=%d", data == nullptr ? -1 : data->disconnect.reason);
            owner.maybeStartAdvertising();
            break;
        default:
            break;
    }
}

void CodexMicroBle::maybeStartAdvertising()
{
    if (!_adv_data_ready.load() || !_scan_rsp_ready.load() || !_hid_ready.load() || connected()) {
        return;
    }
    if (_advertising.exchange(true)) {
        return;
    }
    esp_err_t error = esp_ble_gap_start_advertising(&AdvertisingParams);
    if (error != ESP_OK) {
        _advertising.store(false);
        ESP_LOGE(Tag, "esp_ble_gap_start_advertising failed: %s", esp_err_to_name(error));
    }
}

void CodexMicroBle::setReady(bool ready)
{
    if (_state_mutex == nullptr) {
        return;
    }
    xSemaphoreTake(_state_mutex, portMAX_DELAY);
    _state.ready = ready;
    ++_state.revision;
    xSemaphoreGive(_state_mutex);
}

void CodexMicroBle::onConnected(bool connectedValue)
{
    if (_state_mutex == nullptr) {
        return;
    }
    if (!connectedValue) {
        // Close the input admission gate before publishing a new generation.
        // Otherwise another core could briefly pair the new generation with
        // the old Ready link and send into a connection that is closing.
        _connected.store(false, std::memory_order_release);
    }
    _connection_generation.fetch_add(1, std::memory_order_acq_rel);
    _link_state.store(connectedValue ? ProtocolLinkState::Awaiting : ProtocolLinkState::Disconnected,
                      std::memory_order_release);
    _half_open_recovery_deadline_ms.store(0, std::memory_order_relaxed);
    _connected_since_ms.store(connectedValue ? static_cast<uint32_t>(esp_timer_get_time() / 1000) : 0,
                              std::memory_order_relaxed);
    xSemaphoreTake(_state_mutex, portMAX_DELAY);
    _state.connected     = connectedValue;
    _state.protocolReady = false;
    if (!connectedValue) {
        _state.threads       = {};
        _state.ambient       = {};
        _state.keys          = {};
        _configured_ambient  = {};
        _configured_keys     = {};
        _ambient_sync_thread = -1;
        _keys_sync_thread    = -1;
    }
    ++_state.revision;
    ++_state.attentionRevision;
    xSemaphoreGive(_state_mutex);
    if (connectedValue) {
        _connected.store(true, std::memory_order_release);
    }
    if (_input_queue != nullptr) {
        xQueueReset(_input_queue);
        xQueueReset(_critical_input_queue);
        xQueueReset(_joystick_queue);
        RpcRequest* pending_request = nullptr;
        while (xQueueReceive(_rpc_queue, &pending_request, 0) == pdTRUE) {
            delete pending_request;
            pending_request = nullptr;
        }
    }
    _rpc_buffer.clear();
}

bool CodexMicroBle::markProtocolReady(uint32_t generation)
{
    xSemaphoreTake(_state_mutex, portMAX_DELAY);
    if (!connected() || generation != _connection_generation.load(std::memory_order_acquire)) {
        xSemaphoreGive(_state_mutex);
        return false;
    }
    ProtocolLinkState expected = ProtocolLinkState::Awaiting;
    if (!_link_state.compare_exchange_strong(expected, ProtocolLinkState::Ready, std::memory_order_acq_rel)) {
        const bool already_ready = expected == ProtocolLinkState::Ready && connected() &&
                                   generation == _connection_generation.load(std::memory_order_acquire);
        xSemaphoreGive(_state_mutex);
        return already_ready;
    }
    if (!connected() || generation != _connection_generation.load(std::memory_order_acquire) ||
        _link_state.load(std::memory_order_acquire) != ProtocolLinkState::Ready) {
        xSemaphoreGive(_state_mutex);
        return false;
    }
    _half_open_recovery_deadline_ms.store(0, std::memory_order_relaxed);
    _state.protocolReady = true;
    ++_state.revision;
    ++_state.attentionRevision;
    xSemaphoreGive(_state_mutex);
    ESP_LOGI(Tag, "Codex RPC handshake complete");
    return true;
}

void CodexMicroBle::recoverHalfOpenConnection()
{
    if (!connected()) {
        return;
    }
    ProtocolLinkState expected = ProtocolLinkState::Awaiting;
    if (!_link_state.compare_exchange_strong(expected, ProtocolLinkState::Recovering, std::memory_order_acq_rel)) {
        return;
    }
    ++_half_open_recoveries;
    ESP_LOGW(Tag, "Codex RPC handshake timed out after %ums; reconnecting", ProtocolHandshakeTimeoutMs);

    esp_bd_addr_t peer = {};
    xSemaphoreTake(_state_mutex, portMAX_DELAY);
    const bool peer_valid = _peer_address_valid.load(std::memory_order_acquire);
    if (peer_valid) {
        std::memcpy(peer, _peer_address.data(), ESP_BD_ADDR_LEN);
    }
    xSemaphoreGive(_state_mutex);
    if (peer_valid) {
        const esp_err_t error = esp_ble_gap_disconnect(peer);
        if (error == ESP_OK) {
            const uint32_t now = static_cast<uint32_t>(esp_timer_get_time() / 1000);
            _half_open_recovery_deadline_ms.store(now + 2000U, std::memory_order_relaxed);
            return;
        }
        ESP_LOGE(Tag, "BLE disconnect request failed: %s", esp_err_to_name(error));
    }

    ESP_LOGE(Tag, "current BLE peer unavailable; restarting transport");
    esp_restart();
}

bool CodexMicroBle::connected()
{
    return _connected.load(std::memory_order_acquire);
}

CodexMicroState CodexMicroBle::snapshot()
{
    CodexMicroState copy;
    if (_state_mutex == nullptr) {
        return copy;
    }
    xSemaphoreTake(_state_mutex, portMAX_DELAY);
    copy = _state;
    xSemaphoreGive(_state_mutex);
    return copy;
}

CodexMicroBleDiagnostics CodexMicroBle::diagnostics() const
{
    return {
        .initialized        = _initialized.load(),
        .hidReady           = _hid_ready.load(),
        .connected          = _connected.load(std::memory_order_acquire),
        .protocolReady      = _link_state.load(std::memory_order_acquire) == ProtocolLinkState::Ready,
        .advertising        = _advertising.load(),
        .inputQueued        = _input_queued.load(),
        .inputDropped       = _input_dropped.load(),
        .inputProcessed     = _input_processed.load(),
        .txMessages         = _tx_messages.load(),
        .txReports          = _tx_reports.load(),
        .txFailures         = _tx_failures.load(),
        .rxReports          = _rx_reports.load(),
        .rpcMessages        = _rpc_messages.load(),
        .rpcErrors          = _rpc_errors.load(),
        .queuePending       = _input_queue == nullptr ? 0U
                                                      : static_cast<uint32_t>(uxQueueMessagesWaiting(_input_queue) +
                                                                              uxQueueMessagesWaiting(_critical_input_queue) +
                                                                              uxQueueMessagesWaiting(_joystick_queue)),
        .queueHighWater     = _queue_high_water.load(std::memory_order_relaxed),
        .txMaxUs            = _tx_max_us.load(std::memory_order_relaxed),
        .txTotalUs          = _tx_total_us.load(std::memory_order_relaxed),
        .halfOpenRecoveries = _half_open_recoveries.load(std::memory_order_relaxed),
        .inputTaskCore      = _input_task_core.load(std::memory_order_relaxed),
    };
}

void CodexMicroBle::resetPerformanceDiagnostics()
{
    _queue_high_water.store(0, std::memory_order_relaxed);
    _tx_max_us.store(0, std::memory_order_relaxed);
    _tx_total_us.store(0, std::memory_order_relaxed);
}

bool CodexMicroBle::protocolSelfTest() const
{
    constexpr std::array<std::string_view, 16> ExpectedCodes = {
        "AG00",  "AG01",  "AG02",  "AG03",  "AG04",  "AG05", "ACT06",  "ACT07",
        "ACT08", "ACT09", "ACT10", "ACT11", "ACT12", "ENC",  "ENC_CW", "ENC_CC",
    };
    for (std::size_t index = 0; index < ExpectedCodes.size(); ++index) {
        if (ExpectedCodes[index] != CodexMicroControlCodes[index]) {
            return false;
        }
    }

    if (sizeof(ReportMap) < 2 || ReportMap[8] != ReportId || ReportSize != 63 || PayloadSize != 61 ||
        MaxRpcBufferSize < ReportSize) {
        return false;
    }

    std::array<char, 112> key_message = {};
    const int key_length              = std::snprintf(key_message.data(), key_message.size(),
                                                      "{\"method\":\"v.oai.hid\",\"params\":{\"k\":\"%s\",\"act\":%u,\"ag\":%d}}",
                                                      codexMicroControlCode(CodexMicroControl::Agent1),
                                                      static_cast<unsigned>(CodexMicroKeyAction::Press), 0);
    if (key_length <= 0 || static_cast<std::size_t>(key_length) >= key_message.size() ||
        std::strcmp(key_message.data(), "{\"method\":\"v.oai.hid\",\"params\":{\"k\":\"AG00\",\"act\":1,\"ag\":0}}") !=
            0) {
        return false;
    }

    std::array<char, 112> joystick_message = {};
    const int joystick_length =
        std::snprintf(joystick_message.data(), joystick_message.size(),
                      "{\"method\":\"v.oai.rad\",\"params\":{\"a\":%.6f,\"d\":%.6f}}", 0.0, 0.0);
    return joystick_length > 0 && static_cast<std::size_t>(joystick_length) < joystick_message.size() &&
           std::strcmp(joystick_message.data(),
                       "{\"method\":\"v.oai.rad\",\"params\":{\"a\":0.000000,\"d\":0.000000}}") == 0;
}

void CodexMicroBle::setBattery(uint8_t percentage, bool charging)
{
    percentage = std::min<uint8_t>(percentage, 100);
    if (_state_mutex != nullptr) {
        xSemaphoreTake(_state_mutex, portMAX_DELAY);
        if (_state.battery != percentage || _state.charging != charging) {
            _state.battery  = percentage;
            _state.charging = charging;
            ++_state.revision;
        }
        xSemaphoreGive(_state_mutex);
    }
    if (_hid_device != nullptr) {
        esp_hidd_dev_battery_set(_hid_device, percentage);
    }
}

bool CodexMicroBle::resetPairing()
{
    if (!_initialized.load()) {
        return false;
    }
    if (_pairing_reset_requested.exchange(true)) {
        return true;
    }

    int bond_count = esp_ble_get_bond_device_num();
    if (bond_count < 0) {
        ESP_LOGE(Tag, "failed to read BLE bond database");
        _pairing_reset_requested.store(false);
        return false;
    }

    if (bond_count > 0) {
        std::unique_ptr<esp_ble_bond_dev_t[]> bonds(new (std::nothrow) esp_ble_bond_dev_t[bond_count]);
        if (bonds == nullptr) {
            ESP_LOGE(Tag, "failed to allocate BLE bond list");
            _pairing_reset_requested.store(false);
            return false;
        }
        int listed      = bond_count;
        esp_err_t error = esp_ble_get_bond_device_list(&listed, bonds.get());
        if (error != ESP_OK) {
            ESP_LOGE(Tag, "failed to list BLE bonds: %s", esp_err_to_name(error));
            _pairing_reset_requested.store(false);
            return false;
        }
        if (listed == 0) {
            ESP_LOGW(Tag, "pairing reset requested; bond list became empty");
            return schedulePairingRestart();
        }
        _pairing_bonds_pending.store(listed);
        for (int i = 0; i < listed; ++i) {
            error = esp_ble_remove_bond_device(bonds[i].bd_addr);
            if (error != ESP_OK) {
                ESP_LOGE(Tag, "failed to remove BLE bond %d: %s", i, esp_err_to_name(error));
                _pairing_bonds_pending.store(0);
                _pairing_reset_requested.store(false);
                return false;
            }
        }
        ESP_LOGW(Tag, "pairing reset requested; removing %d bond(s)", listed);
        return true;
    }

    ESP_LOGW(Tag, "pairing reset requested; no stored bonds");
    return schedulePairingRestart();
}

bool CodexMicroBle::schedulePairingRestart()
{
    if (xTaskCreate(restartAfterPairingReset, "codex_pair_reset", 2048, nullptr, 5, nullptr) != pdPASS) {
        ESP_LOGE(Tag, "failed to schedule pairing reset restart");
        _pairing_bonds_pending.store(0);
        _pairing_reset_requested.store(false);
        return false;
    }
    return true;
}

bool CodexMicroBle::sendKey(CodexMicroControl control, CodexMicroKeyAction action, int8_t agent)
{
    const InputEvent event = {
        .kind    = InputEventKind::Key,
        .control = control,
        .action  = action,
        .agent   = agent,
    };
    return queueInput(event);
}

bool CodexMicroBle::sendJoystick(float angle, float distance)
{
    const InputEvent event = {
        .kind     = InputEventKind::Joystick,
        .angle    = angle,
        .distance = distance,
    };
    return queueInput(event);
}

bool CodexMicroBle::sendJoystickButton(float angle, bool pressed)
{
    const InputEvent event = {
        .kind     = InputEventKind::Joystick,
        .angle    = angle,
        .distance = pressed ? 1.0f : 0.0f,
        .ordered  = true,
    };
    return queueInput(event);
}

bool CodexMicroBle::sendEncoderSteps(int direction, uint16_t steps)
{
    if (steps == 0) {
        return true;
    }
    if (steps > CodexMicroMaxEncoderBatchSteps) {
        ++_input_dropped;
        ESP_LOGE(Tag, "encoder batch exceeds limit requested=%u limit=%u", static_cast<unsigned>(steps),
                 static_cast<unsigned>(CodexMicroMaxEncoderBatchSteps));
        return false;
    }
    const InputEvent event = {
        .kind    = InputEventKind::Key,
        .control = direction > 0 ? CodexMicroControl::EncoderClockwise : CodexMicroControl::EncoderCounterClockwise,
        .action  = CodexMicroKeyAction::Rotate,
        .repeat  = steps,
    };
    return queueInput(event);
}

void CodexMicroBle::inputTaskEntry(void* context)
{
    static_cast<CodexMicroBle*>(context)->runInputTask();
}

void CodexMicroBle::runInputTask()
{
    _input_task_core.store(static_cast<int8_t>(xPortGetCoreID()), std::memory_order_relaxed);
    InputEvent event;
    InputEvent encoder_batch;
    RpcRequest* queued_rpc_request = nullptr;
    bool encoder_pending           = false;
    const auto event_is_current    = [this](const InputEvent& candidate) {
        return candidate.generation == _connection_generation.load(std::memory_order_acquire) &&
               _link_state.load(std::memory_order_acquire) == ProtocolLinkState::Ready;
    };
    while (true) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        while (connected()) {
            if (xQueueReceive(_critical_input_queue, &event, 0) == pdTRUE) {
                if (!event_is_current(event)) {
                    continue;
                }
                bool sent = false;
                for (unsigned attempt = 0; attempt < 3 && connected() && event_is_current(event); ++attempt) {
                    sent = event.kind == InputEventKind::Joystick
                               ? sendJoystickNow(event.angle, event.distance, event.generation)
                               : sendKeyNow(event.control, event.action, event.agent, event.generation);
                    if (sent) {
                        break;
                    }
                    vTaskDelay(pdMS_TO_TICKS(8));
                }
                if (sent) {
                    ++_input_processed;
                } else if (connected() && event_is_current(event)) {
                    ++_input_dropped;
                    ESP_LOGE(Tag, "critical input delivery failed; restarting to release host controls");
                    esp_restart();
                }
            } else if (xQueueReceive(_rpc_queue, &queued_rpc_request, 0) == pdTRUE) {
                std::unique_ptr<RpcRequest> rpc_request(queued_rpc_request);
                queued_rpc_request = nullptr;
                if (rpc_request == nullptr ||
                    rpc_request->generation != _connection_generation.load(std::memory_order_acquire)) {
                    continue;
                }
                if (_link_state.load(std::memory_order_acquire) == ProtocolLinkState::Awaiting) {
                    const uint32_t now             = static_cast<uint32_t>(esp_timer_get_time() / 1000);
                    const uint32_t connected_since = _connected_since_ms.load(std::memory_order_relaxed);
                    const uint32_t elapsed         = now - connected_since;
                    if (connected_since != 0 && elapsed < RpcTransportWarmupMs) {
                        vTaskDelay(pdMS_TO_TICKS(RpcTransportWarmupMs - elapsed));
                    }
                }
                if (!connected() || rpc_request->generation != _connection_generation.load(std::memory_order_acquire)) {
                    continue;
                }
                const ProtocolLinkState current_link_state = _link_state.load(std::memory_order_acquire);
                if (current_link_state != ProtocolLinkState::Awaiting &&
                    current_link_state != ProtocolLinkState::Ready) {
                    continue;
                }
                cJSON* request = cJSON_ParseWithLength(rpc_request->json.data(), rpc_request->length);
                if (request == nullptr) {
                    ++_rpc_errors;
                    continue;
                }
                ++_rpc_messages;
                handleRpc(request, rpc_request->generation);
                cJSON_Delete(request);
            } else if (_link_state.load(std::memory_order_acquire) != ProtocolLinkState::Ready) {
                break;
            } else if (xQueueReceive(_joystick_queue, &event, 0) == pdTRUE) {
                if (!event_is_current(event)) {
                    continue;
                }
                if (sendJoystickNow(event.angle, event.distance, event.generation)) {
                    ++_input_processed;
                } else if (connected() && event_is_current(event)) {
                    ++_input_dropped;
                }
            } else {
                if (!encoder_pending && xQueueReceive(_input_queue, &encoder_batch, 0) == pdTRUE) {
                    encoder_pending = true;
                }
                if (encoder_pending && !event_is_current(encoder_batch)) {
                    encoder_pending = false;
                    continue;
                }
                if (!encoder_pending) {
                    break;
                }
                const bool ordered_control = encoder_batch.action != CodexMicroKeyAction::Rotate;
                const unsigned attempts    = ordered_control ? 3U : 1U;
                bool sent                  = false;
                for (unsigned attempt = 0; attempt < attempts && connected() && event_is_current(encoder_batch);
                     ++attempt) {
                    sent = sendKeyNow(encoder_batch.control, encoder_batch.action, encoder_batch.agent,
                                      encoder_batch.generation);
                    if (sent) {
                        break;
                    }
                    if (ordered_control) {
                        vTaskDelay(pdMS_TO_TICKS(8));
                    }
                }
                if (sent) {
                    ++_input_processed;
                } else if (connected() && event_is_current(encoder_batch)) {
                    ++_input_dropped;
                    if (ordered_control) {
                        ESP_LOGE(Tag, "encoder control delivery failed; restarting to release host control");
                        esp_restart();
                    }
                }
                if (encoder_batch.repeat > 0) {
                    --encoder_batch.repeat;
                }
                encoder_pending = encoder_batch.repeat > 0;
            }

            // Process at most one encoder detent before checking control and
            // latest-joystick queues again. This bounds PTT/key release latency.
            vTaskDelay(pdMS_TO_TICKS(4));
        }
        if (!connected()) {
            encoder_pending = false;
        }
    }
}

bool CodexMicroBle::queueInput(const InputEvent& event)
{
    const uint32_t generation = _connection_generation.load(std::memory_order_acquire);
    if (!connected() || _link_state.load(std::memory_order_acquire) != ProtocolLinkState::Ready) {
        return false;
    }
    InputEvent queued_event = event;
    queued_event.generation = generation;
    if (_input_queue == nullptr || _critical_input_queue == nullptr || _joystick_queue == nullptr ||
        _input_task == nullptr) {
        if (queued_event.generation != _connection_generation.load(std::memory_order_acquire) ||
            _link_state.load(std::memory_order_acquire) != ProtocolLinkState::Ready) {
            return false;
        }
        if (queued_event.kind == InputEventKind::Joystick) {
            return sendJoystickNow(queued_event.angle, queued_event.distance, queued_event.generation);
        }
        bool sent = true;
        for (uint16_t index = 0; index < queued_event.repeat; ++index) {
            if (queued_event.generation != _connection_generation.load(std::memory_order_acquire) ||
                _link_state.load(std::memory_order_acquire) != ProtocolLinkState::Ready) {
                return false;
            }
            sent = sendKeyNow(queued_event.control, queued_event.action, queued_event.agent, queued_event.generation) &&
                   sent;
        }
        return sent;
    }

    if (generation != _connection_generation.load(std::memory_order_acquire) ||
        _link_state.load(std::memory_order_acquire) != ProtocolLinkState::Ready) {
        return false;
    }
    BaseType_t queued           = pdFALSE;
    const bool joystick_neutral = queued_event.kind == InputEventKind::Joystick && queued_event.distance <= 0.001f;
    const bool encoder_ordered =
        queued_event.kind == InputEventKind::Key &&
        (queued_event.action == CodexMicroKeyAction::Rotate || queued_event.control == CodexMicroControl::EncoderPress);
    const bool critical =
        queued_event.ordered || joystick_neutral || (queued_event.kind == InputEventKind::Key && !encoder_ordered);
    const bool must_preserve = critical || (encoder_ordered && queued_event.action != CodexMicroKeyAction::Rotate);
    if (joystick_neutral) {
        // Discard the last unsent non-zero position, then place neutral in the
        // ordered control FIFO. A quick new press cannot overtake this barrier.
        xQueueReset(_joystick_queue);
        queued = xQueueSendToBack(_critical_input_queue, &queued_event, pdMS_TO_TICKS(20));
    } else if (queued_event.kind == InputEventKind::Joystick && !critical) {
        // Non-zero analog motion is absolute and only its latest value matters.
        queued = xQueueOverwrite(_joystick_queue, &queued_event);
    } else if (critical) {
        // All key press/release events share one FIFO, preserving per-key
        // ordering while receiving priority over relative encoder motion.
        queued = xQueueSendToBack(_critical_input_queue, &queued_event, pdMS_TO_TICKS(20));
    } else {
        const bool rotation = queued_event.action == CodexMicroKeyAction::Rotate;
        if (!rotation || uxQueueSpacesAvailable(_input_queue) > EncoderControlReserve) {
            queued = xQueueSendToBack(_input_queue, &queued_event, must_preserve ? pdMS_TO_TICKS(20) : 0);
        }
    }

    if (queued != pdTRUE) {
        ++_input_dropped;
        ESP_LOGE(Tag, "input queue full kind=%u action=%u repeat=%u", static_cast<unsigned>(queued_event.kind),
                 static_cast<unsigned>(queued_event.action), static_cast<unsigned>(queued_event.repeat));
        if (must_preserve && connected() &&
            queued_event.generation == _connection_generation.load(std::memory_order_acquire) &&
            _link_state.load(std::memory_order_acquire) == ProtocolLinkState::Ready) {
            ESP_LOGE(Tag, "critical input was not queued; restarting to release host controls");
            esp_restart();
        }
        return false;
    }
    if (queued_event.generation != _connection_generation.load(std::memory_order_acquire) ||
        _link_state.load(std::memory_order_acquire) != ProtocolLinkState::Ready) {
        ++_input_dropped;
        xTaskNotifyGive(_input_task);
        return false;
    }
    ++_input_queued;
    const uint32_t pending =
        static_cast<uint32_t>(uxQueueMessagesWaiting(_input_queue) + uxQueueMessagesWaiting(_critical_input_queue) +
                              uxQueueMessagesWaiting(_joystick_queue));
    updateAtomicMax(_queue_high_water, pending);
    xTaskNotifyGive(_input_task);
    return true;
}

bool CodexMicroBle::sendKeyNow(CodexMicroControl control, CodexMicroKeyAction action, int8_t agent, uint32_t generation)
{
    const char* key = codexMicroControlCode(control);
    if (key == nullptr) {
        return false;
    }
    std::array<char, 112> message = {};
    int length                    = 0;
    if (agent >= 0) {
        length = std::snprintf(message.data(), message.size(),
                               "{\"method\":\"v.oai.hid\",\"params\":{\"k\":\"%s\",\"act\":%u,\"ag\":%d}}", key,
                               static_cast<unsigned>(action), static_cast<int>(agent));
    } else {
        length = std::snprintf(message.data(), message.size(),
                               "{\"method\":\"v.oai.hid\",\"params\":{\"k\":\"%s\",\"act\":%u}}", key,
                               static_cast<unsigned>(action));
    }
    const bool encoded = length > 0 && static_cast<std::size_t>(length) < message.size();
    const bool sent    = encoded && sendJson(message.data(), generation);
    if (action == CodexMicroKeyAction::Rotate) {
        ESP_LOGD(Tag, "TX key=%s act=%u agent=%d sent=%d", key == nullptr ? "" : key, static_cast<unsigned>(action),
                 static_cast<int>(agent), sent ? 1 : 0);
    } else {
        ESP_LOGI(Tag, "TX key=%s act=%u agent=%d sent=%d", key == nullptr ? "" : key, static_cast<unsigned>(action),
                 static_cast<int>(agent), sent ? 1 : 0);
    }
    return sent;
}

bool CodexMicroBle::sendJoystickNow(float angle, float distance, uint32_t generation)
{
    std::array<char, 112> message = {};
    const int length =
        std::snprintf(message.data(), message.size(), "{\"method\":\"v.oai.rad\",\"params\":{\"a\":%.6f,\"d\":%.6f}}",
                      static_cast<double>(angle), static_cast<double>(distance));
    const bool encoded = length > 0 && static_cast<std::size_t>(length) < message.size();
    const bool sent    = encoded && sendJson(message.data(), generation);
    ESP_LOGD(Tag, "TX stick angle=%.3f distance=%.3f sent=%d", static_cast<double>(angle),
             static_cast<double>(distance), sent ? 1 : 0);
    return sent;
}

bool CodexMicroBle::sendJsonObject(cJSON* object, uint32_t expectedGeneration)
{
    if (object == nullptr) {
        return false;
    }
    char* encoded = cJSON_PrintUnformatted(object);
    cJSON_Delete(object);
    if (encoded == nullptr) {
        return false;
    }
    bool sent = false;
    const bool handshake_warmup =
        expectedGeneration != UINT32_MAX && _link_state.load(std::memory_order_acquire) == ProtocolLinkState::Awaiting;
    const unsigned attempts = handshake_warmup ? 5U : 1U;
    for (unsigned attempt = 0; attempt < attempts; ++attempt) {
        sent                                       = sendJson(encoded, expectedGeneration, true);
        const ProtocolLinkState current_link_state = _link_state.load(std::memory_order_acquire);
        if (sent || !connected() ||
            (expectedGeneration != UINT32_MAX &&
             expectedGeneration != _connection_generation.load(std::memory_order_acquire)) ||
            (current_link_state != ProtocolLinkState::Awaiting && current_link_state != ProtocolLinkState::Ready)) {
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(50));
    }
    std::free(encoded);
    return sent;
}

bool CodexMicroBle::sendJson(const char* json, uint32_t expectedGeneration, bool allowAwaiting)
{
    if (json == nullptr || _hid_device == nullptr || _tx_mutex == nullptr || !connected()) {
        return false;
    }

    const int64_t started_us = esp_timer_get_time();
    xSemaphoreTake(_tx_mutex, portMAX_DELAY);
    const ProtocolLinkState link_state = _link_state.load(std::memory_order_acquire);
    const bool link_allows_send =
        link_state == ProtocolLinkState::Ready || (allowAwaiting && link_state == ProtocolLinkState::Awaiting);
    if (!connected() ||
        (expectedGeneration != UINT32_MAX &&
         (expectedGeneration != _connection_generation.load(std::memory_order_acquire) || !link_allows_send))) {
        xSemaphoreGive(_tx_mutex);
        return false;
    }

    const std::size_t json_length   = std::strlen(json);
    const std::size_t framed_length = json_length + 1;
    ++_tx_messages;
    bool success       = true;
    std::size_t offset = 0;
    while (offset < framed_length) {
        const std::size_t chunk      = std::min(PayloadSize, framed_length - offset);
        uint8_t report[ReportSize]   = {};
        report[0]                    = 2;
        report[1]                    = static_cast<uint8_t>(chunk);
        const std::size_t json_chunk = std::min(chunk, json_length - std::min(offset, json_length));
        if (json_chunk > 0) {
            std::memcpy(report + 2, json + offset, json_chunk);
        }
        if (json_chunk < chunk) {
            report[2 + json_chunk] = '\n';
        }
        esp_err_t error = esp_hidd_dev_input_set(_hid_device, 0, ReportId, report, sizeof(report));
        if (error != ESP_OK) {
            const bool warming_up = allowAwaiting && expectedGeneration != UINT32_MAX &&
                                    _link_state.load(std::memory_order_acquire) == ProtocolLinkState::Awaiting;
            if (!warming_up) {
                ++_tx_failures;
            }
            ESP_LOGW(Tag, "input report failed warmup=%d: %s", warming_up ? 1 : 0, esp_err_to_name(error));
            success = false;
            break;
        }
        ++_tx_reports;
        offset += chunk;
        // A fragmented RPC still needs spacing between its BLE reports. Input
        // events receive their inter-message pacing in the background worker.
        if (offset < framed_length) {
            vTaskDelay(pdMS_TO_TICKS(4));
        }
    }
    xSemaphoreGive(_tx_mutex);
    const uint32_t elapsed_us = static_cast<uint32_t>(esp_timer_get_time() - started_us);
    _tx_total_us.fetch_add(elapsed_us, std::memory_order_relaxed);
    updateAtomicMax(_tx_max_us, elapsed_us);
    return success;
}

void CodexMicroBle::onOutput(const uint8_t* data, std::size_t length)
{
    ++_rx_reports;
    if (data == nullptr || length < 2) {
        ++_rpc_errors;
        return;
    }

    std::size_t offset = length >= 3 && data[0] == ReportId ? 1 : 0;
    if (length < offset + 2 || data[offset] != 2) {
        ++_rpc_errors;
        return;
    }

    if (data[offset + 1] > PayloadSize) {
        ESP_LOGW(Tag, "invalid output payload length=%u", static_cast<unsigned>(data[offset + 1]));
        ++_rpc_errors;
        _rpc_buffer.clear();
        return;
    }
    const std::size_t payloadLength = data[offset + 1];
    if (length < offset + 2 + payloadLength) {
        ESP_LOGW(Tag, "truncated output report len=%u payload=%u", static_cast<unsigned>(length),
                 static_cast<unsigned>(payloadLength));
        ++_rpc_errors;
        return;
    }

    const char* payload             = reinterpret_cast<const char*>(data + offset + 2);
    constexpr char TopLevelPrefix[] = "{\"method\"";
    bool startsTopLevel             = payloadLength >= sizeof(TopLevelPrefix) - 1 &&
                          std::memcmp(payload, TopLevelPrefix, sizeof(TopLevelPrefix) - 1) == 0;
    if (startsTopLevel && !_rpc_buffer.empty()) {
        _rpc_buffer.clear();
    }

    if (_rpc_buffer.size() + payloadLength > MaxRpcBufferSize) {
        ESP_LOGW(Tag, "RPC buffer limit exceeded");
        ++_rpc_errors;
        _rpc_buffer.clear();
        return;
    }

    if (_rpc_buffer.empty()) {
        std::size_t jsonStart = 0;
        while (jsonStart < payloadLength && payload[jsonStart] != '{') {
            ++jsonStart;
        }
        if (jsonStart == payloadLength) {
            return;
        }
        _rpc_buffer.append(payload + jsonStart, payloadLength - jsonStart);
    } else {
        _rpc_buffer.append(payload, payloadLength);
    }

    // Work Louder HID writes are length-framed but are not guaranteed to end
    // with a newline. Detect a balanced top-level JSON object here and leave
    // all parsing/allocation to the larger worker task stack.
    const RpcObjectState object_state = rpcObjectState(_rpc_buffer);
    if (object_state == RpcObjectState::Invalid) {
        ESP_LOGW(Tag, "invalid RPC framing");
        ++_rpc_errors;
        _rpc_buffer.clear();
        return;
    }
    if (object_state != RpcObjectState::Complete) {
        return;
    }
    if (!queueRpcRequest(_rpc_buffer.data(), _rpc_buffer.size())) {
        ++_rpc_errors;
    }
    _rpc_buffer.clear();
}

bool CodexMicroBle::queueRpcRequest(const char* json, std::size_t length)
{
    const uint32_t generation          = _connection_generation.load(std::memory_order_acquire);
    const ProtocolLinkState link_state = _link_state.load(std::memory_order_acquire);
    if (json == nullptr || length == 0 || length > MaxRpcBufferSize || _rpc_queue == nullptr ||
        _input_task == nullptr || !connected() ||
        (link_state != ProtocolLinkState::Awaiting && link_state != ProtocolLinkState::Ready)) {
        return false;
    }
    std::unique_ptr<RpcRequest> request(new (std::nothrow) RpcRequest());
    if (request == nullptr) {
        return false;
    }
    request->generation = generation;
    request->length     = static_cast<uint16_t>(length);
    std::memcpy(request->json.data(), json, length);
    const ProtocolLinkState current_link_state = _link_state.load(std::memory_order_acquire);
    if (generation != _connection_generation.load(std::memory_order_acquire) || !connected() ||
        (current_link_state != ProtocolLinkState::Awaiting && current_link_state != ProtocolLinkState::Ready)) {
        return false;
    }
    RpcRequest* queued_request = request.get();
    if (xQueueSendToBack(_rpc_queue, &queued_request, 0) != pdTRUE) {
        ESP_LOGE(Tag, "RPC dispatch queue full length=%u", static_cast<unsigned>(length));
        return false;
    }
    request.release();
    xTaskNotifyGive(_input_task);
    return true;
}

void CodexMicroBle::handleRpc(const cJSON* request, uint32_t generation)
{
    if (!connected() || generation != _connection_generation.load(std::memory_order_acquire)) {
        return;
    }
    const cJSON* methodValue = objectItem(request, "method");
    const char* method       = cJSON_IsString(methodValue) ? methodValue->valuestring : "";
    const cJSON* id          = objectItem(request, "id");
    const cJSON* params      = objectItem(request, "params");
    ESP_LOGI(Tag, "RPC method=%s", method);

    if (std::strcmp(method, "sys.version") == 0) {
        cJSON* result = cJSON_CreateObject();
        if (result == nullptr) {
            ++_rpc_errors;
            return;
        }
        cJSON_AddStringToObject(result, "version", FirmwareVersion);
        if (sendResult(id, result, generation)) {
            markProtocolReady(generation);
        }
        return;
    }

    if (std::strcmp(method, "device.status") == 0) {
        CodexMicroState state = snapshot();
        cJSON* result         = cJSON_CreateObject();
        if (result == nullptr) {
            ++_rpc_errors;
            return;
        }
        cJSON_AddStringToObject(result, "version", FirmwareVersion);
        cJSON_AddNumberToObject(result, "profile_index", 0);
        cJSON_AddNumberToObject(result, "layer_index", 1);
        cJSON_AddNumberToObject(result, "battery", state.battery);
        cJSON_AddBoolToObject(result, "is_charging", state.charging);
        if (sendResult(id, result, generation)) {
            markProtocolReady(generation);
        }
        return;
    }

    if (std::strcmp(method, "v.oai.thstatus") == 0 && cJSON_IsArray(params)) {
        if (!updateThreadLighting(params, generation)) {
            return;
        }
        if (sendSuccess(id, generation)) {
            markProtocolReady(generation);
        }
        return;
    }

    if (std::strcmp(method, "v.oai.rgbcfg") == 0 && cJSON_IsObject(params)) {
        xSemaphoreTake(_state_mutex, portMAX_DELAY);
        const ProtocolLinkState link_state = _link_state.load(std::memory_order_acquire);
        if (!connected() || generation != _connection_generation.load(std::memory_order_acquire) ||
            (link_state != ProtocolLinkState::Awaiting && link_state != ProtocolLinkState::Ready)) {
            xSemaphoreGive(_state_mutex);
            return;
        }
        const CodexMicroLight previous_ambient = _state.ambient;
        const CodexMicroLight previous_keys    = _state.keys;
        updateLightingSide(_configured_ambient, objectItem(params, "ambient"));
        updateLightingSide(_configured_keys, objectItem(params, "keys"));
        if (_ambient_sync_thread < 0) {
            _state.ambient = _configured_ambient;
        }
        if (_keys_sync_thread < 0) {
            _state.keys = _configured_keys;
        }
        if (!sameLight(previous_ambient, _state.ambient) || !sameLight(previous_keys, _state.keys)) {
            ++_state.revision;
            ++_state.attentionRevision;
        }
        xSemaphoreGive(_state_mutex);
        if (sendSuccess(id, generation)) {
            markProtocolReady(generation);
        }
        return;
    }

    if (std::strcmp(method, "lights.preview") == 0 || std::strcmp(method, "host.focused_app") == 0) {
        if (sendSuccess(id, generation)) {
            markProtocolReady(generation);
        }
        return;
    }

    cJSON* response = cJSON_CreateObject();
    addResponseId(response, id);
    cJSON* error = cJSON_AddObjectToObject(response, "error");
    cJSON_AddNumberToObject(error, "code", -32601);
    cJSON_AddStringToObject(error, "message", "Method not found");
    sendJsonObject(response, generation);
}

bool CodexMicroBle::sendResult(const cJSON* id, cJSON* result, uint32_t generation)
{
    cJSON* response = cJSON_CreateObject();
    if (response == nullptr || result == nullptr) {
        cJSON_Delete(response);
        cJSON_Delete(result);
        return false;
    }
    addResponseId(response, id);
    cJSON_AddItemToObject(response, "result", result);
    return sendJsonObject(response, generation);
}

bool CodexMicroBle::sendSuccess(const cJSON* id, uint32_t generation)
{
    cJSON* result = cJSON_CreateObject();
    if (result == nullptr) {
        return false;
    }
    cJSON_AddBoolToObject(result, "ok", true);
    return sendResult(id, result, generation);
}

bool CodexMicroBle::updateThreadLighting(const cJSON* values, uint32_t generation)
{
    xSemaphoreTake(_state_mutex, portMAX_DELAY);
    const ProtocolLinkState link_state = _link_state.load(std::memory_order_acquire);
    if (!connected() || generation != _connection_generation.load(std::memory_order_acquire) ||
        (link_state != ProtocolLinkState::Awaiting && link_state != ProtocolLinkState::Ready)) {
        xSemaphoreGive(_state_mutex);
        return false;
    }
    const auto previous_threads            = _state.threads;
    const CodexMicroLight previous_ambient = _state.ambient;
    const CodexMicroLight previous_keys    = _state.keys;
    const cJSON* value                     = nullptr;
    cJSON_ArrayForEach(value, values)
    {
        const cJSON* idValue = objectItem(value, "id");
        if (!cJSON_IsNumber(idValue) || idValue->valueint < 0 ||
            idValue->valueint >= static_cast<int>(_state.threads.size())) {
            continue;
        }
        const int8_t thread    = static_cast<int8_t>(idValue->valueint);
        CodexMicroLight& light = _state.threads[thread];
        updateLightingSide(light, value);
        const cJSON* sync_keys = objectItem(value, "sk");
        if (sync_keys != nullptr) {
            if (jsonFlag(sync_keys)) {
                _keys_sync_thread = thread;
            } else if (_keys_sync_thread == thread) {
                _keys_sync_thread = -1;
            }
        }
        const cJSON* sync_ambient = objectItem(value, "sa");
        if (sync_ambient != nullptr) {
            if (jsonFlag(sync_ambient)) {
                _ambient_sync_thread = thread;
            } else if (_ambient_sync_thread == thread) {
                _ambient_sync_thread = -1;
            }
        }
    }
    if (_keys_sync_thread >= 0) {
        _state.keys       = _state.threads[_keys_sync_thread];
        _state.keys.magic = 0;
    } else {
        _state.keys = _configured_keys;
    }
    if (_ambient_sync_thread >= 0) {
        _state.ambient       = _state.threads[_ambient_sync_thread];
        _state.ambient.magic = 0;
    } else {
        _state.ambient = _configured_ambient;
    }
    bool changed = !sameLight(previous_ambient, _state.ambient) || !sameLight(previous_keys, _state.keys);
    for (std::size_t index = 0; index < _state.threads.size() && !changed; ++index) {
        changed = !sameLight(previous_threads[index], _state.threads[index]);
    }
    if (changed) {
        ++_state.revision;
        ++_state.attentionRevision;
    }
    xSemaphoreGive(_state_mutex);
    return true;
}

void CodexMicroBle::updateLightingSide(CodexMicroLight& side, const cJSON* value)
{
    if (!cJSON_IsObject(value)) {
        return;
    }
    const cJSON* color      = objectItem(value, "c");
    const cJSON* brightness = objectItem(value, "b");
    const cJSON* effect     = objectItem(value, "e");
    const cJSON* speed      = objectItem(value, "s");
    const cJSON* magic      = objectItem(value, "m");
    if (cJSON_IsNumber(color)) {
        side.color = static_cast<uint32_t>(color->valuedouble);
    }
    if (cJSON_IsNumber(brightness)) {
        side.brightness = static_cast<float>(brightness->valuedouble);
    }
    side.effect = parseLightEffect(effect, side.effect);
    if (cJSON_IsNumber(speed)) {
        side.speed = static_cast<float>(speed->valuedouble);
    }
    if (cJSON_IsNumber(magic)) {
        side.magic = static_cast<uint32_t>(magic->valuedouble);
    }
}
