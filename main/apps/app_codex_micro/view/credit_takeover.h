/* SPDX-License-Identifier: MIT */
#pragma once
#include <host/quota_monitor.h>
#include "credit_format.h"
#include "credit_reel.h"

namespace mosaico_credit {
constexpr uint32_t WeeklyMinutes = 7 * 24 * 60;

constexpr int MaxBalanceDigits = sizeof(QuotaMonitorBucket::creditBalance) - 1;
static_assert(MaxBalanceDigits == ReelMaxDigits, "balance and reel capacities match");
enum class Balance { Unknown, Empty, Finite, Unlimited };
constexpr bool digit(char c) { return c >= '0' && c <= '9'; }
constexpr Balance balanceKind(const QuotaMonitorBucket& b) {
    if (b.creditsKnown && b.creditsUnlimited) return Balance::Unlimited;
    if (!b.creditsKnown || !b.creditsAvailabilityKnown) return Balance::Unknown;
    if (!b.creditsAvailable) return Balance::Empty;
    bool positive = false;
    const char* p = b.creditBalance;
    if (!digit(*p)) return Balance::Unknown;
    for (; digit(*p); ++p) positive |= *p != '0';
    if (*p == '.') {
        if (!digit(*++p)) return Balance::Unknown;
        for (; digit(*p); ++p) positive |= *p != '0';
    }
    if (*p) return Balance::Unknown;
    return positive ? Balance::Finite : Balance::Empty;
}
constexpr bool takeover(Balance b) { return b == Balance::Finite || b == Balance::Unlimited; }
constexpr bool isCodex(const char* p) {
    constexpr char id[] = "codex";
    for (unsigned i = 0; i < sizeof(id); ++i) if (id[i] != p[i]) return false;
    return true;
}
constexpr int exhaustedWeeklyWindow(const QuotaMonitorBucket& b, uint64_t epoch) {
    if (!isCodex(b.id)) return -1;
    int weekly = -1;
    for (int j = 0; j < 2; ++j) {
        const auto& w = b.windows[j];
        if (!w.available || w.durationMinutes != WeeklyMinutes) continue;
        if (weekly >= 0 || !w.resetEpoch || epoch >= w.resetEpoch || w.remainingBasisPoints != 0) return -1;
        weekly = j;
    }
    return weekly;
}
constexpr const char* unpad(const char* s) {
    while (s[0] == '0' && digit(s[1])) ++s;
    return s;
}
constexpr int length(const char* s) {
    int n = 0;
    while (s[n]) ++n;
    return n;
}
// The decimal strings may exceed any machine integer type.
constexpr bool integerDrop(const char* prev, const char* next) {
    prev = unpad(prev); next = unpad(next);
    int a = length(prev), b = length(next);
    if (!a || !b) return false;
    for (int i = 0; i < a; ++i) if (!digit(prev[i])) return false;
    for (int i = 0; i < b; ++i) if (!digit(next[i])) return false;
    if (a != b) return a > b;
    for (int i = 0; i < a; ++i) if (prev[i] != next[i]) return prev[i] > next[i];
    return false;
}
struct Reading {
    char digits[MaxBalanceDigits + 1]{};
    uint32_t capturedEpoch = 0, resetEpoch = 0;
};
constexpr bool identical(const char* a, const char* b) {
    for (; *a && *a == *b; ++a, ++b) {}
    return *a == *b;
}
constexpr Reading animationReference(const Reading& old, const Reading& next) {
    if (old.capturedEpoch && old.capturedEpoch == next.capturedEpoch &&
        (old.resetEpoch != next.resetEpoch || !identical(old.digits, next.digits))) {
        // Conflicting readings of the same timestamp cannot establish a
        // debit baseline. Preserve that timestamp until a distinct sample.
        Reading ambiguous{};
        ambiguous.capturedEpoch = next.capturedEpoch;
        return ambiguous;
    }
    return next;
}
constexpr bool rollDecrease(const Reading& old, const Reading& next, bool freshPrevious,
                            bool visible) {
    return visible && freshPrevious && old.capturedEpoch && next.capturedEpoch > old.capturedEpoch &&
           old.resetEpoch && old.resetEpoch == next.resetEpoch && integerDrop(old.digits, next.digits);
}
} // namespace mosaico_credit
