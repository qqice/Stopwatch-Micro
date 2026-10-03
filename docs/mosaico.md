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
- BQ27220 at0x55 is read-only. Display `~` marks unverified gauge calibration;
  failed samples show unknown. No 65mAh RAM correction or charger writes.
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

The first page is an independent 480x480 dashboard, not the old circular control
page with buttons removed. Each quota bucket has a large remaining-percentage
card, up to two window progress bars, actual duration, reset countdown and optional
plan/credits/limit-state metadata. Multiple buckets can be scrolled; at most eight
are retained, with overflow explicitly marked. Snapshot reset credits are read-only.
All-zero decimal fractions are omitted. A single returned window fills the card;
missing windows are hidden, never shown as a fabricated five-hour allowance or
an empty second column. No window at all shows "Quota unknown". Credits are
separate from Token history. Static screens have no scrollbars or horizontal
scrolling; only multiple actual buckets enable vertical scrolling. Metadata uses
larger 20px text and history cells/details use 16px text.

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
radio-off/80MHz policy, uses a separate quota/battery-only panel, refreshes once
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
