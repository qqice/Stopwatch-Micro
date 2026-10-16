#pragma once
#include <cstdint>

// Pure bounded coordinator; physical POR recurrence prevention requires separate
// integration acceptance. ColdFactory is an outer boot-latched privilege, not recovery.
namespace mosaico_fcc_limit {
constexpr char journalKey[] = "fcc_fix_v1";
constexpr char coldJournalKey[] = "fcc_boot_v1";
enum class Mode : uint8_t { Runtime, ColdFactory };
constexpr uint16_t limitBit = 0x0100;
enum class State : uint8_t { Pending = 1, Failed = 2, Verified = 3 };
enum class Load : uint8_t { Absent, Present, Invalid };
enum class Result : uint8_t { Rejected, Failed, Verified };
enum Intent : uint16_t {
    Unseal = 1, Full = 2, Enter = 4, Config = 8, Fcc = 16,
    RestoreConfig = 32, RestoreFcc = 64, Exit = 128, Seal = 256,
    TargetVerified = 512, Dc = 1024, RestoreDc = 2048
};
struct Identity {
    uint8_t mac[6]{};
    uint16_t device = 0;
    bool historicalNominalProof = false, historicalAccessProof = false;
    // Backend authenticates permanent runtime Verified profile, legacy boot
    // history, outer boot attempted=true + persisted reloadPending + normalized
    // SEC3. Outer boot latch must prevent same-boot reentry and latch any failure.
    bool authorizedColdProfile = false;
    uint16_t coldOriginalConfig = 0xffff; // from authenticated runtime before.config
};
struct Quiet {
    uint16_t operation = 0, soc = 0, mv = 0, standardDc = 0, standardFcc = 0;
    int16_t temperatureDeciK = 0, current = 0, average = 0;
};
struct Security { uint8_t sec = 0; bool cfg = false, cal = true, initialized = false; };
struct Dm { uint16_t config = 0, dc = 0, fcc = 0; };
constexpr bool same(Dm a, Dm b) {
    return a.config == b.config && a.dc == b.dc && a.fcc == b.fcc;
}
// Schema only: backend MUST encode fields canonically (not raw struct padding),
// CRC protect the complete record AND bind it to the current device MAC. Load
// must distinguish absent from corrupt/unreadable. Save must commit then read,
// validate CRC/schema/MAC and compare every field. Never use old journal keys or
// validators. No backend call may retry a key pair or a mutating gauge command.
struct Journal {
    uint16_t schema = 1;
    State state = State::Pending;
    uint8_t mac[6]{};
    uint16_t intents = 0;
    bool backupValid = false, fullAccessVerified = false;
    Dm before{}, target{};
};
constexpr bool quiet(const Quiet& q) {
    return q.soc >= 1 && q.soc <= 100 && q.mv >= 4150 && q.mv <= 4350 &&
        q.temperatureDeciK >= 2832 && q.temperatureDeciK <= 3181 &&
        q.current >= -3 && q.current <= 3 && q.average >= -3 && q.average <= 3 &&
        !(q.operation & 0x0401) && (q.operation & 0x0020) &&
        q.standardDc == 65 && q.standardFcc == 100;
}
constexpr bool coldQuiet(const Quiet& q) {
    return q.mv >= 3500 && q.mv <= 4350 &&
        q.temperatureDeciK >= 2832 && q.temperatureDeciK <= 3181 &&
        q.current >= -130 && q.current <= 130 && q.average >= -130 && q.average <= 130 &&
        !(q.operation & 0x0401) && (q.operation & 0x0020) &&
        q.standardDc == 3000 && q.standardFcc == 3000;
}
constexpr bool normal(Security s) { return s.initialized && !s.cal && !s.cfg; }

// Runtime-only sampled quiet qualification. Every observation must be fresh
// read success plus the external runtime gate; the backend enforces those before
// calling sample(). Any gap/backward clock/physical violation poisons this call.
struct QuietQualification {
    static constexpr int64_t durationUs=120000000;
    static constexpr int64_t maxGapUs=2500000;
    bool started=false,failed=false;
    int64_t firstUs=0,lastUs=0;
    constexpr bool sample(const Quiet& q,Security s,int64_t now) {
        if(failed)return false;
        if(now<0 || !quiet(q) || ((q.operation>>1)&3)!=3 || s.sec!=3 || !normal(s) ||
           (started&&(now<lastUs||now-lastUs>maxGapUs))) {
            failed=true;
            return false;
        }
        if(!started) { firstUs=now;started=true; }
        lastUs=now;
        return true;
    }
    constexpr bool ready(int64_t now) const {
        return started&&!failed&&now>=lastUs&&now-lastUs<=maxGapUs&&now-firstUs>=durationUs;
    }
};

// Backend bool reads must be authenticated/complete; false ACK is ambiguous,
// never permission to retry. verifyTarget(target, false) requires two fresh
// authenticated exact DM snapshots only (standard FCC may lag in CFG).
// verifyTarget(target, true) requires fresh DEVICE=0x0220, standard DC/FCC=65,
// SEALED/nonCFG/initialized/nonCAL. If sealed DM is denied, it may use ONLY
// this journal freshly read back with valid CRC/schema/current MAC, exact target,
// Pending state, backupValid/fullAccessVerified, Unseal/Full/Enter/Config/TargetVerified intents
// (Fcc whenever before.fcc!=65, and Dc for ColdFactory). That is durable FA-DM proof, NOT a fresh
// post-seal DM read. Legacy records or expected values alone are insufficient.
// Cleanup calls are unconditional once after an access attempt, even if their
// journal intent cannot persist: closing access must not depend on NVS health.
// reseal is one guarded API attempt: backend must freshly observe initialized,
// nonCFG/nonCAL and known SEC1/2/3 before any Seal wire command. Unknown or
// unreadable status must return false without sending Seal or clearing CFG.
// All waiting/commands are bounded by the backend, including waitCfg.
template<class Backend> class Coordinator {
    Backend& b;
    Journal j{};
    Mode mode;
    constexpr const char* key() const {
        return mode == Mode::Runtime ? journalKey : coldJournalKey;
    }
    constexpr bool quietGate(const Quiet& q) const {
        return mode == Mode::Runtime ? quiet(q) : coldQuiet(q);
    }
    constexpr bool known(Dm value) const {
        return (value.config == j.before.config || value.config == j.target.config) &&
            (value.dc == j.before.dc || value.dc == j.target.dc) &&
            (value.fcc == j.before.fcc || value.fcc == j.target.fcc);
    }
    bool accessed = false, exited = false;
    constexpr bool save() { return b.journalSaveReadback(key(), j); }
    constexpr bool intent(uint16_t flag) {
        if (j.intents & flag) return false;
        j.intents |= flag;
        return save();
    }
    constexpr bool observe(Security& s) { return b.observedSecurity(s); }
    constexpr bool fullCfg() {
        Security s{};
        return observe(s) && s.sec == 1 && s.cfg && !s.cal && s.initialized;
    }
    constexpr void rollback() {
        // Never reopen access, enter CFG, or write after an automatic seal.
        Dm now{};
        if (!j.backupValid || !fullCfg() || !b.dmSnapshot(now) || !known(now)) return;
        if (now.config != j.before.config) {
            if (!intent(RestoreConfig) || !fullCfg()) return;
            if (!b.writeConfigOnly(j.before.config)) return;
        }
        if (!fullCfg() || !b.dmSnapshot(now) || !known(now) ||
            now.config != j.before.config) return;
        if (now.dc != j.before.dc) {
            if (mode != Mode::ColdFactory || !intent(RestoreDc) || !fullCfg()) return;
            if (!b.writeDcOnly(j.before.dc)) return;
        }
        if (!fullCfg() || !b.dmSnapshot(now) || !known(now) ||
            now.config != j.before.config || now.dc != j.before.dc) return;
        if (now.fcc != j.before.fcc && intent(RestoreFcc) && fullCfg())
            (void)b.writeFccOnly(j.before.fcc);
    }
    constexpr bool cleanup() {
        if (!accessed) return false;
        bool ok = true;
        if (!exited) {
            exited = true;
            if (!intent(Exit)) ok = false;
            if (!b.exitCfg()) ok = false;
        }
        if (!intent(Seal)) ok = false;
        if (!b.reseal()) ok = false;
        Security s{};
        return observe(s) && s.sec == 3 && normal(s) && ok;
    }
    constexpr Result fail() {
        if (accessed) { rollback(); (void)cleanup(); }
        j.state = State::Failed;
        (void)save(); // Pending is deliberately unresolved if this fails.
        return Result::Failed;
    }
public:
    constexpr explicit Coordinator(Backend& backend, Mode selected = Mode::Runtime)
        : b(backend), mode(selected) {}
    constexpr Result run() {
        Identity id{}; Quiet q{}; Security s{}; Journal old{};
        // Runtime rejects every record. Cold permits only authenticated Verified
        // from an earlier boot; its outer one-shot latch is mandatory authorization.
        const Load loaded = b.journalLoad(key(), old);
        if ((loaded != Load::Absent && !(mode == Mode::ColdFactory &&
                loaded == Load::Present && old.state == State::Verified)) || !b.identity(id) ||
            (mode == Mode::ColdFactory && !id.authorizedColdProfile) ||
            id.device != 0x0220 || !id.historicalNominalProof ||
            !id.historicalAccessProof || !b.quietSnapshot(q) || !quietGate(q) ||
            ((q.operation >> 1) & 3) != 3 || !observe(s) ||
            s.sec != 3 || !normal(s)) return Result::Rejected;
        if(mode==Mode::Runtime&&!b.qualifyRuntimeQuiet())return Result::Rejected;
        for (unsigned i = 0; i < 6; ++i) j.mac[i] = id.mac[i];
        if (!intent(Unseal)) return Result::Failed;
        accessed = true;
        if (!b.sendUnsealPair() || !observe(s) || s.sec != 2 || !normal(s)) return fail();
        // A fresh physical quiet gate before the next access escalation.
        if (!b.quietSnapshot(q) || !quietGate(q) || !intent(Full)) return fail();
        if (!b.sendFullPair() || !observe(s) || s.sec != 1 || !normal(s)) return fail();
        j.fullAccessVerified = true;
        Dm first{}, second{};
        if (!b.dmSnapshot(first) || !b.dmSnapshot(second) || !same(first, second) ||
            first.config > 0x1fff ||
            (mode == Mode::Runtime ? (first.dc != 65 || (first.fcc != 65 && first.fcc != 100)) :
                (first.dc != 3000 || first.fcc != 3000 ||
                 first.config != (id.coldOriginalConfig & ~limitBit))) ||
            (first.config & limitBit)) return fail();
        j.before = first;
        j.target = {static_cast<uint16_t>(first.config | limitBit), 65, 65};
        j.backupValid = true;
        if (!intent(Enter) || !b.enterCfg() || !b.waitCfg() || !fullCfg()) return fail();
        if (!intent(Config) || !fullCfg() || !b.writeConfigOnly(j.target.config)) return fail();
        if (mode == Mode::ColdFactory &&
            (!intent(Dc) || !fullCfg() || !b.writeDcOnly(65))) return fail();
        if (first.fcc != 65 &&
            (!intent(Fcc) || !fullCfg() || !b.writeFccOnly(65))) return fail();
        if (!b.verifyTarget(j.target, false) || !intent(TargetVerified)) return fail();
        // Successful exit path also runs cleanup exactly once; do not call fail
        // afterwards (which would duplicate closing commands).
        const bool closed = cleanup();
        const bool verified = closed && b.verifyTarget(j.target, true);
        j.state = verified ? State::Verified : State::Failed;
        const bool committed = save();
        return verified && committed ? Result::Verified : Result::Failed;
    }
};
} // namespace mosaico_fcc_limit
