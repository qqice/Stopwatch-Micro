#pragma once
#include <cstdint>

namespace mosaico_keyboard {
// Factory palette; absolute geometry stays compatible with the existing sheet.
constexpr int X = 0, Y = 204, Width = 440, Height = 198;
constexpr int SheetX = 20, SheetY = 64, Padding = 4, Gap = 4, Radius = 8, FontSize = 20;
constexpr uint32_t Normal = 0x55555A, Function = 0x3B3C3D, Text = 0xECECF0;
constexpr uint32_t Accent = 0xD18C37, GoText = 0x15191F;
constexpr uint8_t HapticStrength = 60;
enum class KeyKind { Normal, Function, Go };
constexpr uint32_t keyColor(KeyKind kind, bool pressed) {
    const uint32_t c = kind == KeyKind::Go ? Accent : kind == KeyKind::Function ? Function : Normal;
    // Flat, subtle pressed state rather than default theme blue/checked keys.
    return pressed ? ((((c >> 16) & 255) * 4 / 5 << 16) |
                      (((c >> 8) & 255) * 4 / 5 << 8) | ((c & 255) * 4 / 5)) : c;
}
struct Rect { int x, y, w, h; };
// Same integer partition arithmetic as LVGL buttonmatrix update_map().
constexpr Rect keyRect(int row, int rows, int index, int count, int unit, int units, int weight) {
    const int w = Width - 2 * Padding - (count - 1) * Gap;
    const int h = Height - 2 * Padding - (rows - 1) * Gap;
    const int x1 = Padding + w * unit / units + index * Gap;
    const int x2 = Padding + w * (unit + weight) / units + index * Gap;
    const int y1 = Padding + h * row / rows + row * Gap;
    const int y2 = Padding + h * (row + 1) / rows + row * Gap;
    return {x1, y1, x2 - x1, y2 - y1};
}
struct PointerHaptic {
    bool contact = false, pulsed = false;
    constexpr void begin(bool pointerPressed, bool allowed) { contact = pointerPressed && allowed; pulsed = false; }
    constexpr bool select(bool pointer, bool validKey, bool allowed) {
        if (!contact || pulsed || !pointer || !validKey || !allowed) return false;
        pulsed = true; return true;
    }
    constexpr void end() { contact = false; pulsed = false; }
};
static_assert(SheetX + X + Width + 2 <= 480 && SheetY + Y + Height + 2 <= 480);
static_assert(56 + 70 + 48 <= Y, "active editor stays above keyboard");
}
