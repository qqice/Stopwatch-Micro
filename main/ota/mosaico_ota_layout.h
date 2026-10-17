#pragma once
#include <cstddef>
#include <cstdint>

namespace MosaicoOtaLayout {
enum class Id { Unknown, DualV1, DualV2 };
struct Partition { uint8_t type, subtype; uint32_t address, size; };
constexpr Partition V1[] = {
    {1, 2, 0xa000, 0x6000}, {1, 0, 0x10000, 0x2000}, {1, 1, 0x12000, 0x1000},
    {0, 0x10, 0x20000, 0x3e0000}, {0, 0x11, 0x400000, 0x3f0000},
    {1, 0x41, 0x7f0000, 0x320000}, {1, 0x83, 0xb10000, 0x2ee000}
};
constexpr Partition V2[] = {
    {1, 2, 0xa000, 0x6000}, {1, 0, 0x10000, 0x2000}, {1, 1, 0x12000, 0x1000},
    {0, 0x10, 0x20000, 0x600000}, {0, 0x11, 0x800000, 0x600000},
    {1, 0x40, 0x620000, 0x1e0000}, {1, 0x40, 0xe00000, 0x200000}
};
constexpr bool equal(Partition a, Partition b) {
    return a.type == b.type && a.subtype == b.subtype && a.address == b.address && a.size == b.size;
}
// Exact multiset: no missing, duplicate, extra or mixed-layout entries.
constexpr bool matches(const Partition* parts, size_t count, const Partition (&expected)[7]) {
    if (count != 7) return false;
    unsigned seen = 0;
    for (size_t i = 0; i < count; ++i) {
        unsigned bit = 0;
        for (unsigned j = 0; j < 7; ++j) if (equal(parts[i], expected[j])) bit = 1U << j;
        if (!bit || (seen & bit)) return false;
        seen |= bit;
    }
    return seen == 127;
}
constexpr Id identify(const Partition* parts, size_t count) {
    return matches(parts, count, V1) ? Id::DualV1 : matches(parts, count, V2) ? Id::DualV2 : Id::Unknown;
}
constexpr const char* name(Id id) {
    return id == Id::DualV1 ? "mosaico-dual-v1" : id == Id::DualV2 ? "mosaico-dual-v2" : "";
}
constexpr uint32_t imageCap(Id id) {
    // V1 deliberately retains its old common-slot cap, including when targeting OTA1.
    return id == Id::DualV1 ? 0x3e0000 : id == Id::DualV2 ? 0x600000 : 0;
}
constexpr bool exactSlot(Id id, Partition p) {
    return id == Id::DualV1 ? (equal(p, V1[3]) || equal(p, V1[4])) :
           id == Id::DualV2 ? (equal(p, V2[3]) || equal(p, V2[4])) : false;
}
constexpr bool imageFits(Id id, Partition target, uint32_t size) {
    return size > 0 && exactSlot(id, target) && size <= imageCap(id) && size <= target.size;
}
} // namespace MosaicoOtaLayout
