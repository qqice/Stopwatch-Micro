#pragma once
#include <cstdint>
#include "../../apps/app_codex_micro/view/dot_patterns.h"

// No LVGL, heap, tasks or board APIs: shared by the panel and PNG preview.
namespace mosaico_boot_splash {
constexpr int Width = 480, Height = 480, StripRows = 8, Brightness = 30;
// Two centered lines retain the existing five-column, square-dot style.
constexpr char FirstLine[] = "MOSAICO", SecondLine[] = "BEACON";
constexpr int Pitch = 10, Diameter = 6, LineGap = 30;
constexpr int lineWidth(int letters) { return (letters * 6 - 2) * Pitch + Diameter; }
constexpr int LineHeight = 6 * Pitch + Diameter, LineAdvance = LineHeight + LineGap;
constexpr int LogoWidth = lineWidth(sizeof(FirstLine) - 1), LogoHeight = LineAdvance + LineHeight;
constexpr int Left = (Width - LogoWidth) / 2, Top = (Height - LogoHeight) / 2;
constexpr uint8_t row(char letter, int y) {
    constexpr uint8_t extra[5][7] = {
        {15,16,16,14,1,1,30}, // S
        {14,17,17,31,17,17,17}, // A
        {14,4,4,4,4,4,14}, // I
        {31,16,16,30,16,16,31}, // E
        {17,25,25,21,19,19,17}, // N
    };
    switch (letter) {
        case 'S': return extra[0][y];
        case 'A': return extra[1][y];
        case 'I': return extra[2][y];
        case 'E': return extra[3][y];
        case 'N': return extra[4][y];
        default: return mosaico_dot::detail::glyph(letter).rows[y];
    }
}
constexpr uint16_t pixel(int x, int y) {
    y -= Top;
    if (y < 0 || y >= LogoHeight) return 0;
    const bool second = y >= LineAdvance;
    const char* text = second ? SecondLine : FirstLine;
    const int width = lineWidth(second ? sizeof(SecondLine) - 1 : sizeof(FirstLine) - 1);
    x -= (Width - width) / 2;
    if (second) y -= LineAdvance;
    if (x < 0 || x >= width || y >= LineHeight) return 0;
    const int col = x / Pitch, dotRow = y / Pitch;
    if (col % 6 >= 5 || x % Pitch >= Diameter || y % Pitch >= Diameter) return 0;
    return (row(text[col / 6], dotRow) & (1U << (4-col%6))) ? 0xffff : 0;
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
static_assert(Brightness == 30 && Left > 0 && Top > 0);
static_assert(Left + LogoWidth < Width && Top + LogoHeight < Height);
}

