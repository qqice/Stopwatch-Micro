#pragma once

#include <array>
#include <cstdint>

namespace hal_legacy_factory_mac {

// The SDK default reader may write an eight-byte extended EUI-64. Preserve the
// historical first-six identity used by existing journals/app/network callers;
// this is deliberately NOT a physical factory/STA MAC identity conversion.
// Reader returns zero on success, as esp_efuse_mac_get_default does.
template <class Reader>
constexpr bool read(std::array<uint8_t, 6>& output, Reader&& reader)
{
    std::array<uint8_t, 8> received{};
    output = {};
    if (reader(received.data()) != 0) return false;
    for (unsigned i = 0; i < output.size(); ++i) output[i] = received[i];
    return true;
}

}  // namespace hal_legacy_factory_mac
