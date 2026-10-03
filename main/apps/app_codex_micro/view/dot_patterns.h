#pragma once
#include <cstdint>
#include <initializer_list>

namespace mosaico_dot { namespace detail {
constexpr int patternsMaxTextLength = 32;
constexpr int min(int a, int b) { return a < b ? a : b; }
constexpr int max(int a, int b) { return a > b ? a : b; }
struct Layout { int pitch, diameter, width, height, x, y; };
// Normal numeric percentages retain gaps. Dense detail text may use one-pixel
// dots; callers should prefer their ordinary text style for long exact values.
constexpr Layout textLayout(int w, int h, int count, int requested) {
    const int cols = count > 0 ? count * 6 - 1 : 0;
    const int p = cols ? min(max(2, requested), min(w / cols, h / 7)) : 0;
    const int d = p >= 1 ? max(1, p * 7 / 10) : 0;
    const int width = d ? (cols - 1) * p + d : 0;
    const int height = d ? 6 * p + d : 0;
    return {p, d, width, height, (w - width) / 2, (h - height) / 2};
}
struct Grid { int rows, columns, pitch, diameter, width, height, x, y; };
constexpr Grid meterLayout(int w, int h, int requestedRows) {
    const int rows = min(4, max(1, requestedRows));
    const int p = min(8, h / rows);
    const int cols = p >= 2 ? min(200 / rows, w / p) : 0;
    const int d = cols ? max(1, p * 7 / 10) : 0;
    const int width = cols ? (cols - 1) * p + d : 0;
    const int height = cols ? (rows - 1) * p + d : 0;
    return {rows, cols, p, d, width, height, (w - width) / 2, (h - height) / 2};
}
constexpr int filledDots(int total, uint16_t bp) {
    return (total * min(10000, bp) + 5000) / 10000;
}
constexpr bool meterLit(int column, int row, Grid g, uint16_t bp, bool known) {
    return known ? column * g.rows + row < filledDots(g.columns * g.rows, bp)
                 : (column + row) % 2 == 0;
}
struct Glyph { char key; uint8_t rows[7]; };
// Original compact five-column glyphs; MSB is the leftmost dot.
constexpr Glyph glyphs[] = {
    {'0',{14,17,19,21,25,17,14}}, {'1',{4,12,4,4,4,4,14}},
    {'2',{14,17,1,2,4,8,31}}, {'3',{30,1,1,14,1,1,30}},
    {'4',{2,6,10,18,31,2,2}}, {'5',{31,16,16,30,1,1,30}},
    {'6',{6,8,16,30,17,17,14}}, {'7',{31,1,2,4,8,8,8}},
    {'8',{14,17,17,14,17,17,14}}, {'9',{14,17,17,15,1,2,12}},
    {'.',{0,0,0,0,0,6,6}}, {'%',{25,26,2,4,8,11,19}},
    {'-',{0,0,0,31,0,0,0}}, {'?',{14,17,1,2,4,0,4}},
    {'+',{0,4,4,31,4,4,0}}, {'C',{14,17,16,16,16,17,14}},
    {'K',{17,18,20,24,20,18,17}}, {'M',{17,27,21,21,17,17,17}},
    {'B',{30,17,17,30,17,17,30}}, {'T',{31,4,4,4,4,4,4}},
    {'P',{30,17,17,30,16,16,16}}, {'d',{1,1,15,17,17,17,15}},
    {'h',{16,16,22,25,17,17,17}}, {'m',{0,0,26,21,21,21,21}},
    {' ',{0,0,0,0,0,0,0}}, {'r',{0,0,22,25,16,16,16}},
    {'o',{0,0,14,17,17,17,14}}
};
constexpr const Glyph& glyph(char c) {
    for (const auto& g : glyphs) if (g.key == c) return g;
    return glyphs[13];
}
constexpr bool validBounds(Layout l, int w, int h) {
    return l.x >= 0 && l.y >= 0 && l.x + l.width <= w && l.y + l.height <= h
           && (!l.diameter || (l.pitch >= 1 && l.diameter <= l.pitch));
}
constexpr bool algorithmTest() {
    const auto pro = textLayout(408, 42, 6, 8); // Pro200
    if (!pro.diameter || !validBounds(pro, 408, 42) || pro.diameter >= pro.pitch)
        return false;
    if (glyph('r').key != 'r' || glyph('o').key != 'o' || glyph('P').key != 'P')
        return false;
    for (int w : {198, 408}) {
        for (int count : {4, 6, 12, 15, 17, 32}) {
            const auto l = textLayout(w, 78, count, 10);
            if (!l.diameter || !validBounds(l, w, 78)) return false;
            if (count <= 6 && l.diameter >= l.pitch) return false;
        }
    }
    for (int rows = 1; rows <= 4; ++rows) {
        const auto g = meterLayout(198, 28, rows);
        const int n = g.rows * g.columns;
        if (n <= 0 || n > 200 || filledDots(n, 0) != 0 || filledDots(n, 10000) != n)
            return false;
        if (!meterLit(0, 0, g, 0, false) || meterLit(0, 0, g, 0, true)) return false;
        int previous = 0;
        for (int bp = 0; bp <= 10000; ++bp) {
            const int filled = filledDots(n, static_cast<uint16_t>(bp));
            if (filled < previous || filled > n) return false;
            previous = filled;
        }
    }
    for (const auto& g : glyphs) for (auto row : g.rows) if (row > 31) return false;
    return glyph('!').key == '?';
}
static_assert(algorithmTest(), "dot widget layout and fill invariants");
} }
