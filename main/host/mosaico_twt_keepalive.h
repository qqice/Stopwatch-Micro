#pragma once
namespace MosaicoTwtKeepalive {
struct State {
    int requested = -1, trialRequested = -1; // Unset is never an SDK call.
    int configResult = 0, restoreResult = 0;
    bool restoreOwed = false; // Any SDK setter attempt may have partially changed driver state.
    bool applied = false, configKnown = false, actualKeepalive = false;
    bool priorConfigUnknown = true, restoredKnownBaseline = false;
    bool needsRestartToRestoreDefaults = false, pending = false;
    constexpr void beginTrial(bool on) {
        trialRequested = on ? requested : -1;
        applied = false; configResult = restoreResult = 0;
        priorConfigUnknown = !configKnown; restoredKnownBaseline = false;
    }
    constexpr bool shouldApply() const { return trialRequested >= 0 && !restoreOwed; }
    constexpr void configured(int result) {
        configResult = result;
        restoreOwed = needsRestartToRestoreDefaults = true;
        if (!result) {
            applied = configKnown = needsRestartToRestoreDefaults = true;
            actualKeepalive = trialRequested != 0;
        } else configKnown = false; // No getter or partial-failure guarantee.
    }
    constexpr void restored(int result) {
        restoreResult = result;
        configKnown = result == 0;
        if (!result) { restoreOwed = applied = false; actualKeepalive = false; restoredKnownBaseline = true; }
    }
};
}
