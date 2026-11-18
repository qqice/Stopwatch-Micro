/* SPDX-License-Identifier: MIT */
#pragma once
#include <host/quota_monitor.h>
#include <host/system_clock.h>
#include <cstdio>

namespace mosaico_api_credit {
// Card-local geometry. Split the existing [14,422) footer equally.
constexpr int Split = 218;
constexpr int FooterY = 278, HalfWidth = 204, FooterHeight = 50;
constexpr int IconX = 6, IconSize = 28, TextX = 42, TextWidth = HalfWidth - TextX;
constexpr int AmountY = 2, AmountHeight = 26, DateY = 32, DateHeight = 18;
constexpr int CreditsWidth = 160, MetaWidth = 198;
constexpr uint32_t Rose = 0xF065B4, DimOrange = 0xB9773E;
constexpr uint64_t LocalStaleSeconds = 30U * 60U; // Two default server polling periods.
static_assert(52 + CreditsWidth < Split && 14 + MetaWidth < Split);
static_assert(IconX + IconSize < TextX && AmountY + AmountHeight < DateY);
static_assert(Split + HalfWidth == 422 && FooterY + FooterHeight < 350);
static_assert(DateY + DateHeight == FooterHeight);

struct Presentation {
    bool known = false;
    bool fresh = false;
    const char* detail = "";
};

constexpr Presentation present(const QuotaMonitorApiCredit& credit, uint64_t epoch) {
    if (!credit.present) return {};
    // A cached aggregate may include a grant that has now expired. Do not
    // extrapolate which part of that balance is still spendable.
    if (credit.expiresEpoch && epoch >= credit.expiresEpoch) return {false, false, "EXPIRED"};
    const bool known = credit.capturedEpoch != 0;
    switch (credit.state) {
    case ApiCreditState::Reauth: return {known, false, "LOGIN"};
    case ApiCreditState::Forbidden: return {known, false, "NO ACCESS"};
    case ApiCreditState::Unavailable: return {known, false, "UNAVAILABLE"};
    case ApiCreditState::Stale: return {known, false, "STALE"};
    case ApiCreditState::Ok: break;
    }
    const bool old = epoch > credit.capturedEpoch && epoch - credit.capturedEpoch >= LocalStaleSeconds;
    return {known, known && !old, old && known ? "STALE" : ""};
}

inline void formatAmount(const QuotaMonitorApiCredit& credit, Presentation view, char* out, size_t size) {
    if (!view.known) { std::snprintf(out, size, "-- USD"); return; }
    std::snprintf(out, size, "%lu.%02lu USD", static_cast<unsigned long>(credit.remainingCents / 100),
                 static_cast<unsigned long>(credit.remainingCents % 100));
}

inline void formatDate(const QuotaMonitorApiCredit& credit, Presentation view, char* out, size_t size) {
    std::tm local{};
    if (!MosaicoClock::shanghaiTime(credit.expiresEpoch, local)) {
        std::snprintf(out, size, "%s", view.detail);
        return;
    }
    const unsigned month = static_cast<unsigned>(local.tm_mon + 1), day = static_cast<unsigned>(local.tm_mday);
    std::snprintf(out, size, "%02u/%02u%s%s", month, day, view.detail[0] ? " " : "", view.detail);
}
} // namespace mosaico_api_credit
