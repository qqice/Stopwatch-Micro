#include <apps/app_codex_micro/view/history_hit_test.h>
#include "boot_trace.h"
/*
 * SPDX-License-Identifier: MIT
 */
#include "serial_debug.h"
#ifdef MOSAICO_BOARD
#include <host/system_clock.h>
#include <host/mosaico_display_settings.h>
#include <host/standby_sleep.h>
#include <host/touch_sleep.h>
#include <hal/mosaico/usb_console.h>
#include <main_idle_wait.h>
#include <host/uart_fifo_recovery_model.h>
#include <host/mosaico_session_monitor.h>
#include <cJSON.h>
#include <mbedtls/base64.h>
#endif

#include <apps/app_codex_micro/app_codex_micro.h>
#include <apps/common/audio/audio.h>
#include <hal/ble/codex_micro_ble.h>
#include <hal/hal.h>
#include <host/host_bridge.h>
#ifdef MOSAICO_BOARD
#include <host/quota_monitor.h>
#include <ota/mosaico_ota.h>
#include <ota/panic_capture.h>
#include <apps/app_codex_micro/view/dot_widgets.h>
#endif
#include <host/network_quota.h>
#include <host/tailscale_transport.h>
#include <host/token_history.h>
#include <host/token_units_font.h>
#include <esp_bt.h>
extern "C" esp_err_t ml_noise_selftest(void);
extern "C" esp_err_t ml_derp_pin_selftest(void);
extern "C" esp_err_t ml_derp_patch_selftest(void);
#include <system_config.h>

#include <algorithm>
#include <cerrno>
#include <cmath>
#include <cstdio>
#include <cstdarg>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <unistd.h>

#include <esp_heap_caps.h>
#include <esp_system.h>
#include <esp_timer.h>
#include <driver/usb_serial_jtag.h>
#include <driver/usb_serial_jtag_vfs.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <lvgl.h>
#include <sdkconfig.h>
#if defined(MOSAICO_BOARD) && CONFIG_IDF_TARGET_ESP32S31
#include <driver/uart.h>
#endif

namespace {

#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT
bool twtCycleUart = false; // SerialDebug poll/parser only; never network-thread output.
#endif

constexpr uint32_t DefaultMicTestMs         = 2000;
constexpr uint32_t DefaultInputTestMs       = 15000;
constexpr uint32_t DefaultPerformanceTestMs = 3000;
constexpr uint32_t PerformanceSendPeriodMs  = 20;
constexpr uint32_t UiCycleStepMs            = 300;
constexpr uint32_t TransportWaitMs          = 500;
constexpr float MicrophoneSignalFloor       = 0.02f;

bool elapsed(uint32_t now, uint32_t deadline)
{
    return static_cast<int32_t>(now - deadline) >= 0;
}

const char* onOff(bool value)
{
    return value ? "1" : "0";
}

bool parseUnsignedStrict(const char* value, uint32_t minimum, uint32_t maximum, uint32_t& parsed)
{
    if (value == nullptr || value[0] == '\0') {
        return false;
    }
    for (const char* cursor = value; *cursor != '\0'; ++cursor) {
        if (*cursor < '0' || *cursor > '9') {
            return false;
        }
    }
    errno                      = 0;
    char* end                  = nullptr;
    const unsigned long number = std::strtoul(value, &end, 10);
    if (errno != 0 || end == value || *end != '\0' || number < minimum || number > maximum) {
        return false;
    }
    parsed = static_cast<uint32_t>(number);
    return true;
}


#ifdef MOSAICO_BOARD
// Local physical console trust only: base64/CRC are not encryption or authentication.
bool settingsDecode(const char* encoded, unsigned char* bytes, size_t capacity, size_t& length)
{
    if (!encoded || !*encoded || std::strlen(encoded)>768) return false;
    for (const char* p=encoded; *p; ++p)
        if (!((*p>='A'&&*p<='Z')||(*p>='a'&&*p<='z')||(*p>='0'&&*p<='9')||*p=='+'||*p=='/'||*p=='=')) return false;
    if (mbedtls_base64_decode(bytes,capacity-1,&length,
        reinterpret_cast<const unsigned char*>(encoded),std::strlen(encoded)) || !length) return false;
    bytes[length]=0;
    return !std::memchr(bytes,0,length);
}
bool settingsFlatJson(const unsigned char* bytes, size_t length)
{
    bool quoted=false, escaped=false; unsigned depth=0;
    for(size_t i=0;i<length;++i) {
        const unsigned char c=bytes[i];
        if(quoted) {
            if(escaped) escaped=false;
            else if(c=='\\') escaped=true;
            else if(c=='"') quoted=false;
        } else {
            if(c=='"') quoted=true;
            else if(c=='[' || c==']') return false;
            else if(c=='{') { if(++depth!=1) return false; }
            else if(c=='}') { if(depth!=1) return false; --depth; }
        }
    }
    return !quoted && !depth;
}
void settingsScrubJson(cJSON* node)
{
    for(;node;node=node->next) {
        if(node->valuestring) MosaicoWifi::scrub(node->valuestring,std::strlen(node->valuestring));
        if(node->string) MosaicoWifi::scrub(node->string,std::strlen(node->string));
        settingsScrubJson(node->child);
    }
}
bool settingsWifiSave(const char* encoded)
{
    unsigned char decoded[577]{}; size_t length=0;
    char ssid[33]{},password[65]{};
    cJSON* root=nullptr; bool ok=false;
    if (settingsDecode(encoded,decoded,sizeof(decoded),length) && settingsFlatJson(decoded,length) &&
        !std::strstr(reinterpret_cast<char*>(decoded),"\\u0000")) {
        const char* end=nullptr;
        root=cJSON_ParseWithLengthOpts(reinterpret_cast<char*>(decoded),length+1,&end,true);
        const cJSON* s=cJSON_GetObjectItemCaseSensitive(root,"ssid");
        const cJSON* p=cJSON_GetObjectItemCaseSensitive(root,"password");
        if(cJSON_IsObject(root) && cJSON_GetArraySize(root)==2 && cJSON_IsString(s) && cJSON_IsString(p) &&
            s->valuestring && p->valuestring && std::strlen(s->valuestring)<sizeof(ssid) &&
            std::strlen(p->valuestring)<sizeof(password)) {
            std::strcpy(ssid,s->valuestring); std::strcpy(password,p->valuestring);
            if(MosaicoWifi::validCredentials(ssid,password)) ok=GetNetworkQuota().requestWifiCredentials(ssid,password);
        }
    }
    // cJSON owns additional decoded copies; wipe every string, including rejected keys.
    settingsScrubJson(root);
    cJSON_Delete(root);
    MosaicoWifi::scrub(decoded,sizeof(decoded)); MosaicoWifi::scrub(password,sizeof(password));
    MosaicoWifi::scrub(ssid,sizeof(ssid)); return ok;
}
void settingsBase64(const char* source, char (&encoded)[89])
{
    size_t length=0;
    if(mbedtls_base64_encode(reinterpret_cast<unsigned char*>(encoded),sizeof(encoded)-1,&length,
        reinterpret_cast<const unsigned char*>(source),std::strlen(source))) encoded[0]=0;
    else encoded[length]=0;
}
#endif

}  // namespace

SerialDebug* SerialDebug::_writer = nullptr;
int SerialDebug::debugPrintf(const char* format, ...)
{
    char bytes[1536];
    va_list args; va_start(args, format);
    const int count = std::vsnprintf(bytes, sizeof(bytes), format, args);
    va_end(args);
    if (count < 0) return count;
    if (static_cast<std::size_t>(count) >= sizeof(bytes)) {
        if (_writer) ++_writer->_format_overflow;
        return -1; // Do not emit a misleading truncated result or secret fragment.
    }
    if (_writer && _writer->_reply_uart) {
        return _writer->_uart_tx.enqueue(bytes, count) ? count : -1;
    }
    return static_cast<int>(std::fwrite(bytes, 1, count, stdout));
}

SerialDebug::SerialDebug(AppCodexMicro& app) : _app(app)
{
}

bool SerialDebug::begin()
{
#ifdef MOSAICO_BOARD
#if CONFIG_IDF_TARGET_ESP32S31
    if (!uart_is_driver_installed(UART_NUM_0)) {
        uart_config_t config{};
        QueueHandle_t events = nullptr;
        config.baud_rate = 115200;
        config.data_bits = UART_DATA_8_BITS;
        config.parity = UART_PARITY_DISABLE;
        config.stop_bits = UART_STOP_BITS_1;
        config.flow_ctrl = UART_HW_FLOWCTRL_DISABLE;
        config.source_clk = UART_SCLK_XTAL; // Independent of CPU 160/320 MHz changes.
        if (uart_param_config(UART_NUM_0, &config) == ESP_OK &&
            uart_set_pin(UART_NUM_0, 58, 59, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE) == ESP_OK &&
            uart_driver_install(UART_NUM_0, 2048, 0, serial_debug_transport::UartEventCapacity, &events, 0) == ESP_OK) {
            _uart_active = true;
            _uart_events = events;
        }
    } // Do not reconfigure a UART already owned by another subsystem.
    bool recoveryRxReady=_uart_active;
    int32_t recoveryRxError=0;
#if CONFIG_MOSAICO_UART_FIFO_RECOVERY
    // Only configure the freshly installed, exclusively owned UART0 driver.
    if(recoveryRxReady) {
        recoveryRxError=uart_set_rx_full_threshold(UART_NUM_0,UartFifoRecovery::FullThreshold);
        recoveryRxReady=recoveryRxError==ESP_OK;
    }
#endif
    MainIdleWait::uartRxConfigured(recoveryRxReady,recoveryRxError);
    StandbySleep::uartReady(recoveryRxReady);
    MainIdleWait::uartOwner(recoveryRxReady);
#endif
    // TinyUSB console/VFS is initialized before board bring-up in app_main.
    setvbuf(stdin, nullptr, _IONBF, 0);
    setvbuf(stdout, nullptr, _IONBF, 0);
    const int flags = fcntl(STDIN_FILENO, F_GETFL, 0);
    if (flags < 0 || fcntl(STDIN_FILENO, F_SETFL, flags | O_NONBLOCK) < 0) {
        result("init", "FAIL", "reason=nonblocking_cdc_stdin");
        _active = _uart_active;
        return _active;
    }
    _usb_active = true;
    _active = true;
    debugPrintf("DBG READY version=%s transport=tinyusb-cdc mode=nonblocking\r\n", system_config::FirmwareVersion);
    if (!_writer || !_writer->_reply_uart) std::fflush(stdout);
    return true;
#elif !CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG
    result("init", "FAIL", "reason=usb_serial_jtag_not_primary");
    return false;
#else
    if (!usb_serial_jtag_is_driver_installed()) {
        usb_serial_jtag_driver_config_t config = {
            .tx_buffer_size = 2048,
            .rx_buffer_size = 2048,
        };
        const esp_err_t error = usb_serial_jtag_driver_install(&config);
        if (error != ESP_OK) {
            result("init", "FAIL", "reason=usb_serial_jtag_driver_install");
            return false;
        }
    }
    // The default VFS path polls hardware registers and proved unreliable for
    // host-to-device traffic while BLE/LVGL were active. The official
    // interrupt-driven driver buffers RX independently of the UI loop.
    usb_serial_jtag_vfs_use_driver();
    const int flags = fcntl(STDIN_FILENO, F_GETFL, 0);
    if (flags < 0 || fcntl(STDIN_FILENO, F_SETFL, flags | O_NONBLOCK) < 0) {
        result("init", "FAIL", "reason=nonblocking_stdin");
        return false;
    }
    _usb_active = true;
    _active = true;
    debugPrintf("DBG READY version=%s transport=usb-serial-jtag mode=nonblocking\r\n", system_config::FirmwareVersion);
    if (!_writer || !_writer->_reply_uart) std::fflush(stdout);
    return true;
#endif
}

void SerialDebug::end()
{
    if (!_active) {
        return;
    }
    cancelAsyncTest("shutdown", false);
#if defined(MOSAICO_BOARD) && CONFIG_IDF_TARGET_ESP32S31
    StandbySleep::cancelForActivity(); StandbySleep::uartReady(false);
    TouchSleep::off();
    MainIdleWait::uartOwner(false);
    MainIdleWait::uartRxConfigured(false,0);
    MainIdleWait::uartRecoveryConfigured(false,0);
    MainIdleWait::serialState(true,false);
    if (_uart_active) uart_driver_delete(UART_NUM_0);
#endif
    _uart_active = _usb_active = _active = false;
    _uart_events = nullptr;
    _line.fill(0); _uart_line.fill(0); _uart_tx.clear();
    _line_length = _uart_line_length = 0;
    _line_overflow = _uart_line_overflow = false;
    _async_uart = false;
}

void SerialDebug::poll()
{
    if (!_active) {
        return;
    }

    drainUart();
    std::array<char, 128> bytes{};
    // One read per source and fixed RX/TX budgets: a flood cannot starve its peer.
    if (_usb_active) {
        const ssize_t count = read(STDIN_FILENO, bytes.data(), bytes.size());
        for (ssize_t i = 0; i < count; ++i) consumeFrom(bytes[i], false);
        if (count < 0 && errno != EAGAIN && errno != EWOULDBLOCK)
            result("read", "FAIL", "reason=stdin_error");
    }
#if defined(MOSAICO_BOARD) && CONFIG_IDF_TARGET_ESP32S31
    if (_uart_active) {
        uart_event_t event{};
        auto saturated = [this]() {
            const auto depth=uxQueueMessagesWaiting(static_cast<QueueHandle_t>(_uart_events));
            MainIdleWait::uartEvent(false,false,depth);
            return serial_debug_transport::uartQueueSaturated(depth);
        };
        // A full event queue may have lost an error notification. Desynchronize,
        // rather than trusting a subsequently observed UART_DATA event.
        if (saturated()) invalidateUartInput();
        for (unsigned i = 0; i < serial_debug_transport::UartEventCapacity; ++i) {
            if (saturated()) { invalidateUartInput(); break; }
            if (!xQueueReceive(static_cast<QueueHandle_t>(_uart_events), &event, 0)) break;
            MainIdleWait::uartEvent(event.type==UART_WAKEUP,event.type==UART_DATA,0);
            if (event.type == UART_WAKEUP) { StandbySleep::uartWake(); MainIdleWait::serialState(true,true); }
            if (event.type == UART_FIFO_OVF || event.type == UART_BUFFER_FULL ||
                event.type == UART_FRAME_ERR || event.type == UART_PARITY_ERR) {
                invalidateUartInput();
                break;
            }
        }
        if (saturated()) invalidateUartInput();
        const int count = uart_read_bytes(UART_NUM_0, bytes.data(), bytes.size(), 0);
        if (count < 0 || saturated()) {
            invalidateUartInput();
            bytes.fill(0);
        } else {
            StandbySleep::uartTraffic(_uart_line_length || _uart_line_overflow || count>0 || _uart_tx.pending(),count>0);
            if(count>0)MainIdleWait::serialState(true,true);
            for (int i = 0; i < count; ++i) {
                if (saturated()) { invalidateUartInput(); bytes.fill(0); break; }
                consumeFrom(bytes[i], true);
            }
        }
    }
#endif
    SerialDebug* previous = _writer;
    _writer = this; _reply_uart = _async_uart;
#ifdef MOSAICO_BOARD
    if(_touch_trial_observing) {
        const auto trial=TouchSleep::snapshot();
        const auto phase=trial.model.phase;
        if(trial.model.revision!=_touch_trial_revision && (phase==TouchSleep::Phase::SleepRequested || phase==TouchSleep::Phase::Complete ||
            phase==TouchSleep::Phase::HeldClosed || phase==TouchSleep::Phase::FailedRestored || phase==TouchSleep::Phase::RebootPending)) {
            _touch_trial_revision=trial.model.revision;
            _reply_uart=_touch_trial_uart;
            char details[1000]{};TouchSleep::status(details,sizeof(details));
            result("touch-sleep",trial.model.failed?"FAIL":phase==TouchSleep::Phase::HeldClosed?"OBSERVE":"PASS",details);
            if(phase!=TouchSleep::Phase::SleepRequested)_touch_trial_observing=false;
            _reply_uart=_async_uart;
        }
    }
#endif
    updateAsyncTest();
    if (_async_test == AsyncTest::None) _async_uart = false;
#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT
    MosaicoTwt::Cycle cycle;
    if (GetNetworkQuota().popTwtCycle(cycle)) {
        _reply_uart = twtCycleUart;
        debugPrintf("DBG TWT_CYCLE phase=%u device_us=%lld trial_id=%u cycle_seq=%lu associated=%d fetch_flags=%u reason=%u dropped=%lu\r\n",
            unsigned(cycle.phase), static_cast<long long>(cycle.deviceUs), unsigned(cycle.trialId),
            static_cast<unsigned long>(cycle.cycleSeq), cycle.associated, unsigned(cycle.fetchFlags),
            unsigned(cycle.reason), static_cast<unsigned long>(cycle.dropped));
    }
#endif
    _reply_uart = false; _writer = previous;
    drainUart();
#if defined(MOSAICO_BOARD) && CONFIG_IDF_TARGET_ESP32S31
    if(_uart_active && (StandbySleep::monitoring() || MainIdleWait::snapshot().supported)) {
        size_t buffered=0;
        const bool rxUnknown=uart_get_buffered_data_len(UART_NUM_0,&buffered)!=ESP_OK;
        const bool txPending=uart_wait_tx_done(UART_NUM_0,0)!=ESP_OK; // Zero-tick query, no waits.
        const bool pending=rxUnknown || buffered || txPending || _uart_tx.pending() || _uart_line_length || _uart_line_overflow;
        StandbySleep::uartTraffic(pending,false);
        MainIdleWait::serialState(pending || _line_length || _line_overflow || _async_test!=AsyncTest::None,false);
    }
#endif
}

void SerialDebug::drainUart()
{
#if defined(MOSAICO_BOARD) && CONFIG_IDF_TARGET_ESP32S31
    if (_uart_active) _uart_tx.drain([](const char* bytes, std::size_t count) {
        return uart_tx_chars(UART_NUM_0, bytes, count);
    }, 128);
#endif
}

void SerialDebug::invalidateUartInput()
{
#if defined(MOSAICO_BOARD) && CONFIG_IDF_TARGET_ESP32S31
    ++_uart_read_errors;
    uart_flush_input(UART_NUM_0);
    xQueueReset(static_cast<QueueHandle_t>(_uart_events));
    _uart_line.fill(0); _uart_line_length = 0;
    _uart_line_overflow = true; // Drop through a fresh delimiter, never execute a damaged tail.
#endif
}

void SerialDebug::consumeFrom(char value, bool uart)
{
    SerialDebug* previous = _writer;
    _writer = this; _reply_uart = uart;
    const auto before = _async_test;
    consume(value);
    if (before == AsyncTest::None && _async_test != AsyncTest::None) _async_uart = uart;
    if (_async_test == AsyncTest::None) _async_uart = false;
    _reply_uart = false; _writer = previous;
}

void SerialDebug::consume(char value)
{
    char* line = _reply_uart ? _uart_line.data() : _line.data();
    const auto capacity = _reply_uart ? _uart_line.size() : _line.size();
    auto& length = _reply_uart ? _uart_line_length : _line_length;
    auto& overflow = _reply_uart ? _uart_line_overflow : _line_overflow;
    if (value == '\r' || value == '\n') {
        if (length == 0 && !overflow) {
            return;
        }
        if (overflow) {
            result("parse", "FAIL", "reason=line_too_long");
        } else {
            line[length] = '\0';
            char* command = _reply_uart ? serial_debug_transport::decodeUartLine(line, length) : line;
            if (command) handleLine(command);
            else result("parse", "FAIL", "reason=uart_crc_frame_required no_changes=1");
        }
        std::fill_n(line, capacity, 0); // Scrub credentials and completed command text.
        length   = 0;
        overflow = false;
        return;
    }
    if (_reply_uart && (value < 0x20 || value > 0x7E)) {
        overflow = true;
        return;
    }
    if (value == '\b' || value == 0x7F) {
        if (length > 0) {
            line[--length] = '\0';
        }
        return;
    }
    if (value < 0x20 || value > 0x7E) {
        return;
    }
    if (length + 1 >= capacity) {
        overflow = true;
        return;
    }
    line[length++] = value;
}

void SerialDebug::handleLine(char* line)
{
    char* save = nullptr;
    char* root = ::strtok_r(line, " \t", &save);
    if (root == nullptr) {
        return;
    }
    if (std::strcmp(root, "debug") != 0 && std::strcmp(root, "dbg") != 0) {
        result("parse", "FAIL", "reason=expected_debug_prefix");
        return;
    }

    char* command = ::strtok_r(nullptr, " \t", &save);
    if (_reply_uart && !serial_debug_transport::uartAllowed(command)) {
        result(command, "FAIL", "reason=uart_requires_extended_authorization no_changes=1"); return;
    }
    if (_reply_uart && command && !std::strcmp(command, "display-test-frequency")) {
        char value[8]{};
        if (!save || std::sscanf(save, "%7s", value) != 1 || (std::strcmp(value, "160") && std::strcmp(value, "320"))) {
            result(command, "FAIL", "reason=uart_frequency_allowed_160_320_only no_changes=1"); return;
        }
    }
    if (command && !std::strcmp(command, "debug-transport")) {
        char details[256];
        std::snprintf(details, sizeof(details), "usb=%d uart=%d uart_rx_errors=%lu tx_pending=%u tx_overflow=%lu tx_stalls=%lu format_overflow=%lu rx_budget=128 tx_budget=256 uart_usb_power_proof=0",
            _usb_active, _uart_active, static_cast<unsigned long>(_uart_read_errors), unsigned(_uart_tx.pending()),
            static_cast<unsigned long>(_uart_tx.overflow), static_cast<unsigned long>(_uart_tx.stalls), static_cast<unsigned long>(_format_overflow));
        result(command, "PASS", details); return;
    }
    if (_async_test != AsyncTest::None && command && (!std::strcmp(command, "mic") || !std::strcmp(command, "inputs") ||
        !std::strcmp(command, "ui") || !std::strcmp(command, "transport") || !std::strcmp(command, "perf") || !std::strcmp(command, "selftest"))) {
        result(command, "FAIL", "reason=async_diagnostic_active cancel_first=1"); return;
    }

#ifdef MOSAICO_BOARD
    if(command && !std::strcmp(command,"sleep-io")) {
        if(::strtok_r(nullptr," \t",&save)) {result(command,"FAIL","reason=report_only_no_arguments no_changes=1");return;}
        const auto s=GetHAL().sleepIoRetentionInfo();char pins[512]{},normal[448]{};size_t used=0,normalUsed=0;
        for(const auto& pin:s.pins) if(pin.pin>=0) {
            const int count=std::snprintf(pins+used,sizeof(pins)-used,"%s%d:%u:%d>%d>%d:%ld/%ld/%ld/%ld",used?",":"",
                pin.pin,pin.signal,pin.before,pin.after,pin.current,static_cast<long>(pin.beforeRc),static_cast<long>(pin.applyRc),
                static_cast<long>(pin.afterRc),static_cast<long>(pin.currentRc));
            if(count<0 || static_cast<size_t>(count)>=sizeof(pins)-used) {result(command,"FAIL","reason=diagnostic_capacity");return;}
            used+=static_cast<size_t>(count);
            const int n=std::snprintf(normal+normalUsed,sizeof(normal)-normalUsed,"%s%d:%010llx/%010llx/%010llx",normalUsed?",":"",
                pin.pin,static_cast<unsigned long long>(pin.beforeNormal),static_cast<unsigned long long>(pin.afterNormal),
                static_cast<unsigned long long>(pin.currentNormal));
            if(n<0 || static_cast<size_t>(n)>=sizeof(normal)-normalUsed) {result(command,"FAIL","reason=diagnostic_capacity");return;}
            normalUsed+=static_cast<size_t>(n);
        }
        char details[1492]{};
        static_assert(sizeof(details)+42<=1536,"sleep-io reply including DBG prefix stays bounded");
        const int count=std::snprintf(details,sizeof(details),
            "enabled=%d ready=%d schema_error=%d error=%ld target=%016llx unused=%016llx rail=%016llx panel=%016llx before_read=%016llx before_slp_sel=%016llx after_read=%016llx after_slp_sel=%016llx apply_attempted=%016llx apply_rc_error=%016llx apply_ok=%016llx failed=%016llx unsafe=%016llx normal_cfg_same=%016llx current_read=%016llx current_slp_sel=%016llx current_normal_cfg_same=%016llx current_failed=%016llx pin_format=pin:sig:before-after-current:read-apply-after-current_rc pins=%s n_bits=f0-7,s8-23,d24-31,ie32,oe33,pc34,iv35,od36,pu37,pd38 n_valid=read_masks n=%s keep_normal_only=1 gpio60_not_toggled=1 level_readback=not_claimed",
            s.enabled,s.ready,s.schemaError,static_cast<long>(s.error),
            static_cast<unsigned long long>(s.target),static_cast<unsigned long long>(s.unused),static_cast<unsigned long long>(s.rail),static_cast<unsigned long long>(s.panel),
            static_cast<unsigned long long>(s.beforeRead),static_cast<unsigned long long>(s.beforeSelected),static_cast<unsigned long long>(s.afterRead),static_cast<unsigned long long>(s.afterSelected),
            static_cast<unsigned long long>(s.attempted),static_cast<unsigned long long>(s.applyErrors),static_cast<unsigned long long>(s.applied),static_cast<unsigned long long>(s.failed),static_cast<unsigned long long>(s.unsafe),static_cast<unsigned long long>(s.normalSame),
            static_cast<unsigned long long>(s.currentRead),static_cast<unsigned long long>(s.currentSelected),static_cast<unsigned long long>(s.currentNormalSame),static_cast<unsigned long long>(s.currentFailed),pins,normal);
        if(count<0 || static_cast<size_t>(count)>=sizeof(details)) {result(command,"FAIL","reason=diagnostic_capacity");return;}
        result(command,s.error?"FAIL":"PASS",details);return;
    }
    if (command && !std::strcmp(command,"touch-sleep")) {
        const char* action=::strtok_r(nullptr," \t",&save);
        if(!action || (std::strcmp(action,"120") && std::strcmp(action,"off") && std::strcmp(action,"status")) || ::strtok_r(nullptr," \t",&save)) {
            result(command,"FAIL","expected=120_off_status no_changes=1");return;
        }
        if(!std::strcmp(action,"120")) {
            const auto sleep=StandbySleep::snapshot();
            if(!TouchSleep::request120(_app.debugDisplayLocked(),_app.debugUiReady() && sleep.eligible,_async_test!=AsyncTest::None)) {
                result(command,"BUSY","reason=unsupported_consumed_or_safety_gate no_changes=1");return;
            }
        } else if(!std::strcmp(action,"off"))TouchSleep::off();
        const bool wait=std::strcmp(action,"status") && TouchSleep::active();
        if(wait) {_touch_trial_uart=_reply_uart;_touch_trial_observing=true;_touch_trial_revision=TouchSleep::snapshot().model.revision;}
        char details[1000]{};TouchSleep::status(details,sizeof(details));
        result(command,wait?"RUNNING":"PASS",details);return;
    }
    if(TouchSleep::active() && command && (!std::strcmp(command,"ota-check") || !std::strcmp(command,"ota-download") ||
        !std::strcmp(command,"ota-install") || !std::strcmp(command,"ota-update") || !std::strcmp(command,"ota-bypass") ||
        !std::strcmp(command,"ota-reboot") || !std::strcmp(command,"ota-rollback-test"))) {
        TouchSleep::off();result(command,"BUSY","reason=touch_sleep_restore_required no_changes=1");return;
    }
    if(TouchSleep::active() && command && (!std::strcmp(command,"mic") || !std::strcmp(command,"inputs") || !std::strcmp(command,"ui") ||
        !std::strcmp(command,"transport") || !std::strcmp(command,"perf") || !std::strcmp(command,"trace") || !std::strcmp(command,"selftest"))) {
        result(command,"BUSY","reason=touch_sleep_lease no_changes=1");return;
    }
    if (command && !std::strcmp(command,"idle-wait")) {
        const char* action=::strtok_r(nullptr," \t",&save);
        if(!action || (std::strcmp(action,"on") && std::strcmp(action,"off") && std::strcmp(action,"status")) ||
            ::strtok_r(nullptr," \t",&save)) {result(command,"FAIL","expected=on_off_status no_changes=1");return;}
        if(std::strcmp(action,"status") && !MainIdleWait::setEnabled(!std::strcmp(action,"on"))) {
            result(command,"FAIL","reason=unsupported_or_wake_not_ready no_changes=1");return;
        }
        const auto s=MainIdleWait::snapshot();char details[1400]{};
        const int count=std::snprintf(details,sizeof(details),
            "supported=%d enabled=%d gpio_ready=%d uart_ready=%d gpio_wakes=%lu uart_wakes=%lu event_waits=%lu requested_ms=%lu actual_wait_max_us=%lu fallback=%s error=%ld ram_only=1 notification_index=1 usb_mounted_power_proof=0 requested_wake_mode=%ld applied_wake_mode=%ld wake_threshold=%lu rxfull_threshold=%lu rx_ready=%d recovery_ready=%d recovery_error=%ld queue_capacity=%lu queue_peak=%lu wake_events=%lu data_events=%lu read_notifies=%lu error_notifies=%lu last_wake_us32=%lu last_data_us32=%lu last_read_us32=%lu last_wait_notify=%lu last_wait_us=%lu last_wait_ms=%lu last_wait_cause=%s max_wait_ms=%lu max_wait_cause=%s clock_policy_requested=%s clock_gate_readback=0",
            s.supported,s.enabled,s.gpio,s.uart,static_cast<unsigned long>(s.gpioWakes),static_cast<unsigned long>(s.uartWakes),
            static_cast<unsigned long>(s.eventWaits),static_cast<unsigned long>(s.requestedMs),static_cast<unsigned long>(s.maxWaitUs),
            MainIdleWait::causeName(s.cause),static_cast<long>(s.error),static_cast<long>(s.requestedWakeMode),static_cast<long>(s.appliedWakeMode),
            static_cast<unsigned long>(s.wakeThreshold),static_cast<unsigned long>(s.rxFullThreshold),s.rxReady,s.recoveryReady,static_cast<long>(s.recoveryError),
            static_cast<unsigned long>(s.queueCapacity),static_cast<unsigned long>(s.queuePeak),static_cast<unsigned long>(s.wakeEvents),static_cast<unsigned long>(s.dataEvents),
            static_cast<unsigned long>(s.readNotifies),static_cast<unsigned long>(s.errorNotifies),static_cast<unsigned long>(s.lastWakeUs),static_cast<unsigned long>(s.lastDataUs),
            static_cast<unsigned long>(s.lastReadUs),static_cast<unsigned long>(s.lastWaitNotify),static_cast<unsigned long>(s.lastWaitUs),static_cast<unsigned long>(s.lastWaitMs),
            MainIdleWait::causeName(s.lastWaitCause),static_cast<unsigned long>(s.maxWaitMs),MainIdleWait::causeName(s.maxWaitCause),
            s.requestedWakeMode==1 ? "sdk_xtal_on_uart0_iomux_ungate" : "active_thresh");
        if(count<0 || static_cast<size_t>(count)>=sizeof(details)) {result(command,"FAIL","reason=diagnostic_capacity");return;}
        result(command,"PASS",details);return;
    }
    if (command && !std::strcmp(command,"standby-sleep")) {
        const char* action=::strtok_r(nullptr," \t",&save);
        if(!action) { result(command,"FAIL","expected=on_lease30..300_or_auto_CONFIRM_or_off_or_status no_changes=1");return; }
        if(!std::strcmp(action,"on")) {
            const char* lease=::strtok_r(nullptr," \t",&save);uint32_t seconds=180;
            const auto power=GetNetworkQuota().powerStats();const auto ble=GetCodexMicroBle().diagnostics();
            const bool safe=_app.debugDisplayLocked() && power.cpuMHz==160 && !power.wifiRunning && !power.clockError &&
                !ble.advertising && !ble.connected && !MosaicoOta::busy() && !MosaicoOta::healthPending();
            const bool valid=(!lease || (serial_debug_transport::settingsInteger(lease,300,seconds) && seconds>=30)) &&
                !::strtok_r(nullptr," \t",&save);
            const bool ok=valid && safe && StandbySleep::request(seconds);
            result(command,ok?"PASS":"FAIL",ok?"lease_ram_only=1 automatic_policy=0 finite_trial=1 requires_uart_recovery=1":"reason=range_profile_or_safety_gate no_changes=1");return;
        }
        if(!std::strcmp(action,"auto")) {
            const char* confirm=::strtok_r(nullptr," \t",&save);
            const bool valid=confirm && !std::strcmp(confirm,"CONFIRM") && !::strtok_r(nullptr," \t",&save);
            const bool ok=valid && StandbySleep::enableAutomatic(true);
            result(command,ok?"PASS":"FAIL",ok?"automatic_policy=1 finite_lease=0 ram_only=1 strict_runtime_gates=1":"reason=CONFIRM_profile_or_uart_required no_changes=1");return;
        }
        if(std::strcmp(action,"off") && std::strcmp(action,"status")) { result(command,"FAIL","reason=unknown_action no_changes=1");return; }
        if(::strtok_r(nullptr," \t",&save)) {result(command,"FAIL","reason=extra_arguments no_changes=1");return;}
        if(!std::strcmp(action,"off")) { StandbySleep::off();GetNetworkQuota().serviceStandbySleep(); }
        const auto s=StandbySleep::snapshot();char details[640]{};
        std::snprintf(details,sizeof(details),
            "supported=%d automatic_policy=%d policy_mode=%s active_mode=%s lease=%d remaining_s=%lu configured_ls=%d eligible=%d uart_ready=%d uart_blocked=%d successful_sleeps=%llu rejected_sleeps=%llu pm_counts_valid=%d framework_positive_intervals=%lu framework_interval_us_including_overhead=%llu no_ls_locks_created=%lu no_ls_locks_acquired=%lu error=%ld ram_only=1",
            s.supported,s.automaticPolicy,StandbySleep::modeName(s.policyMode),StandbySleep::modeName(s.activeMode),s.lease,static_cast<unsigned long>(s.remainingSeconds),s.configured,s.eligible,s.uartReady,s.uartBlocked,
            static_cast<unsigned long long>(s.successfulSleeps),static_cast<unsigned long long>(s.rejectedSleeps),s.pmCountsValid,
            static_cast<unsigned long>(s.positiveIntervals),static_cast<unsigned long long>(s.frameworkIntervalUs),
            static_cast<unsigned long>(s.lockCreated),static_cast<unsigned long>(s.lockAcquired),static_cast<long>(s.error));
        result(command,s.error?"FAIL":"PASS",details);return;
    }
    if (command && !std::strcmp(command, "settings")) {
        const char* action=::strtok_r(nullptr, " \t", &save);
        bool ok=false;
        if (action && !std::strcmp(action,"usb")) {
            if (::strtok_r(nullptr," \t",&save)) {result(command,"FAIL","reason=arguments no_changes=1");return;}
            const auto s=mosaico_console_usb_snapshot(); char details[400]{};
            std::snprintf(details,sizeof(details),"mounted=%lu connected=%lu suspended=%lu effective_active=%lu mounts=%lu unmounts=%lu suspends=%lu resumes=%lu rx_events=%lu wakeup_ready=%lu wakeup_error=%ld sleep_safe=%lu physical_power_proof=0",
                static_cast<unsigned long>(s.mounted),static_cast<unsigned long>(s.connected),static_cast<unsigned long>(s.suspended),
                static_cast<unsigned long>(s.effective_active),static_cast<unsigned long>(s.mounts),static_cast<unsigned long>(s.unmounts),
                static_cast<unsigned long>(s.suspends),static_cast<unsigned long>(s.resumes),static_cast<unsigned long>(s.rx_events),static_cast<unsigned long>(s.wakeup_ready),
                static_cast<long>(s.wakeup_error),static_cast<unsigned long>(s.sleep_safe));
            result(command,"PASS",details);return;
        }
        if (action && !std::strcmp(action,"ui")) {
            if (::strtok_r(nullptr," \t",&save)) {result(command,"FAIL","reason=arguments no_changes=1");return;}
            view::CodexMicroView::WifiEditorDebugSnapshot s;
            if (!_app.debugWifiEditorSnapshot(s)) {result(command,"SKIP","reason=snapshot_contention no_changes=1");return;}
            char details[240]{};
            std::snprintf(details,sizeof(details),"presses=%lu clicks=%lu releases=%lu field=%lu guards=%lu keyboard_visible=%lu pending=%lu numeric_only=1",
                static_cast<unsigned long>(s.presses),static_cast<unsigned long>(s.clicks),static_cast<unsigned long>(s.releases),
                static_cast<unsigned long>(s.field),static_cast<unsigned long>(s.guards),static_cast<unsigned long>(s.keyboardVisible),static_cast<unsigned long>(s.pending));
            result(command,"PASS",details);return;
        }
        if (action && !std::strcmp(action,"wifi")) {
            const char* operation=::strtok_r(nullptr," \t",&save);
            const char* argument=::strtok_r(nullptr," \t",&save);
            const bool extra=::strtok_r(nullptr," \t",&save)!=nullptr;
            if(operation && !std::strcmp(operation,"list") && !argument && !extra) {
                WifiSettingsSnapshot w{};
                if(!GetNetworkQuota().wifiSettingsSnapshot(w)) {result(command,"FAIL","reason=busy no_changes=1");return;}
                for(unsigned i=0;i<w.count && i<6;++i) {
                    char encoded[89]{}; settingsBase64(w.names[i],encoded);
                    debugPrintf("DBG WIFI_PROFILE index=%u ssid_b64=%s\r\n",i,encoded);
                }
                char details[256]{};
                std::snprintf(details,sizeof(details),"wifi_count=%u wifi_current_index=%u pending=%d reboot_required=%d restart_pending=%d error=%ld restart_error=%ld scan_error=%ld credentials_redacted=1",
                    w.count,w.currentIndex,w.pending,w.rebootRequired,w.restartPending,static_cast<long>(w.error),static_cast<long>(w.restartError),static_cast<long>(w.scanError));
                result(command,"PASS",details);return;
            }
            if(MosaicoOta::busy() || MosaicoOta::healthPending() || TouchSleep::active()) {
                result(command,"FAIL","reason=ota_health_or_touch_lease no_changes=1");return;
            }
            if(!extra && operation && argument) {
                if(!std::strcmp(operation,"save")) ok=settingsWifiSave(argument);
                else if(!std::strcmp(operation,"forget")) {
                    unsigned char ssid[33]{};size_t length=0;
                    if(settingsDecode(argument,ssid,sizeof(ssid),length) &&
                        MosaicoWifi::validCredentials(reinterpret_cast<char*>(ssid),""))
                        ok=GetNetworkQuota().requestWifiForget(reinterpret_cast<char*>(ssid));
                    MosaicoWifi::scrub(ssid,sizeof(ssid));
                } else if(!std::strcmp(operation,"restart") && !std::strcmp(argument,"CONFIRM"))
                    ok=GetNetworkQuota().requestWifiRestart();
            }
            result(command,ok?"PASS":"FAIL",ok?"accepted_owner_pending=1 verify_wifi_list=1":"reason=arguments_range_busy_or_contention no_changes=1");return;
        }
        if(action && std::strcmp(action,"get") &&
            (MosaicoOta::busy() || MosaicoOta::healthPending() || TouchSleep::active())) {
            result(command,"FAIL","reason=ota_health_or_touch_lease no_changes=1");return;
        }
        if (action && !std::strcmp(action,"get")) {
            if (::strtok_r(nullptr, " \t", &save)) { result(command,"FAIL","reason=arguments no_changes=1"); return; }
            const auto s=MosaicoDisplay::snapshot(); const auto& c=s.effectiveConfig;
            char details[512]{};
            std::snprintf(details,sizeof(details),
                "charge_timeout=%lu battery_timeout=%lu charge_brightness=%u battery_brightness=%u lock_brightness=%u burn_in=%d lock_wifi_minutes=%u lock_ble_minutes=%u temporary=%d lease_remaining_s=%lu runtime_revision=%lu revision=%lu saved_revision=%lu pending=%d error=%ld",
                static_cast<unsigned long>(c.chargeTimeoutSeconds),static_cast<unsigned long>(c.batteryTimeoutSeconds),
                c.chargeBrightness,c.batteryBrightness,c.lockBrightness,c.burnIn,c.lockWifiMinutes,c.lockBleMinutes,s.temporary,
                static_cast<unsigned long>(s.remainingLeaseSeconds),static_cast<unsigned long>(s.runtimeRevision),
                static_cast<unsigned long>(s.revision),static_cast<unsigned long>(s.savedRevision),s.pending,static_cast<long>(s.error));
            WifiSettingsSnapshot w{};
            const bool ready=GetNetworkQuota().wifiSettingsSnapshot(w);
            char name[89]{}; settingsBase64(system_config::ProductName,name);
            debugPrintf("DBG SETTINGS ble_name_b64=%s ble_name_mutable=0 wifi_available=%d wifi_count=%u wifi_current_index=%u wifi_pending=%d wifi_reboot_required=%d wifi_scan_error=%ld\r\n",
                name,ready && w.available,w.count,w.currentIndex,w.pending,w.rebootRequired,static_cast<long>(w.scanError));
            result(command,"PASS",details); return;
        } else if (action && !std::strcmp(action,"set")) {
            const char* field=::strtok_r(nullptr, " \t", &save);
            const char* text=::strtok_r(nullptr, " \t", &save);
            const char* lease=::strtok_r(nullptr, " \t", &save);
            uint32_t value=0,seconds=180;
            if (field && serial_debug_transport::settingsInteger(text,600,value) &&
                (!lease || (serial_debug_transport::settingsInteger(lease,600,seconds) && seconds>=30)) &&
                !::strtok_r(nullptr, " \t", &save)) ok=MosaicoDisplay::setTemporary(field,value,seconds);
        } else if (action && !std::strcmp(action,"restore")) {
            if (!::strtok_r(nullptr, " \t", &save)) ok=MosaicoDisplay::restoreTemporary();
        } else if (action && !std::strcmp(action,"save")) {
            const char* confirm=::strtok_r(nullptr, " \t", &save);
            if (confirm && !std::strcmp(confirm,"CONFIRM") && !::strtok_r(nullptr, " \t", &save)) ok=MosaicoDisplay::saveTemporary(true);
        }
        result(command,ok?"PASS":"FAIL",ok?
            (!std::strcmp(action,"save") ? "accepted_persist_pending=1 verify_saved_revision=1" : "accepted_ram_only=1 use_get_for_state=1"):
            "reason=arguments_range_busy_or_contention no_changes=1"); return;
    }
    if (command && std::strcmp(command, "display-settings") == 0) {
        const auto s = MosaicoDisplay::snapshot();
        char details[256];
        std::snprintf(details, sizeof(details),
            "charge_timeout=%lu battery_timeout=%lu charge_brightness=%u battery_brightness=%u lock_brightness=%u burn_in=%d revision=%lu saved_revision=%lu pending=%d error=%ld",
            static_cast<unsigned long>(s.config.chargeTimeoutSeconds), static_cast<unsigned long>(s.config.batteryTimeoutSeconds),
            s.config.chargeBrightness, s.config.batteryBrightness, s.config.lockBrightness, s.config.burnIn,
            static_cast<unsigned long>(s.revision), static_cast<unsigned long>(s.savedRevision), s.pending, static_cast<long>(s.error));
        result("display-settings", s.error ? "FAIL" : "PASS", details); return;
    }
    if (command && std::strcmp(command, "ota-status") == 0) {
        char details[384]{};
        MosaicoOta::status(details, sizeof(details));
        result("ota-status", "PASS", details);
        return;
    }
#if CONFIG_IDF_TARGET_ESP32S31 && CONFIG_IDF_TARGET_ARCH_RISCV
    if (command && std::strcmp(command, "panic") == 0) {
        char details[768];
        const bool saved = MosaicoPanicStatus(details, sizeof(details));
        result("panic", saved ? "PASS" : "SKIP", details);
        return;
    }
#endif
    if (MosaicoOta::busy() && (!command || (std::strcmp(command, "ping") && std::strcmp(command, "status")))) {
        result(command ? command : "parse", "FAIL", "reason=ota_busy no_changes=1");
        return;
    }
    if (command && (!std::strcmp(command, "ota-check") || !std::strcmp(command, "ota-download") ||
                    !std::strcmp(command, "ota-install") || !std::strcmp(command, "ota-reboot"))) {
        const char* sha = ::strtok_r(nullptr, " \t", &save);
        const bool needsHash = !std::strcmp(command, "ota-download") || !std::strcmp(command, "ota-install");
        if ((needsHash && !sha) || (!needsHash && sha) || ::strtok_r(nullptr, " \t", &save) || _async_test != AsyncTest::None) {
            result(command, "FAIL", "reason=arguments_or_async_diagnostic"); return;
        }
        const bool ok = !std::strcmp(command, "ota-check") ? MosaicoOta::requestCheck() :
            !std::strcmp(command, "ota-download") ? MosaicoOta::approveUpdate(sha) :
            !std::strcmp(command, "ota-install") ? MosaicoOta::approveInstall(sha) : MosaicoOta::requestReboot();
        if (ok) GetNetworkQuota().wakeForFirmwareUpdate();
        result(command, ok ? "PASS" : "FAIL", "queued_only=1 safety_gates_retained=1"); return;
    }
    if (command && (!std::strcmp(command, "ota-update") || !std::strcmp(command, "ota-bypass"))) {
        const char* confirm = ::strtok_r(nullptr, " \t", &save);
        if (!confirm || std::strcmp(confirm, "CONFIRM_EXTERNAL_POWER") || ::strtok_r(nullptr, " \t", &save)) {
            result(command, "FAIL", "expected=CONFIRM_EXTERNAL_POWER manual_external_power_confirmation_required=1");
            return;
        }
        if (_async_test != AsyncTest::None) {
            result(command, "FAIL", "reason=async_diagnostic_active cancel_first=1");
            return;
        }
        const bool ok = MosaicoOta::request();
        if (ok) GetNetworkQuota().wakeForFirmwareUpdate();
        result(command, ok ? "PASS" : "FAIL", "external_power=human_confirmed_not_measured install_policy=manual_battery_floor safety_gates_retained=1");
        return;
    }
    if (command && std::strcmp(command, "ota-rollback-test") == 0) {
        const char* confirm = ::strtok_r(nullptr, " \t", &save);
        if (!confirm || std::strcmp(confirm, "CONFIRM") || ::strtok_r(nullptr, " \t", &save)) {
            result("ota-rollback-test", "FAIL", "expected=CONFIRM");
            return;
        }
        result("ota-rollback-test", "RUNNING", "explicit_reboot_test=1");
        if (!MosaicoOta::rollbackTest()) result("ota-rollback-test", "FAIL", "reason=unsafe_or_no_bootable_rollback_image");
        return;
    }
#endif
    if (command == nullptr || std::strcmp(command, "help") == 0) {
        printHelp();
        return;
    }
#ifdef MOSAICO_BOARD
    if (command && std::strcmp(command, "sessions") == 0) {
        const auto s = MosaicoSessions::snapshot();
        char details[512];
        const uint32_t windowMs = s.lockedWindowCount ?
            ((s.lockedRefreshing ? s.nowMs : s.lockedWindowEndedMs) - s.lockedWindowStartedMs) : 0;
        const uint32_t cacheAge = s.lockedCacheValid ? (s.nowMs - s.lockedCapturedMs) / 1000U : 0;
        std::snprintf(details, sizeof(details),
            "ready=%d failed=%d connected=%d radio_requested=%d known=%02x generation=%lu cache_valid=%d cache_known=%02x cache_age_s=%lu refreshing=%d fresh=%02x timed_out=%d error=%ld windows=%lu window_ms=%lu",
            s.ready, s.failed, s.state.connected, s.radioRequestedEnabled, s.state.knownMask,
            static_cast<unsigned long>(s.state.connectionGeneration), s.lockedCacheValid,
            s.lockedState.knownMask, static_cast<unsigned long>(cacheAge), s.lockedRefreshing,
            s.freshnessKnownMask, s.lockedRefreshTimedOut, static_cast<long>(s.lockedRefreshError),
            static_cast<unsigned long>(s.lockedWindowCount), static_cast<unsigned long>(windowMs));
        result("sessions", s.failed ? "FAIL" : "PASS", details); return;
    }
    if (command && std::strcmp(command, "clock") == 0) {
        const auto clock = MosaicoClock::snapshot();
        std::tm local{}; char wall[32] = "uncalibrated";
        if (MosaicoClock::shanghaiTime(clock.epoch, local))
            std::strftime(wall, sizeof(wall), "%Y-%m-%dT%H:%M:%S+08:00", &local);
        char details[192];
        std::snprintf(details, sizeof(details), "valid=%d epoch=%lld local=%s ntp_sync_epoch=%lld source=%s hard_off_retention=0",
            clock.valid, static_cast<long long>(clock.epoch), wall,
            static_cast<long long>(clock.lastNtpSync), clock.lastNtpSync ? "ntp" : (clock.valid ? "rtc" : "unknown"));
        result("clock", clock.valid ? "PASS" : "SKIP", details); return;
    }
    if (command && std::strcmp(command, "motion") == 0) {
        const auto motion = GetHAL().motionOrientation();
        char details[256];
        std::snprintf(details, sizeof(details), "available=%d idle=%d valid=%d chip=%02x init=%u error=%ld candidate=%u display=%u display_ok=%d generation=%lu samples=%lu read_errors=%lu ax=%.3f ay=%.3f az=%.3f",
            motion.available, motion.idle, motion.valid, motion.chipId, motion.initStage,
            static_cast<long>(motion.error), motion.degrees, GetHAL().getDisplayOrientation(), GetHAL().isDisplayOrientationHealthy(),
            static_cast<unsigned long>(motion.generation), static_cast<unsigned long>(motion.samples),
            static_cast<unsigned long>(motion.readErrors), motion.ax, motion.ay, motion.az);
        result("motion", motion.available ? "PASS" : "SKIP", details); return;
    }
#endif
    if (std::strcmp(command, "ping") == 0) {
        result("ping", "PASS", "reply=pong");
        return;
    }
    if (std::strcmp(command, "network-config") == 0) {
        const char* encoded = ::strtok_r(nullptr, " \t", &save);
        const bool ok       = GetNetworkQuota().configure(encoded);
        const bool running  = GetNetworkQuota().configured();
        if (ok && !running) GetNetworkQuota().begin();
        result("network-config", ok ? "PASS" : "FAIL",
               ok ? (running ? "restart_required=1" : "restart_required=0") : "invalid_config");
        return;
    }
    if (std::strcmp(command, "network") == 0) {
        char details[192]{};
        auto& network = GetNetworkQuota();
        std::snprintf(details, sizeof(details),
                      "configured=%d connected=%d accepted=%lu failures=%lu history_accepted=%lu history_failures=%lu",
                      network.configured(), network.connected(), static_cast<unsigned long>(network.accepted()),
                      static_cast<unsigned long>(network.failures()),
                      static_cast<unsigned long>(network.historyAccepted()),
                      static_cast<unsigned long>(network.historyFailures()));
        const auto rx = GetTailnetQuota().rxDiagnostics();
        debugPrintf("DBG TAIL_RX derp_frame=%lu derp_enqueue=%lu derp_drop=%lu wg_dequeue=%lu wg_unavailable=%lu pbuf_fail=%lu receiver_index_miss=%lu key_reject=%lu inner_pbuf_fail=%lu decrypt_fail=%lu replay_drop=%lu inner_reject=%lu inner_ok=%lu input_ok=%lu input_error=%lu tcp_synack=%lu owner_dispatch_drop=%lu owner_processed=%lu transport_rx=%lu invalid_packet=%lu empty_keepalive=%lu\r\n",
                    static_cast<unsigned long>(rx.derp_frame),
                    static_cast<unsigned long>(rx.derp_enqueue),
                    static_cast<unsigned long>(rx.derp_drop),
                    static_cast<unsigned long>(rx.wg_dequeue),
                    static_cast<unsigned long>(rx.wg_unavailable),
                    static_cast<unsigned long>(rx.pbuf_fail),
                    static_cast<unsigned long>(rx.receiver_index_miss),
                    static_cast<unsigned long>(rx.key_reject),
                    static_cast<unsigned long>(rx.inner_pbuf_fail),
                    static_cast<unsigned long>(rx.decrypt_fail),
                    static_cast<unsigned long>(rx.replay_drop),
                    static_cast<unsigned long>(rx.inner_reject),
                    static_cast<unsigned long>(rx.inner_ok),
                    static_cast<unsigned long>(rx.input_ok),
                    static_cast<unsigned long>(rx.input_error),
                    static_cast<unsigned long>(rx.tcp_synack),
                    static_cast<unsigned long>(rx.owner_dispatch_drop),
                    static_cast<unsigned long>(rx.owner_processed),
                    static_cast<unsigned long>(rx.transport_rx),
                    static_cast<unsigned long>(rx.invalid_packet),
                    static_cast<unsigned long>(rx.empty_keepalive));
        for (unsigned path = 0; path < 4; ++path) {
            const auto d = GetTailnetQuota().fetchDiagnostics(static_cast<TailnetQuota::FetchPath>(path));
            debugPrintf("DBG TAIL_FETCH path=%u stage=%u http=%d received=%lu content_length=%lu elapsed_ms=%lu result=%u\r\n",
                        path, static_cast<unsigned>(d.stage), d.httpStatus, static_cast<unsigned long>(d.received),
                        static_cast<unsigned long>(d.contentLength), static_cast<unsigned long>(d.elapsedMs), d.result ? 1U : 0U);
        }
#ifdef MOSAICO_BOARD
        char retryDetails[192]{};
        std::snprintf(retryDetails, sizeof(retryDetails), "connect_attempts=%lu locked_budget_closures=%lu locked_attempt_limit=3 awake_backoff_ms=5000,15000,60000 credentials_redacted=1",
            static_cast<unsigned long>(network.wifiConnectAttempts()),
            static_cast<unsigned long>(network.wifiBudgetClosures()));
        debugPrintf("DBG WIFI_RETRY %s\r\n", retryDetails);
#endif
        result("network", "PASS", details);
        return;
    }
#ifdef MOSAICO_BOARD
    if (std::strcmp(command, "idle-runtime") == 0) {
        const auto touch = GetHAL().touchPollingInfo();
        char details[256]{};
        std::snprintf(details, sizeof(details),
            "touch_idle=%d touch_period_ms=%lu unused_gate_commands_ok=%d configured_idle_mhz=%lu main_awake_delay_ms=10 lvgl_timer_period_ms=%lu lvgl_tick_source=%s",
            touch.idle, static_cast<unsigned long>(touch.periodMs), touch.unusedGatesOff,
            static_cast<unsigned long>(GetNetworkQuota().idleCpuFrequency()),
            static_cast<unsigned long>(touch.lvglTimerPeriodMs),touch.monotonicTick?"esp_timer_monotonic":"periodic_increment");
        result("idle-runtime", "PASS", details);
        return;
    }
    if (std::strcmp(command, "display-idle-frequency") == 0) {
        const char* value = ::strtok_r(nullptr, " \t", &save);
        if (!value) {
            char details[80]{};
            std::snprintf(details, sizeof(details), "configured_idle_mhz=%lu default=320 allowed=160,320",
                          static_cast<unsigned long>(GetNetworkQuota().idleCpuFrequency()));
            result("display-idle-frequency", "PASS", details);
            return;
        }
        uint32_t mhz = 0;
        const char* confirm = ::strtok_r(nullptr, " \t", &save);
        if (!parseUnsignedStrict(value, 160, 320, mhz) || (mhz != 160 && mhz != 320) ||
            !confirm || std::strcmp(confirm, "CONFIRM") || ::strtok_r(nullptr, " \t", &save)) {
            result("display-idle-frequency", "FAIL", "expected=160_or_320_CONFIRM no_changes=1");
            return;
        }
        const auto power = GetNetworkQuota().powerStats();
        if (power.locked || power.phase != 0 ||
            GetHAL().gaugeBootReloadInfo().status == Hal::GaugeBootReloadStatus::Critical) {
            result("display-idle-frequency", "SKIP", "requires_awake_phase_and_safe_gauge no_changes=1");
            return;
        }
        const bool ok = GetNetworkQuota().setIdleCpuFrequency(mhz);
        result("display-idle-frequency", ok ? "PASS" : "FAIL",
               ok ? "saved_readback_verified active_cpu320=1 reversible=1" : "settings_commit_or_readback_failed");
        return;
    }
#if SOC_WIFI_HE_SUPPORT
    if (std::strcmp(command, "twt") == 0) {
        const char* mode = ::strtok_r(nullptr, " \t", &save);
        const char* option = ::strtok_r(nullptr, " \t", &save);
        if (!mode || ::strtok_r(nullptr, " \t", &save)) {
            result("twt", "FAIL", "expected=baseline_on_on-announced_600_or_1800_off_status_observe_on_off ram_only=1"); return;
        }
        if (!std::strcmp(mode, "observe")) {
            if (!option || (std::strcmp(option, "on") && std::strcmp(option, "off"))) {
                result("twt", "FAIL", "expected=observe_on_off"); return;
            }
            twtCycleUart = _reply_uart;
            GetNetworkQuota().requestTwtObserve(!std::strcmp(option, "on"));
            result("twt", "PASS", "observe_only=1 connection_policy_unchanged=1"); return;
        }
        const bool announced = !std::strcmp(mode, "on-announced");
        if (announced && !option) { result("twt", "FAIL", "expected=on-announced_600_or_1800 no_changes=1"); return; }
        uint32_t leaseSeconds = 600;
        if (option && ((std::strcmp(mode, "baseline") && std::strcmp(mode, "on") && !announced) ||
            !serial_debug_transport::settingsInteger(option, 1800, leaseSeconds) || !MosaicoTwt::validLease(leaseSeconds))) {
            result("twt", "FAIL", "expected=baseline_or_on_or_on-announced_600_or_1800 no_changes=1"); return;
        }
        if (!std::strcmp(mode, "status")) {
            const auto t = GetNetworkQuota().twtSnapshot();
            char details[896]{};
            std::snprintf(details, sizeof(details),
                "lease_s=%lu mode=%u profile=%u stage=%u stop=%u id=%u expiry_us=%lld bootstrap_deadline_us=%lld setup_deadline_us=%lld associated=%d control_ready=%u ap_ax=%d phy=%d status=%d reason=%u flow=%u actual_trigger=%hhu actual_flow_type=%hhu interval_us=%llu duration_us=%llu target_wake_us=%llu fetch_attempts=%lu fetch_ok=%lu fetch_us=%llu losses=%lu late=%lu error=%d restore_error=%d teardown_error=%d cleanup_pending=%d cleanup_failed=%d last_failure=%u cleanup_stage=%u cleanup_deadline_us=%lld sta_disconnect_valid=%d sta_disconnect_reason=%hu sta_disconnect_time_us=%lld sta_owner_trial_id=%hu ram_only=1",
                static_cast<unsigned long>(t.leaseSeconds), unsigned(t.requested), unsigned(t.profile), unsigned(t.stage), unsigned(t.stop), unsigned(t.id),
                static_cast<long long>(t.expiryUs), static_cast<long long>(t.bootstrapDeadlineUs), static_cast<long long>(t.setupDeadlineUs), t.associated, unsigned(t.controlReady), t.apAx, t.phy,
                t.actual.status, unsigned(t.actual.reason), unsigned(t.actual.flow), unsigned(t.actual.trigger), unsigned(t.actual.flowType),
                static_cast<unsigned long long>(t.intervalUs), static_cast<unsigned long long>(t.durationUs),
                static_cast<unsigned long long>(t.actual.targetWakeUs), static_cast<unsigned long>(t.fetchAttempts),
                static_cast<unsigned long>(t.fetchOk), static_cast<unsigned long long>(t.fetchUs),
                static_cast<unsigned long>(t.losses), static_cast<unsigned long>(t.lateEvents), t.error, t.restoreError, t.teardownError,
                t.cleanupPending, t.cleanupFailed, unsigned(t.lastFailure), unsigned(t.cleanupStage),
                static_cast<long long>(t.cleanupDeadlineUs), t.staDisconnect.valid, unsigned(t.staDisconnect.reason),
                static_cast<long long>(t.staDisconnect.eventUs), unsigned(t.staDisconnect.ownerTrialId));
            result("twt", "PASS", details); return;
        }
        MosaicoTwt::Mode requested;
        if (!std::strcmp(mode, "baseline")) requested = MosaicoTwt::Mode::Baseline;
        else if (!std::strcmp(mode, "on") || announced) requested = MosaicoTwt::Mode::On;
        else if (!std::strcmp(mode, "off")) requested = MosaicoTwt::Mode::Off;
        else { result("twt", "FAIL", "expected=baseline_on_on-announced_off_status"); return; }
        const bool queued = GetNetworkQuota().requestTwtTrial(requested, leaseSeconds, announced ? MosaicoTwt::Profile::AnnouncedTrigger : MosaicoTwt::Profile::Default);
        if (queued) twtCycleUart = _reply_uart;
        char details[160]{};
        std::snprintf(details, sizeof(details), "lease_s=%lu setup_cap_s=10 locked_trial_only=1 no_nvs=1 no_power_claim=1", static_cast<unsigned long>(leaseSeconds));
        result("twt", queued ? "QUEUED" : "FAIL", details); return;
    }
#endif
    if (std::strcmp(command, "display-clocks") == 0) {
        const auto clocks = GetHAL().displayClockDiagnostics();
        char details[260]{};
        std::snprintf(details, sizeof(details),
            "cpu_hz=%lu sys_hz=%lu apb_hz=%lu mem_bus_hz_derived=%lu direct_psram_dma=%d spi_config_hz=40000000 diagnostic_low_clock=%d errors=%ld,%ld,%ld",
            static_cast<unsigned long>(clocks.cpuHz), static_cast<unsigned long>(clocks.sysHz),
            static_cast<unsigned long>(clocks.apbHz), static_cast<unsigned long>(clocks.memBusDerivedHz),
            clocks.directDmaTrue, GetNetworkQuota().lowClockDiagnosticEnabled(),
            static_cast<long>(clocks.clockErrors[0]), static_cast<long>(clocks.clockErrors[1]),
            static_cast<long>(clocks.clockErrors[2]));
        result("display-clocks", clocks.cpuHz && !clocks.clockErrors[0] && !clocks.clockErrors[1] && !clocks.clockErrors[2] ? "PASS" : "FAIL", details);
        return;
    }
    if (std::strcmp(command, "display-ram-probe") == 0) {
        const auto power = GetNetworkQuota().powerStats();
        if (!power.locked || power.phase != 2 || power.wifiRunning) {
            result("display-ram-probe", "SKIP", "reason=requires_locked_radio_off_no_flash_writers");
            return;
        }
        uint32_t errors = 0;
        const bool ok = GetHAL().displayRamProbe(errors);
        char details[100]{};
        std::snprintf(details, sizeof(details), "independent_psram_bytes=4096 mismatches=%lu live_buffer_touched=0",
                      static_cast<unsigned long>(errors));
        result("display-ram-probe", ok ? "PASS" : "FAIL", details);
        return;
    }
    if (std::strcmp(command, "display-low-clock") == 0) {
        const char* mode = ::strtok_r(nullptr, " \t", &save);
        if (!mode || (std::strcmp(mode, "on") && std::strcmp(mode, "off")) || ::strtok_r(nullptr, " \t", &save)) {
            result("display-low-clock", "FAIL", "expected=on_or_off explicit_test_only=1");
            return;
        }
        GetNetworkQuota().setDiagnosticIdleFrequency(!std::strcmp(mode, "on") ? 80 : 320);
        result("display-low-clock", "PASS", "ram_only=1 reset_restores_guard=1 applied_by_network_owner=1");
        return;
    }
    if (std::strcmp(command, "display-test-frequency") == 0) {
        uint32_t mhz = 0;
        const char* value = ::strtok_r(nullptr, " \t", &save);
        if (!parseUnsignedStrict(value, 80, 320, mhz) || (mhz != 80 && mhz != 160 && mhz != 320) ||
            ::strtok_r(nullptr, " \t", &save)) {
            result("display-test-frequency", "FAIL", "expected=80_160_320 explicit_idle_test_only=1");
            return;
        }
        GetNetworkQuota().setDiagnosticIdleFrequency(mhz);
        char details[100]{};
        std::snprintf(details, sizeof(details), "requested_idle_mhz=%lu ram_only=1 active_cpu320=1 default_guard_restored=%d",
                      static_cast<unsigned long>(mhz), mhz == 320);
        result("display-test-frequency", "PASS", details);
        return;
    }
    if (std::strcmp(command, "gauge") == 0) {
        const auto battery = GetHAL().batteryTelemetry(true);
        char details[260]{};
        std::snprintf(details, sizeof(details),
            "valid=%d soc=%u mv=%u current_ma=%d avg_ma=%d rm_mah=%u fcc_mah=%u design_mah=%u capacity_valid=%d nominal_consistent=%d op_status=%04x battery_status=%04x soh=%u cycles=%u",
            battery.valid, battery.reportedSoc, battery.voltageMv, battery.currentMa,
            battery.averageCurrentMa, battery.remainingMah, battery.fullMah, battery.designMah,
            battery.capacityValid, battery.nominalConfigured, battery.operationStatus, battery.batteryStatus,
            battery.stateOfHealth, battery.cycleCount);
        result("gauge", battery.valid ? "PASS" : "FAIL", details);
        return;
    }
    if (std::strcmp(command, "gauge-boot") == 0) {
        if (::strtok_r(nullptr, " \t", &save)) {
            result("gauge-boot", "FAIL", "expected=no_arguments readonly_diagnostic=1");
            return;
        }
        const auto info = GetHAL().gaugeBootReloadInfo();
        char details[200]{};
        std::snprintf(details, sizeof(details), "state=%u attempted=%d reason=%s readonly_diagnostic=1",
                      static_cast<unsigned>(info.status), info.attempted, info.reason);
        result("gauge-boot", info.status == Hal::GaugeBootReloadStatus::Critical ? "FAIL" :
               (info.status == Hal::GaugeBootReloadStatus::Deferred ? "SKIP" : "PASS"), details);
        return;
    }
    if (std::strcmp(command, "runtime-restart") == 0) {
        const char* confirm = ::strtok_r(nullptr, " \t", &save);
        if (!confirm || std::strcmp(confirm, "CONFIRM") || ::strtok_r(nullptr, " \t", &save)) {
            result("runtime-restart", "SKIP", "requires_CONFIRM no_changes=1");
            return;
        }
        const auto power = GetNetworkQuota().powerStats();
        if (power.locked || power.phase != 0) {
            result("runtime-restart", "SKIP", "requires_awake_radio_phase no_changes=1");
            return;
        }
        // Snapshot waits for any boot transaction to finish. No ROM download
        // request, flash erase, bond reset or parameter write is involved.
        const auto info = GetHAL().gaugeBootReloadInfo();
        if (info.status == Hal::GaugeBootReloadStatus::Critical) {
            result("runtime-restart", "SKIP", "critical_gauge_requires_inspection no_changes=1");
            return;
        }
        result("runtime-restart", "PASS", "action=normal_application_restart");
        if (!_writer || !_writer->_reply_uart) std::fflush(stdout);
        GetHAL().delay(250);
        GetHAL().reboot();
        return;
    }
    if (std::strcmp(command, "gauge-selftest") == 0) {
        result("gauge-selftest", GetHAL().gaugeSafetySelfTest() ? "PASS" : "FAIL",
               "nominal_only=1 mac_crc_journal_unit_binding=1 quiet_temperature_guards=1 no_device_writes=1");
        return;
    }
    if (std::strcmp(command, "gauge-access") == 0) {
        const char* mode = ::strtok_r(nullptr, " \t", &save);
        if (!mode || (std::strcmp(mode, "open") && std::strcmp(mode, "restore")) ||
            ::strtok_r(nullptr, " \t", &save)) {
            result("gauge-access", "FAIL", "expected=open_or_restore explicit_authorized_action=1");
            return;
        }
        const auto power = GetNetworkQuota().powerStats();
        if (!power.locked || power.phase != 2 || power.wifiRunning) {
            result("gauge-access", "SKIP", "blocked_requires_locked_radio_off no_changes=1");
            return;
        }
        char reason[160]{};
        const bool ok = GetHAL().gaugeAccess(!std::strcmp(mode, "open") ? Hal::GaugeAccessAction::Open : Hal::GaugeAccessAction::Restore,
                                             reason, sizeof(reason));
        result("gauge-access", ok ? "PASS" : (!std::strncmp(reason, "blocked_", 8) ? "SKIP" : "FAIL"), reason);
        return;
    }
    if (std::strcmp(command, "gauge-reconcile") == 0) {
        if (::strtok_r(nullptr, " \t", &save)) {
            result("gauge-reconcile", "FAIL", "expected=no_arguments");
            return;
        }
        const auto power = GetNetworkQuota().powerStats();
        if (!power.locked || power.phase != 2 || power.wifiRunning) {
            result("gauge-reconcile", "SKIP", "blocked_requires_locked_radio_off no_changes=1");
            return;
        }
        char reason[160]{};
        const bool ok = GetHAL().gaugeReconcileNominal(reason, sizeof(reason));
        result("gauge-reconcile", ok ? "PASS" : (!std::strncmp(reason, "blocked_", 8) ? "SKIP" : "FAIL"), reason);
        return;
    }
    if (std::strcmp(command, "gauge-nominal") == 0) {
        const char* mode = ::strtok_r(nullptr, " \t", &save);
        uint32_t expected = 0;
        const char* old = ::strtok_r(nullptr, " \t", &save);
        if (!mode || (std::strcmp(mode, "apply") && std::strcmp(mode, "restore")) ||
            !parseUnsignedStrict(old, 1, 32767, expected) || ::strtok_r(nullptr, " \t", &save)) {
            result("gauge-nominal", "FAIL", "expected=apply_or_restore_and_expected_current_design nominal_only_not_learned_calibration=1");
            return;
        }
        char reason[160]{};
        const auto power = GetNetworkQuota().powerStats();
        if (!power.locked || power.phase != 2 || power.wifiRunning) {
            result("gauge-nominal", "SKIP", "requires_locked_radio_off=1 no_changes=1");
            return;
        }
        const bool ok = GetHAL().gaugeSetNominalCapacity(static_cast<uint16_t>(expected), 65,
                          !std::strcmp(mode, "restore"), reason, sizeof(reason));
        // Only precondition refusals are SKIP. A transaction/rollback/exit
        // failure must never masquerade as a harmless no-write refusal.
        result("gauge-nominal", ok ? "PASS" : (!std::strncmp(reason, "blocked_", 8) ? "SKIP" : "FAIL"), reason);
        return;
    }
    if (std::strcmp(command, "dot-selftest") == 0) {
        result("dot-selftest", mosaico_dot::selfTest() ? "PASS" : "FAIL",
               "layout_font_meter_bounds=1 known_unknown_distinct=1 endpoints_monotonic=1");
        return;
    }
    if (std::strcmp(command, "quota-selftest") == 0) {
        result("quota-selftest", QuotaMonitorRejectionSelfTest() ? "PASS" : "FAIL",
               "known_unknown_windows=1 malformed_depth_size_rejected=1 nonmutating=1");
        return;
    }
    if (std::strcmp(command, "quota") == 0) {
        std::unique_ptr<QuotaMonitorSnapshot> quota(new (std::nothrow) QuotaMonitorSnapshot());
        if (!quota || !CopyQuotaMonitor(*quota, GetHAL().millis())) {
            result("quota", "SKIP", "reason=no_snapshot");
            return;
        }
        for (std::size_t i = 0; i < quota->bucketCount; ++i) {
            const auto& bucket = quota->buckets[i];
            debugPrintf("DBG QUOTA bucket=%s plan=%s credits_known=%d balance=%s unlimited=%d\r\n",
                        bucket.id, bucket.plan, bucket.creditsKnown, bucket.creditBalance, bucket.creditsUnlimited);
            for (std::size_t w = 0; w < 2; ++w)
                if (bucket.windows[w].available) debugPrintf("DBG QUOTA window=%u remaining_bp=%u duration_minutes=%lu reset_epoch=%lu\r\n",
                    static_cast<unsigned>(w), bucket.windows[w].remainingBasisPoints,
                    static_cast<unsigned long>(bucket.windows[w].durationMinutes),
                    static_cast<unsigned long>(bucket.windows[w].resetEpoch));
        }
        char details[128]{};
        std::snprintf(details, sizeof(details), "buckets=%u available=%d stale=%d age_seconds=%lu reset_known=%d resets=%u",
            quota->bucketCount, quota->available, quota->stale, static_cast<unsigned long>(quota->ageSeconds),
            quota->resetCreditsKnown, quota->resetCredits);
        result("quota", quota->available ? "PASS" : "SKIP", details);
        return;
    }
#endif
    if (std::strcmp(command, "tailscale-config") == 0) {
        const bool running = GetTailnetQuota().enabled();
        const bool ok      = GetTailnetQuota().configure(::strtok_r(nullptr, " \t", &save));
        if (!running) GetTailnetQuota().load();
        result("tailscale-config", ok ? "PASS" : "FAIL",
               ok ? (running ? "restart_required=1" : "restart_required=0") : "invalid_config");
        return;
    }
    if (std::strcmp(command, "tailscale") == 0) {
        char details[100]{};
        auto& tail        = GetTailnetQuota();
        const uint32_t ip = tail.ip();
        std::snprintf(details, sizeof(details), "enabled=%d state=%d ip=%lu.%lu.%lu.%lu", tail.enabled(), tail.state(),
                      static_cast<unsigned long>(ip >> 24), static_cast<unsigned long>((ip >> 16) & 255),
                      static_cast<unsigned long>((ip >> 8) & 255), static_cast<unsigned long>(ip & 255));
        result("tailscale", "PASS", details);
        return;
    }
    if (std::strcmp(command, "tailscale-crypto") == 0) {
        result("tailscale-crypto", ml_noise_selftest() == ESP_OK && ml_derp_pin_selftest() == ESP_OK && ml_derp_patch_selftest() == ESP_OK ? "PASS" : "FAIL",
               "known_answer_and_tampered_tag=1 derp_pin_match_and_reject=1 derp_peer_patch_cases=5");
        return;
    }
    if (std::strcmp(command, "network-selftest") == 0) {
        HostBridge test;
        bool ok = test.applyUsage(100, 8000, 5000, 1000, 0, 100);
        ok      = ok && test.applyNetworkUsage(7000, 5000, 1001, 0, 200) && test.lastUsageSequence() == 100;
        ok      = ok && test.applyUsage(101, 6000, 5000, 1002, 0, 300);
        ok      = ok && test.applyNetworkUsage(9000, 5000, 999, 0, 400);
        ok      = ok && test.applyUsage(102, 9000, 5000, 1001, 0, 400) && test.lastUsageSequence() == 102;
        ok      = ok && test.snapshot(400).remainingBasisPoints == 6000;
        ok      = ok && test.snapshot(131000).usageStale && !test.snapshot(601000).usageAvailable;
        result("network-selftest", ok ? "PASS" : "FAIL", "cases=7 source_sequence_and_freshness=1");
        return;
    }
    if (std::strcmp(command, "standby-dim") == 0) {
        const char* value = ::strtok_r(nullptr, " \t", &save);
        const char* lease = ::strtok_r(nullptr, " \t", &save);
        uint32_t brightness = 0, seconds = 180;
        const bool statusOnly = !value || !std::strcmp(value, "status");
        const bool off = value && !std::strcmp(value, "off");
        if (::strtok_r(nullptr, " \t", &save) || ((statusOnly || off) && lease) ||
            (!statusOnly && !off && (!parseUnsignedStrict(value, 0, 100, brightness) ||
                (lease && !parseUnsignedStrict(lease, 30, 300, seconds))))) {
            result(command, "FAIL", "expected=status|off|brightness_0..100_lease_s_30..300 default_lease_s=180");
            return;
        }
        const bool ok = statusOnly || _app.debugStandbyDim(off ? -1 : static_cast<int>(brightness), seconds);
        char details[160]{};
        _app.debugStandbyDimDetails(details, sizeof(details));
        result(command, ok ? "PASS" : "FAIL", details);
        return;
    }
    if (std::strcmp(command, "display-lock") == 0) {
        result("display-lock", _app.debugLockDisplay() ? "PASS" : "FAIL");
        return;
    }
    if (std::strcmp(command, "power-refresh") == 0) {
        GetNetworkQuota().refreshWhileLocked();
        result("power-refresh", "PASS");
        return;
    }
    if (std::strcmp(command, "power") == 0) {
        const char* profile = ::strtok_r(nullptr, " \t", &save);
        if (profile) {
            if (!std::strcmp(profile, "baseline"))
                GetNetworkQuota().setPowerProfile(0);
            else if (!std::strcmp(profile, "radio"))
                GetNetworkQuota().setPowerProfile(1);
            else if (!std::strcmp(profile, "eco"))
                GetNetworkQuota().setPowerProfile(2);
            else {
                result("power", "FAIL", "invalid_profile");
                return;
            }
        }
        const auto state = GetNetworkQuota().powerStats();
        const auto ble   = GetCodexMicroBle().diagnostics();
#if CONFIG_IDF_TARGET_ESP32S31
        const int controller_sleeping = -1;
#else
        const int controller_sleeping = esp_bt_controller_is_sleeping();
#endif
        char details[256]{};
        std::snprintf(
            details, sizeof(details),
            "locked=%d profile=%u phase=%u wifi_running=%d wifi_connected=%d bt_connected=%d "
            "bt_advertising=%d bt_sleeping=%d cpu_mhz=%lu off_ms=%llu cycles=%lu clock_error=%d audio_suspended=%d",
            state.locked, state.profile, state.phase, state.wifiRunning, GetNetworkQuota().connected(),
            GetCodexMicroBle().connected(), ble.advertising, controller_sleeping,
            static_cast<unsigned long>(state.cpuMHz), static_cast<unsigned long long>(state.offMs),
            static_cast<unsigned long>(state.cycles), state.clockError, GetHAL().audioSuspended());
        result("power", "PASS", details);
        return;
    }
    if (std::strcmp(command, "display-wake") == 0) {
        _app.debugWakeDisplay();
        result("display-wake", "PASS");
        return;
    }
    if (std::strcmp(command, "display") == 0) {
        char details[128]{};
        std::snprintf(details, sizeof(details), "locked=%d refreshes=%lu frames=%lu brightness=%d",
                      _app.debugDisplayLocked(), static_cast<unsigned long>(_app.debugLockRefreshCount()),
                      static_cast<unsigned long>(GetDisplayFrameCount()), GetHAL().getBackLightBrightness());
        result("display", "PASS", details);
        return;
    }
    if (std::strcmp(command, "history") == 0) {
        const char* action = ::strtok_r(nullptr, " \t", &save);
        bool ok            = true;
        if (action && std::strcmp(action, "hours") == 0)
            ok = _app.debugShowHistory(true);
        else if (action && std::strcmp(action, "days") == 0)
            ok = _app.debugShowHistory(false);
        else if (action && std::strcmp(action, "select") == 0) {
            const char* index         = ::strtok_r(nullptr, " \t", &save);
            char* end                 = nullptr;
            const unsigned long value = index ? std::strtoul(index, &end, 10) : UINT32_MAX;
            ok                        = index && end && *end == 0 && value < 168 && _app.debugSelectHistory(value);
        } else if (action)
            ok = false;
        char details[240]{};
        _app.debugHistoryDetails(details, sizeof(details));
        result("history", ok ? "PASS" : "FAIL", details);
        return;
    }
    if (std::strcmp(command, "history-selftest") == 0) {
        result("history-selftest", TokenHistoryRejectionSelfTest() && TokenAmountSelfTest() && HistoryHitSelfTest() ? "PASS" : "FAIL",
               "malformed_depth_size_rejected=1 cache_preserved=1 token_units=K/M touch_partition=1");
        return;
    }
    if (std::strcmp(command, "status") == 0) {
        printStatus();
        return;
    }
    if (std::strcmp(command, "boot") == 0) {
        BootTracePrint(debugPrintf);
        return;
    }
    if (std::strcmp(command, "selftest") == 0) {
        runSelfTest();
        return;
    }
    if (std::strcmp(command, "controls") == 0) {
#ifdef MOSAICO_BOARD
        result("controls", "SKIP", "monitor_only_no_host_controls=1");
#else
        printControls();
#endif
        return;
    }
    if (std::strcmp(command, "protocol") == 0) {
        const CodexMicroBleDiagnostics diagnostics = GetCodexMicroBle().diagnostics();
        const bool healthy = GetCodexMicroBle().protocolSelfTest() && diagnostics.inputDropped == 0 &&
                             diagnostics.txFailures == 0 && diagnostics.rpcErrors == 0;
        char details[208]  = {};
        std::snprintf(details, sizeof(details),
                      "controls=13 encoder=3 report_id=6 report_bytes=63 payload_bytes=61 dropped=%lu tx_failures=%lu "
                      "rpc_errors=%lu wireless_usage_accepted=%lu wireless_usage_rejected=%lu",
                      static_cast<unsigned long>(diagnostics.inputDropped),
                      static_cast<unsigned long>(diagnostics.txFailures),
                      static_cast<unsigned long>(diagnostics.rpcErrors),
                      static_cast<unsigned long>(diagnostics.wirelessUsageAccepted),
                      static_cast<unsigned long>(diagnostics.wirelessUsageRejected));
        result("protocol", healthy ? "PASS" : "FAIL", details);
        return;
    }
    if (std::strcmp(command, "host-usage") == 0) {
        uint32_t sequence       = 0;
        uint32_t remaining      = 0;
        uint32_t reset_epoch    = 0;
        uint32_t captured_epoch = 0;
        uint32_t reset_credits  = 0;
        char* sequence_value    = ::strtok_r(nullptr, " \t", &save);
        char* remaining_value   = ::strtok_r(nullptr, " \t", &save);
        char* reset_value       = ::strtok_r(nullptr, " \t", &save);
        char* captured_value    = ::strtok_r(nullptr, " \t", &save);
        char* credits_value     = ::strtok_r(nullptr, " \t", &save);
        char* extra             = ::strtok_r(nullptr, " \t", &save);
        const bool valid        = extra == nullptr && parseUnsignedStrict(sequence_value, 1, UINT32_MAX, sequence) &&
                                  parseUnsignedStrict(remaining_value, 0, 10000, remaining) &&
                                  parseUnsignedStrict(reset_value, 0, UINT32_MAX, reset_epoch) &&
                                  parseUnsignedStrict(captured_value, 1, UINT32_MAX, captured_epoch) &&
                                  parseUnsignedStrict(credits_value, 0, 99, reset_credits);
        if (!valid) {
            result("host-usage", "FAIL", "reason=invalid_fields");
            return;
        }
        const bool applied =
            GetHostBridge().applyUsage(sequence, static_cast<uint16_t>(remaining), reset_epoch, captured_epoch,
                                       static_cast<uint8_t>(reset_credits), GetHAL().millis());
        char details[112] = {};
        std::snprintf(details, sizeof(details), "seq=%lu remaining_bp=%lu reset_epoch=%lu credits=%lu",
                      static_cast<unsigned long>(sequence), static_cast<unsigned long>(remaining),
                      static_cast<unsigned long>(reset_epoch), static_cast<unsigned long>(reset_credits));
        if (applied) {
            result("host-usage", "PASS", details);
        } else {
            char rejected[96] = {};
            std::snprintf(rejected, sizeof(rejected), "seq=%lu reason=stale_sequence current=%lu",
                          static_cast<unsigned long>(sequence),
                          static_cast<unsigned long>(GetHostBridge().lastUsageSequence()));
            result("host-usage", "FAIL", rejected);
        }
        return;
    }
    if (std::strcmp(command, "mic") == 0) {
        char* duration = ::strtok_r(nullptr, " \t", &save);
        startMicrophoneTest(parseUnsigned(duration, DefaultMicTestMs, 500, 30000));
        return;
    }
    if (std::strcmp(command, "inputs") == 0) {
        char* duration = ::strtok_r(nullptr, " \t", &save);
        startInputTest(parseUnsigned(duration, DefaultInputTestMs, 1000, 60000));
        return;
    }
    if (std::strcmp(command, "ui") == 0) {
        char* screen = ::strtok_r(nullptr, " \t", &save);
        if (screen == nullptr || std::strcmp(screen, "cycle") == 0) {
            startUiCycle();
            return;
        }
        AppCodexMicro::DebugScreen target;
#ifdef MOSAICO_BOARD
        if (std::strcmp(screen, "quota") == 0) {
#else
        if (std::strcmp(screen, "command") == 0) {
#endif
            target = AppCodexMicro::DebugScreen::Command;
        } else if (std::strcmp(screen, "agent") == 0) {
            target = AppCodexMicro::DebugScreen::Agent;
        } else if (std::strcmp(screen, "mic") == 0) {
            target = AppCodexMicro::DebugScreen::Mic;
        } else {
            result("ui", "FAIL", "reason=invalid_screen");
            return;
        }
        const bool changed = _app.debugSetScreen(target);
        char details[64]   = {};
        std::snprintf(details, sizeof(details), "screen=%s actual=%s", screen, _app.debugScreenName());
        result("ui", changed && std::strcmp(screen, _app.debugScreenName()) == 0 ? "PASS" : "FAIL", details);
        return;
    }
    if (std::strcmp(command, "transport") == 0) {
        startTransportTest();
        return;
    }
    if (std::strcmp(command, "perf") == 0) {
        char* duration = ::strtok_r(nullptr, " \t", &save);
        startPerformanceTest(parseUnsigned(duration, DefaultPerformanceTestMs, 1000, 15000), true);
        return;
    }
    if (std::strcmp(command, "trace") == 0) {
        char* duration = ::strtok_r(nullptr, " \t", &save);
        startPerformanceTest(parseUnsigned(duration, DefaultPerformanceTestMs, 1000, 60000), false);
        return;
    }
    if (std::strcmp(command, "tone") == 0) {
        char* frequency   = ::strtok_r(nullptr, " \t", &save);
        char* duration    = ::strtok_r(nullptr, " \t", &save);
        const uint32_t hz = parseUnsigned(frequency, 880, 100, 4000);
        const uint32_t ms = parseUnsigned(duration, 250, 20, 1000);
        audio::play_tone(static_cast<int>(hz), static_cast<float>(ms) / 1000.0f, 0.6f);
        char details[64] = {};
        std::snprintf(details, sizeof(details), "hz=%lu duration_ms=%lu verify=audible", static_cast<unsigned long>(hz),
                      static_cast<unsigned long>(ms));
        result("tone", "OBSERVE", details);
        return;
    }
    if (std::strcmp(command, "vibrate") == 0) {
        char* duration        = ::strtok_r(nullptr, " \t", &save);
        char* strength        = ::strtok_r(nullptr, " \t", &save);
        const uint32_t ms     = parseUnsigned(duration, 300, 20, 3000);
        const uint32_t amount = parseUnsigned(strength, 80, 1, 100);
        GetHAL().vibrate(static_cast<uint16_t>(ms), static_cast<uint8_t>(amount));
        char details[80] = {};
        std::snprintf(details, sizeof(details), "duration_ms=%lu strength=%lu verify=physical",
                      static_cast<unsigned long>(ms), static_cast<unsigned long>(amount));
        result("vibrate", "OBSERVE", details);
        return;
    }
    if (std::strcmp(command, "backlight") == 0) {
        char* level               = ::strtok_r(nullptr, " \t", &save);
        const uint32_t brightness = parseUnsigned(level, 80, 10, 100);
        GetHAL().setBackLightBrightness(static_cast<int>(brightness), false);
        char details[64] = {};
        std::snprintf(details, sizeof(details), "brightness=%lu verify=visible",
                      static_cast<unsigned long>(brightness));
        result("backlight", "OBSERVE", details);
        return;
    }
    if (std::strcmp(command, "cancel") == 0) {
        cancelAsyncTest("requested", true);
        return;
    }
    if (std::strcmp(command, "pairing-reset") == 0) {
        char* confirmation = ::strtok_r(nullptr, " \t", &save);
        if (confirmation == nullptr || std::strcmp(confirmation, "CONFIRM") != 0) {
            result("pairing-reset", "SKIP", "reason=requires_CONFIRM warning=erases_bonds_and_restarts");
            return;
        }
        result("pairing-reset", GetCodexMicroBle().resetPairing() ? "PASS" : "FAIL", "action=erase_bonds_and_restart");
        return;
    }
    result(command, "FAIL", "reason=unknown_command");
}

void SerialDebug::printHelp()
{
#ifdef MOSAICO_BOARD
    debugPrintf("DBG HELP sleep-io report_only=1 registered_outputs_only=1\r\n");
    debugPrintf("DBG HELP touch-sleep 120 | off | status single_use_ram_lease=1 unverified_candidate=1 sleep_verified=0\r\n");
    debugPrintf("DBG HELP idle-wait on | off | status ram_only=1 locked_event_wait_ms=500 safe_gates_required=1\r\n");
    debugPrintf("DBG HELP standby-sleep on [lease_s=180,30..300] | standby-sleep off | standby-sleep auto CONFIRM | standby-sleep status automatic_default=profile UART_wake_preamble_required=1\r\n");
    debugPrintf("DBG HELP settings get | settings set <field> <int> [lease_s=180,30..600] | settings restore | settings save CONFIRM\r\n");
    debugPrintf("DBG HELP settings ui numeric_editor_diagnostic=1 no_input_text=1\r\n");
    debugPrintf("DBG HELP settings fields=charge_timeout:0,15,30,60,120,300,600 battery_timeout:15,30,45,60 charge_brightness:10..100 battery_brightness:10..100 lock_brightness:0..100 burn_in:0,1 lock_wifi_minutes:1,2,5,10,15,30,60 lock_ble_minutes:1,2,5,10,15,30,60\r\n");
    debugPrintf("DBG HELP settings wifi list | save <base64JSON_ssid_password_only> | forget <base64SSID> | restart CONFIRM physical_local_console_trust=1 base64_crc_not_security=1 save_async=1 restart_requires_saved=1\r\n");
#endif

    debugPrintf("DBG HELP commands=ping,status,selftest,controls,protocol,debug-transport\r\n");
#ifdef MOSAICO_BOARD
    debugPrintf("DBG HELP ota=ota-status,ota-update_CONFIRM_EXTERNAL_POWER,ota-rollback-test_CONFIRM\r\n");
#endif
    debugPrintf(
        "DBG HELP commands=ui_[command|agent|mic|cycle],transport,perf_[ms],trace_[ms],mic_[ms],inputs_[ms]\r\n");
    debugPrintf("DBG HELP commands=tone_[hz]_[ms],vibrate_[ms]_[strength],backlight_[10-100],cancel\r\n");
    debugPrintf("DBG HELP bridge=host-usage_[seq]_[remaining_bp]_[reset_epoch]_[captured_epoch]_[credits]\r\n");
    debugPrintf("DBG HELP destructive=pairing-reset_CONFIRM\r\n");
    if (!_writer || !_writer->_reply_uart) std::fflush(stdout);
    result("help", "PASS");
}

void SerialDebug::printStatus()
{
    const Hal::Diagnostics hal         = GetHAL().diagnostics();
    const CodexMicroState state        = GetCodexMicroBle().snapshot();
    const CodexMicroBleDiagnostics ble = GetCodexMicroBle().diagnostics();
    debugPrintf("DBG STATUS firmware=%s battery=%u charging=%s ui=%s heap_free=%lu heap_min=%lu\r\n",
                system_config::FirmwareVersion, static_cast<unsigned>(GetHAL().getBatteryLevel()),
                onOff(GetHAL().isBatteryCharging()), _app.debugScreenName(),
                static_cast<unsigned long>(esp_get_free_heap_size()),
                static_cast<unsigned long>(esp_get_minimum_free_heap_size()));
    debugPrintf(
        "DBG STATUS hal_i2c=%s hal_pmic=%s hal_ioe=%s hal_display=%s hal_touch=%s hal_audio=%s hal_vibrator=%s "
        "hal_buttons=%s\r\n",
        onOff(hal.i2c), onOff(hal.pmic), onOff(hal.ioExpander), onOff(hal.display), onOff(hal.touch), onOff(hal.audio),
        onOff(hal.vibrator), onOff(hal.buttons));
    debugPrintf(
        "DBG STATUS ble_ready=%s ble_connected=%s ble_protocol=%s advertising=%s revision=%lu queued=%lu dropped=%lu "
        "processed=%lu tx_messages=%lu tx_reports=%lu tx_failures=%lu rx_reports=%lu rpc=%lu rpc_errors=%lu "
        "wireless_usage_accepted=%lu wireless_usage_rejected=%lu pending=%lu half_open_recoveries=%lu\r\n",
        onOff(state.ready && ble.hidReady), onOff(state.connected), onOff(state.protocolReady && ble.protocolReady),
        onOff(ble.advertising), static_cast<unsigned long>(state.revision), static_cast<unsigned long>(ble.inputQueued),
        static_cast<unsigned long>(ble.inputDropped), static_cast<unsigned long>(ble.inputProcessed),
        static_cast<unsigned long>(ble.txMessages), static_cast<unsigned long>(ble.txReports),
        static_cast<unsigned long>(ble.txFailures), static_cast<unsigned long>(ble.rxReports),
        static_cast<unsigned long>(ble.rpcMessages), static_cast<unsigned long>(ble.rpcErrors),
        static_cast<unsigned long>(ble.wirelessUsageAccepted), static_cast<unsigned long>(ble.wirelessUsageRejected),
        static_cast<unsigned long>(ble.queuePending), static_cast<unsigned long>(ble.halfOpenRecoveries));
    const Hal::PerformanceDiagnostics performance = GetHAL().performanceDiagnostics();
    debugPrintf(
        "DBG STATUS perf_lvgl_core=%d perf_tx_core=%d lvgl_calls=%lu lvgl_max_us=%lu touch_reads=%lu "
        "touch_gap_max_us=%lu queue_high=%lu tx_max_us=%lu tx_total_us=%lu\r\n",
        static_cast<int>(performance.lvglTaskCore), static_cast<int>(ble.inputTaskCore),
        static_cast<unsigned long>(performance.lvglHandlerCalls),
        static_cast<unsigned long>(performance.lvglHandlerMaxUs), static_cast<unsigned long>(performance.touchReads),
        static_cast<unsigned long>(performance.touchMaxGapUs), static_cast<unsigned long>(ble.queueHighWater),
        static_cast<unsigned long>(ble.txMaxUs), static_cast<unsigned long>(ble.txTotalUs));
    const HostBridgeSnapshot host = GetHostBridge().snapshot(GetHAL().millis());
    debugPrintf(
        "DBG STATUS host_bridge=%s usage_available=%s usage_stale=%s remaining_bp=%u reset_seconds=%lu "
        "reset_credits=%u usage_seq=%lu\r\n",
        onOff(host.online), onOff(host.usageAvailable), onOff(host.usageStale),
        static_cast<unsigned>(host.remainingBasisPoints), static_cast<unsigned long>(host.resetSeconds),
        static_cast<unsigned>(host.resetCredits), static_cast<unsigned long>(GetHostBridge().lastUsageSequence()));
    if (!_writer || !_writer->_reply_uart) std::fflush(stdout);
    result("status", "PASS");
}

void SerialDebug::runSelfTest()
{
    if (_async_test != AsyncTest::None) {
        result("selftest", "FAIL", "reason=async_test_active");
        return;
    }
    unsigned passed  = 0;
    unsigned failed  = 0;
    unsigned skipped = 0;
    const auto check = [&passed, &failed](const char* name, bool ok, const char* details = nullptr) {
        if (ok) {
            ++passed;
        } else {
            ++failed;
        }
        SerialDebug::result(name, ok ? "PASS" : "FAIL", details);
    };

    const Hal::Diagnostics hal = GetHAL().diagnostics();
    check("hal.i2c", hal.i2c);
    check("hal.pmic", hal.pmic);
#ifdef MOSAICO_BOARD
    ++skipped;
    result("hal.io_expander", "SKIP", "unsupported_board_peripheral");
#else
    check("hal.io_expander", hal.ioExpander);
#endif
    check("hal.display", hal.display);
    check("hal.touch", hal.touch);
#ifdef MOSAICO_BOARD
    ++skipped;
    result("hal.audio", "SKIP", "unsupported_board_peripheral");
#else
    check("hal.audio", hal.audio);
#endif
#ifdef MOSAICO_BOARD
    ++skipped;
    result("hal.vibrator", "SKIP", "unsupported_board_peripheral");
#else
    check("hal.vibrator", hal.vibrator);
#endif
    check("hal.buttons", hal.buttons);

    const bool heap_ok              = heap_caps_check_integrity_all(false);
    const std::size_t internal_free = heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    const std::size_t psram_total   = heap_caps_get_total_size(MALLOC_CAP_SPIRAM);
    char heap_details[96]           = {};
    std::snprintf(heap_details, sizeof(heap_details), "internal_free=%lu psram_total=%lu",
                  static_cast<unsigned long>(internal_free), static_cast<unsigned long>(psram_total));
    check("memory.integrity", heap_ok && internal_free > 32768 && psram_total > 0, heap_details);

    lv_display_t* display    = lv_display_get_default();
    const int32_t width      = display == nullptr ? 0 : lv_display_get_horizontal_resolution(display);
    const int32_t height     = display == nullptr ? 0 : lv_display_get_vertical_resolution(display);
    char display_details[96] = {};
    std::snprintf(display_details, sizeof(display_details), "width=%ld height=%ld always_on=%s pixel_shift=1",
                  static_cast<long>(width), static_cast<long>(height), onOff(system_config::DisplayAlwaysOn));
    check("display.geometry", display != nullptr && width > 0 && height > 0, display_details);

    const uint8_t battery    = GetHAL().getBatteryLevel();
    char battery_details[64] = {};
    std::snprintf(battery_details, sizeof(battery_details), "level=%u charging=%s", static_cast<unsigned>(battery),
                  onOff(GetHAL().isBatteryCharging()));
    check("power.telemetry", GetHAL().isBatteryLevelValid() && battery <= 100, battery_details);

    char audio_details[64] = {};
    std::snprintf(audio_details, sizeof(audio_details), "sample_rate=%d volume=%d", GetHAL().getAudioSampleRate(),
                  GetHAL().getSpeakerVolume());
#ifdef MOSAICO_BOARD
    ++skipped;
    result("audio.configuration", "SKIP", "audio_not_implemented");
#else
    check("audio.configuration",
          GetHAL().getAudioSampleRate() == 44100 && GetHAL().getSpeakerVolume() >= 0 &&
              GetHAL().getSpeakerVolume() <= 100,
          audio_details);
#endif

    const CodexMicroState state        = GetCodexMicroBle().snapshot();
    const CodexMicroBleDiagnostics ble = GetCodexMicroBle().diagnostics();
    char ble_details[80]               = {};
    std::snprintf(ble_details, sizeof(ble_details), "ready=%s connected=%s advertising=%s", onOff(state.ready),
                  onOff(state.connected), onOff(ble.advertising));
#ifdef MOSAICO_BOARD
    check("ble.disabled", !ble.initialized && !ble.advertising && !state.connected, "monitor_only=1");
#else
    check("ble.service", ble.initialized && ble.hidReady && state.ready, ble_details);
#endif
    char protocol_details[176] = {};
    std::snprintf(protocol_details, sizeof(protocol_details),
                  "controls=13 encoder=3 rpc_buffer=4096 dropped=%lu tx_failures=%lu rpc_errors=%lu "
                  "wireless_usage_accepted=%lu wireless_usage_rejected=%lu",
                  static_cast<unsigned long>(ble.inputDropped), static_cast<unsigned long>(ble.txFailures),
                  static_cast<unsigned long>(ble.rpcErrors), static_cast<unsigned long>(ble.wirelessUsageAccepted),
                  static_cast<unsigned long>(ble.wirelessUsageRejected));
    check("ble.protocol",
          GetCodexMicroBle().protocolSelfTest() && ble.inputDropped == 0 && ble.txFailures == 0 && ble.rpcErrors == 0,
          protocol_details);
    check("ui.objects", _app.debugUiReady(), _app.debugScreenName());
#ifdef MOSAICO_BOARD
    check("serial.transport", true, "primary=tinyusb-cdc nonblocking=1");
#else
    check("serial.transport", true, "primary=usb-serial-jtag nonblocking=1");
#endif
    const HostBridgeSnapshot host = GetHostBridge().snapshot(GetHAL().millis());
    char host_details[96]         = {};
    std::snprintf(host_details, sizeof(host_details), "online=%s available=%s stale=%s remaining_bp=%u",
                  onOff(host.online), onOff(host.usageAvailable), onOff(host.usageStale),
                  static_cast<unsigned>(host.remainingBasisPoints));
    check("host.bridge", host.remainingBasisPoints <= 10000, host_details);

    char summary[64] = {};
    std::snprintf(summary, sizeof(summary), "passed=%u failed=%u skipped=%u", passed, failed, skipped);
    result("selftest", failed == 0 ? "PASS" : "FAIL", summary);
}

void SerialDebug::printControls()
{
    constexpr std::size_t PhysicalControlCount = 13;
    bool all_valid                             = CodexMicroControlCodes.size() >= PhysicalControlCount;
    for (std::size_t index = 0; index < PhysicalControlCount; ++index) {
        const char* code = CodexMicroControlCodes[index];
        const bool valid = code != nullptr && code[0] != '\0';
        all_valid        = all_valid && valid;
        debugPrintf("DBG CONTROL index=%u code=%s status=%s\r\n", static_cast<unsigned>(index),
                    valid ? code : "invalid", valid ? "PASS" : "FAIL");
    }
    if (!_writer || !_writer->_reply_uart) std::fflush(stdout);
    result("controls", all_valid ? "PASS" : "FAIL", "physical=13");
}

void SerialDebug::startMicrophoneTest(uint32_t durationMs)
{
    cancelAsyncTest("replaced", false);
    (void)durationMs;
    const bool disabled = !GetHAL().isMicrophoneMeterEnabled() && GetHAL().getMicrophoneLevel() == 0.0f;
    result("mic", disabled ? "PASS" : "FAIL", "policy=computer_microphone local_capture=disabled pcm_transport=none");
}

void SerialDebug::updateMicrophoneTest(uint32_t now)
{
    const float level = GetHAL().getMicrophoneLevel();
    if (std::isfinite(level) && level >= 0.0f && level <= 1.0f) {
        _microphone_peak = std::max(_microphone_peak, level);
        ++_microphone_samples;
    }
    if (!elapsed(now, _test_deadline_ms)) {
        return;
    }
    if (!_meter_was_enabled) {
        GetHAL().setMicrophoneMeterEnabled(false);
    }
    char details[96] = {};
    std::snprintf(details, sizeof(details), "peak=%.3f samples=%lu signal=%s", static_cast<double>(_microphone_peak),
                  static_cast<unsigned long>(_microphone_samples),
                  _microphone_peak >= MicrophoneSignalFloor ? "detected" : "quiet");
    const char* status = _microphone_samples == 0                    ? "FAIL"
                         : _microphone_peak >= MicrophoneSignalFloor ? "PASS"
                                                                     : "OBSERVE";
    _async_test        = AsyncTest::None;
    result("mic", status, details);
}

void SerialDebug::startInputTest(uint32_t durationMs)
{
    cancelAsyncTest("replaced", false);
    const AppCodexMicro::DebugInputState current = _app.debugInputState();
    _input_seen_a = _input_seen_b = _input_seen_touch = false;
    _input_previous_a                                 = current.buttonA;
    _input_previous_b                                 = current.buttonB;
    _input_previous_touch                             = current.touch;
    _input_min_x = _input_min_y = 32767;
    _input_max_x = _input_max_y = -1;
    _app.debugSetInputCapture(true);
    _async_test       = AsyncTest::Inputs;
    _test_started_ms  = GetHAL().millis();
    _test_deadline_ms = _test_started_ms + durationMs;
    char details[96]  = {};
    std::snprintf(details, sizeof(details), "duration_ms=%lu capture=1 expected=button_a,button_b,touch",
                  static_cast<unsigned long>(durationMs));
    result("inputs", "RUNNING", details);
}

void SerialDebug::updateInputTest(uint32_t now)
{
    const AppCodexMicro::DebugInputState state = _app.debugInputState();
    if (state.buttonA && !_input_previous_a) {
        _input_seen_a = true;
        debugPrintf("DBG INPUT event=button_a_pressed\r\n");
    }
    if (state.buttonB && !_input_previous_b) {
        _input_seen_b = true;
        debugPrintf("DBG INPUT event=button_b_pressed\r\n");
    }
    if (state.touch) {
        _input_seen_touch = true;
        _input_min_x      = std::min(_input_min_x, state.x);
        _input_min_y      = std::min(_input_min_y, state.y);
        _input_max_x      = std::max(_input_max_x, state.x);
        _input_max_y      = std::max(_input_max_y, state.y);
        if (!_input_previous_touch) {
            debugPrintf("DBG INPUT event=touch_pressed x=%d y=%d\r\n", state.x, state.y);
        }
    } else if (_input_previous_touch) {
        debugPrintf("DBG INPUT event=touch_released\r\n");
    }
    _input_previous_a     = state.buttonA;
    _input_previous_b     = state.buttonB;
    _input_previous_touch = state.touch;
    if (!_writer || !_writer->_reply_uart) std::fflush(stdout);

    if (!elapsed(now, _test_deadline_ms) && !(_input_seen_a && _input_seen_b && _input_seen_touch)) {
        return;
    }
    _app.debugSetInputCapture(false);
    char details[128] = {};
    std::snprintf(details, sizeof(details), "button_a=%s button_b=%s touch=%s min_x=%d min_y=%d max_x=%d max_y=%d",
                  onOff(_input_seen_a), onOff(_input_seen_b), onOff(_input_seen_touch), _input_min_x, _input_min_y,
                  _input_max_x, _input_max_y);
    const bool complete = _input_seen_a && _input_seen_b && _input_seen_touch;
    _async_test         = AsyncTest::None;
    result("inputs", complete ? "PASS" : "FAIL", details);
}

void SerialDebug::startUiCycle()
{
    cancelAsyncTest("replaced", false);
    if (!_app.debugSetScreen(AppCodexMicro::DebugScreen::Command)) {
        result("ui-cycle", "SKIP", "reason=requires_ble_connection");
        return;
    }
    _ui_cycle_stage   = 0;
    _async_test       = AsyncTest::UiCycle;
    _test_started_ms  = GetHAL().millis();
    _test_deadline_ms = _test_started_ms + UiCycleStepMs;
    result("ui-cycle", "RUNNING", "screen=command");
}

void SerialDebug::updateUiCycle(uint32_t now)
{
    if (!elapsed(now, _test_deadline_ms)) {
        return;
    }
    bool ok              = false;
    const char* expected = nullptr;
#ifdef MOSAICO_BOARD
    if (_ui_cycle_stage < 2) {
        expected = "history";
        ok = _app.debugShowHistory(_ui_cycle_stage == 1);
    } else {
        expected = "quota";
        ok = _app.debugSetScreen(AppCodexMicro::DebugScreen::Command);
    }
#else
    if (_ui_cycle_stage == 0) {
        expected = "agent";
        ok       = _app.debugSetScreen(AppCodexMicro::DebugScreen::Agent);
    } else if (_ui_cycle_stage == 1) {
        expected = "mic";
        ok       = _app.debugSetScreen(AppCodexMicro::DebugScreen::Mic);
    } else {
        expected = "command";
        ok       = _app.debugSetScreen(AppCodexMicro::DebugScreen::Command);
    }
#endif
    ok = ok && std::strcmp(_app.debugScreenName(), expected) == 0;
    debugPrintf("DBG UI stage=%u expected=%s actual=%s status=%s\r\n", static_cast<unsigned>(_ui_cycle_stage + 1),
                expected, _app.debugScreenName(), ok ? "PASS" : "FAIL");
    if (!_writer || !_writer->_reply_uart) std::fflush(stdout);
    if (!ok || _ui_cycle_stage >= 2) {
        _async_test = AsyncTest::None;
        result("ui-cycle", ok ? "PASS" : "FAIL", "final=command verify=visible");
        return;
    }
    ++_ui_cycle_stage;
    _test_deadline_ms = now + UiCycleStepMs;
}

void SerialDebug::startTransportTest()
{
    cancelAsyncTest("replaced", false);
    if (!GetCodexMicroBle().diagnostics().protocolReady) {
        result("transport", "SKIP", "reason=requires_codex_rpc_handshake");
        return;
    }
    const CodexMicroBleDiagnostics before = GetCodexMicroBle().diagnostics();
    _transport_tx_messages                = before.txMessages;
    _transport_tx_reports                 = before.txReports;
    _transport_tx_failures                = before.txFailures;
    if (!GetCodexMicroBle().sendJoystick(0.0f, 0.0f)) {
        result("transport", "FAIL", "reason=neutral_report_not_queued");
        return;
    }
    _async_test       = AsyncTest::Transport;
    _test_started_ms  = GetHAL().millis();
    _test_deadline_ms = _test_started_ms + TransportWaitMs;
    result("transport", "RUNNING", "payload=neutral_joystick safe=1");
}

void SerialDebug::updateTransportTest(uint32_t now)
{
    if (!elapsed(now, _test_deadline_ms)) {
        return;
    }
    const CodexMicroBleDiagnostics after = GetCodexMicroBle().diagnostics();
    const bool sent   = after.txMessages > _transport_tx_messages && after.txReports > _transport_tx_reports &&
                        after.txFailures == _transport_tx_failures;
    char details[112] = {};
    std::snprintf(details, sizeof(details), "messages_delta=%lu reports_delta=%lu failures_delta=%lu",
                  static_cast<unsigned long>(after.txMessages - _transport_tx_messages),
                  static_cast<unsigned long>(after.txReports - _transport_tx_reports),
                  static_cast<unsigned long>(after.txFailures - _transport_tx_failures));
    _async_test = AsyncTest::None;
    result("transport", sent ? "PASS" : "FAIL", details);
}

void SerialDebug::startPerformanceTest(uint32_t durationMs, bool generateTraffic)
{
    cancelAsyncTest("replaced", false);
    if (!GetCodexMicroBle().diagnostics().protocolReady) {
        result("perf", "SKIP", "reason=requires_codex_rpc_handshake");
        return;
    }
    GetHAL().resetPerformanceDiagnostics();
    GetCodexMicroBle().resetPerformanceDiagnostics();
    const CodexMicroBleDiagnostics before = GetCodexMicroBle().diagnostics();
    _performance_input_queued             = before.inputQueued;
    _performance_input_dropped            = before.inputDropped;
    _performance_input_processed          = before.inputProcessed;
    _performance_tx_messages              = before.txMessages;
    _performance_tx_reports               = before.txReports;
    _performance_tx_failures              = before.txFailures;
    _performance_generated                = 0;
    _performance_accepted                 = 0;
    _performance_draining                 = false;
    _performance_generate_traffic         = generateTraffic;
    _performance_loop_max_gap_us          = 0;
    _performance_last_poll_us             = esp_timer_get_time();
    _test_started_ms                      = GetHAL().millis();
    _performance_next_send_ms             = _test_started_ms;
    _test_deadline_ms                     = _test_started_ms + durationMs;
    _async_test                           = AsyncTest::Performance;
    char details[80]                      = {};
    std::snprintf(details, sizeof(details), "duration_ms=%lu mode=%s rate_hz=%u",
                  static_cast<unsigned long>(durationMs), generateTraffic ? "synthetic" : "observe",
                  generateTraffic ? 50U : 0U);
    result(generateTraffic ? "perf" : "trace", "RUNNING", details);
}

void SerialDebug::updatePerformanceTest(uint32_t now)
{
    const int64_t now_us = esp_timer_get_time();
    if (_performance_last_poll_us > 0 && now_us > _performance_last_poll_us) {
        _performance_loop_max_gap_us =
            std::max(_performance_loop_max_gap_us, static_cast<uint32_t>(now_us - _performance_last_poll_us));
    }
    _performance_last_poll_us = now_us;

    if (_performance_generate_traffic && !_performance_draining && elapsed(now, _performance_next_send_ms)) {
        ++_performance_generated;
        if (GetCodexMicroBle().sendJoystick(0.0f, 0.0f)) {
            ++_performance_accepted;
        }
        _performance_next_send_ms += PerformanceSendPeriodMs;
        if (elapsed(now, _performance_next_send_ms + PerformanceSendPeriodMs)) {
            _performance_next_send_ms = now + PerformanceSendPeriodMs;
        }
    }

    if (!_performance_draining && elapsed(now, _test_deadline_ms)) {
        _performance_draining = true;
        _test_deadline_ms     = now + 250;
        return;
    }
    if (!_performance_draining || !elapsed(now, _test_deadline_ms)) {
        return;
    }

    const CodexMicroBleDiagnostics after      = GetCodexMicroBle().diagnostics();
    const Hal::PerformanceDiagnostics display = GetHAL().performanceDiagnostics();
    const uint32_t queued                     = after.inputQueued - _performance_input_queued;
    const uint32_t dropped                    = after.inputDropped - _performance_input_dropped;
    const uint32_t processed                  = after.inputProcessed - _performance_input_processed;
    const uint32_t messages                   = after.txMessages - _performance_tx_messages;
    const uint32_t reports                    = after.txReports - _performance_tx_reports;
    const uint32_t failures                   = after.txFailures - _performance_tx_failures;
    const bool activity_ok = _performance_generate_traffic
                                 ? _performance_generated >= 40 && _performance_accepted == _performance_generated &&
                                       queued == _performance_accepted
                                 : queued > 0 && processed > 0;
    const bool responsive  = activity_ok && dropped == 0 && processed > 0 && messages > 0 && reports > 0 &&
                             failures == 0 && display.touchReads > 0 && display.touchMaxGapUs <= 50000 &&
                             _performance_loop_max_gap_us <= 50000;
    char details[320]      = {};
    std::snprintf(details, sizeof(details),
                  "generated=%lu accepted=%lu queued=%lu processed=%lu dropped=%lu messages=%lu reports=%lu "
                  "failures=%lu queue_high=%lu tx_max_us=%lu loop_gap_max_us=%lu lvgl_core=%d tx_core=%d "
                  "lvgl_max_us=%lu touch_reads=%lu touch_gap_max_us=%lu",
                  static_cast<unsigned long>(_performance_generated), static_cast<unsigned long>(_performance_accepted),
                  static_cast<unsigned long>(queued), static_cast<unsigned long>(processed),
                  static_cast<unsigned long>(dropped), static_cast<unsigned long>(messages),
                  static_cast<unsigned long>(reports), static_cast<unsigned long>(failures),
                  static_cast<unsigned long>(after.queueHighWater), static_cast<unsigned long>(after.txMaxUs),
                  static_cast<unsigned long>(_performance_loop_max_gap_us), static_cast<int>(display.lvglTaskCore),
                  static_cast<int>(after.inputTaskCore), static_cast<unsigned long>(display.lvglHandlerMaxUs),
                  static_cast<unsigned long>(display.touchReads), static_cast<unsigned long>(display.touchMaxGapUs));
    _async_test = AsyncTest::None;
    result(_performance_generate_traffic ? "perf" : "trace", responsive ? "PASS" : "FAIL", details);
}

void SerialDebug::updateAsyncTest()
{
    const uint32_t now = GetHAL().millis();
    switch (_async_test) {
        case AsyncTest::None:
            return;
        case AsyncTest::Microphone:
            updateMicrophoneTest(now);
            return;
        case AsyncTest::Inputs:
            updateInputTest(now);
            return;
        case AsyncTest::UiCycle:
            updateUiCycle(now);
            return;
        case AsyncTest::Transport:
            updateTransportTest(now);
            return;
        case AsyncTest::Performance:
            updatePerformanceTest(now);
            return;
    }
}

void SerialDebug::cancelAsyncTest(const char* reason, bool reportCancellation)
{
    if (_async_test == AsyncTest::None) {
        if (reportCancellation) {
            result("cancel", "SKIP", "reason=no_async_test");
        }
        return;
    }
    if (_async_test == AsyncTest::Microphone && !_meter_was_enabled) {
        GetHAL().setMicrophoneMeterEnabled(false);
    }
    if (_async_test == AsyncTest::Inputs) {
        _app.debugSetInputCapture(false);
    }
    _async_test = AsyncTest::None;
    if (reportCancellation) {
        char details[64] = {};
        std::snprintf(details, sizeof(details), "reason=%s", reason == nullptr ? "unknown" : reason);
        result("cancel", "PASS", details);
    }
}

uint32_t SerialDebug::parseUnsigned(const char* value, uint32_t fallback, uint32_t minimum, uint32_t maximum)
{
    if (value == nullptr || value[0] == '\0') {
        return fallback;
    }
    char* end                  = nullptr;
    errno                      = 0;
    const unsigned long parsed = std::strtoul(value, &end, 10);
    if (errno != 0 || end == value || *end != '\0') {
        return fallback;
    }
    return std::clamp<uint32_t>(static_cast<uint32_t>(parsed), minimum, maximum);
}

void SerialDebug::result(const char* command, const char* status, const char* details)
{
    debugPrintf("DBG RESULT command=%s status=%s%s%s\r\n", command == nullptr ? "unknown" : command,
                status == nullptr ? "FAIL" : status, details && details[0] ? " " : "",
                details && details[0] ? details : "");
    if (!_writer || !_writer->_reply_uart) std::fflush(stdout);
}
