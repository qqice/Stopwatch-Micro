# Scaling experiment admission and bounded trial

The only proposed experimental variable is the reporting scale factor. It is
not a one-register edit: current/capacity gains and dependent quantities must
form a coherent unit conversion. The original read-only audit firmware has no hardware writer; the new closed
trial implementation described below is separately gated.

TI's [BQ27220 product page](https://www.ti.com/product/BQ27220), checked on
2026-10-21, lists a 100 mAh minimum battery capacity. Native DC65 being accepted
by a register's numeric range is therefore not evidence of advertised support.
An x2 candidate reports DC130, but the physical battery remains 65 mAh. This
supports investigating scaling; it does not prove the cold-start plateau cause
or make the candidate hardware-ready. TI's
[scaling application note](https://www.ti.com/lit/an/slua792/slua792.pdf) requires
dependent current/capacity configuration to follow the ratio. Its worked example
is another gauge: do not copy its field units or claim gain-only scaling improves
analog noise or sense-resistor accuracy.

## Required before any gauge parameter write

1. Read and authenticate all 131 public RAM parameters; retain the original
   raw bytes, widths, identities and hashes. A 66-field audit is not enough.
   Preserve all previous NAND traces and original files.
2. Keep dimensionless values, voltage, temperature, time and all security,
   charging/EDV configuration bits unchanged. Do not guess missing Qmax/DOD
   addresses or interpret bqStudio's displayed sense resistance as raw gain.
3. Independently classify every dependent field, not just capacity/current
   thresholds. TRM 1.1.5 uses `ILOAD * R0 * FTZ`: scaling ILOAD without handling
   R0 changes physical EDV compensation. Baseline R0=867 is not divisible by
   two or four; inverse scaling needs quantified rounding and hardware EDV
   checks. This is not authorization to write R0 now.
4. Account for fixed native learning constants: a native 10 mAh charge condition
   has a different physical meaning after scaling. Do not claim full algorithm
   invariance from a unit conversion.
5. Separate raw diagnostic values from physical telemetry throughout HAL,
   UI charging/supply logic, OTA admission, traces/NAND decoding and boot
   configuration verification. Keep historical records unambiguous; never
   reinterpret old raw records using only today's scale factor. A native
   RawCC delta needs signed modular differencing before division, not division
   of the unsigned accumulated register.
6. Durable read-back-verified recovery journal precedes the first write. It
   must distinguish original, pending, applied and restoring states, bind the
   exact original/target field sets and scale to device identity, and fail
   closed on torn records, unexpected current values or power loss. Recovery
   restores original bytes, not a reconstructed decimal approximation.
7. The running and retained recovery images must understand the unit journal
   and restore policy before mutation. Old 0.13.77/83/84 images are not scale
   aware. Never apply scaled parameters and then blindly boot one of them.
8. Revalidate after true gauge RAM loss, not just MCU software restart. A
   factory 3000 mAh profile with default gains is not an applied scaled profile.
   The display and OTA power policy must not consume it as physical data while
   recovery is incomplete.

## Bounded hardware protocol once admission is complete

- Use isolated UART TX/RX/GND for logging. V1 is the sole external 5 V source
  when connected; remove board USB before enabling V1. Do not parallel sources.
- V1 measures the external power path; its 5 V current is not the cell current.
  Pure-battery observations require V1 output off/disconnected while UART remains.
- Start at scale 1, collect the baseline, then test one reviewed scale at a time.
  Do not simultaneously retune Near Full, SC, EDV voltages or mode thresholds.
- Accept only consistent native current/capacity ratios, physical supply/OTA
  behavior, persistent offline logging, no panic, and a demonstrated exact
  restore to the saved scale-1 parameter set. Scaling is not a cure claim until
  the recorded plateau is absent in the relevant cold-start sequence.
- Stop and restore on non-finite/out-of-range reads, identity/profile mismatch,
  inconsistent scale, gauge transaction failure, excessive temperature or voltage
  outside the existing safe gate. Never force a low-voltage discharge.

Baseline state before writer integration: all 131 public fields were double-read and backed up with
their raw bytes; offline F4 arithmetic and the pure recovery decision model have
passed host tests. Those tests alone do not admit a scaling writer. The whole unit/recovery
implementation and hardware admission are not complete. No SOC/OCV reset,
hidden-register write, OTP programming,
full-chip erase or TWT experiment is part of this protocol.

## Production integration boundary

Do not divide the existing `BatteryTelemetry` fields in place. That API explicitly
promises native values, and boot recovery, safety transactions and raw history
share its cache. A separate physical snapshot must derive exact uA/uAh values
under the same gauge transaction and unit-context lock. Include factor, unitEpoch,
generation, phase and validity; never pair a new factor with an old sample.

Explicit consumers include HAL `isBatteryCharging`, UI `refreshBattery` and its
`ChargeSupplyState` anchor, powerbank supply inference, every OTA power gate, and
the trace recorder's physical +5 mA charge-after-low decision. SOC, voltage,
temperature, time, flags, raw diagnostics and raw history must retain their units.
NAND formatting's fixed-65 guard must remain prohibited during an experiment,
not silently widened to accept any scaled state.

Legacy trace epochs are wall-clock timestamps; NAND generation is a storage
counter. Neither is a unit epoch. Legacy trace reserved bytes must stay zero.
Add a separately associated context record (boot, monotonic boundary, factor,
unitEpoch, generation and phase); missing association cannot default to scale 1.
The full original/target parameter recovery record is also new storage work:
current threshold-audit records hold only a snapshot CRC, not all 131 raw values.

The new record's admission and recovery fence must run before both startup
`gaugeBootReload` and its deferred radios-off revisit. A scaled DC130/260 merely
being rejected by an old fixed-65 guard is not a recovery implementation. Damaged
scale state must not fall through to legacy 65 restoration or trusted physical
telemetry. The retained recovery image must understand this fence too.

## Restore-aware bounded writer (2026-10-21 integration)

The new implementation is a CLOSED x2 trial, not a generic DM writer or a
permanent calibration. Hardware admission still requires the actual signed,
full-image SHA-256 of two restore-aware VALID slots (0.13.90 and 0.13.91), a
fresh authenticated 131-field audit, and verified durable recovery storage.
A version string alone is not sufficient. Do not use an old native-only slot
while the gauge is scaled. The old native-only trace schema is suspended during
this trial; raw NAND samples carry an adjacent explicit unit-context record.

Only the reviewed 17 fields change. Every other field is preserved. In
particular, documented autonomous CC Offset (9180) may change without a host
write. Its ACTUAL bytes remain in the complete backup and digests; normalization
is only for comparison with the reviewed template. A fresh changed offset needs
a new immutable complete source/target backup before mutation. Never write a
stale offset back to manufacture a matching CRC. Undocumented changes are not
automatically treated as autonomous.

Restore only writes owned fields whose freshly authenticated value is original
or target. Unknown owned values stop without overwrite. Unknown untouched
values are retained; known 17-field rollback may proceed only when the separate
voltage/temperature calibration safety set remains unchanged, and unknown
untouched state cannot be declared trusted Original. Do not Seal a gauge in
CFG1, CAL1 or unknown configuration state. An interrupted necessary CFG exit is
not permission for a blind reset. Original data, including prior failures,
remain immutable.

Trial stop rules include independent voltage/temperature bounds, read/security
or profile failure, and a 300-second device deadline. The host's bounded V1
battery observation is shorter (at most 180 seconds), with ON restoration in
finally and fresh full settings readback. V1 control is not a hardware watchdog;
PC power loss cannot be covered by Python finally. Turning V1 off removes only
external 5 V, not the internal cell, and does not reproduce hours of real
powered-off storage. Stable external power is explicitly HOST-attested at the
local console, not inferred from a USB flag or the small lightning icon.

First validate apply and exact rollback while V1 remains ON. Then compare a
matched native and x2 short battery interval without changing radio policy,
brightness, sleep policy or gauge thresholds independently. A SOC jump from the
required documented 0091 config exit is NOT a fix or evidence of accuracy.
Measure native-current scaling and capacity-counter behavior, physical-policy
consistency, raw-context persistence, and the exact rollback. The original
cold-start plateau remains open until the relevant cold-start condition is
actually observed under an accepted candidate.

### Supply evidence during the bounded trial

The diagnostic field `host_external_power_attested` records the historical
local-console attestation AT APPLY. It is not a sensor, an ongoing supply lease,
or evidence that V1 remains on. The write begins only with freshly verified V1
ON. If the subsequent separately logged battery test is admitted, the host must
record `external_power_currently_attested=false`, the fresh exact V1 OFF
readback, negative cell current and its monotonic interval. No current-supply
claim may be inferred from that historical firmware field. Restore remains
permitted without external power, subject to the independent voltage,
temperature, current, identity and recovery gates. No OTA or further apply is
admitted during the scaled interval.
