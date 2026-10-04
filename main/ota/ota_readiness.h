#pragma once
#include <cstdint>
namespace MosaicoOta {
// Pure gate shared with host tests; no hardware or network dependency.
constexpr bool appReady(bool viewReady, bool keysReady, bool serialReady, uint32_t completedLoops)
{
    return viewReady && keysReady && serialReady && completedLoops > 0;
}
}
