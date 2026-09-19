#pragma once

// Visual cells are 48x26 on a 56x38 pitch. Assign gaps to the nearest
// cell centre, with disjoint boundaries and a four-pixel outer margin.
constexpr int HistoryHitCell(int x, int y, bool hourly)
{
    const int rows = hourly ? 4 : 5;
    if (x < -4 || x >= 344 || y < -4 || y >= rows * 38 + 4) return -1;
    int col = (x + 4) / 56;
    int row = (y + 6) / 38;
    if (col > 5) col = 5;
    if (row >= rows) row = rows - 1;
    return row * 6 + col;
}

constexpr bool HistoryHitSelfTest()
{
    for (int mode = 0; mode < 2; ++mode) {
        const int count = mode ? 24 : 30;
        for (int i = 0; i < count; ++i) {
            for (int y = 0; y < 26; ++y)
                for (int x = 0; x < 48; ++x)
                    if (HistoryHitCell(i % 6 * 56 + x, i / 6 * 38 + y, mode) != i) return false;
        }
    }
    return HistoryHitCell(51, 12, false) == 0 && HistoryHitCell(52, 12, false) == 1 &&
           HistoryHitCell(24, 31, false) == 0 && HistoryHitCell(24, 32, false) == 6 &&
           HistoryHitCell(-4, -4, false) == 0 && HistoryHitCell(-5, 0, false) == -1 &&
           HistoryHitCell(343, 193, false) == 29 && HistoryHitCell(344, 0, false) == -1 &&
           HistoryHitCell(24, 156, true) == -1 && HistoryHitCell(24, 194, false) == -1;
}
static_assert(HistoryHitSelfTest(), "History touch targets must partition gaps without changing visible cell identity");
