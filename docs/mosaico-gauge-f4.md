# Offline BQ27220 F4 arithmetic

`tools/mosaico_gauge_f4.py` has no device, serial, or write path. It decodes
storage-order bytes exactly as `Fraction`, not IEEE-754. There is deliberately
no arbitrary decimal encoder.

## Evidence and interpretation

- In the [BQ27220 calibration thread](https://e2e.ti.com/support/power-management-group/power-management/f/power-management-forum/827030/bq27220-how-to-calibration-bq27220-with-few-parameters),
  TI engineer Onyx points to the
  [shared floating-point format discussion](https://e2e.ti.com/support/power-management-group/power-management/f/power-management-forum/313974/values-of-cc-gain-and-cc-delta).
- [TI SLVA148A, Appendix A](https://www.ti.com/lit/an/slva148a/slva148a.pdf)
  documents Xemics storage conversion. With bytes `b0..b3`, sign is `b1` bit 7,
  mantissa is `0x800000 | (BE(b1..b3) & 0x7fffff)`, and exact value is
  `sign * mantissa * 2**(b0 - 128 - 24)`.
- The public reference examples `7f306c4d` and `7c0d2371` decode near
  `0.3445762694` and `0.03445762694`; printed decimals are rounded.
- [BQ27220 TRM Rev. A](https://www.ti.com/lit/ug/sluubd4a/sluubd4a.pdf), sections
  4.9.1.1/2, gives the chip-specific meanings: CC Gain is proportional to mA,
  CC Delta to mAh. Shared storage format does not justify copying another
  chip's automatic CC Delta calibration relationship. Table 3-2 supplies the
  raw-domain range gates used below; they are not bqStudio's displayed sense
  resistance and are not a physical current-calibration acceptance test.

All-zero storage decodes to `2**-129` under this formula, not IEEE zero.
The bounded calibration helper rejects it as outside the calibration domain.

## Candidate boundary

`scale_power_two(raw, factor, parameter)` accepts only integer factors 1, 2, 4
(not booleans). It changes only `b0`, keeping the remaining bytes unchanged.
It checks both input and output in the reviewed domains: gain 0.1..4 and delta
30000..3000000, inclusive, and rejects exponent overflow. These are candidate
domain gates, not proof of physical suitability. Generic decoding can represent
negative values; candidate validation rejects them.

The scaling worksheet now records exact and numeric F4 values and labels the
format `REFERENCE_VERIFIED_XEMICS_F4`. This resolves the former opaque-format
blocker only. Hardware effects, complete restore, and every host consumer's
current/capacity/power conversion remain unvalidated: `ready_for_hardware` stays
false. No changes to other units, SC/EDV bits, Qmax, device state, or firmware
are implied. Neither arithmetic nor a candidate resolves the 97 percent symptom.

Run the portable tests with the existing SDK Python:

```powershell
& C:\Espressif\tools\python\v6.1\venv\Scripts\python.exe -m unittest discover -s tools -p test_mosaico_gauge_f4.py
& C:\Espressif\tools\python\v6.1\venv\Scripts\python.exe -m unittest discover -s tools -p test_mosaico_gauge_scaling_plan.py
```

## Units preparation (not production-integrated)

`main/hal/mosaico/mosaico_gauge_units_model.h` is an unused pure C++ header,
not a runtime switch or calibration writer. Its factor must explicitly be 1, 2,
or 4. Current and capacity converters validate the signed-int16 / unsigned-int16
register domains and return exact integer microamps / microamp-hours, respectively.
They reject invalid inputs without changing the output. Signed physical mA
threshold comparisons cross-multiply in a widened type rather than truncating
the current first; physical thresholds outside signed-int32 are rejected.
SOC, voltage, time, temperature and flags are not scaled or handled by this API.

`relativeNativeCounterDelta` preserves both raw uint16 counter samples, computes
the modulo difference first, then interprets a signed displacement and scales
it into relative counter milliunits. It is **not absolute remaining capacity**;
the helper makes no additional RawCC-to-mAh claim. It requires the same boot and
factor, strictly increasing monotonic timestamps, explicit valid elapsed context,
and caller confirmation that displacement is strictly below half a ring. An
exact half-ring displacement is ambiguous and rejected. The caller must supply
that external bound; the helper cannot establish it from two raw samples.

`tools/test_mosaico_gauge_units.py` checks compile-time assertions using a native
`g++` (`CXX` override) or the installed Espressif cross compiler, without linking
or executing firmware. Factor 1 is checked for every current/capacity register
value. Other checks cover signed sub-mA precision, threshold boundaries, ratios,
wrapping relative counters, invalid contexts and integer extremes. No production
source includes this header, and `readyForHardware` remains false.

```powershell
& C:\Espressif\tools\python\v6.1\venv\Scripts\python.exe -m unittest discover -s tools -p test_mosaico_gauge_units.py
```

## Full public131 worksheet input

The planner still accepts the legacy cached 66 records with 11 selected ranges.
It also accepts a complete public131 backup dictionary with `fields` and all
131 official reader ranges. In the full mode every address must occur exactly
once and agree with official metadata type/width; every value is decoded afresh
from `rawBE` and checked against exact official Min/Max numbers. Saved `value`,
`name`, and unit strings in the backup do not drive arithmetic. Reader statuses
for Min/Max/Units/Read/Write must be zero; formulas are reported inputs, never
executed. Completeness, generation, no parameter writes, and `SEALED` are required
backup attestations, explicitly not independently verified device facts. The
output retains input SHA-256 digests and separately labels reported upstream
digests that this planner did not rehash. It is public-only, not a hidden/OTP image.

Roles are closed address lists, not name matching. Electronics Load `9269` has
native units of **3 uA**, so its raw candidate scales by k without relabeling it
mA. EOC Smooth Current `927b` is 0.1 percent and stays unchanged. Battery Low
`9251` is .01 percent (raw 700 means 7 percent), not mAh. Max Smoothing Current's
`9276` official seconds unit remains contradictory and blocked. R0 `92ab` remains
blocked: current scaling changes `ILOAD * R0 * FTZ`; inverse coefficient scaling
and integer quantization need separate validation, and no R0 target is emitted.
Closed voltage/time/temperature/percentage/flags/ADC-count roles are preserved;
other units/model coefficients remain explicitly unreviewed and preserved.
Full input and complete arithmetic do not make hardware ready.

Counter samples now also carry a `unitEpoch` (default zero for existing four-item
aggregate initializers). Any apply or restore must advance that epoch. Cross-epoch
relative deltas are rejected even when boot and factor match again after a
1 -> 2 -> 1 transition; production epoch lifecycle integration is not provided.
