#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <memory>
#include <new>

#include <esp_gap_ble_api.h>
#include <esp_gatt_defs.h>
#include <esp_gatts_api.h>
#include <esp_hid_common.h>
#include <esp_hidd_gatts.h>
#include <esp_log.h>

namespace {

constexpr const char* Tag = "CodexMicro-GATT";

struct CompatibilityState {
    esp_gatt_if_t pendingGattIf                                                         = ESP_GATT_IF_NONE;
    uint16_t pendingInputCccIndex                                                       = UINT16_MAX;
    esp_gatt_if_t hidGattIf                                                             = ESP_GATT_IF_NONE;
    uint16_t inputCccHandle                                                             = 0;
    uint16_t connectionId                                                               = 0;
    bool connected                                                                      = false;
    bool notificationsEnabled                                                           = false;
    std::array<std::array<uint8_t, ESP_BD_ADDR_LEN>, CONFIG_BT_SMP_MAX_BONDS> bootBonds = {};
    std::size_t bootBondCount                                                           = 0;
};

CompatibilityState State;

bool hasUuid16(const esp_attr_desc_t& attribute, uint16_t expected)
{
    if (attribute.uuid_length != ESP_UUID_LEN_16 || attribute.uuid_p == nullptr) {
        return false;
    }
    const uint16_t actual =
        static_cast<uint16_t>(attribute.uuid_p[0]) | (static_cast<uint16_t>(attribute.uuid_p[1]) << 8U);
    return actual == expected;
}

bool isWritableCharacteristic(const esp_gatts_attr_db_t& declaration)
{
    const esp_attr_desc_t& attribute = declaration.att_desc;
    if (!hasUuid16(attribute, ESP_GATT_UUID_CHAR_DECLARE) || attribute.value == nullptr || attribute.length < 1) {
        return false;
    }
    const uint8_t properties = attribute.value[0];
    return (properties & (ESP_GATT_CHAR_PROP_BIT_WRITE | ESP_GATT_CHAR_PROP_BIT_WRITE_NR)) != 0;
}

bool isNotifiableCharacteristic(const esp_gatts_attr_db_t& declaration)
{
    const esp_attr_desc_t& attribute = declaration.att_desc;
    if (!hasUuid16(attribute, ESP_GATT_UUID_CHAR_DECLARE) || attribute.value == nullptr || attribute.length < 1) {
        return false;
    }
    return (attribute.value[0] & ESP_GATT_CHAR_PROP_BIT_NOTIFY) != 0;
}

bool isReportReference(const esp_attr_desc_t& attribute, uint8_t report_id, uint8_t report_type)
{
    return hasUuid16(attribute, ESP_GATT_UUID_RPT_REF_DESCR) && attribute.value != nullptr && attribute.length >= 2 &&
           attribute.value[0] == report_id && attribute.value[1] == report_type;
}

bool wasBondedAtBoot(const uint8_t* address)
{
    if (address == nullptr) {
        return false;
    }
    for (std::size_t index = 0; index < State.bootBondCount; ++index) {
        if (std::memcmp(State.bootBonds[index].data(), address, ESP_BD_ADDR_LEN) == 0) {
            return true;
        }
    }
    return false;
}

}  // namespace

extern "C" esp_err_t __real_esp_ble_gatts_create_attr_tab(const esp_gatts_attr_db_t* gatts_attr_db,
                                                          esp_gatt_if_t gatts_if, uint16_t max_nb_attr,
                                                          uint8_t srvc_inst_id);

extern "C" void codex_micro_hid_gatt_compat_link_anchor()
{
}

extern "C" void codex_micro_hid_gatt_compat_snapshot_bonds()
{
    State.bootBondCount = 0;
    int device_count    = esp_ble_get_bond_device_num();
    if (device_count <= 0) {
        ESP_LOGI(Tag, "boot bond snapshot count=0");
        return;
    }
    device_count = std::min(device_count, static_cast<int>(State.bootBonds.size()));
    std::unique_ptr<esp_ble_bond_dev_t[]> devices(new (std::nothrow) esp_ble_bond_dev_t[device_count]);
    if (devices == nullptr || esp_ble_get_bond_device_list(&device_count, devices.get()) != ESP_OK) {
        ESP_LOGW(Tag, "unable to snapshot existing BLE bonds");
        return;
    }
    for (int index = 0; index < device_count; ++index) {
        std::memcpy(State.bootBonds[State.bootBondCount].data(), devices[index].bd_addr, ESP_BD_ADDR_LEN);
        ++State.bootBondCount;
    }
    ESP_LOGI(Tag, "boot bond snapshot count=%u", static_cast<unsigned>(State.bootBondCount));
}

extern "C" void codex_micro_hid_gatt_compat_gatts_event(esp_gatts_cb_event_t event, esp_gatt_if_t gatts_if,
                                                        esp_ble_gatts_cb_param_t* param)
{
    if (param == nullptr) {
        return;
    }
    if (event == ESP_GATTS_CREAT_ATTR_TAB_EVT && gatts_if == State.pendingGattIf) {
        if (param->add_attr_tab.status == ESP_GATT_OK && State.pendingInputCccIndex < param->add_attr_tab.num_handle &&
            param->add_attr_tab.handles != nullptr) {
            State.hidGattIf      = gatts_if;
            State.inputCccHandle = param->add_attr_tab.handles[State.pendingInputCccIndex];
            ESP_LOGI(Tag, "captured HID input CCC handle=%u", static_cast<unsigned>(State.inputCccHandle));
        } else {
            ESP_LOGE(Tag, "unable to capture HID input CCC handle status=%d",
                     static_cast<int>(param->add_attr_tab.status));
        }
        State.pendingGattIf        = ESP_GATT_IF_NONE;
        State.pendingInputCccIndex = UINT16_MAX;
        return;
    }
    if (gatts_if != State.hidGattIf || State.inputCccHandle == 0) {
        return;
    }
    if (event == ESP_GATTS_CONNECT_EVT) {
        State.connectionId         = param->connect.conn_id;
        State.connected            = true;
        State.notificationsEnabled = false;
    } else if (event == ESP_GATTS_DISCONNECT_EVT) {
        State.connected            = false;
        State.notificationsEnabled = false;
    } else if (event == ESP_GATTS_WRITE_EVT && param->write.handle == State.inputCccHandle &&
               param->write.value != nullptr && param->write.len >= 2) {
        State.notificationsEnabled = (param->write.value[0] & ESP_HID_CCC_NOTIFICATIONS_ENABLED) != 0;
    }
}

extern "C" void codex_micro_hid_gatt_compat_authenticated(const uint8_t* address)
{
    if (!State.connected || State.notificationsEnabled || State.hidGattIf == ESP_GATT_IF_NONE ||
        State.inputCccHandle == 0 || !wasBondedAtBoot(address)) {
        return;
    }

    // This is deliberately limited to bonds that existed before this boot. A
    // newly pairing client must perform its real encrypted CCCD write. Windows
    // restores existing subscriptions from its cache after a peripheral reboot,
    // while ESP-IDF 5.5.4 leaves the HID helper's in-memory CCC flag cleared.
    uint8_t ccc_value[2]                     = {ESP_HID_CCC_NOTIFICATIONS_ENABLED, 0};
    esp_ble_gatts_cb_param_t synthetic_write = {};
    synthetic_write.write.conn_id            = State.connectionId;
    synthetic_write.write.handle             = State.inputCccHandle;
    synthetic_write.write.len                = sizeof(ccc_value);
    synthetic_write.write.value              = ccc_value;
    esp_hidd_gatts_event_handler(ESP_GATTS_WRITE_EVT, State.hidGattIf, &synthetic_write);
    State.notificationsEnabled = true;
    ESP_LOGI(Tag, "restored authenticated HID input notifications");
}

extern "C" esp_err_t __wrap_esp_ble_gatts_create_attr_tab(const esp_gatts_attr_db_t* gatts_attr_db,
                                                          esp_gatt_if_t gatts_if, uint16_t max_nb_attr,
                                                          uint8_t srvc_inst_id)
{
    // node-hid passes the non-zero Report ID in its 64-byte SetReport buffer.
    // macOS forwards all 64 bytes to the BLE Output Report characteristic. The
    // ESP-IDF HID helper sizes that characteristic from the 63-byte report body,
    // so the ATT server rejects the otherwise valid write with error 0x0D
    // (Invalid Attribute Value Length). Keep the HID descriptor unchanged and
    // widen only the writable HID Report characteristic by the Report ID byte.
    if (gatts_attr_db != nullptr) {
        auto* mutable_db         = const_cast<esp_gatts_attr_db_t*>(gatts_attr_db);
        uint16_t input_ccc_index = UINT16_MAX;
        for (uint16_t index = 1; index < max_nb_attr; ++index) {
            esp_attr_desc_t& attribute = mutable_db[index].att_desc;
            if (hasUuid16(attribute, ESP_GATT_UUID_HID_REPORT) && isNotifiableCharacteristic(mutable_db[index - 1]) &&
                index + 2 < max_nb_attr &&
                hasUuid16(mutable_db[index + 1].att_desc, ESP_GATT_UUID_CHAR_CLIENT_CONFIG) &&
                isReportReference(mutable_db[index + 2].att_desc, 6, ESP_HID_REPORT_TYPE_INPUT)) {
                input_ccc_index = index + 1;
            }
            if (hasUuid16(attribute, ESP_GATT_UUID_HID_REPORT) && isWritableCharacteristic(mutable_db[index - 1]) &&
                attribute.max_length == 63) {
                attribute.max_length = 64;
                ESP_LOGI(Tag, "expanded HID output report characteristic to 64 bytes");
            }
        }
        if (input_ccc_index != UINT16_MAX) {
            State.pendingGattIf        = gatts_if;
            State.pendingInputCccIndex = input_ccc_index;
        }
    }

    return __real_esp_ble_gatts_create_attr_tab(gatts_attr_db, gatts_if, max_nb_attr, srvc_inst_id);
}
