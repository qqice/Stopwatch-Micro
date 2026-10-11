// SPDX-License-Identifier: MIT
#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>
namespace mosaico_wifi_rx_observer {
struct Record {
    uint64_t localUs = 0; // Monotonic esp_timer clock; native RX clock can drift during sleep.
    uint32_t timestampUs = 0;
    uint16_t length = 0, fc = 0, sequence = 0;
    uint8_t type = 0, subtype = 0, fragment = 0, tid = 255, rxState = 0;
    int8_t rssi = 0;
    bool retry = false, protectedFrame = false;
};
struct Snapshot {
    bool active = false, queued = false, actualPromisc = false, actualKnown = false;
    bool cleanupPending = false, psKnown = false;
    uint32_t deadlineMs = 0, seen = 0, filtered = 0, invalid = 0, target = 0, drops = 0;
    int32_t lastError = 0, originalPs = -1, currentPs = -1;
};
bool request(bool on, uint32_t ttlSeconds = 120);
void service(bool allowEnable = true); // Only network owner. SDK calls outside locks.
Snapshot snapshot();
std::size_t drain(Record* out, std::size_t capacity);
namespace detail {
inline bool canStopRadio(const Snapshot& s) {
    return !s.cleanupPending || (s.actualKnown && !s.actualPromisc);
}
inline bool alive(uint32_t now, uint32_t deadline) { return int32_t(now - deadline) < 0; }
enum class Command { Invalid, On, Off, Status, Drain };
inline Command arguments(const char* action, const char* arg, const char* extra, uint32_t& ttl) {
    if (!action || extra) return Command::Invalid;
    if (!std::strcmp(action, "on")) {
        if (!arg || !*arg) return Command::Invalid;
        uint32_t n=0;
        for (; *arg; ++arg) {
            if (*arg<'0' || *arg>'9') return Command::Invalid;
            const uint32_t d=uint32_t(*arg-'0');
            if (n>(120-d)/10) return Command::Invalid;
            n=n*10+d;
        }
        if (!n || n>120) return Command::Invalid;
        ttl=n; return Command::On;
    }
    if (arg) return Command::Invalid;
    if (!std::strcmp(action,"off")) return Command::Off;
    if (!std::strcmp(action,"status")) return Command::Status;
    if (!std::strcmp(action,"drain")) return Command::Drain;
    return Command::Invalid;
}
using Read = bool (*)(void*, uint16_t, uint8_t*, uint16_t);
enum class Parsed { Invalid, Filtered, Target };
inline uint16_t le16(const uint8_t* p) { return uint16_t(p[0]) | uint16_t(uint16_t(p[1])<<8); }
// available excludes FCS. Only supported 3-address DATA/QoS DATA headers are read.
// HT control length is checked, never read. Body, crypto and FCS are never read.
inline Parsed parse(Read read, void* ctx, uint16_t available, const uint8_t sta[6], const uint8_t ap[6], Record& r) {
    uint8_t h[26]{};
    if (available<2 || !read(ctx,0,h,2)) return Parsed::Invalid;
    r.fc=le16(h); r.type=(r.fc>>2)&3; r.subtype=(r.fc>>4)&15;
    if ((r.fc&3) || r.type!=2 || (r.subtype!=0 && r.subtype!=8) || (r.fc&0x0300)==0x0300)
        return Parsed::Invalid;
    if (available<24 || !read(ctx,2,h+2,22)) return Parsed::Invalid;
    const bool qos=r.subtype==8;
    const uint16_t minimum=uint16_t(24+(qos?2:0)+((qos && (r.fc&0x8000))?4:0));
    if (available<minimum) return Parsed::Invalid;
    if (qos && !read(ctx,24,h+24,2)) return Parsed::Invalid;
    if ((r.fc&0x0300)!=0x0200 || std::memcmp(h+4,sta,6) || std::memcmp(h+10,ap,6)) return Parsed::Filtered;
    r.retry=(r.fc&0x0800)!=0; r.protectedFrame=(r.fc&0x4000)!=0;
    const uint16_t sc=le16(h+22); r.sequence=sc>>4; r.fragment=sc&15;
    r.tid=qos ? h[24]&15 : 255;
    return Parsed::Target;
}
struct MetadataRing {
    Record records[128]{}; uint16_t head=0, count=0;
    void clear() { head=count=0; }
    bool push(const Record& r) { if(count==128)return false; records[(head+count)%128]=r; ++count; return true; }
    std::size_t pop(Record* out, std::size_t capacity) {
        if(!out)return 0;
        if(capacity>16)capacity=16;
        const auto n=capacity<count?capacity:count;
        for(std::size_t i=0;i<n;++i){out[i]=records[head];head=(head+1)%128;}
        count-=uint16_t(n);return n;
    }
};
}
}
