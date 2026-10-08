#pragma once
#include <cstdint>

namespace MosaicoTwt {
enum class Mode : uint8_t { Off, Baseline, On };
enum class Stage : uint8_t { Off, Armed, Negotiating, Active, Baseline, Failed };
enum class Stop : uint8_t { None, Explicit, Expired, Unsupported, Rejected, Timeout, Lost, Ota, Awake, Driver, Invalid };
constexpr bool validLease(uint32_t seconds) { return seconds == 600 || seconds == 1800; }
constexpr int encodeRequest(Mode mode, uint32_t seconds) {
    return mode == Mode::Off ? 0 : validLease(seconds) ? static_cast<int>(mode) | (seconds == 1800 ? 4 : 0) : -1;
}
enum class CyclePhase : uint8_t { Start, Fetch, End };
enum class CycleReason : uint8_t { None, Success, Deadline, Awake, RetryBudget, NoCandidate, Cancel };
struct Cycle {
    CyclePhase phase = CyclePhase::Start;
    CycleReason reason = CycleReason::None;
    int64_t deviceUs = 0;
    uint16_t trialId = 0;
    uint32_t cycleSeq = 0, dropped = 0;
    bool associated = false;
    uint8_t fetchFlags = 0; // quota attempted/OK, history attempted/OK: bits 0/1/2/3
};
struct CycleRing {
    Cycle entries[32]{};
    uint8_t read = 0, write = 0;
    uint32_t dropped = 0;
    constexpr void push(Cycle value) {
        const uint8_t next = (write + 1) % 32;
        if (next == read) { ++dropped; return; }
        value.dropped = dropped; entries[write] = value; write = next;
    }
    constexpr bool pop(Cycle& value) {
        if (read == write) return false;
        value = entries[read]; read = (read + 1) % 32; return true;
    }
};
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
    uint32_t leaseSeconds = 600;
    int64_t expiryUs = 0, setupDeadlineUs = 0, cleanupDeadlineUs = 0;
    uint8_t cleanupStage = 0; // 0 clean, 1 setup drain, 2 teardown ack, 3 ioctl barrier, 4 PS restore, 5 failed
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
    constexpr bool start(Mode mode, int64_t now, uint32_t leaseSeconds = 600) {
        if (mode == Mode::Off) { state.requested = mode; end(Stop::Explicit); return true; }
        // No identifier reuse this boot: stale SDK responses cannot become a new trial.
        if (!validLease(leaseSeconds) || nextId == 32767) return false;
        state = Snapshot{};
        state.requested = mode; state.id = ++nextId;
        state.leaseSeconds = leaseSeconds;
        state.stage = Stage::Armed; state.expiryUs = now + int64_t(leaseSeconds) * 1000000;
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
