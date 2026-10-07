// SPDX-License-Identifier: MIT
#pragma once
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
namespace serial_debug_transport {
constexpr std::size_t UartEventCapacity = 8;
constexpr bool uartQueueSaturated(std::size_t pending) { return pending >= UartEventCapacity; }
// CRC-32/ISO-HDLC (same as Python zlib.crc32). Integrity check, not authentication.
constexpr uint32_t crc32(const char* bytes, std::size_t length) {
    uint32_t crc = 0xffffffffU;
    for (std::size_t i = 0; i < length; ++i) {
        crc ^= static_cast<unsigned char>(bytes[i]);
        for (unsigned bit = 0; bit < 8; ++bit)
            crc = (crc >> 1) ^ ((crc & 1U) ? 0xedb88320U : 0U);
    }
    return ~crc;
}
constexpr int hexDigit(char value) {
    return value >= '0' && value <= '9' ? value - '0' :
        value >= 'a' && value <= 'f' ? value - 'a' + 10 :
        value >= 'A' && value <= 'F' ? value - 'A' + 10 : -1;
}
// Only an intact exact ASCII debug command may reach the shared parser.
constexpr char* decodeUartLine(char* line, std::size_t length) {
    if (length < 17 || line[0] != 'u' || line[1] != 'a' || line[2] != 'r' || line[3] != 't' ||
        line[4] != ' ' || line[13] != ' ') return nullptr;
    uint32_t expected = 0;
    for (std::size_t i = 5; i < 13; ++i) {
        const int digit = hexDigit(line[i]);
        if (digit < 0) return nullptr;
        expected = (expected << 4) | static_cast<uint32_t>(digit);
    }
    char* payload = line + 14;
    const auto size = length - 14;
    for (std::size_t i = 0; i < size; ++i)
        if (payload[i] < 0x20 || payload[i] > 0x7e) return nullptr;
    const bool debug = size >= 5 && payload[0] == 'd' && payload[1] == 'e' && payload[2] == 'b' &&
        payload[3] == 'u' && payload[4] == 'g' && (size == 5 || payload[5] == ' ');
    const bool dbg = size >= 3 && payload[0] == 'd' && payload[1] == 'b' && payload[2] == 'g' &&
        (size == 3 || payload[3] == ' ');
    return (debug || dbg) && crc32(payload, size) == expected ? payload : nullptr;
}

// No driver waits: a poll offers at most one bounded contiguous chunk.
template<std::size_t Capacity> class TxRing {
public:
    constexpr bool enqueue(const char* bytes, std::size_t size) {
        if (size > Capacity - used_) { ++overflow; return false; }
        for (std::size_t i = 0; i < size; ++i) data_[(head_ + used_ + i) % Capacity] = bytes[i];
        used_ += size; return true;
    }
    template<class Writer> constexpr void drain(Writer writer, std::size_t budget) {
        const auto count = used_ < budget ? used_ : budget;
        const auto contiguous = count < Capacity - head_ ? count : Capacity - head_;
        if (!contiguous) return;
        const int sent = writer(data_.data() + head_, contiguous);
        if (sent <= 0) { ++stalls; return; }
        const auto accepted = static_cast<std::size_t>(sent) < contiguous ? static_cast<std::size_t>(sent) : contiguous;
        head_ = (head_ + accepted) % Capacity; used_ -= accepted;
    }
    constexpr void clear() { data_.fill(0); head_ = used_ = 0; }
    constexpr std::size_t pending() const { return used_; }
    uint32_t overflow = 0, stalls = 0;
private:
    std::array<char, Capacity> data_{};
    std::size_t head_ = 0, used_ = 0;
};
inline bool uartAllowed(const char* command) {
    if (!command) return true;
    const char* allowed[] = {"help", "ping", "status", "debug-transport", "ota-status", "panic", "sessions", "clock", "motion", "network", "idle-runtime", "display-settings", "display-idle-frequency", "display-clocks", "display-test-frequency", "gauge", "gauge-boot", "gauge-selftest", "dot-selftest", "quota-selftest", "quota", "tailscale", "tailscale-crypto", "network-selftest", "display-lock", "display-wake", "display", "power", "power-refresh", "history", "history-selftest", "boot", "selftest", "controls", "protocol", "mic", "inputs", "ui", "transport", "perf", "cancel"};
    for (const char* item : allowed) if (!std::strcmp(command, item)) return true;
    return false;
}
}
