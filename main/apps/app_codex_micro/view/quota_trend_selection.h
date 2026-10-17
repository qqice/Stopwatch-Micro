#pragma once
#include "quota_trend_geometry.h"

namespace mosaico_trend {
constexpr size_t NoPoint = static_cast<size_t>(-1);
constexpr bool inPlot(int x, int y) { return x >= 32 && x <= 428 && y >= 0 && y <= 58; }
constexpr int clampX(int x) { return x < 32 ? 32 : x > 428 ? 428 : x; }
// Snap only to real drawable samples. Equal projected X prefers the newer sample,
// then the later slot for duplicate timestamps. Gaps never synthesize points.
constexpr size_t nearest(const QuotaTrendPoint* points, size_t count, uint32_t start, uint32_t end, int x) {
    size_t best = NoPoint; int distance = 397;
    x = clampX(x);
    for (size_t i = 0; i < count; ++i) {
        const auto p = position(points[i], start, end);
        if (!p.valid) continue;
        const int d = p.x > x ? p.x - x : x - p.x;
        if (d < distance || (d == distance && (best == NoPoint || points[i].epoch >= points[best].epoch))) {
            best = i; distance = d;
        }
    }
    return best;
}
constexpr size_t selected(const QuotaTrendPoint* points, size_t count, uint32_t start, uint32_t end, uint32_t epoch) {
    size_t best = NoPoint; uint64_t distance = UINT64_MAX;
    for (size_t i = 0; i < count; ++i) {
        if (!position(points[i], start, end).valid) continue;
        const uint64_t d = points[i].epoch > epoch ? points[i].epoch - epoch : epoch - points[i].epoch;
        if (d <= distance) { best = i; distance = d; }
    }
    return best;
}
struct TouchSelection {
    bool dragging = false;
    uint32_t epoch = UINT32_MAX; // Initial/mode-reset selection: latest real sample.
    constexpr bool begin(int x, int y) { dragging = inPlot(x,y); return dragging; }
    constexpr void cancel() { dragging = false; } // Selection survives release/lifecycle cancellation.
    constexpr void reset() { cancel(); epoch = UINT32_MAX; }
    constexpr bool blocksPageSwipe() const { return dragging; }
};
}
