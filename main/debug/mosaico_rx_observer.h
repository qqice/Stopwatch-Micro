// SPDX-License-Identifier: MIT
#pragma once
#include <cstddef>
#include <cstdint>

namespace mosaico_rx_observer {
enum class Checksum : uint8_t { Unknown, Valid, Invalid };
struct Record {
    uint32_t millis = 0, src = 0, dst = 0, seq = 0, ack = 0, receiverIndex = 0;
    uint64_t tsf = 0;
    uint32_t tsfMillis = 0;
    uint16_t srcPort = 0, dstPort = 0, ipLength = 0, transportPayloadLength = 0;
    uint8_t protocol = 0, tcpFlags = 0, wgType = 0;
    Checksum ipChecksum = Checksum::Unknown, tcpChecksum = Checksum::Unknown;
};
struct Snapshot {
    bool active = false, queued = false, reporting = false;
    uint32_t deadlineMs = 0, seen = 0, malformed = 0, fragments = 0, tcp = 0;
    uint32_t udp = 0, otherUdp = 0, wireguard[4] = {}, ringDrops = 0;
    uint32_t rxEvents = 0, txEvents = 0, eventMillis = 0;
    uint32_t wakeEvents = 0, suspendEvents = 0, probeEvents = 0, twtEventMillis = 0;
    int32_t lastError = 0;
    uint64_t tsf = 0;
    uint32_t tsfMillis = 0;
};
// Counts are hook observations, not stack acceptance; 32-bit counters may wrap.
// Traffic events are best effort (SDK posting timeout is zero).
bool request(bool on, uint32_t ttlSeconds = 1200);
void service(bool allowEnable = true); // Network owner only; false atomically rejects queued enable.
Snapshot snapshot();
std::size_t drain(Record* out, std::size_t capacity); // At most 32 records.

namespace detail {
struct MetadataRing {
    Record records[128]{};
    uint16_t head = 0, count = 0;
    bool push(const Record& r) {
        if (count == 128) return false;
        records[(head + count) % 128] = r; ++count; return true;
    }
    std::size_t pop(Record* out, std::size_t capacity) {
        if (!out) return 0;
        if (capacity > 32) capacity = 32;
        const std::size_t n = capacity < count ? capacity : count;
        for (std::size_t i = 0; i < n; ++i) { out[i] = records[head]; head = (head + 1) % 128; }
        count -= uint16_t(n); return n;
    }
};
using Read = bool (*)(void*, uint16_t, uint8_t*, uint16_t);
enum class Parsed : uint8_t { Ignore, Malformed, Fragment, Tcp, OtherUdp, Wireguard };
inline uint16_t be16(const uint8_t* p) { return uint16_t((uint16_t(p[0]) << 8) | p[1]); }
inline uint32_t be32(const uint8_t* p) { return (uint32_t(p[0]) << 24) | (uint32_t(p[1]) << 16) | (uint32_t(p[2]) << 8) | p[3]; }
inline uint32_t le32(const uint8_t* p) { return uint32_t(p[0]) | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24); }
inline uint32_t sum(const uint8_t* p, uint16_t n, uint32_t s = 0) {
    for (uint16_t i = 0; i < n; i += 2) s += uint16_t(uint16_t(p[i]) << 8) | (i + 1 < n ? p[i + 1] : 0);
    return s;
}
inline bool validSum(uint32_t s) { while (s >> 16) s = (s & 65535) + (s >> 16); return s == 65535; }
inline bool alive(uint32_t now, uint32_t deadline) { return int32_t(now - deadline) < 0; }
// No transport body is copied: options and public WireGuard header fields only.
inline Parsed parse(Read read, void* ctx, uint16_t available, uint32_t localIp, Record& r) {
    uint8_t ip[60] = {}, tr[60] = {};
    if (available < 20 || !read(ctx, 0, ip, 20) || (ip[0] >> 4) != 4) return Parsed::Malformed;
    const uint16_t ih = uint16_t(ip[0] & 15) * 4, total = be16(ip + 2);
    if (ih < 20 || ih > 60 || total < ih || total > available || (ih > 20 && !read(ctx, 20, ip + 20, ih - 20))) return Parsed::Malformed;
    r.src = be32(ip + 12); r.dst = be32(ip + 16);
    if (!localIp || r.dst != localIp || (r.dst >> 28) == 14 || r.dst == 0xffffffff) return Parsed::Ignore;
    r.ipLength = total; r.protocol = ip[9]; r.ipChecksum = validSum(sum(ip, ih)) ? Checksum::Valid : Checksum::Invalid;
    if (be16(ip + 6) & 0x3fff) return Parsed::Fragment;
    const uint16_t len = total - ih;
    if (r.protocol == 6) {
        if (len < 20 || !read(ctx, ih, tr, 20)) return Parsed::Malformed;
        const uint16_t th = uint16_t(tr[12] >> 4) * 4;
        if (th < 20 || th > 60 || th > len || (th > 20 && !read(ctx, ih + 20, tr + 20, th - 20))) return Parsed::Malformed;
        r.srcPort = be16(tr); r.dstPort = be16(tr + 2); r.seq = be32(tr + 4); r.ack = be32(tr + 8); r.tcpFlags = tr[13]; r.transportPayloadLength = len - th;
        if (!r.transportPayloadLength && (r.tcpFlags & 0x12) == 0x12) {
            uint32_t s = sum(ip + 12, 8) + 6 + len;
            r.tcpChecksum = validSum(sum(tr, th, s)) ? Checksum::Valid : Checksum::Invalid;
        }
        return Parsed::Tcp;
    }
    if (r.protocol != 17) return Parsed::Ignore;
    if (len < 8 || !read(ctx, ih, tr, 8)) return Parsed::Malformed;
    const uint16_t ul = be16(tr + 4);
    if (ul < 8 || ul > len) return Parsed::Malformed;
    r.srcPort = be16(tr); r.dstPort = be16(tr + 2); r.transportPayloadLength = ul - 8;
    if (ul < 12 || !read(ctx, ih + 8, tr, 4)) return Parsed::OtherUdp;
    const uint32_t type = le32(tr);
    // Fixed protocol lengths include authentication tags, which are never read.
    const uint16_t minimum[] = {0, 148, 92, 64, 32};
    if (type < 1 || type > 4 || ul - 8 < minimum[type]) return Parsed::OtherUdp;
    r.wgType = uint8_t(type);
    const uint16_t header = type == 1 ? 8 : (type == 2 ? 12 : 8);
    if (!read(ctx, ih + 8, tr, header)) return Parsed::Malformed;
    if (type != 1) r.receiverIndex = le32(tr + (type == 2 ? 8 : 4));
    return Parsed::Wireguard;
}
} // namespace detail
} // namespace mosaico_rx_observer
