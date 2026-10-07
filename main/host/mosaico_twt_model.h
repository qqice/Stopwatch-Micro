#pragma once
#include <cstdint>

namespace MosaicoTwt {
enum class Mode : uint8_t { Off, Baseline, On };
enum class Stage : uint8_t { Off, Armed, Negotiating, Active, Baseline, Failed };
enum class Stop : uint8_t { None, Explicit, Expired, Unsupported, Rejected, Timeout, Lost, Ota, Awake, Driver, Invalid };
struct Result {
    uint16_t id = 0, mantissa = 0;
    uint8_t flow = 0, exponent = 0, duration = 0, unit = 0, reason = 0;
    int status = 0;
    uint64_t targetWakeUs = 0;
};
struct Snapshot {
    Mode requested = Mode::Off;
    Stage stage = Stage::Off;
    Stop stop = Stop::None;
    Stop lastFailure = Stop::None;
    uint16_t id = 0;
    int64_t expiryUs = 0, setupDeadlineUs = 0;
    bool apAx = false, associated = false, cleanupPending = false, cleanupFailed = false;
    int phy = -1, error = 0, restoreError = 0, teardownError = 0;
    Result actual{};
    uint64_t intervalUs = 0, durationUs = 0, fetchUs = 0;
    uint32_t fetchAttempts = 0, fetchOk = 0, losses = 0, lateEvents = 0;
};
struct Model {
    Snapshot state{};
    uint16_t nextId = 0;
    constexpr bool live() const { return state.stage == Stage::Armed || state.stage == Stage::Negotiating ||
        state.stage == Stage::Active || state.stage == Stage::Baseline; }
    constexpr void end(Stop why) {
        state.stop = why;
        state.stage = why == Stop::Explicit || why == Stop::Expired || why == Stop::Ota || why == Stop::Awake
            ? Stage::Off : Stage::Failed;
        if (state.stage == Stage::Failed) state.lastFailure = why;
        state.expiryUs = state.setupDeadlineUs = 0;
    }
    constexpr bool start(Mode mode, int64_t now) {
        if (mode == Mode::Off) { state.requested = mode; end(Stop::Explicit); return true; }
        // No identifier reuse this boot: stale SDK responses cannot become a new trial.
        if (nextId == 32767) return false;
        state = Snapshot{};
        state.requested = mode; state.id = ++nextId;
        state.stage = Stage::Armed; state.expiryUs = now + 600LL * 1000000;
        state.setupDeadlineUs = now + 10LL * 1000000;
        return true;
    }
    constexpr bool check(int64_t now, bool ota, bool locked) {
        if (!live()) return false;
        if (ota) end(Stop::Ota);
        else if (now >= state.expiryUs) end(Stop::Expired);
        else if (!locked) end(Stop::Awake);
        else if ((state.stage == Stage::Armed || state.stage == Stage::Negotiating) && now >= state.setupDeadlineUs) end(Stop::Timeout);
        return live();
    }
    constexpr bool accept(const Result& r) {
        if (state.stage != Stage::Negotiating || r.id != state.id) { ++state.lateEvents; return false; }
        state.actual = r;
        state.intervalUs = r.exponent <= 31 ? uint64_t(r.mantissa) << r.exponent : 0;
        state.durationUs = uint64_t(r.duration) * (r.unit ? 1024 : 256);
        if (r.status != 1) end(Stop::Rejected); // ESP_OK is NOT negotiation success.
        else if (!r.mantissa || !r.duration || r.exponent > 31 || r.flow > 7 || r.unit > 1 ||
                 state.intervalUs > 2000000 || state.durationUs >= state.intervalUs) end(Stop::Invalid);
        else { state.stage = Stage::Active; state.setupDeadlineUs = 0; return true; }
        return false;
    }
};
} // namespace MosaicoTwt
