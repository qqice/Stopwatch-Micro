/*
 * SPDX-License-Identifier: MIT
 *
 * Codex Micro compatibility protocol adapted from imliubo/codex-micro-4-core2.
 * Copyright (c) 2026 imliubo.
 */
#pragma once

#include "codex_micro_protocol.h"

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <string>

#include <esp_event.h>
#include <esp_gap_ble_api.h>
#include <esp_gatts_api.h>
#include <esp_hidd.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/semphr.h>
#include <freertos/task.h>

struct cJSON;

struct CodexMicroLight {
    uint32_t color               = 0;
    float brightness             = 0.0f;
    CodexMicroLightEffect effect = CodexMicroLightEffect::Off;
    float speed                  = 0.0f;
    uint32_t magic               = 0;
};

struct CodexMicroState {
    std::array<CodexMicroLight, 6> threads = {};
    CodexMicroLight ambient;
    CodexMicroLight keys;
    bool ready                 = false;
    bool connected             = false;
    bool protocolReady         = false;
    uint8_t battery            = 100;
    bool charging              = false;
    uint32_t revision          = 0;
    uint32_t attentionRevision = 0;
};

struct CodexMicroBleDiagnostics {
    bool initialized               = false;
    bool hidReady                  = false;
    bool connected                 = false;
    bool protocolReady             = false;
    bool advertising               = false;
    uint32_t inputQueued           = 0;
    uint32_t inputDropped          = 0;
    uint32_t inputProcessed        = 0;
    uint32_t txMessages            = 0;
    uint32_t txReports             = 0;
    uint32_t txFailures            = 0;
    uint32_t rxReports             = 0;
    uint32_t rpcMessages           = 0;
    uint32_t rpcErrors             = 0;
    uint32_t wirelessUsageAccepted = 0;
    uint32_t wirelessUsageRejected = 0;
    uint32_t queuePending          = 0;
    uint32_t queueHighWater        = 0;
    uint32_t txMaxUs               = 0;
    uint32_t txTotalUs             = 0;
    uint32_t halfOpenRecoveries    = 0;
    int8_t inputTaskCore           = -1;
};

class CodexMicroBle {
public:
    bool begin();
    void poll();
    bool connected();
    CodexMicroState snapshot();
    CodexMicroBleDiagnostics diagnostics() const;
    void resetPerformanceDiagnostics();
    bool protocolSelfTest() const;
    void setBattery(uint8_t percentage, bool charging);
    bool resetPairing();
    void requestRadioIdle(bool requested);
    bool radioIdleRequested() const;

    bool sendKey(CodexMicroControl control, CodexMicroKeyAction action, int8_t agent = -1);
    bool sendJoystick(float angle, float distance);
    bool sendJoystickButton(float angle, bool pressed);
    bool sendEncoderSteps(int direction, uint16_t steps);

private:
    enum class ProtocolLinkState : uint8_t {
        Disconnected,
        Awaiting,
        Ready,
        Recovering,
    };

    static constexpr uint8_t ReportId                    = 6;
    static constexpr std::size_t ReportSize              = 63;
    static constexpr std::size_t PayloadSize             = 61;
    static constexpr std::size_t MaxRpcBufferSize        = 4096;
    static constexpr std::size_t NormalInputQueueDepth   = 5;
    static constexpr std::size_t CriticalInputQueueDepth = 16;
    static constexpr std::size_t EncoderControlReserve   = 3;
    static constexpr std::size_t RpcQueueDepth           = 4;
    static constexpr uint32_t RpcTransportWarmupMs       = 200;
    static constexpr uint32_t ProtocolHandshakeTimeoutMs = 25000;

    enum class InputEventKind : uint8_t {
        Key,
        Joystick,
    };

    struct InputEvent {
        InputEventKind kind        = InputEventKind::Key;
        CodexMicroControl control  = CodexMicroControl::Agent1;
        CodexMicroKeyAction action = CodexMicroKeyAction::Release;
        int8_t agent               = -1;
        uint16_t repeat            = 1;
        float angle                = 0.0f;
        float distance             = 0.0f;
        bool ordered               = false;
        uint32_t generation        = 0;
    };

    struct RpcRequest {
        std::array<char, MaxRpcBufferSize> json = {};
        uint32_t generation                     = 0;
        uint16_t length                         = 0;
    };

    static void gapEventCallback(esp_gap_ble_cb_event_t event, esp_ble_gap_cb_param_t* param);
    static void gattsEventCallback(esp_gatts_cb_event_t event, esp_gatt_if_t gattsIf, esp_ble_gatts_cb_param_t* param);
    static void hidEventCallback(void* handlerArgs, esp_event_base_t base, int32_t id, void* eventData);
    static void inputTaskEntry(void* context);

    bool initializeController();
    bool configureAdvertising();
    bool schedulePairingRestart();
    void maybeStartAdvertising();
    void serviceRadioIdle();
    void stopAdvertisingForIdle(uint32_t now);
    void disconnectForIdle(uint32_t now);
    void onConnected(bool connected);
    bool markProtocolReady(uint32_t generation);
    void recoverHalfOpenConnection();
    void onOutput(const uint8_t* data, std::size_t length);
    bool queueRpcRequest(const char* json, std::size_t length);
    void handleRpc(const cJSON* request, uint32_t generation);
    bool updateThreadLighting(const cJSON* values, uint32_t generation);
    void updateLightingSide(CodexMicroLight& side, const cJSON* value);
    void runInputTask();

    bool queueInput(const InputEvent& event);
    bool sendKeyNow(CodexMicroControl control, CodexMicroKeyAction action, int8_t agent, uint32_t generation);
    bool sendJoystickNow(float angle, float distance, uint32_t generation);
    bool sendJson(const char* json, uint32_t expectedGeneration = UINT32_MAX, bool allowAwaiting = false);
    bool sendJsonObject(cJSON* object, uint32_t expectedGeneration = UINT32_MAX);
    bool sendResult(const cJSON* id, cJSON* result, uint32_t generation);
    bool sendSuccess(const cJSON* id, uint32_t generation);
    void setReady(bool ready);

    esp_hidd_dev_t* _hid_device         = nullptr;
    SemaphoreHandle_t _state_mutex      = nullptr;
    SemaphoreHandle_t _tx_mutex         = nullptr;
    QueueHandle_t _input_queue          = nullptr;
    QueueHandle_t _critical_input_queue = nullptr;
    QueueHandle_t _joystick_queue       = nullptr;
    QueueHandle_t _rpc_queue            = nullptr;
    TaskHandle_t _input_task            = nullptr;
    CodexMicroState _state;
    std::array<uint8_t, ESP_BD_ADDR_LEN> _peer_address = {};
    CodexMicroLight _configured_ambient;
    CodexMicroLight _configured_keys;
    std::string _rpc_buffer;
    int8_t _ambient_sync_thread                                 = -1;
    int8_t _keys_sync_thread                                    = -1;
    std::atomic_bool _initialized                               = false;
    std::atomic_bool _adv_data_ready                            = false;
    std::atomic_bool _scan_rsp_ready                            = false;
    std::atomic_bool _hid_ready                                 = false;
    std::atomic_bool _connected                                 = false;
    std::atomic_bool _advertising                               = false;
    std::atomic_bool _radio_idle_requested                      = false;
    std::atomic_bool _radio_idle_stop_in_flight                 = false;
    std::atomic_bool _radio_idle_disconnect_in_flight           = false;
    std::atomic_bool _peer_address_valid                        = false;
    std::atomic<ProtocolLinkState> _link_state                  = ProtocolLinkState::Disconnected;
    std::atomic_bool _pairing_reset_requested                   = false;
    std::atomic_int _pairing_bonds_pending                      = 0;
    std::atomic_uint32_t _input_queued                          = 0;
    std::atomic_uint32_t _input_dropped                         = 0;
    std::atomic_uint32_t _input_processed                       = 0;
    std::atomic_uint32_t _tx_messages                           = 0;
    std::atomic_uint32_t _tx_reports                            = 0;
    std::atomic_uint32_t _tx_failures                           = 0;
    std::atomic_uint32_t _rx_reports                            = 0;
    std::atomic_uint32_t _rpc_messages                          = 0;
    std::atomic_uint32_t _rpc_errors                            = 0;
    std::atomic_uint32_t _wireless_usage_accepted               = 0;
    std::atomic_uint32_t _wireless_usage_rejected               = 0;
    std::atomic_uint32_t _queue_high_water                      = 0;
    std::atomic_uint32_t _tx_max_us                             = 0;
    std::atomic_uint32_t _tx_total_us                           = 0;
    std::atomic_uint32_t _connected_since_ms                    = 0;
    std::atomic_uint32_t _connection_generation                 = 0;
    std::atomic_uint32_t _half_open_recovery_deadline_ms        = 0;
    std::atomic_uint32_t _half_open_recoveries                  = 0;
    std::atomic_uint32_t _radio_idle_last_stop_attempt_ms       = 0;
    std::atomic_uint32_t _radio_idle_last_disconnect_attempt_ms = 0;
    std::atomic_int8_t _input_task_core                         = -1;
};

CodexMicroBle& GetCodexMicroBle();
