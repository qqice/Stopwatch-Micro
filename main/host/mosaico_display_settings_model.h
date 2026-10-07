#pragma once
#include <array>
#include <cstdint>

namespace MosaicoDisplay {
struct Config {
    uint32_t chargeTimeoutSeconds = 60, batteryTimeoutSeconds = 60;
    uint8_t chargeBrightness = 80, batteryBrightness = 80, lockBrightness = 8;
    bool burnIn = true;
    uint8_t lockWifiMinutes = 5, lockBleMinutes = 1;
};
constexpr bool operator==(const Config& a, const Config& b) {
    return a.chargeTimeoutSeconds == b.chargeTimeoutSeconds && a.batteryTimeoutSeconds == b.batteryTimeoutSeconds &&
        a.chargeBrightness == b.chargeBrightness && a.batteryBrightness == b.batteryBrightness &&
        a.lockBrightness == b.lockBrightness && a.burnIn == b.burnIn &&
        a.lockWifiMinutes == b.lockWifiMinutes && a.lockBleMinutes == b.lockBleMinutes;
}
constexpr bool validLockMinutes(uint32_t m) { return m==1 || m==2 || m==5 || m==10 || m==15 || m==30 || m==60; }
constexpr uint32_t lockIntervalMs(uint32_t m, uint8_t fallback) { return (validLockMinutes(m) ? m : fallback) * 60000U; }
constexpr Config sanitize(Config c) {
    if (!validLockMinutes(c.lockWifiMinutes)) c.lockWifiMinutes=5;
    if (!validLockMinutes(c.lockBleMinutes)) c.lockBleMinutes=1;
    const auto t = c.chargeTimeoutSeconds;
    if (t != 0 && t != 15 && t != 30 && t != 60 && t != 120 && t != 300 && t != 600) c.chargeTimeoutSeconds = 60;
    const auto b = c.batteryTimeoutSeconds;
    if (b != 15 && b != 30 && b != 45 && b != 60) c.batteryTimeoutSeconds = 60;
    c.chargeBrightness = c.chargeBrightness < 10 ? 10 : c.chargeBrightness > 100 ? 100 : c.chargeBrightness;
    c.batteryBrightness = c.batteryBrightness < 10 ? 10 : c.batteryBrightness > 100 ? 100 : c.batteryBrightness;
    c.lockBrightness = c.lockBrightness > 100 ? 100 : c.lockBrightness;
    return c;
}
// Validate before narrowing: rejected patches leave the entire config unchanged.
constexpr bool fieldEquals(const char* a, const char* b) {
    if (!a || !b) return false;
    while (*a && *a==*b) { ++a; ++b; }
    return *a==*b;
}
constexpr bool patch(Config& c, const char* field, int64_t value) {
    if (!field || value < 0 || value > 600) return false;
    Config next=c;
    if (fieldEquals(field,"charge_timeout")) next.chargeTimeoutSeconds=static_cast<uint32_t>(value);
    else if (fieldEquals(field,"battery_timeout")) next.batteryTimeoutSeconds=static_cast<uint32_t>(value);
    else if (fieldEquals(field,"charge_brightness")) { if(value>100) return false; next.chargeBrightness=static_cast<uint8_t>(value); }
    else if (fieldEquals(field,"battery_brightness")) { if(value>100) return false; next.batteryBrightness=static_cast<uint8_t>(value); }
    else if (fieldEquals(field,"lock_brightness")) { if(value>100) return false; next.lockBrightness=static_cast<uint8_t>(value); }
    else if (fieldEquals(field,"burn_in")) { if(value>1) return false; next.burnIn=value!=0; }
    else if (fieldEquals(field,"lock_wifi_minutes")) { if(value>60) return false; next.lockWifiMinutes=static_cast<uint8_t>(value); }
    else if (fieldEquals(field,"lock_ble_minutes")) { if(value>60) return false; next.lockBleMinutes=static_cast<uint8_t>(value); }
    else return false;
    if (!(sanitize(next)==next)) return false;
    c=next; return true;
}
struct Snapshot {
    Config config{}, effectiveConfig{};
    bool temporary = false;
    uint32_t runtimeRevision = 1, remainingLeaseSeconds = 0;
    uint32_t revision = 1, savedRevision = 0;
    bool pending = false;
    int32_t error = 0;
};
// Defaults loaded without a saved blob need no write. Accepted edits must be
// acknowledged by the persistence owner before another action can reboot.
constexpr bool rebootSaveReady(const Snapshot& s) {
    return !s.pending && !s.error && (s.savedRevision == s.revision ||
        (s.revision == 1 && s.savedRevision == 0));
}
using Blob = std::array<uint8_t, 24>;
constexpr void put32(Blob& b, unsigned p, uint32_t v) { for (unsigned i=0;i<4;++i) b[p+i]=static_cast<uint8_t>(v>>(8*i)); }
constexpr uint32_t get32(const Blob& b, unsigned p) {
    uint32_t v=0; for (unsigned i=0;i<4;++i) v |= uint32_t(b[p+i])<<(8*i); return v;
}
constexpr uint32_t crc(const Blob& b) {
    uint32_t v=0xffffffffU;
    for (unsigned i=0;i<20;++i) { v ^= b[i]; for (unsigned k=0;k<8;++k) v=(v>>1)^((v&1)?0xedb88320U:0); }
    return ~v;
}
constexpr Blob encode(Config c) {
    Blob b{}; put32(b,0,0x4453504dU); b[4]=2; b[5]=c.burnIn; b[6]=c.chargeBrightness;
    b[7]=c.batteryBrightness; b[8]=c.lockBrightness; b[9]=c.lockWifiMinutes; b[10]=c.lockBleMinutes; put32(b,12,c.chargeTimeoutSeconds);
    put32(b,16,c.batteryTimeoutSeconds); put32(b,20,crc(b)); return b;
}
constexpr bool decode(const Blob& b, Config& out) {
    if (get32(b,0)!=0x4453504dU || (b[4]!=1 && b[4]!=2) || b[5]>1 || (b[4]==1 && (b[9] || b[10])) || b[11] || get32(b,20)!=crc(b)) return false;
    Config c{get32(b,12),get32(b,16),b[6],b[7],b[8],bool(b[5])};
    if (b[4]==2) { c.lockWifiMinutes=b[9]; c.lockBleMinutes=b[10]; }
    if (!(sanitize(c)==c)) return false;
    out=c; return true;
}
// One attempt per revision. A later request is a new event, not a busy retry.
struct Model {
    Snapshot state{};
    Config overlay{};
    int64_t leaseUntil = 0;
    uint32_t runtimeRevision = 1;
    constexpr Config effective() const { return leaseUntil ? overlay : state.config; }
    constexpr bool restore() {
        if (!leaseUntil) return false;
        leaseUntil=0; ++runtimeRevision; return true;
    }
    constexpr bool expire(int64_t now, bool unsafe=false) {
        return leaseUntil && (unsafe || now>=leaseUntil) ? restore() : false;
    }
    constexpr Snapshot snapshot(int64_t now) const {
        Snapshot out=state; out.effectiveConfig=effective(); out.temporary=leaseUntil!=0;
        out.runtimeRevision=runtimeRevision;
        out.remainingLeaseSeconds=leaseUntil && now<leaseUntil ? static_cast<uint32_t>((leaseUntil-now+999999)/1000000) : 0;
        return out;
    }
    constexpr bool setTemporary(const char* field, int64_t value, uint32_t seconds, int64_t now) {
        if (seconds<30 || seconds>600) return false;
        // Invalid input must not cancel, extend or otherwise mutate a lease.
        Config next=leaseUntil && now<leaseUntil ? overlay : state.config;
        if (!patch(next,field,value)) return false;
        expire(now); overlay=next; leaseUntil=now+int64_t(seconds)*1000000;
        ++runtimeRevision; return true;
    }
    constexpr bool requestPersistentField(const char* field, int64_t value, int64_t now) {
        Config next=state.config;
        if (!patch(next,field,value)) return false;
        request(next,now); return true;
    }
    constexpr bool saveTemporary(bool confirmed, int64_t now) {
        if (!confirmed) return false;
        expire(now); const Config next=effective(); request(next,now); return true;
    }
    int64_t requestedAt = 0;
    uint32_t attemptedRevision = 0;
    constexpr void request(Config c, int64_t now) {
        restore();
        c=sanitize(c);
        if (c==state.config && !state.error) return;
        state.config=c; ++runtimeRevision; ++state.revision; state.pending=true; state.error=0; requestedAt=now;
    }
    constexpr bool due(int64_t now) const {
        return state.pending && attemptedRevision!=state.revision && now-requestedAt>=1000000;
    }
    constexpr void completed(uint32_t revision, int32_t error) {
        if (!error) state.savedRevision=revision;
        if (revision==state.revision) { state.pending=false; state.error=error; }
    }
};
}
