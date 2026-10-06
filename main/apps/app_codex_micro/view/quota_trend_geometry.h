#pragma once
#include <host/token_history.h>

namespace mosaico_trend {
template <size_t N>
constexpr const QuotaTrendPoint* latest(const std::array<QuotaTrendPoint,N>& points) {
    for (size_t i=N; i>0; --i) if (points[i-1].valid) return &points[i-1];
    return nullptr;
}
struct Position { int x = 0, y = 0; bool valid = false; };
// Fixed percentage axis, no normalization to the sample/token maximum.
constexpr Position position(const QuotaTrendPoint& p, uint32_t start, uint32_t end) {
    if (!p.valid || p.remainingBasisPoints > 10000 || end <= start || p.epoch < start || p.epoch > end) return {};
    return {32 + static_cast<int>(static_cast<uint64_t>(p.epoch - start) * 396 / (end - start)),
            54 - static_cast<int>(static_cast<uint32_t>(p.remainingBasisPoints) * 48 / 10000), true};
}
constexpr bool connects(const QuotaTrendPoint& a, const QuotaTrendPoint& b, uint32_t start, uint32_t end) {
    return position(a,start,end).valid && position(b,start,end).valid && b.epoch > a.epoch &&
           !b.breakBefore && !b.resetBefore;
}
}
