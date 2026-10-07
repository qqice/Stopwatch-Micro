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
struct Snapshot {
    Config config{};
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
    int64_t requestedAt = 0;
    uint32_t attemptedRevision = 0;
    constexpr void request(Config c, int64_t now) {
        c=sanitize(c);
        if (c==state.config && !state.error) return;
        state.config=c; ++state.revision; state.pending=true; state.error=0; requestedAt=now;
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
