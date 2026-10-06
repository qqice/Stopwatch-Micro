#pragma once
#include <array>
#include <cstddef>
#include <cstdint>

enum class TokenHistoryQuality : uint8_t { Missing, Official, Observed, Partial, Correction, Pending, Local, Gap };
struct TokenHistoryCell {
    char label[24]{};
    uint64_t tokens             = 0;
    int64_t correctionDelta     = 0;
    int64_t gapDelta            = 0;
    bool valid                  = false;
    TokenHistoryQuality quality = TokenHistoryQuality::Missing;
};
struct QuotaTrendPoint {
    uint32_t epoch = 0;
    uint16_t remainingBasisPoints = 0;
    bool valid = false, breakBefore = false, resetBefore = false;
};
struct QuotaTrendSnapshot {
    std::array<QuotaTrendPoint, 25> hours{};
    std::array<QuotaTrendPoint, 8> days{};
    char limitId[64]{};
    uint32_t capturedEpoch = 0, ageSecondsAtReceipt = 0, receivedAtMs = 0;
    uint32_t start24hEpoch = 0, start7dEpoch = 0, endEpoch = 0;
    uint32_t durationMinutes = 10080;
    int16_t timezoneOffsetMinutes = 480;
    bool available = false;
};
struct TokenHistorySnapshot {
    std::array<TokenHistoryCell, 30> days{};
    std::array<TokenHistoryCell, 24> hours{};
    uint32_t revision             = 0;
    uint32_t capturedEpoch        = 0;
    uint32_t ageSecondsAtReceipt  = 0;
    uint32_t receivedAtMs         = 0;
    bool available                = false; // Legacy token-only availability, not root v2 aggregate.
    bool tokenAvailable           = false;
    QuotaTrendSnapshot quotaTrend{};
    int16_t timezoneOffsetMinutes = 480;
};
bool InitTokenHistory();
bool JsonNestingValid(const char* data, size_t length);
uint32_t TokenHistoryRevision();
bool CopyTokenHistory(TokenHistorySnapshot& out);
bool ApplyTokenHistory(const char* json, size_t length);
bool TokenHistoryRejectionSelfTest();
