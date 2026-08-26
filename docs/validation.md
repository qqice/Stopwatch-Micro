# Validation

Use evidence from all applicable layers before treating a release as complete.

## Source and dependency checks

```bash
python3 -m py_compile fetch_repos.py
python3 -m unittest -v tools.test_host_tools
git diff --check
```

Confirm that `repos.json` contains immutable commit SHAs and that `fetch_repos.py` fails when a local patch
matches neither the clean nor already-applied state.

## Browser prototype

Open `web/index.html` in a browser and check:

- Pairing is the only interactive screen before connection.
- Pairing success opens Command; disconnect returns to Pairing.
- Plan, New Task, Fast, Approve, Decline, and Fork have large independent hit targets.
- The six Command buttons fit inside the circular safe area without overlapping the center status card.
- Plan emits an ordered radial press/neutral pair and New Task emits `ACT11`.
- The reasoning arc emits `ENC_CW` on the left and `ENC_CC` on the right, gives discrete feedback,
  and returns to its midpoint.
- The center dial shows real quota/reset data after a bridge update, explicit stale/unavailable
  states, and a level-aware StopWatch battery icon with percentage and charging indicator.
- The six Command buttons retain 78-pixel hit targets while their face gradient, mechanical outline,
  highlight, shadow, and pressed depth remain visually distinct.
- Holding A opens Mic, its visualization animates, and release returns to the previous page.
- B simulates Send and A+B toggles Command/Agent.

## Firmware build

```bash
source "$HOME/esp/esp-idf/export.sh"
idf.py build
```

The build must complete without compile/link errors and fit in the configured application partition.
Run `tools/package_release.py` and verify both the individual-file `SHA256SUMS` and ZIP checksum
before uploading release assets.

## USB serial diagnostics

Flash the firmware, keep ChatGPT connected for the transport/UI checks, and run:

```bash
source "$HOME/esp/esp-idf/export.sh"
python3 -u tools/serial_debug_test.py --port /dev/cu.usbmodem21301
```

The automated runner verifies the USB receive/transmit handshake, eight HAL readiness signals,
heap integrity and PSRAM, display geometry, battery telemetry, output-only audio configuration, BLE
service, all 13 physical Codex control codes, the three encoder codes, report framing, an actual
neutral HID transmission, Command/Agent/Mic UI construction, and the local-microphone privacy
boundary. Host-tool tests separately verify the compact wireless-usage frame and its fixed checksum
vector. Strict mode requires `PASS` for every functional check; only an unconfirmed pairing reset may
return `SKIP`. A complete strict run reports `pass=10 skip=1 failures=0`. Use `--allow-offline`
only during pre-pair bring-up.

Use `--interactive` for tests requiring a human observer. During `debug inputs`, normal UI/host
actions are suppressed so A, B, and touch can be exercised without sending Approve, Decline, or
other Codex actions. Audio, vibration, and display commands prove that the firmware requested the
actuation; audible, physical, and visual effects still require observation. The bond-erasing
`debug pairing-reset CONFIRM` command is destructive and is not part of the default suite.

The default suite also runs a 3-second 50 Hz neutral-HID performance test. A valid run must keep
`dropped=0`, `failures=0`, `queue_high` bounded, `touch_gap_max_us` below 50000, and report
`lvgl_core=1` with `tx_core=0`. Capture real physical interaction separately with:

```bash
python3 -u tools/serial_debug_test.py --port /dev/cu.usbmodem21301 --trace-seconds 30
```

The trace is expected to fail when no physical control is operated; that is an absence of test
input, not evidence that the input path passed.

## Device and host

After flashing, capture serial evidence for a clean boot with no panic or LVGL stack warning, then
verify the following against ChatGPT Settings:

1. The device is discovered as `Codex Micro`, connects, reconnects, and returns to Pairing after a
   disconnect.
2. `AG00`–`AG05` and `ACT06`–`ACT12` each trigger their mapped host action. Enable **Use separate
   microphone keys** before assigning ACT11 to New Task; otherwise Desktop intentionally ignores
   standalone ACT11 events.
3. Agent labels and lights follow host thread updates.
4. The top reasoning arc produces `ENC_CC`, `ENC`, and `ENC_CW` without stalling during a fast drag;
   Plan produces one ordered `v.oai.rad` Plan pulse and neutral, while New Task produces `ACT11`.
5. Holding physical A starts host push-to-talk using the computer microphone, releasing A stops it,
   physical B sends, and A+B only toggles pages. `debug mic` must report local capture disabled.
6. Every accepted input produces haptic feedback without making touch or page changes sluggish;
   sound effects are off by default.
7. Holding the reset control for 3 seconds on either the connected page or Pairing screen erases
   stored bonds, restarts, and advertises for a fresh pairing.
8. Leave the device untouched for more than two minutes. The display must remain at its configured
   brightness, while the one-pixel five-position drift continues on its one-minute cadence.
9. Hard-reset the ESP32-S3 without erasing bonds, wait for the existing Windows bond to reconnect,
   and rerun the strict suite. It must reach `ble_protocol=1` without manual pairing, with
   `rpc_errors=0`, `tx_failures=0`, and `half_open_recoveries=0`.
10. Run `tools/stopwatch.ps1 bridge -Transport bluetooth -Once`; require
    `wireless_usage_accepted` to increment with `wireless_usage_rejected=0`, and verify the center
    dial matches the normalized `account/rateLimits/read` percentage and reset window. This command
    must not open or require COM5.

The compatibility protocol only triggers host push-to-talk. ChatGPT uses the computer's selected
microphone, the StopWatch I2S RX path remains disabled, and the BLE vendor HID channel never streams
PCM audio.

## Validated Windows hardware runs

### 0.4.0 wireless usage bridge

The 2026-08-26 v0.4.0 run used the same ESP32-S3 revision 0.2 StopWatch and Codex Desktop
26.820.7780.0. Windows enumerated and shared-opened the paired Bluetooth HID vendor collection as
VID:PID `303A:8360`, usage page `FF00`, usage `0001`, while Codex Desktop remained connected. The
final firmware image was `0x1a54c0` bytes and all flashed regions passed esptool hash verification.

The bridge reported `transport=bluetooth remaining_bp=6200`. A separate USB diagnostic read then
proved the frame was handled wirelessly: `wireless_usage_accepted=1`,
`wireless_usage_rejected=0`, `host_bridge=1`, `usage_available=1`, `usage_stale=0`, and the same
`remaining_bp=6200` with a valid reset countdown. Strict verification also retained:

- HAL self-test `17/17`, physical controls `13/13`, BLE ready/connected/protocol `1/1/1`
- 50 Hz transport `151/151`, dropped `0`, failures `0`, queue high-water mark `1`
- RPC errors `0`, TX failures `0`, half-open recoveries `0`
- `HOST SUMMARY pass=10 skip=1 failures=0`

### 0.3.1 always-on and material controls

The 2026-08-26 v0.3.1 run used the same ESP32-S3 revision 0.2 StopWatch and Codex Desktop
26.820.7780.0. Browser QA confirmed six non-overlapping 78-pixel circular controls, layered button
material, an SVG battery/percentage row, the always-on marker, Plan/New Task events, and reasoning
return-to-center with no console errors. The final firmware image was `0x1a5040` bytes and all flashed
regions passed esptool hash verification.

Strict hardware verification reported:

- `display.geometry ... always_on=1 pixel_shift=1`
- HAL self-test `17/17`, physical controls `13/13`, BLE ready/connected/protocol `1/1/1`
- 50 Hz transport `151/151`, dropped `0`, failures `0`, queue high-water mark `1`
- `HOST SUMMARY pass=10 skip=1 failures=0`
- live usage bridge `remaining_bp=6700`, reset timestamp present, reset credits `1`

### 0.3.0 circular Command UI

The 2026-08-26 final run used the same M5Stack StopWatch Dev Kit (ESP32-S3 revision 0.2, 16 MiB
flash, 8 MiB PSRAM), ESP-IDF 5.5.4, and Codex Desktop 26.820.7780.0 on Windows. The verified 16 MiB
factory recovery image was checked again before flashing. The final `Stopwatch-Micro.bin` was
`0x1a4cc0` bytes; all four flashed regions passed esptool's post-write hash verification.

Browser QA confirmed the six 78-pixel circular command buttons, 174-pixel center status dial, and
reasoning arc had no overlap or console errors. Plan produced an ordered radial press/neutral event,
New Task produced `ACT11`, and a right reasoning step produced `ENC_CC` before returning to center.

Strict hardware verification after the final flash reported:

- `HOST SUMMARY pass=10 skip=1 failures=0` (the unconfirmed destructive pairing reset is the skip)
- HAL self-test `17/17`, physical controls `13/13`, BLE ready/connected/protocol `1/1/1`
- RPC errors `0`, TX failures `0`, half-open recoveries `0`
- 50 Hz transport `151/151`, dropped `0`, failures `0`, queue high-water mark `1`
- live bridge `PASS`: `remaining_bp=6900`, reset timestamp present, reset credits `1`, stale `0`

### 0.2.0 baseline (historical)

The 2026-08-21 acceptance run used an M5Stack StopWatch Dev Kit (ESP32-S3 revision 0.2, 16 MiB flash,
8 MiB PSRAM), ESP-IDF 5.5.4, and Codex Desktop 26.818.3698.0 on Windows. A complete 16 MiB factory
image was captured and SHA-256 verified before the first write. The final `Stopwatch-Micro.bin` was
`0x1a5270` bytes and each flashed region passed esptool's post-write hash verification.

Strict verification passed immediately after flashing and again after a separate esptool hard reset:

- `HOST SUMMARY pass=10 skip=1 failures=0` (the unconfirmed destructive pairing reset is the skip)
- HAL self-test `16/16`, PSRAM `8388608`, BLE ready/connected/protocol `1/1/1`
- RPC `3`, RPC errors `0`, TX failures `0`, half-open recoveries `0`
- 50 Hz transport `151/151`, dropped `0`, failures `0`, queue high-water mark `1`

Codex Desktop logs independently recorded successful responses for `v.oai.rgbcfg`,
`v.oai.thstatus`, and `device.status` after both cold reconnects.
