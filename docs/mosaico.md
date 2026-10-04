# ESP-Mosaico board profile

This is an independent **ESP-Mosaico V1.0 / ESP32-S31** build of the quota/history
monitor. The root project remains the M5Stack StopWatch / ESP32-S3 build; its
SDK config and dependency lock are not replaced. Both use the installed IDF6.1,
with `--preview` for S31. Do not select ESP32-S3 as a substitute.

## Capabilities and hardware boundary

- CO5300 480x480 QSPI: CLK44, D0/D1/D2/D3=36/51/35/9, CS50, RESET42.
- Peripheral rail GPIO60 active-low, I2C SDA0/SCL1, CST9220-compatible CST9217
  touch with IRQ6. Driver packages pinned in this profile's manifest/lock.
- GPIO7 Function Button maps to B: local page switching. A/power-button inputs
  are not fabricated. Touch actions and hourly/daily selections stay in RAM.
- BQ27220 at0x55 is normally read-only. Its reported SOC is shown directly;
  `?` marks unverified nominal-capacity consistency and failed samples are unknown.
  Only the explicitly requested, guarded nominal transaction below can modify
  capacity parameters. No charger, gain, offset or OTP write exists.
- Audio, microphone meter and vibration are not implemented in this profile.
  Their HAL tests explicitly SKIP, not PASS. The software tone format remains
 44100 for compatibility, but no PCM is sent to hardware.
- This profile is a standalone **quota-only monitor**. BLE is not initialized,
  advertised or paired; there are no agent, approval, microphone, HID or encoder
  controls. StopWatch retains its original BLE control UI and v1 status contract.
- Wi-Fi monitoring, separate MicroLink node `mosaico-micro`, quota, 24h observed
  increments and30 API dates reuse the shared application/service. No OpenAI
  credentials enter firmware. See [history semantics](history.md).

The board driver profile is based on the verified OpenFrameTap-Mosaico native
CO5300/CST9217 configuration and Espressif ESP-Mosaico V1.0 GPIO definitions.
No camera, Linux, decoder, SDK-source patch or M5 peripheral driver is imported.

The Mosaico control-plane H2/JSON buffers use MicroLink's512KiB defaults in PSRAM.
The previous64KiB small-tailnet profile can truncate the full map with77 peers;
response-size diagnostics reveal capacity without dumping peer data or auth keys.
The local peer table remains bounded to4 entries with the Mac prioritized.
DERP connections now wait for the authenticated map, track the actually connected
region and switch inside the I/O owner task; map publication/snapshot use a short
mutex released before DNS/TLS. Control-plane recovery invalidates map readiness.
TLS certificate/pin verification is unchanged. These changes prevent an early
failed map from leaving a physical default relay behind the advertised home.
The owner's tailnet ACL must permit the Mosaico node to initiate TCP to the quota
server on8765. A one-time Mac ping was used as a diagnosis only; no background
reverse-activation or periodic ping is installed. Device-initiated requests and
radio-off/reconnect tests are the acceptance criteria after ACL updates.

## Standby display investigation and guard

The owner's later photograph showed horizontal corruption in the lock-screen
percentage/battery dirty regions, while most black background remained intact.
The pre-update serial sample showed about 35 minutes radio-off standby at 80MHz,
five successful refresh cycles, continued once-per-minute redraws and no new
reset or heap-integrity failure. This is not proof of a physical row-scan defect.
Candidate stages remain software rendering, PSRAM/cache, DMA/QSPI and GRAM writes.

S31's 80/320MHz transition also changes SYS/MEM/APB domains. The retained QSPI
source is BBPLL and SPI already owns PM/source references, so an unmeasured SCLK
change cannot be asserted. Mosaico now defaults to fixed 320MHz even while locked
as a **conservative isolation guard**, retaining radio duty cycling, 8% brightness,
once-per-minute lock updates and pixel shifts. This trades some CPU power savings
for stability; it is not a demonstrated root-cause fix or measured energy result.
Do not simultaneously change PSRAM DMA, SPI source, buffer geometry or the SDK.

`debug display-clocks` reports cached public CPU/SYS/APB frequencies in Hz and a
MEM-bus value derived from its divider, not the physical 200MHz PSRAM frequency.
`debug display-ram-probe` performs a bounded 4KiB cache writeback/invalidate check
on a separate allocation only while locked/radio-off, never live pixel buffers.
A passing probe does not prove LCD DMA/QSPI correctness. Explicit
`debug display-low-clock on` temporarily disables the guard for diagnostics;
`off` restores it. The flag is RAM-only, defaults off on boot and is never enabled
automatically. Long optical standby acceptance still requires user observation.

The owner subsequently observed no corruption at320MHz. The installed6.1 S31
clock code gives these coupled domains (MHz):

| CPU | MEM bus | SYS | APB |
| --- | --- | --- | --- |
| 320 | 160 | 106.67 | 53.33 |
| 160 | 160 | 80 | 40 |
| 80 | 80 | 80 | 40 |

This strengthens a low-frequency/display-memory-path association, not proof of
a particular defect. `debug display-test-frequency 160` is a RAM-only idle test:
160 shares the80MHz SYS/APB clocks but retains the320MHz MEM-bus speed, making it
a useful next optical comparison. `320` restores protection; normal active
operation remains320. Source SPI clock is still configured40MHz BBPLL.

## Battery telemetry and guarded nominal configuration

The [official V1.0 guide](https://docs.espressif.com/projects/esp-dev-kits/en/latest/esp32s31/esp-mosaico/user_guide_v1.0.html)
specifies a 3.7V/65mAh pack and BQ27220 gauge. Read-only standard commands now expose
SOC, voltage, instantaneous/average current, RM, FCC, Design Capacity, status, SOH
and cycles. Their capacity/current units are physical mAh/mA: no inferred virtual
scale or voltage-to-SOC conversion is applied. `debug gauge` reads these fields.

`debug gauge-selftest` tests pure policies/CRC without hardware writes.
`debug gauge-nominal apply <expected-current-design>` is an explicit, closed
nominal-configuration operation, not measured ADC/cell calibration. It allows
only Design Capacity at 0x929F and, only for the unambiguous factory 3000/3000mAh
default, initial FCC at 0x929D. A plausible learned FCC is preserved. Target is
fixed at documented nominal65. No gain, EDV, offset, charger, security key or OTP
write exists; no seal/unseal/access escalation or automatic startup write exists.

Writing requires existing FULL_ACCESS, gauge ID0x0220, initialized/quiet near-full
conditions and safe temperature. Typed expected values, authenticated MAC
length/checksum, standard/DM consistency and a durable readback-verified journal
bound to chip MAC, gauge type, units and CRC precede CFG. Failure gets a bounded
rollback and safe CFG exit; critical failures remain explicit, never PASS.
`restore` requires this same unit's valid journal and only restores recorded values.
If access, readback or physical conditions do not match, the operation refuses
without parameter writes. The journal is in its own `gaugecal` NVS namespace.

Even successful nominal configuration does not characterize the physical cell.
Real FCC accuracy needs the [TI qualified learning cycle](https://www.ti.com/lit/ug/sluubd4a/sluubd4a.pdf)
and current/voltage calibration requires suitable independent measurements. Do
not force a deep discharge or label a nominal65 initialization as a measured65.

The owner subsequently explicitly authorized unsealing. `debug gauge-access
open` now supports one documented factory-key sequence on this validated sealed
device, with a separate readback-verified access journal bound to chip MAC, type,
prior security state and CRC. Failed attempts are latched; keys are not printed,
changed, enumerated or retried. `debug gauge-access restore` safely exits CFG and
restores the original SEALED state. Unknown-prior-unsealed access is refused;
there is still no automatic boot-time access change or OTP write.

Important new boundary: [TI lists a100mAh recommended minimum](https://www.ti.com/product/BQ27220).
The writable I2 range including65 does not establish supported accuracy at65mAh.
Accordingly, direct65 initialization is an explicitly owner-authorized experiment,
not characterized calibration; `*` on a consistent SOC value preserves that
qualification. TI's [scaling method](https://www.ti.com/lit/pdf/SLUA792) also requires
matching current calibration and unit-dependent parameters. Without independent
current measurements, this build does not blindly double CC gain or copy the
reference19-field virtual130 profile. Default charge/learning thresholds can
still be inappropriate for a65mA charger; capacity learning remains unverified.

## Important boot contract

The attached unit has a preserved bootloader which loads executable/rodata into
PSRAM. Its original app's mapped segment starts at0x50490020. A default Flash-XIP
build (0x400... mapping) compiled and flashed but did not boot. The profile MUST
keep `CONFIG_SPIRAM_XIP_FROM_PSRAM=y` (selects fetch/rodata/flash-load-to-PSRAM).
This works with installed IDF6.1; original app's6.2-dev version was not by itself
proof that a new SDK was required. Never resolve this by replacing bootloader.

Runtime USB is **USB-OTG TinyUSB CDC**, not StopWatch USB Serial/JTAG. CDC PID4001
and ROM PID0020 may enumerate different COM ports. Always re-enumerate. 1200-baud
recovery requests only volatile ROM entry; some Windows/S31 combinations need a
physical USB reconnect afterward to recover a descriptor failure. Prefer the
board's physical BOOT procedure for deployment. Stop/resume of the LVGL port
also stops touch sampling; normal idle lock only dims the display and retains
sampling for wake. It does not call the port-stop API.

## Square-screen monitor UI

### Design rules

Every visible element must communicate a value, a state, a unit, an identity or
an actionable selection. Do not add decoration, redundant headings, tutorials,
explanatory sentences or placeholder panels. Keep full data semantics in this
document and diagnostics rather than repeating prose on the display. Missing or
pending values remain unavailable (`?`), never zero; stale, gap and correction
have distinct color/symbol states. Short data units (`UTC+8`, `TZ?`, `%`, `C`)
are retained because they change how a value is interpreted.

Use dot-matrix numerical type and segmented dot meters for the large quota and
reset-time values, drawing widgets rather than allocating one object per dot.
Earned resets are represented by reset-card icons; battery capacity, charging,
Wi-Fi and cache age use status glyphs. These icons are read-only, not reset or
purchase controls. The reset-time meter shows time remaining within the returned
window duration, separately from quota remaining; neither estimates token usage.
The charge glyph represents observed positive gauge current, not mere USB
presence. Battery percentage is the gauge's directly reported SOC, not voltage
interpolation. A `?` marker indicates that capacity configuration has not passed
the nominal consistency checks; absence of this marker is not proof of physical
cell accuracy or a completed learning cycle.

The reset-card count is capped visually at three cards plus the remaining count,
not silently clipped. Large percentages and countdowns use a 5x7 original dot
alphabet with gaps; very long exact history values remain available in details.
There are no per-dot LVGL objects, animation timers or additional network polls.

When awake, fresh visible meters can highlight only their already-filled region.
Coins and reset cards turn slowly about their vertical axis; the capacity icon
pulses only during measured charging. One UI scheduler runs at most ten motion
steps per second, with no per-widget timer. Hidden, stale, unknown, offline and
locked states stop motion. The meter fill and capacity never change just for
animation. Time uses restrained cyan; quota and battery use red/amber/mint
according to their actual percentages.

Rotation now completes a turn in twelve seconds; meter highlights traverse in
eight seconds and use a more visible three-column reflection. The original
40-second turn, small icons' integer-coordinate quantization and single-column
24/255 highlight explained the barely visible effect. Fill counts remain unchanged.

The visual inspiration is the compact dot-graph language of
[btop](https://github.com/aristocratos/btop); glyph patterns and drawing code are
original and do not copy btop assets or require a terminal/font dependency.

Use restrained functional accents: percentage-dependent mint/amber/red for quota
and battery, cyan for time, gold for the credit coin, cool blue for Wi-Fi and
muted violet for earned-reset cards; other
chrome remains black/gray/white. Quota and time rows use identical text, icon and
meter dimensions. The full window length stays an internal timer denominator,
not an extra standalone label. `Pro200` is the owner's requested local badge for
the canonical Pro bucket, **not** an API-returned price or a change to allowance.
`Points` is the display name for the unchanged official credit balance; there is
no conversion to dollars or tokens.

The first page is an independent 480x480 dashboard, not the old circular control
page with buttons removed. Each quota bucket has a large remaining-percentage
card, up to two window progress bars, actual duration, reset countdown and optional
plan/credits/limit-state metadata. Multiple buckets can be scrolled; at most eight
are retained, with overflow explicitly marked. Snapshot reset credits are read-only.
All-zero decimal fractions are omitted. A single returned window fills the card;
missing windows are hidden, never shown as a fabricated five-hour allowance or
an empty second column. No window at all shows `--`. Credits are
separate from Token history. Static screens have no scrollbars or horizontal
scrolling; only multiple actual buckets enable vertical scrolling. Metadata uses
larger 20px text; history cells use 16px and selected details use 20px text.

History uses 6x4 (24h) or 6x5 (30d) full-width grids. Each rounded tile has a 73x44
touch slot, displays an hour or day and compact amount, and opens a separate exact
value panel from RAM. Mode buttons are 216x48 and highlight the current mode.
Hourly amounts use blue levels; daily amounts use green levels. White borders
mark day/month changes, purple marks gaps/partial data, orange marks corrections,
and gray marks unavailable values. Header dates and details disambiguate boundaries
without repeating month/today/yesterday in every tile. Ordinary amounts use K/M;
B/T/P keep very large API integers bounded, while details retain exact counts.

The 24h page remains **official reported increments at observation time**, not
measured consumption time. API reporting can lag, sampling gaps are unallocated,
negative corrections are separate, and daily API timezone is unknown. No device
token logs or quota-percent-to-token estimates are used.

New quota revisions update the active page promptly; clicks and mode switches do
not perform HTTP. Offline age/stale state is explicit, with quota values hidden
after ten minutes without a usable update. Idle locking retains the previous
radio-off policy with the protective fixed-320MHz guard described above, uses a
separate quota/battery-only panel, refreshes once
per minute and shifts pixels. Long presses and scrolling prevent idle lock; the
first wake gesture is consumed. The full-redraw-before-brightness mitigation is
retained. User observations after that mitigation found no repeat of large-area
corruption across multiple long sleeps; the original root cause is still unproven.

This profile reads the authenticated `/v2/status` endpoint described in
[macOS service deployment](macos-service.md). It uses no OpenAI login credentials
on the device and does not initiate quota resets, purchases or computer controls.

## Build and app-only deployment

```powershell
./tools/mosaico.ps1 build
./tools/mosaico.ps1 probe -Port COM13 # device must already be in download mode
./tools/mosaico.ps1 flash -Port COM13 -AllowOverwriteWithoutBackup
```

The current owner explicitly waived backup of the overwritten original app after
an interrupted USB read. The saved protected prefix/NVS excerpts are NOT a full
rollback image. The wrapper does not back up the app; its explicit overwrite
switch must not substitute for a verified same-device backup on another board.
It additionally binds to the hash of the validated bootloader; a different
bootloader is refused even with the same partition layout.

The script defaults to EIM6.1 environment and bounds compiler concurrency to2.
Build/config are private under `.artifacts/mosaico`; dependency lock is isolated
under `boards/mosaico`. Do not run default `idf.py flash` or `@flash_args`:
those include bootloader/table/otadata. Only write `0x20000 Stopwatch-Mosaico.bin`.

This unit's MD5-validated table is at0x9000: NVS0xa000/0x6000, factory0x20000/
0x7d0000, ui_apps0x7f0000/0x320000, system0xb10000/0x2ee000. The guard checks live
versus compiled table, S31 chip/image, project name, image fit and PSRAM mapping;
it refuses different layouts rather than rewriting them. These offsets do not
claim to cover every Mosaico revision. Preserve NVS, PHY, NAND, security and
camera calibration. Factory-wide NVS reset is disabled in the Mosaico HAL.

Provision with private JSON/key files via tools/provision_network.py and
tools/provision_tailscale.py, using the current **runtime** COM port. A5GHz-only
SSID cannot be used by this board: select the matching2.4GHz SSID. If provisioning
reports restart_required=1, restart normally; do not unnecessarily enter ROM.
Never put passwords, auth keys, full flash/NVS dumps or API tokens in Git.

`debug inputs` is a capture-only test: it intentionally suppresses UI touch
actions and the legacy A+B completion predicate assumes two physical buttons.
Do NOT use that mode to assess whether normal history taps/mode switches work on
this one-button board. Exit it with `debug cancel` before interaction acceptance.

A successful flash hash is not hardware acceptance. Require stable startup,
480x480 display/touch, actual Function Button/selection, independent tailnet IP,
fresh quota/history acceptance and an idle wake/update cycle. Record skipped
peripherals and distinguish submitted-frame timing from optical latency. Previous
StopWatch MicroLink warm-reconnect limitations are not declared fixed by this port.

## Wake redraw mitigation

The Mosaico backend keeps its lock-screen brightness (8%) until a full-screen
LVGL refresh has been submitted. The subsequent CO5300 brightness command drains
queued SPI pixel DMA before raising brightness. A new dim request cancels the
pending wake. This ordering does not guarantee optical scanout/vblank completion.

Six pre-mitigation wake observations (three at fixed 320 MHz and three with an
80 MHz locked phase) did not reproduce the original large-area corruption;
minor artifacts were reported in trials 1 and 4. Frequency transition therefore
was not necessary for those minor artifacts. The ordering change is a bounded
mitigation, not a confirmed root-cause fix for the earlier large-area incident.

### This unit's nominal experiment (2026-10-04)

Explicit factory access was verified (SEALED to FULL_ACCESS). The two authorized
capacity fields were changed from 3000 to65mAh. The transaction reported
`critical_cfg_exit_or_security`; its historical FAIL is retained. Cleanup then
passed and independent standard reads showed Design/FCC/RM65mAh, SEC3 (SEALED),
CFG0, SOC100,4203mV and zero instantaneous/average current. No gain, EDV,
charger, key-memory or OTP parameter was changed. The intermediate failing
exit's security state was not captured, so automatic sealing is an inference,
not a demonstrated chip behavior or a TI-guaranteed0091 result.

A strict read-only journal reconciliation is provided separately: it may close
only this board's matching pending transaction after identity, two complete
standard target readbacks and verified sealed/initialized/non-CFG/non-CAL state.
It performs no access-key or capacity writes and does not retroactively turn
the original failed experiment into PASS. Nominal correction is not a completed
learning cycle, calibrated cell capacity, or characterized accuracy below TI's
recommended100mAh range.

### Visible wave and cached-data readability

Filled meter columns now travel right-to-left through a bounded upward wave;
reflection and vertical motion do not alter the recorded percentage. Unknown,
zero, stale, offline, hidden and locked meters remain still. Coin/card rotation
is unchanged. Stale known quota uses80% of its normal hue/brightness on both
awake and lock views, retaining red/amber low-quota warnings. Gray means unknown;
the amber clock/age continues to distinguish cached readings from fresh ones.

The owner reported no artifact at160MHz, but the immediately following live
capture showed CPU320/diagnostic0 after18min radio-off. The earlier scripted160
probe was short, not a long optical trial. Do not label160 long-standby accepted
without concurrent actual-frequency and duration evidence.

### Safe next battery steps

Changing DC/FCC is not cell learning or current/voltage calibration. Before a
qualified discharge, audit the remaining charge/discharge/taper/near-full and
EDV profile: TI default thresholds may be inappropriate for a65mAh pack. Do not
request repeated forced deep discharges or change gain from guessed scaling.
For now charge normally, then use on battery without intermittent USB charging;
recharge promptly at a low-battery warning. This is an observation cycle, not
proof of qualified FCC learning. Capture SOC/RM/FCC/current/voltage and gauge
qualification flags at endpoints. Offset/gain calibration requires independently
known zero/load/voltage; no such reference is currently available.
