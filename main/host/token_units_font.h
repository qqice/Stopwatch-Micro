#pragma once
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <lvgl.h>

inline const lv_font_t* GetTokenUnitFont()
{
    return &lv_font_montserrat_14;
}

// Compact display only; the history details retain the exact integer separately.
inline void FormatTokenAmount(uint64_t value, char* out, size_t capacity)
{
    if (!out || capacity == 0) return;
    const uint64_t divisor = value >= 1000000 ? 1000000 : value >= 1000 ? 1000 : 1;
    if (divisor == 1) {
        std::snprintf(out, capacity, "%llu", static_cast<unsigned long long>(value));
        return;
    }
    uint64_t whole    = value / divisor;
    unsigned fraction = static_cast<unsigned>(((value % divisor) * 100 + divisor / 2) / divisor);
    if (fraction == 100) {
        ++whole;
        fraction = 0;
    }
    char suffix = divisor == 1000000 ? 'M' : 'K';
    if (suffix == 'K' && whole == 1000) {
        whole    = 1;
        fraction = 0;
        suffix   = 'M';
    }
    if (fraction == 0)
        std::snprintf(out, capacity, "%llu%c", static_cast<unsigned long long>(whole), suffix);
    else if (fraction % 10 == 0)
        std::snprintf(out, capacity, "%llu.%u%c", static_cast<unsigned long long>(whole), fraction / 10, suffix);
    else
        std::snprintf(out, capacity, "%llu.%02u%c", static_cast<unsigned long long>(whole), fraction, suffix);
}

inline bool TokenAmountSelfTest()
{
    char value[40]{};
    const uint64_t inputs[] = {0, 999, 1000, 1250, 10000, 999999, 1000000, 413594325, 100000000};
    const char* expected[]  = {"0", "999", "1K", "1.25K", "10K", "1M", "1M", "413.59M", "100M"};
    for (size_t i = 0; i < sizeof(inputs) / sizeof(inputs[0]); ++i) {
        FormatTokenAmount(inputs[i], value, sizeof(value));
        if (std::strcmp(value, expected[i])) return false;
    }
    return true;
}
