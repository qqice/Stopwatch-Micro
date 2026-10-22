#pragma once
#include "mosaico_gauge_recovery_model.h"
#include <array>

// Pure cached-data codec. No device I/O, storage, allocation or unit attestation.
namespace mosaico_factory_observed_page {
namespace recovery = mosaico_gauge_recovery;
inline constexpr size_t perPage = 32;
inline constexpr size_t pageCount = (recovery::fieldCount + perPage - 1) / perPage;
inline constexpr size_t fieldBytes = 7;
struct Page {
    std::array<uint8_t, perPage * fieldBytes> bytes{};
    size_t first = 0, count = 0, size = 0;
    uint32_t observedCrc = 0, pageCrc = 0;
};
constexpr uint32_t feed(uint32_t crc, uint8_t byte) {
    crc ^= byte;
    for (unsigned b = 0; b < 8; ++b)
        crc = (crc >> 1) ^ ((crc & 1) ? 0xedb88320U : 0);
    return crc;
}
// The established public131 digest uses LE address, width, then BE raw value
// right-padded with zero to four bytes. Unused observation bytes are not data.
constexpr bool encode(const recovery::Snapshot& snapshot,
                      const recovery::Observation& observed,
                      size_t index, Page& out) {
    out = {};
    if (index >= pageCount || snapshot.layoutId != recovery::Layout::Public131V1 ||
        snapshot.count != recovery::fieldCount ||
        observed.count != recovery::fieldCount || !observed.authenticated)
        return false;
    for (size_t i = 0; i < recovery::fieldCount; ++i) {
        const auto& f = snapshot.fields[i];
        if (f.address != recovery::layout[i].address ||
            f.width != recovery::layout[i].width)
            return false;
    }
    out.first = index * perPage;
    out.count = recovery::fieldCount - out.first;
    if (out.count > perPage) out.count = perPage;
    out.size = out.count * fieldBytes;
    uint32_t whole = 0xffffffffU, page = 0xffffffffU;
    for (size_t i = 0; i < recovery::fieldCount; ++i) {
        const auto& f = snapshot.fields[i];
        for (size_t b = 0; b < fieldBytes; ++b) {
            const uint8_t value = b < 2 ? uint8_t(f.address >> (8 * b)) :
                b == 2 ? f.width : b - 3 < f.width ? observed.raw[i][b - 3] : 0;
            whole = feed(whole, value);
            if (i >= out.first && i < out.first + out.count) {
                out.bytes[(i - out.first) * fieldBytes + b] = value;
                page = feed(page, value);
            }
        }
    }
    out.observedCrc = ~whole;
    out.pageCrc = ~page;
    return true;
}
static_assert(recovery::fieldCount == 131 && pageCount == 5);
static_assert(perPage * fieldBytes == 224);
} // namespace mosaico_factory_observed_page
