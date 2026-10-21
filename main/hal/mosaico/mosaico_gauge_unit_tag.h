#pragma once
#include <cstdint>
namespace mosaico_gauge_unit_tag {
// RAM metadata only. Zero/unknown is NEVER implicitly scale 1. Native readings
// remain native; persistent logs serialize an explicit adjacent context record.
struct Tag {int64_t factor;uint64_t epoch,generation;bool verified;uint8_t phase;};
}
