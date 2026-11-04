# Vendored upstream provenance

- Component: Espressif `esp_delta_ota` 1.1.4.
- Repository: https://github.com/espressif/idf-extra-components
- Pinned tree: `691c87db8e8dfede3455b0bcdd7a1c2dee50c173`, subdirectory `esp_delta_ota`.
- Nested detools gitlink: `074929b6c97f0dbc931fc8876e1a7df83199b0c8` in https://github.com/eerimoq/detools.
- Retained detools files: `c/` and `LICENSE`, as required by the upstream component build.
- `UPSTREAM-SHA256SUMS` records SHA-256 of the originally vendored files before the local change below. All files other than `src/esp_delta_ota.c` remain byte-identical to that baseline. Upstream licenses are retained.

## Local compatibility/safety patch

Only `src/esp_delta_ota.c` differs from the vendored source:

- Add `<limits.h>` for `INT_MAX`.
- Read callback rejects invalid negative source state, zero size, size greater than `INT_MAX`, or an advance beyond `INT_MAX` **before calling the user callback**. Compute the advance using `int64_t`, and commit the checked offset only after successful reading.
- Seek callback rejects null context, invalid negative source state, negative result, or a result above `INT_MAX`; compute the relative sum using `int64_t` before narrowing to `int`.
- Invalid operations return `-ESP_ERR_INVALID_ARG`, leave the offset unchanged, and do not perform an overflowing signed addition.

No public API/ABI, patch wire format, SDK source, or hardware behavior is changed. The callback `int` offset contract is preserved. Rollback is to restore the pinned upstream `src/esp_delta_ota.c` (original SHA-256 `3097c798e753e61194a6d0c0e1ed00b519875b5828c2f5a2b8fcad657f6cdc9a`).

## Validation evidence

Private local evidence: `.artifacts/private/mosaico/delta-bench-001/`.

- `fixture/offset_fixture.c` includes the actual patched component implementation, with only ESP platform headers stubbed. Espressif clang 21.1.3 cross-compiled it for RV32; it is not a Windows-native compiler (registered targets: RV32/RV64/Xtensa).
- The same fixture was linked with the actual vendored detools/heatshrink sources and executed with Apple clang 21 ASan/UBSan in the isolated MacMiniM4 tool directory: 19 boundary checks passed. Checks cover null context, negative seek/`INT_MIN`, `INT_MAX` seek overflow, rejected read advance before callback, oversized/zero reads, successful boundary read, invalid state, callback failure, relative seek and both callback variants.
- This is a host callback-boundary test, not device or flash validation. The raw heatshrink benchmark is separately documented in `benchmark.json`.
