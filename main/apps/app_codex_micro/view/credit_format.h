/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstddef>

namespace mosaico_credit {
// Lexical only: never round an API balance through floating point. Malformed
// strings and non-decimal representations are left unchanged.
constexpr size_t displayLength(const char* balance) {
    size_t length = 0;
    while (balance[length]) ++length;
    size_t pos = (balance[0] == '+' || balance[0] == '-') ? 1 : 0;
    const size_t integerStart = pos;
    while (balance[pos] >= '0' && balance[pos] <= '9') ++pos;
    if (pos == integerStart || balance[pos] != '.') return length;
    const size_t dot = pos++;
    const size_t fractionStart = pos;
    while (balance[pos] >= '0' && balance[pos] <= '9') ++pos;
    if (pos == fractionStart || pos != length) return length;
    while (pos > fractionStart && balance[pos - 1] == '0') --pos;
    return pos == fractionStart ? dot : pos;
}

constexpr void formatBalance(const char* balance, char* out, size_t size) {
    if (!size) return;
    if (!balance || !balance[0]) balance = "--";
    const size_t length = displayLength(balance);
    size_t pos = 0;
    while (pos < length && pos < size - 1) { out[pos] = balance[pos]; ++pos; }
    out[pos] = '\0';
}
} // namespace mosaico_credit
