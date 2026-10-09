#pragma once

namespace MosaicoQuota {
// History is an awake-only request. A locked-origin quota request must not
// acquire an extra history request merely because the user wakes mid-fetch.
struct RequestPolicy {
    bool lockedAtStart;
    constexpr bool historyRequired(bool lockedNow) const
    {
        return !lockedAtStart && !lockedNow;
    }
};

constexpr bool requestsSucceeded(bool quotaOk, bool historyRequired, bool historyOk)
{
    return quotaOk && (!historyRequired || historyOk);
}
} // namespace MosaicoQuota
