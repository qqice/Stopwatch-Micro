# ColdFactory first-post-exit observation

This is instrumentation, not native calibration, an FCC repair, or proof of discharge tracking.
"Pre-seal" in this observer means before the coordinator's **explicit** reseal
call, not an assertion that the gauge is unsealed. Actual `OperationStatus.SEC`
in each snapshot is authoritative. On the observed 0.13.83 cold boot it was
already SEALED (SEC3) at phase 4, with CFG0 and INITCOMP1. The observer therefore
cannot separate reinitialization from any automatic security transition.

- Only the first successful `GaugeFccBackend::exitCfg()` in ColdFactory mode captures evidence. Runtime FCC repair, no-CFG cleanup, rejected/skipped boots, and unsuccessful exits do not produce phase 4.
- The existing `gauge_exit_config()` command, CFG exit minimum 2 s wait, safety gates, cleanup, reseal grace, and retries remain unchanged. Immediately after successful exit and before reseal, the already-locked caller reads standard registers plus the closed read-only engine selectors (identity 0x0002, CEDV 0x0073, smooth 0x0076). The one-shot observation adds approximately 80 ms of intentional MAC completion delays plus bus time; it is not a periodic sampler.
- A partial/all-failed observation still retains phase 4, original per-snapshot monotonic timestamps, and validity masks, and is never retried. Diagnostic validity does not alter coordinator success/failure. Standard POD remains 48 B; engine POD remains 72 B.
- `Hal::gaugeBootPostExit()` only copies RAM under the outer transaction mutex, without taking the nonrecursive battery mutex or issuing I2C/NVS operations. Source cache outcomes stay zero; terminal status is attached to copies when collected. The status is fetched before the cache to avoid treating a pre-transaction empty cache as terminal absence.
- Logger independently acknowledges each accepted record and retries queue-full failures without duplicating accepted prefixes. A terminal boot without a post-exit observation completes this optional capture without synthesizing a sample. Before/after/outcome records remain present. Phase 4 uses existing Type 3 and Type 13 records, unchanged 64 B wire layout, sample-time monotonic milliseconds and epoch zero. Existing decoder already accepts phase values without schema changes.
- Read-only getters, private-SPI/storage leases, OTA/power/NAND gates and journal ownership are unchanged. No OCV/RESET/0x0092/key/CFG/parameter action is added.

Host-only verification: `tools/test_mosaico_gauge_post_exit.py` (actual exit method extraction, all-failed validity, cold-only/one-shot/no-exit behavior, pre-seal call order, independent queue-full retry, sample-time preservation, Type 13 production packing, old and phase-4 decoding). Existing boot/backend/logger/engine regressions also pass. Logs: `artifacts/gauge-post-exit-*.log`. No full IDF build, device access, flashing or power-state manipulation was performed by this worker.

Hardware evidence: the deployed 0.13.83 observer captured phase 4 on a natural
cold boot after charging-off/unplug. Standard RM and native CEDV/Smooth values
were already equal at phase 4 and remained equal after the explicit reseal call.
Later negative-current samples showed changing RawCC with unchanged RM/CEDV;
this confirms a native plateau rather than a UI-only cache issue. A single
phase-4 observation alone does not prove accumulation was already frozen at
that instant. Readbacks and initialization flags do not establish discharge
tracking or accurate cell capacity. No parameter or timing repair is justified
by that observation alone.
