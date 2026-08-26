# Architecture

Stopwatch Micro is a single-purpose ESP-IDF firmware. It keeps hardware setup, the Codex Micro
compatibility transport, and the LVGL application separated so connection state and input handling
have one owner each.

## Startup and ownership

1. `main/main.cpp` initializes the StopWatch hardware abstraction and starts the BLE service.
2. `app_codex_micro` creates the only Mooncake application and polls physical inputs.
3. `CodexMicroView` owns the two LVGL pages, Pairing overlay, Mic overlay, touch controls, and their
   local animation state.
4. `CodexMicroBle` owns advertising, bonding, HID/GATT services, JSON-RPC input messages, protocol
   readiness, and host state updates.

The BLE service outlives UI page changes. Connection state flows from `CodexMicroBle` to the view;
the view cannot dismiss Pairing until both the HID link and a recognized Codex RPC are live. A HID
link that receives no recognized RPC for 25 seconds is treated as a Windows half-open connection and
is disconnected or restarted so advertising can resume.

## Input path

Touch controls are handled by LVGL event callbacks. Physical A/B transitions are debounced by
`KeyManager` and consumed by `CodexMicroApp`. Host-facing inputs are encoded with the constants in
`main/hal/ble/codex_micro_protocol.h`; A+B is deliberately local and only toggles the active page.

The top reasoning arc uses a finger-sized hit target and converts left/right displacement into the
official encoder events used by Codex Desktop's Reasoning-only dial mode. A fast drag keeps all host
encoder steps but collapses local audio/haptic work into one feedback pulse per touch sample. The
Plan button sends an ordered radial Plan press followed by a neutral barrier, so even a fast tap
cannot lose or reorder the toggle. New Task uses the otherwise-empty official `ACT11` slot.

The serializer retains the reference 4 ms pacing between fragmented reports, and the background HID
worker applies the same inter-message pacing without blocking LVGL. Normal key, joystick, and encoder
events fit in one 63-byte report.

## Runtime scheduling

The ESP-IDF configuration pins the Bluetooth controller, Bluedroid host, and `main_task` to CPU0.
Stopwatch Micro therefore pins its HID TX, audio, vibration, and battery workers to
CPU0 as well, while LVGL rendering and 8 ms touch sampling run alone on CPU1 at priority 2. The main
loop yields for one RTOS tick on each iteration. This prevents Codex traffic and feedback work from
stealing touch/render time while keeping all host communication off the UI core.

Every accepted user input requests audio and vibration feedback through the HAL. The view keeps
rendering state local so protocol delivery is not coupled to an LVGL redraw.

## Host state path

Incoming JSON-RPC requests update a thread-state snapshot in the BLE service. The Agent page reads
that snapshot during its periodic refresh and maps the six host states to button labels and lights.
The GATT callback validates the length-framed, possibly non-newline-terminated stream with a bounded
JSON structure scanner, then moves complete requests as heap pointers to a four-entry transport
queue. Parsing and dispatch happen only on the larger CPU0 worker stack. The worker waits up to
200 ms after a reconnect before replying and validates the connection generation again before any
host-state mutation or response.

Windows treats a bonded HID Input Report subscription as persistent across a peripheral reboot,
while the ESP-IDF 5.5.4 HID helper starts with its in-memory CCC flag cleared. At boot the
compatibility layer snapshots existing bonds and locates the exact Report ID 6 Input CCC handle.
After an identity from that boot snapshot authenticates, it restores the helper's notification flag.
New pairings still require the client's real encrypted CCC write. This prevents the half-open
connect/retry loop after a hard reset without weakening first-pair subscription semantics.

## Usage bridge

The vendor Micro RPC does not contain account usage. A separate, optional Windows companion starts
the desktop-managed `codex app-server`, reads the stable `account/rateLimits/read` method once per
minute, normalizes the canonical `codex` bucket, and sends only remaining basis points, reset epoch,
capture epoch, and reset-credit count over USB Serial/JTAG. The firmware validates an atomic
single-line update and derives the countdown from monotonic time. Stale and unavailable states are
explicit, and periodic quota refreshes do not wake the display.

## Microphone boundary

Codex Micro's `ACT10` event controls push-to-talk in ChatGPT Desktop. ChatGPT captures the computer's
selected system microphone; the vendor HID/JSON-RPC protocol has no PCM audio message. The firmware
configures ES8311 and I2S for output only and never starts an RX channel. The on-device Mic animation
is deliberately synthetic and communicates PTT state without sampling the built-in microphone.

## Display power and input safety

The view owns AMOLED power state. It dims after 30 seconds, turns off after two minutes, and wakes for
host state or physical input. A touch that wakes a fully dark display is consumed through release,
preventing an invisible Command or Agent action. Five one-pixel offsets rotate once per minute while
the display is visible to reduce static AMOLED wear.

Input delivery uses three paths: an ordered control FIFO for every key transition and joystick
neutral barrier, a one-slot latest-state queue for non-zero joystick motion, and a FIFO of relative
encoder batches. The worker checks controls and joystick state between every encoder detent. A
critical delivery is retried; persistent failure restarts BLE so the host releases PTT and held
controls instead of remaining stuck. Rotation batches are capped at 20 detents and at most two may
wait ahead of an encoder click, bounding worst-case click backlog to roughly 240 ms at 4 ms pacing.

## Dependency boundary

Source dependencies are pinned in `repos.json` and materialized under the ignored `components/`
directory by `fetch_repos.py`. Locally patched dependencies must be either cleanly patchable or
already patched; a mismatched patch is a hard error rather than a silent skip.

The browser prototype in `web/index.html` is a review artifact, not firmware source. UI changes are
implemented independently in HTML/CSS/JavaScript and LVGL, then checked for interaction parity.
