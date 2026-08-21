#!/usr/bin/env python3
"""Run Stopwatch Micro's machine-readable USB serial diagnostics."""

from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass

try:
    import serial
    from serial.tools import list_ports
except ImportError as exc:  # pragma: no cover - depends on the ESP-IDF environment
    raise SystemExit("pyserial is required; source the ESP-IDF export script first") from exc


RESULT_RE = re.compile(r"DBG RESULT command=([^ ]+) status=([^ ]+)(?: (.*))?")


@dataclass
class Result:
    command: str
    status: str
    details: str


def discover_port(explicit: str | None) -> str:
    if explicit:
        return explicit

    ports = sorted(list_ports.comports(), key=lambda port: port.device.casefold())
    candidates = [
        port
        for port in ports
        if (port.vid, port.pid) == (0x303A, 0x1001)
        or "usb jtag/serial debug" in (port.description or "").casefold()
    ]
    if len(candidates) == 1:
        return candidates[0].device

    found = "; ".join(
        f"{port.device} ({port.description or 'unknown'}, "
        f"VID:PID={port.vid or 0:04X}:{port.pid or 0:04X})"
        for port in ports
    )
    raise SystemExit(
        "expected one ESP32-S3 USB Serial/JTAG port; pass --port explicitly. "
        f"Matching devices: {[port.device for port in candidates] or 'none'}. "
        f"All serial ports: {found or 'none'}"
    )


class DebugClient:
    def __init__(self, port: str) -> None:
        # Configure control lines before opening so attaching diagnostics does
        # not create an avoidable DTR/RTS reset pulse.
        self.serial = serial.Serial(port=None, baudrate=115200, timeout=0.1, write_timeout=1)
        self.serial.dtr = False
        self.serial.rts = False
        self.serial.port = port
        self.serial.open()

    def handshake(self, timeout: float = 12.0) -> None:
        deadline = time.monotonic() + timeout
        next_ping = time.monotonic()
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_ping:
                self.serial.write(b"debug ping\n")
                next_ping = now + 1.0
            raw = self.serial.readline()
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            print(line)
            if "DBG READY " in line:
                next_ping = time.monotonic()
            match = RESULT_RE.search(line)
            if match and match.group(1) == "ping" and match.group(2) == "PASS":
                return
        raise TimeoutError("serial debug handshake timed out")

    def close(self) -> None:
        self.serial.close()

    def command(self, text: str, expected: str, timeout: float = 5.0) -> Result:
        self.serial.write((text + "\n").encode("ascii"))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            raw = self.serial.readline()
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            print(line)
            match = RESULT_RE.search(line)
            if match and match.group(1) == expected and match.group(2) not in {"RUNNING"}:
                return Result(match.group(1), match.group(2), match.group(3) or "")
        raise TimeoutError(f"timed out waiting for {expected!r} after {text!r}")


def run_automated(client: DebugClient, allow_offline: bool) -> tuple[list[Result], list[str]]:
    cases = [
        ("debug help", "help", 3.0, {"PASS"}),
        ("debug status", "status", 3.0, {"PASS"}),
        ("debug selftest", "selftest", 8.0, {"PASS"}),
        ("debug controls", "controls", 5.0, {"PASS"}),
        ("debug protocol", "protocol", 3.0, {"PASS"}),
        ("debug ui cycle", "ui-cycle", 5.0, {"PASS"}),
        ("debug transport", "transport", 5.0, {"PASS"}),
        ("debug perf 3000", "perf", 6.0, {"PASS"}),
        ("debug mic 500", "mic", 3.0, {"PASS"}),
        ("debug status", "status", 3.0, {"PASS"}),
        ("debug pairing-reset", "pairing-reset", 3.0, {"SKIP"}),
    ]
    results: list[Result] = []
    failures: list[str] = []
    offline_optional = {"ui-cycle", "transport", "perf"}
    for command, expected, timeout, accepted in cases:
        result = client.command(command, expected, timeout)
        results.append(result)
        allowed = accepted | ({"SKIP"} if allow_offline and expected in offline_optional else set())
        if result.status not in allowed:
            allowed_text = "/".join(sorted(allowed))
            failures.append(
                f"{expected}: expected {allowed_text}, got {result.status} {result.details}"
            )
    return results, failures


def run_interactive(client: DebugClient, failures: list[str]) -> None:
    print("\nInteractive checks: watch the screen and feel/listen to the device.")
    tone = client.command("debug tone 880 350", "tone")
    if tone.status not in {"PASS", "OBSERVE"}:
        failures.append(f"tone: unexpected status {tone.status} ({tone.details})")
    if input("Did you hear one tone? [y/N] ").strip().lower() != "y":
        failures.append(f"tone: not confirmed ({tone.details})")

    vibration = client.command("debug vibrate 500 80", "vibrate")
    if vibration.status not in {"PASS", "OBSERVE"}:
        failures.append(
            f"vibrate: unexpected status {vibration.status} ({vibration.details})"
        )
    if input("Did you feel one vibration? [y/N] ").strip().lower() != "y":
        failures.append(f"vibrate: not confirmed ({vibration.details})")

    print("Within 20 seconds press yellow A, press blue B, then touch/drag the display.")
    inputs = client.command("debug inputs 20000", "inputs", timeout=23.0)
    if inputs.status != "PASS":
        failures.append(f"inputs: {inputs.status} {inputs.details}")

    if input("Did the UI cycle Command -> Agent -> Mic -> Command without artifacts? [y/N] ").strip().lower() != "y":
        failures.append("display: UI cycle not visually confirmed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", help="USB Serial/JTAG port; auto-detected when unique")
    parser.add_argument(
        "--allow-offline",
        action="store_true",
        help="permit SKIP/OBSERVE results during hardware bring-up before Codex is connected",
    )
    parser.add_argument("--interactive", action="store_true", help="also run physical observation checks")
    parser.add_argument(
        "--trace-seconds",
        type=int,
        help="capture real A/B/touch/joystick/slider transport performance instead of the automated suite",
    )
    args = parser.parse_args()
    port = discover_port(args.port)
    print(f"HOST port={port}")

    client: DebugClient | None = None
    try:
        client = DebugClient(port)
        client.handshake()
        if args.trace_seconds is not None:
            duration = max(1, min(args.trace_seconds, 60))
            print(f"HOST TRACE duration={duration}s; operate the physical controls now")
            time.sleep(1.0)
            trace = client.command(f"debug trace {duration * 1000}", "trace", timeout=duration + 4.0)
            results = [trace]
            failures = [] if trace.status == "PASS" else [f"trace: {trace.status} {trace.details}"]
        else:
            results, failures = run_automated(client, args.allow_offline)
            if args.interactive:
                run_interactive(client, failures)
    except (TimeoutError, serial.SerialException) as exc:
        print(f"HOST ERROR {exc}")
        return 2
    finally:
        if client is not None:
            client.close()

    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    summary = " ".join(f"{key.lower()}={counts[key]}" for key in sorted(counts))
    print(f"HOST SUMMARY {summary} failures={len(failures)}")
    for failure in failures:
        print(f"HOST FAILURE {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
