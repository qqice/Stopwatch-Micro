#pragma once
#include <array>
#include <cstddef>
#include <cstdint>

enum class TokenHistoryQuality : uint8_t { Missing, Official, Observed, Partial, Correction };
struct TokenHistoryCell {
    char label[24]{};
    uint64_t tokens             = 0;
    bool valid                  = false;
    TokenHistoryQuality quality = TokenHistoryQuality::Missing;
};
struct TokenHistorySnapshot {
    std::array<TokenHistoryCell, 30> days{};
    std::array<TokenHistoryCell, 24> hours{};
    uint32_t revision             = 0;
    uint32_t capturedEpoch        = 0;
    uint32_t ageSecondsAtReceipt  = 0;
    uint32_t receivedAtMs         = 0;
    bool available                = false;
    int16_t timezoneOffsetMinutes = 480;
};
bool InitTokenHistory();
bool JsonNestingValid(const char* data, size_t length);
uint32_t TokenHistoryRevision();
bool CopyTokenHistory(TokenHistorySnapshot& out);
bool ApplyTokenHistory(const char* json, size_t length);
bool TokenHistoryRejectionSelfTest();
