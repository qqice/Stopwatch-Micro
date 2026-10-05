#pragma once
#include <atomic>
#include <cstdint>
#include <ctime>
#include <sys/time.h>

namespace MosaicoClock {
// UTC epochs; never restore an NVS timestamp as if hard-off time elapsed.
constexpr int64_t FirstValidEpoch = 1704067200LL; // 2024-01-01
constexpr int64_t LastValidEpoch = 4102444800LL; // 2100-01-01, exclusive
constexpr bool validEpoch(int64_t epoch) {
    return epoch >= FirstValidEpoch && epoch < LastValidEpoch;
}
inline std::atomic<uint32_t> lastNtpEpoch{0};
inline void onSntpTime(timeval* tv) {
    if (tv && validEpoch(tv->tv_sec)) lastNtpEpoch.store(static_cast<uint32_t>(tv->tv_sec));
}
struct Snapshot { bool valid; int64_t epoch; int64_t lastNtpSync; };
inline Snapshot snapshot() {
    const int64_t epoch = static_cast<int64_t>(std::time(nullptr));
    return {validEpoch(epoch), epoch, lastNtpEpoch.load()};
}
inline bool shanghaiTime(int64_t epoch, std::tm& out) {
    if (!validEpoch(epoch)) return false;
    const time_t local = static_cast<time_t>(epoch + 8 * 3600);
    return gmtime_r(&local, &out) != nullptr;
}
static_assert(!validEpoch(0) && validEpoch(FirstValidEpoch) && !validEpoch(LastValidEpoch));
} // namespace MosaicoClock
