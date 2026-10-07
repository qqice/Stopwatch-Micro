/*
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include "serial_debug_transport.h"
#include <sdkconfig.h>

class AppCodexMicro;

/** Non-blocking, line-oriented hardware diagnostics over USB Serial/JTAG. */
class SerialDebug {
public:
    explicit SerialDebug(AppCodexMicro& app);

    bool begin();
    void poll();
    void end();

private:
    enum class AsyncTest : uint8_t {
        None,
        Microphone,
        Inputs,
        UiCycle,
        Transport,
        Performance,
    };

    static constexpr std::size_t LineCapacity = 1536;

    void consume(char value);
    void consumeFrom(char value, bool uart);
    void drainUart();
    void invalidateUartInput();
    static int debugPrintf(const char* format, ...);
    static SerialDebug* _writer;
    bool _reply_uart = false;
    bool _async_uart = false;
    bool _usb_active = false;
    bool _uart_active = false;
    void* _uart_events = nullptr;
#if defined(MOSAICO_BOARD) && CONFIG_IDF_TARGET_ESP32S31
    static constexpr std::size_t UartLineCapacity = LineCapacity;
    static constexpr std::size_t UartTxCapacity = 8192;
#else
    // No UART storage penalty on the existing StopWatch/S3 path.
    static constexpr std::size_t UartLineCapacity = 1;
    static constexpr std::size_t UartTxCapacity = 1;
#endif
    std::array<char, UartLineCapacity> _uart_line{};
    std::size_t _uart_line_length = 0;
    bool _uart_line_overflow = false;
    serial_debug_transport::TxRing<UartTxCapacity> _uart_tx;
    uint32_t _format_overflow = 0;
    uint32_t _uart_read_errors = 0;
    void handleLine(char* line);
    void updateAsyncTest();
    void cancelAsyncTest(const char* reason, bool report);

    void printHelp();
    void printStatus();
    void runSelfTest();
    void printControls();
    void startMicrophoneTest(uint32_t durationMs);
    void updateMicrophoneTest(uint32_t now);
    void startInputTest(uint32_t durationMs);
    void updateInputTest(uint32_t now);
    void startUiCycle();
    void updateUiCycle(uint32_t now);
    void startTransportTest();
    void updateTransportTest(uint32_t now);
    void startPerformanceTest(uint32_t durationMs, bool generateTraffic);
    void updatePerformanceTest(uint32_t now);

    static uint32_t parseUnsigned(const char* value, uint32_t fallback, uint32_t minimum, uint32_t maximum);
    static void result(const char* command, const char* status, const char* details = nullptr);

    AppCodexMicro& _app;
    std::array<char, LineCapacity> _line = {};
    std::size_t _line_length             = 0;
    bool _line_overflow                  = false;
    bool _active                         = false;

    AsyncTest _async_test      = AsyncTest::None;
    uint32_t _test_started_ms  = 0;
    uint32_t _test_deadline_ms = 0;

    bool _meter_was_enabled      = false;
    float _microphone_peak       = 0.0f;
    uint32_t _microphone_samples = 0;

    bool _input_seen_a         = false;
    bool _input_seen_b         = false;
    bool _input_seen_touch     = false;
    bool _input_previous_a     = false;
    bool _input_previous_b     = false;
    bool _input_previous_touch = false;
    int16_t _input_min_x       = 32767;
    int16_t _input_min_y       = 32767;
    int16_t _input_max_x       = -1;
    int16_t _input_max_y       = -1;

    uint8_t _ui_cycle_stage         = 0;
    uint32_t _transport_tx_messages = 0;
    uint32_t _transport_tx_reports  = 0;
    uint32_t _transport_tx_failures = 0;

    uint32_t _performance_next_send_ms    = 0;
    uint32_t _performance_generated       = 0;
    uint32_t _performance_accepted        = 0;
    bool _performance_draining            = false;
    bool _performance_generate_traffic    = false;
    uint32_t _performance_loop_max_gap_us = 0;
    int64_t _performance_last_poll_us     = 0;
    uint32_t _performance_input_queued    = 0;
    uint32_t _performance_input_dropped   = 0;
    uint32_t _performance_input_processed = 0;
    uint32_t _performance_tx_messages     = 0;
    uint32_t _performance_tx_reports      = 0;
    uint32_t _performance_tx_failures     = 0;
};
