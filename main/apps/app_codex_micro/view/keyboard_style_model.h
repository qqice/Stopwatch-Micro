#pragma once
#include <cstdint>

namespace mosaico_keyboard {
// Factory frame and LVGL integer partitions, shared by firmware and offline preview.
constexpr int X = 0, Y = 231, Width = 480, Height = 233;
constexpr int SheetX = 0, SheetY = 0, Padding = 16, PaddingY = 0, Gap = 3, Radius = 8, FontSize = 24;
constexpr int FunctionFontSize = 18, EditorX = 24, EditorY = 164, EditorWidth = 432, EditorHeight = 50;
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
    const int h = Height - 2 * PaddingY - (rows - 1) * Gap;
    const int x1 = Padding + w * unit / units + index * Gap;
    const int x2 = Padding + w * (unit + weight) / units + index * Gap;
    const int y1 = PaddingY + h * row / rows + row * Gap;
    const int y2 = PaddingY + h * (row + 1) / rows + row * Gap;
    return {x1, y1, x2 - x1, y2 - y1};
}
// Completion is isolated from page swipes; orientationCompleted can leave that
// latch set without any editor contact bubbling to the root to clear it.
constexpr uint32_t fieldEventGuards(uint32_t guards, bool completing) {
    return completing ? (guards & ~512U) : guards;
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
static_assert(Y + Height == 464 && EditorY + EditorHeight < Y);
}

#ifdef LV_KEYBOARD_CTRL_BUTTON_FLAGS
namespace mosaico_keyboard {
constexpr lv_buttonmatrix_ctrl_t control(unsigned value) { return static_cast<lv_buttonmatrix_ctrl_t>(value); }
inline const char* const Map_lc[] = {"q", "w", "e", "r", "t", "y", "u", "i", "o", "p", "\n", "a", "s", "d", "f", "g", "h", "j", "k", "l", "\n", "ABC", "z", "x", "c", "v", "b", "n", "m", LV_SYMBOL_BACKSPACE, "\n", "1#", " ", LV_SYMBOL_OK, ""};
inline const lv_buttonmatrix_ctrl_t Ctrl_lc[] = {control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(LV_KEYBOARD_CTRL_BUTTON_FLAGS | 3), control(2), control(2), control(2), control(2), control(2), control(2), control(2), control(3), control(LV_KEYBOARD_CTRL_BUTTON_FLAGS | 1), control(3), control(LV_KEYBOARD_CTRL_BUTTON_FLAGS | 1)};
inline const char* const Map_uc[] = {"Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P", "\n", "A", "S", "D", "F", "G", "H", "J", "K", "L", "\n", "abc", "Z", "X", "C", "V", "B", "N", "M", LV_SYMBOL_BACKSPACE, "\n", "1#", " ", LV_SYMBOL_OK, ""};
inline const lv_buttonmatrix_ctrl_t Ctrl_uc[] = {control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(LV_KEYBOARD_CTRL_BUTTON_FLAGS | 3), control(2), control(2), control(2), control(2), control(2), control(2), control(2), control(3), control(LV_KEYBOARD_CTRL_BUTTON_FLAGS | 1), control(3), control(LV_KEYBOARD_CTRL_BUTTON_FLAGS | 1)};
inline const char* const Map_spec[] = {"1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "\n", "!", "@", "#", "$", "%", "&", "*", "(", ")", "-", "\n", "_", "=", "+", ";", ":", "'", "\"", ",", ".", "?", "\n", "abc", " ", LV_SYMBOL_BACKSPACE, LV_SYMBOL_OK, ""};
inline const lv_buttonmatrix_ctrl_t Ctrl_spec[] = {control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(LV_KEYBOARD_CTRL_BUTTON_FLAGS | 4), control(9), control(3), control(LV_KEYBOARD_CTRL_BUTTON_FLAGS | 4)};
inline const char* const Map_extra[] = {"[", "]", "{", "}", "\\", "|", "<", ">", "/", "~", "\n", "`", "^", "0", "1", "2", "3", "4", "5", "6", "7", "\n", "8", "9", "!", "@", "#", "$", "%", "&", "*", "?", "\n", "abc", " ", LV_SYMBOL_BACKSPACE, LV_SYMBOL_OK, ""};
inline const lv_buttonmatrix_ctrl_t Ctrl_extra[] = {control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(1), control(LV_KEYBOARD_CTRL_BUTTON_FLAGS | 4), control(9), control(3), control(LV_KEYBOARD_CTRL_BUTTON_FLAGS | 4)};
}
#endif
