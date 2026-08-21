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
- Fast, Approve, Decline, and Fork have large independent hit targets.
- The joystick respects its invisible circular limit and returns to center.
- The arc slider sends discrete feedback and returns to its midpoint.
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
service, all 12 physical Codex control codes, the three encoder codes, report framing, an actual
neutral HID transmission, Command/Agent/Mic UI construction, and the local-microphone privacy
boundary. Strict mode requires `PASS` for every functional check; only an unconfirmed pairing reset
may return `SKIP`. A complete strict run reports `pass=10 skip=1 failures=0`. Use `--allow-offline`
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
2. `AG00`–`AG05`, `ACT06`–`ACT10`, and `ACT12` each trigger their mapped host action.
3. Agent labels and lights follow host thread updates.
4. The enlarged arc-slider target produces `ENC_CC`, `ENC`, and `ENC_CW` without stalling during a
   fast drag; the planar joystick crosses the host action threshold before reaching the visual edge
   and produces `v.oai.rad` direction updates.
5. Holding physical A starts host push-to-talk using the computer microphone, releasing A stops it,
   physical B sends, and A+B only toggles pages. `debug mic` must report local capture disabled.
6. Every accepted input produces haptic feedback without making touch or page changes sluggish;
   sound effects are off by default.
7. Holding the reset control for 3 seconds on either the connected page or Pairing screen erases
   stored bonds, restarts, and advertises for a fresh pairing.
8. After 30 seconds the display dims; after two minutes it turns off. The first dark-screen touch
   wakes without sending a host action, while new host state wakes immediately.
9. Hard-reset the ESP32-S3 without erasing bonds, wait for the existing Windows bond to reconnect,
   and rerun the strict suite. It must reach `ble_protocol=1` without manual pairing, with
   `rpc_errors=0`, `tx_failures=0`, and `half_open_recoveries=0`.

The compatibility protocol only triggers host push-to-talk. ChatGPT uses the computer's selected
microphone, the StopWatch I2S RX path remains disabled, and the BLE vendor HID channel never streams
PCM audio.

## Validated Windows hardware run

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
