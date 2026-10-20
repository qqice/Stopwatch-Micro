# Bounded IoTPower V1 output control

`tools/iotpower_output_control.py` defaults to **snapshot only**. It has no
voltage/current-limit selection arguments and no raw serial opcodes. The existing
read-only `iotpower_capture.py` is unchanged. Never run concurrent meter captures
or the vendor GUI against the same adapter.

## API evidence

[Vendor API documentation](https://gitee.com/dingjg8944/iot-power/blob/master/dynamic_library/readme.md)
declares `iot_uart_send_set(uint32_t current, uint8_t unit, uint32_t voltage,
uint8_t on)` with void return. Settings getters are `iot_get_set_current()` and
`iot_get_set_voltage()` returning uint16, and `iot_get_current_uint()` /
`iot_get_power_on()` returning uint8. `unit` and `on` accept 0/1. The documentation's
V1 parameter table conflicts with its prototype/ranges; the tool preserves raw
getter values, without narrowing the setter or applying that table's ranges.

The local approved AMD64 native DLL's exports were statically checked. SHA256:
`100bc284bc998252d0be924629f1c16e0264122549df10697357444296d244e7`.
[Device.cs](https://gitee.com/dingjg8944/iot-power/blob/master/dynamic_library/IoTPowerExample/Device.cs)
uses cdecl for its existing imports, but contains no setter declaration. Thus the
setter binding follows the vendor C prototype and exported name; **setter-specific
managed ABI verification is not claimed**. The tool reuses the capture backend's
explicit absolute-path, SHA256, AMD64 PE and Windows-x64 checks before binding.
It never downloads or auto-discovers a native binary.

## Transaction and safety boundary

- The explicitly selected port must uniquely identify CP210x `10c4:ea60`, serial
  `0001`. Espressif `303a` USB presence is rejected. Unidentified USB CDC devices
  are conservatively rejected; JLink `1366:0105` CDC UART and Bluetooth ports are
  allowed. This is not electrical proof of single supply.
- One parser thread immediately snapshots all four raw settings on each `0x04`.
  Measurement packets are filtered, not queued. Initial state is queried twice;
  both complete settings snapshots must match. Controlled runs require initial ON.
  The first session request is a standalone bootstrap initial: its epoch and
  send-QPC marker are established and it is sent before the reader starts, then
  its complete `0x04` must be consumed before any subsequent command.
- Each setter repeats the original raw current/unit/voltage and changes only ON.
  The identified V1 was observed to reply to OFF with fresh `0x04`, and to ON
  without `0x06`. A setter therefore waits for either eligible `0x06` or eligible
  `0x04` matching all four expected raw fields. A mismatching status fails
  immediately. Either matching case is followed by one initialization query and
  fresh complete `0x04` readback. If neither setting reply arrives within the
  bounded wait, the tool still performs that one read-only query, never retries
  SET. Only **exact full query readback** succeeds. Query timeout/mismatch,
  disconnect or parser failure remains failure with ON recovery.
  `set_reply_evidence` / `verified` log `confirmation_source` as `06+query`,
  `status04+query`, or `query_only`, with explicit `ack_seen`. Status/query success
  is never reported as a nonexistent `0x06`.
- Published old events are discarded before requests. Each blocking read retains
  the request epoch captured **before entering parse**; timestamp is captured as
  soon as the read returns. A read spanning an epoch boundary is discarded, never
  relabeled as fresh at publication time. After bootstrap, each transaction
  advances the epoch and waits for the continuously arriving V1 measurement
  stream to advance the reader into it, including while output is OFF. **No
  wake-up initialization request is sent.** The target is sent exactly once.
  A setting reply is consumed before the readback query, or its bounded wait
  expires before that query-only fallback. No synchronization initial remains
  pending ahead of a setter. An OFF setter is never retried. Missing stream progress
  causes a bounded barrier timeout before OFF; recovery still attempts ON once
  even if that barrier or parser fails, and unverified recovery remains unknown.
  Sequence/epoch/timestamps are
  logged. Replies must also have a read-return `perf_counter_ns()` tick strictly
  greater than the target action's send-start tick (captured immediately before
  the call, after request logging). Equal ticks are conservatively rejected;
  coarse Windows `monotonic()` timestamps are not used for freshness decisions.
  Thus same-epoch buffered replies already read during synchronization
  cannot become fresh merely by waiting for the publication lock.
  V1 exposes no transaction ID: delayed old wire responses cannot be
  distinguished with certainty. Exclusive ownership and sequential requests are
  required; host freshness filtering is not wire-level correlation proof.
- The prior wake-up-initial scheme could send a setter while that initial's
  `0x04` was still pending. This request overlap is removed independently of
  whether it explains the failed real trial. Host tests model a device that
  discards overlapping setters and require complete request/reply ordering.
  Passing those tests does not establish V1 firmware compatibility or authorize
  repeating hardware trials.
- Controlled runs require `--confirm-single-supply` and integer `--off-seconds`
  1..600. There is no automatic OFF retry. Any attempted OFF triggers one ON
  restoration attempt in `finally`, including timeout/error/KeyboardInterrupt.
  Restoration repeats the original settings using the same V1 confirmation
  contract: `0x06` is optional; exact fresh query readback is mandatory.
  Audit-write failure also triggers recovery; further logging failures must not
  prevent the ON attempt. Unverified restoration is explicitly STATE UNKNOWN and
  exits nonzero, even when a setter may actually have succeeded.
- Host callers may pass `run(..., stop_requested=callback)`. During the verified
  OFF hold, the callback is checked at most 0.1 seconds apart (subject to OS
  scheduling); a true result or callback exception aborts into `finally` ON
  recovery. It can carry a caller-owned UART low-voltage/offline guard. The tool
  does not read that UART itself, and this callback is not polled during protocol
  verification or native blocking calls. Default CLI behavior is unchanged.
- Timeout defaults to 5 seconds (maximum 30). The requested OFF interval starts
  at the OFF attempt, not after its ACK; verification overhead reduces the hold.
  Native blocking calls, OS scheduling and recovery latency can exceed requested
  timing. **600 seconds is an input cap, not a hardware watchdog guarantee.**
- Finally requests parser close and joins for two seconds. A still-running parser
  is an error; do not unload/reuse that session. Close failure or join timeout
  **retains the COM lock**, and the finish record flags this for manual handling.
  Only confirmed close plus parser termination allows normal lock deletion.
  A cooperative exclusive COM lock
  is created under `.artifacts/iotpower`; abnormal termination leaves it in place.
  Do not remove a stale lock until confirming no owner or vendor process remains.
- PC power loss, SIGKILL, process crashes and USB loss can prevent recovery.
  Software `finally` is not a safety relay, battery isolation guarantee, or proof
  that an attached board has no other supply. Do not alter wiring automatically.

## Usage

Use the full existing SDK Python path. Supply approved absolute DLL path/hash and
an unused `.jsonl` output file under an existing directory. The file uses exclusive
creation; logs include raw settings, requests, ACK/readback events and wall/monotonic
timestamps. A pre-existing output or lock fails closed without opening the meter.

```powershell
& C:\Espressif\tools\python\v6.1\venv\Scripts\python.exe tools/iotpower_output_control.py --port COM21 --dll <approved-absolute-path> --dll-sha256 <verified-sha256> --output <new-private-audit.jsonl>
```

Only after authorized wiring verification, add
`--confirm-single-supply --off-seconds 5` for a bounded OFF→ON study. Do not use
this tool while another task owns the meter or board. The implementation's host
tests never load the DLL or enumerate/open actual serial ports:

```powershell
& C:\Espressif\tools\python\v6.1\venv\Scripts\python.exe -m unittest discover -s tools -p test_iotpower_output_control.py
```
