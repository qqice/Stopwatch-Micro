/* SPDX-License-Identifier: MIT */
#pragma once
namespace MosaicoFirstQuotaRefresh {
// Sole network-owner boot state; no rearm on wake, failure or settings edits.
class Policy {
    bool consumed = false;
public:
    constexpr bool takeOnLock(bool hasAccepted) {
        if (consumed) return false;
        consumed = true;
        return !hasAccepted;
    }
};
}
