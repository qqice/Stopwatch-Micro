#pragma once
#include <cstdint>
#include "../../apps/app_codex_micro/view/dot_patterns.h"

// No LVGL, heap, tasks or board APIs: shared by the panel and PNG preview.
namespace mosaico_boot_splash {
constexpr int Width = 480, Height = 480, StripRows = 8, Brightness = 30;
constexpr int Pitch = 4, Diameter = 2, LogoWidth = 114, LogoHeight = 26;
constexpr int Left = (Width - LogoWidth) / 2, Top = (Height - LogoHeight) / 2;
constexpr uint8_t row(int letter, int y) {
    constexpr uint8_t extra[4][7] = {
        {14,17,17,17,17,17,14}, // O
        {30,17,17,17,17,17,30}, // D
        {31,16,16,30,16,16,31}, // E
        {17,17,10,4,10,17,17}, // X
    };
    return letter == 0 ? mosaico_dot::detail::glyph('C').rows[y] : extra[letter-1][y];
}
constexpr uint16_t pixel(int x, int y) {
    x -= Left; y -= Top;
    if (x < 0 || y < 0 || x >= LogoWidth || y >= LogoHeight) return 0;
    const int col = x / Pitch, dotRow = y / Pitch;
    if (col % 6 >= 5 || x % Pitch >= Diameter || y % Pitch >= Diameter) return 0;
    return (row(col / 6, dotRow) & (1U << (4-col%6))) ? 0xffff : 0;
}
template<class Backend> constexpr bool show(Backend& io) {
    if (!io.allocate()) return false; // Panel stays OFF and zero brightness.
    for (int y = 0; y < Height; y += StripRows) {
        io.render(y);
        io.draw(y);
        // Public synchronous tx_param command drains ALL queued color DMA.
        // Must finish before rendering into the same strip or freeing it.
        io.drainDark();
    }
    io.release();
    io.enable();
    return true;
}
static_assert(Width == 480 && Height == 480 && Height % StripRows == 0);
static_assert(Brightness <= 30 && Left > 0 && Top > 0);
}

