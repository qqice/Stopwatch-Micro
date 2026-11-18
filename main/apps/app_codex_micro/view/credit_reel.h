/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>

namespace mosaico_credit {
constexpr uint16_t ReelRollMs = 420, ReelStaggerMs = 45;
constexpr int ReelMaxDigits = 31;
struct Reel {
    char from[ReelMaxDigits + 1]{};
};
constexpr int reelLength(const char* s) {
    int n = 0;
    while (s[n]) ++n;
    return n;
}
constexpr char column(const char* s, int count, int grid, int i) {
    const int j = i - (grid - count);
    return j < 0 ? ' ' : s[j];
}
constexpr uint16_t duration(int columns) {
    return ReelRollMs + (columns > 0 ? (columns - 1) * ReelStaggerMs : 0);
}
constexpr int lift(int span, int columnsToRight, uint16_t elapsed) {
    const int t = static_cast<int>(elapsed) - columnsToRight * ReelStaggerMs;
    if (t <= 0) return 0;
    if (t >= ReelRollMs) return span;
    const int r = ReelRollMs - t;
    // Same cubic ease-out as the preview. Use 64-bit for span*420^3.
    constexpr int64_t cube = int64_t(ReelRollMs) * ReelRollMs * ReelRollMs;
    return static_cast<int>((span * (cube - int64_t(r) * r * r) + cube / 2) / cube);
}
} // namespace mosaico_credit
