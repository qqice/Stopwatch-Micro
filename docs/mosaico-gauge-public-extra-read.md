# Public gauge complement (RAM-only)

`gauge-thresholds audit CONFIRM` (CDC only) reuses the existing authenticated
full-access transaction. After the unchanged 59 journal fields and 7 calibration
fields pass, it double-reads exactly the 65 remaining public metadata-003 fields.
Each response requires exact selector echo, 36-byte length and checksum; compare
only the declared 1 or 2 payload bytes. No security/OTP selection, generic address
API, parameter write, new unlock, CFG entry or persisted snapshot is added.

The 59-field schema/CRC and `gauge-profile-field 0..65` indexes are unchanged.
The complement has an independent `extra_readable`, `extra_complete`, `extra_error`
result. A complement failure does not rewrite the old journal meaning and does
not bypass the original close/SEALED verification. PASS on the old audit is NOT
proof of a complete 131-field backup: also require `closed=1`, `extra_complete=1`
and all 66 old cache fields. No hardware acceptance is claimed by host tests.

`gauge-public-extra-field 0..64` is a pure cache query, including on UART. It emits
address, typed width, rawBE, generation, readable/complete and error within 192
bytes. Values have no invented units. Cache publication requires the whole old
transaction to succeed and close; every new audit clears the old complement
first. Partial or torn complement data never yields complete values. Unprivileged
`gauge-thresholds` does not read these extra selectors.

Host regression: use the configured full-path Python interpreter with
`-m unittest tools.test_mosaico_gauge_public_extra
 tools.test_mosaico_gauge_calibration_read tools.test_mosaico_gauge_thresholds`.
This compiles actual production readers/getters and closed models without SDK,
network or device access; logs are under `artifacts/gauge-public-extra-tests/`.
