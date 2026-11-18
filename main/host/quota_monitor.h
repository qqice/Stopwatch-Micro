#pragma once
#include <array>
#include <cstddef>
#include <cstdint>

constexpr std::size_t QuotaMonitorMaxBuckets = 8;
struct QuotaMonitorWindow {
    bool available = false;
    uint16_t remainingBasisPoints = 0;
    uint32_t durationMinutes = 0;
    uint32_t resetEpoch = 0;
};
struct QuotaMonitorBucket {
    char id[64]{};
    char name[64]{};
    char plan[24]{};
    char reached[48]{};
    char creditBalance[32]{};
    bool creditsKnown = false;
    bool creditsUnlimited = false;
    // Old v2 snapshots omit has_credits. A present credit object alone does
    // not establish whether the account can fall back to Credits.
    bool creditsAvailabilityKnown = false;
    bool creditsAvailable = false;
    std::array<QuotaMonitorWindow, 2> windows{};
};
enum class ApiCreditState : uint8_t { Unavailable, Ok, Stale, Reauth, Forbidden };
struct QuotaMonitorApiCredit {
    bool present = false;
    ApiCreditState state = ApiCreditState::Unavailable;
    uint32_t remainingCents = 0;
    uint32_t expiresEpoch = 0;
    uint32_t capturedEpoch = 0;
};
struct QuotaMonitorSnapshot {
    std::array<QuotaMonitorBucket, QuotaMonitorMaxBuckets> buckets{};
    QuotaMonitorApiCredit apiCredit{};
    uint8_t bucketCount = 0;
    uint32_t totalBuckets = 0;
    bool truncated = false;
    bool resetCreditsKnown = false;
    uint16_t resetCredits = 0;
    bool available = false;
    bool stale = false;
    uint32_t capturedEpoch = 0;
    uint32_t receivedAtMs = 0;
    uint64_t receivedAtUs = 0;
    uint64_t ageMilliseconds = 0;
    uint32_t ageSecondsAtReceipt = 0;
    uint32_t ageSeconds = 0;
    uint32_t revision = 0;
};
bool InitQuotaMonitor();
uint32_t QuotaMonitorRevision();
bool ApplyQuotaMonitor(const char* json, std::size_t length, uint32_t receivedAtMs);
bool CopyQuotaMonitor(QuotaMonitorSnapshot& out, uint32_t nowMs);
bool QuotaMonitorRejectionSelfTest();
