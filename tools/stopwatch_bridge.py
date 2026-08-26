#!/usr/bin/env python3
"""Bridge Codex account usage to Stopwatch Micro over USB Serial/JTAG."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover - exercised only outside the IDF environment
    serial = None
    list_ports = None


USB_SERIAL_JTAG_VID_PID = (0x303A, 0x1001)
POLL_SECONDS = 60.0
REQUEST_TIMEOUT_SECONDS = 15.0
RECONNECT_DELAYS_SECONDS = (1.0, 2.0, 5.0, 10.0, 30.0, 60.0)
MAX_SEQUENCE = 0xFFFFFFFF
RESULT_RE = re.compile(r"DBG RESULT command=([^ ]+) status=([^ ]+)(?: (.*))?")
MAX_EPOCH = 0xFFFFFFFF
MAX_RESET_CREDITS = 99


class BridgeError(RuntimeError):
    """Expected companion failure with a safe, non-sensitive message."""


class StaleSequenceError(BridgeError):
    def __init__(self, current_sequence: int) -> None:
        super().__init__(f"StopWatch usage sequence is stale (current={current_sequence})")
        self.current_sequence = current_sequence


@dataclass(frozen=True)
class UsageSnapshot:
    remaining_basis_points: int
    reset_epoch: int
    captured_epoch: int
    reset_credits: int


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BridgeError(f"invalid {field} in Codex rate-limit response")
    number = float(value)
    if not math.isfinite(number):
        raise BridgeError(f"invalid {field} in Codex rate-limit response")
    return number


def _canonical_codex_bucket(result: dict[str, Any]) -> dict[str, Any]:
    by_id = result.get("rateLimitsByLimitId")
    if isinstance(by_id, dict):
        direct = by_id.get("codex")
        if isinstance(direct, dict):
            return direct
        for bucket in by_id.values():
            if isinstance(bucket, dict) and bucket.get("limitId") == "codex":
                return bucket

    legacy = result.get("rateLimits")
    if isinstance(legacy, dict) and legacy.get("limitId") in (None, "codex"):
        return legacy
    raise BridgeError("Codex rate-limit response has no canonical codex bucket")


def _select_window(bucket: dict[str, Any]) -> dict[str, Any]:
    windows: list[dict[str, Any]] = []
    for key in ("primary", "secondary"):
        candidate = bucket.get(key)
        if isinstance(candidate, dict):
            _finite_number(candidate.get("usedPercent"), f"{key}.usedPercent")
            windows.append(candidate)
    if not windows:
        raise BridgeError("canonical codex bucket has no usage window")

    def rank(window: dict[str, Any]) -> tuple[float, float]:
        used = _finite_number(window.get("usedPercent"), "usedPercent")
        duration = window.get("windowDurationMins")
        duration_number = (
            _finite_number(duration, "windowDurationMins") if duration is not None else 0.0
        )
        return used, duration_number

    return max(windows, key=rank)


def normalize_rate_limits(result: Any, captured_epoch: int | None = None) -> UsageSnapshot:
    """Reduce an app-server rate-limit result to the device's canonical Codex view."""
    if not isinstance(result, dict):
        raise BridgeError("invalid Codex rate-limit response")
    bucket = _canonical_codex_bucket(result)
    window = _select_window(bucket)
    used_percent = _finite_number(window.get("usedPercent"), "usedPercent")
    remaining_percent = min(max(100.0 - used_percent, 0.0), 100.0)
    remaining_basis_points = int(round(remaining_percent * 100.0))

    reset_value = window.get("resetsAt")
    reset_epoch = 0
    if reset_value is not None:
        reset_number = _finite_number(reset_value, "resetsAt")
        if reset_number > MAX_EPOCH:
            raise BridgeError("resetsAt is out of range")
        if reset_number > 0:
            reset_epoch = int(reset_number)

    credits_value = result.get("rateLimitResetCredits")
    credits = 0
    if isinstance(credits_value, dict) and credits_value.get("availableCount") is not None:
        credits_number = _finite_number(
            credits_value.get("availableCount"), "rateLimitResetCredits.availableCount"
        )
        if credits_number < 0 or credits_number > MAX_RESET_CREDITS:
            raise BridgeError("rateLimitResetCredits.availableCount is out of range")
        credits = int(credits_number)

    captured = int(time.time()) if captured_epoch is None else int(captured_epoch)
    if captured < 1 or captured > MAX_EPOCH:
        raise BridgeError("captured epoch is out of range")
    return UsageSnapshot(
        remaining_basis_points=remaining_basis_points,
        reset_epoch=reset_epoch,
        captured_epoch=captured,
        reset_credits=credits,
    )


def format_host_usage_line(sequence: int, snapshot: UsageSnapshot) -> str:
    if sequence < 1 or sequence > MAX_SEQUENCE:
        raise BridgeError("usage sequence is out of range")
    if not 0 <= snapshot.remaining_basis_points <= 10000:
        raise BridgeError("remaining basis points are out of range")
    if not 0 <= snapshot.reset_epoch <= MAX_EPOCH:
        raise BridgeError("reset epoch is out of range")
    if not 1 <= snapshot.captured_epoch <= MAX_EPOCH:
        raise BridgeError("captured epoch is out of range")
    if not 0 <= snapshot.reset_credits <= MAX_RESET_CREDITS:
        raise BridgeError("reset credits are out of range")
    return (
        f"debug host-usage {sequence} {snapshot.remaining_basis_points} "
        f"{snapshot.reset_epoch} {snapshot.captured_epoch} {snapshot.reset_credits}\n"
    )


def next_sequence(sequence: int) -> int:
    if sequence < 1 or sequence > MAX_SEQUENCE:
        raise BridgeError("usage sequence is out of range")
    return 1 if sequence == MAX_SEQUENCE else sequence + 1


def locate_codex(explicit: Path | None = None) -> Path:
    if explicit is not None:
        candidate = explicit.expanduser().resolve()
        if not candidate.is_file():
            raise BridgeError(f"Codex executable was not found: {candidate}")
        return candidate

    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise BridgeError("LOCALAPPDATA is unavailable; pass --codex-path")
    root = Path(local_app_data) / "OpenAI" / "Codex" / "bin"
    candidates = [path for path in root.glob("**/codex.exe") if path.is_file()]
    if not candidates:
        raise BridgeError(f"No desktop-managed Codex executable was found under {root}")
    try:
        return max(candidates, key=lambda path: path.stat().st_mtime_ns)
    except OSError as exc:
        raise BridgeError("Unable to inspect desktop-managed Codex executables") from exc


class AppServerClient:
    def __init__(self, codex_path: Path, timeout: float = REQUEST_TIMEOUT_SECONDS) -> None:
        self._codex_path = codex_path
        self._timeout = timeout
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._condition = threading.Condition()
        self._responses: dict[int, dict[str, Any]] = {}
        self._next_id = 1
        self._closed = False

    def start(self) -> None:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._process = subprocess.Popen(
                [str(self._codex_path), "app-server", "--stdio"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise BridgeError("Unable to start Codex app-server") from exc
        self._reader = threading.Thread(target=self._read_loop, name="codex-app-server", daemon=True)
        self._reader.start()
        self._request(
            "initialize",
            {
                "clientInfo": {
                    "name": "stopwatch_micro_usage_bridge",
                    "title": "Stopwatch Micro Usage Bridge",
                    "version": "0.1.0",
                }
            },
        )
        self._notify("initialized", {})

    def _read_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            for line in process.stdout:
                try:
                    message = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if (
                    not isinstance(message, dict)
                    or isinstance(message.get("id"), bool)
                    or not isinstance(message.get("id"), int)
                    or "method" in message
                    or ("result" not in message and "error" not in message)
                ):
                    continue
                with self._condition:
                    self._responses[message["id"]] = message
                    self._condition.notify_all()
        finally:
            with self._condition:
                self._closed = True
                self._condition.notify_all()

    def _send(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise BridgeError("Codex app-server is not running")
        try:
            process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            process.stdin.flush()
        except (OSError, BrokenPipeError) as exc:
            raise BridgeError("Codex app-server connection closed") from exc

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"method": method, "params": params})

    def _request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        with self._condition:
            request_id = self._next_id
            self._next_id += 1
        message: dict[str, Any] = {"method": method, "id": request_id}
        if params is not None:
            message["params"] = params
        self._send(message)

        deadline = time.monotonic() + self._timeout
        with self._condition:
            while request_id not in self._responses:
                if self._closed:
                    raise BridgeError("Codex app-server exited")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BridgeError(f"Codex app-server timed out during {method}")
                self._condition.wait(remaining)
            response = self._responses.pop(request_id)
        if response.get("error") is not None:
            error = response.get("error")
            code = error.get("code") if isinstance(error, dict) else "unknown"
            raise BridgeError(f"Codex app-server rejected {method} (code={code})")
        if "result" not in response:
            raise BridgeError(f"Codex app-server returned no result for {method}")
        return response["result"]

    def read_usage(self) -> UsageSnapshot:
        return normalize_rate_limits(self._request("account/rateLimits/read"))

    def close(self) -> None:
        process = self._process
        self._closed = True
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        if self._reader is not None:
            self._reader.join(timeout=1)


def discover_port(explicit: str | None) -> str:
    if explicit:
        if re.fullmatch(r"COM\d+", explicit, flags=re.IGNORECASE) is None:
            raise BridgeError("--port must be COM followed by a number")
        return explicit.upper()
    if list_ports is None:
        raise BridgeError("pyserial is required to discover the StopWatch port")
    matches = [
        port.device
        for port in list_ports.comports()
        if (port.vid, port.pid) == USB_SERIAL_JTAG_VID_PID
        or "usb jtag/serial debug" in (port.description or "").casefold()
    ]
    matches = sorted(set(matches), key=str.casefold)
    if len(matches) != 1:
        found = ", ".join(matches) if matches else "none"
        raise BridgeError(f"Expected one ESP32-S3 USB Serial/JTAG port; found: {found}")
    return matches[0]


class SerialUsageTransport:
    def __init__(
        self,
        port: str,
        *,
        handshake_timeout: float = 12.0,
        acknowledgement_timeout: float = 5.0,
    ) -> None:
        if serial is None:
            raise BridgeError("pyserial is required; activate the ESP-IDF Python environment")
        self._serial = serial.Serial(port=None, baudrate=115200, timeout=0.2, write_timeout=2)
        self._serial.dtr = False
        self._serial.rts = False
        self._serial.port = port
        try:
            self._serial.open()
        except serial.SerialException as exc:
            raise BridgeError(f"Unable to open StopWatch serial port {port}") from exc
        self._stop = threading.Event()
        self._condition = threading.Condition()
        self._ping_passes = 0
        self._usage_results: dict[int, tuple[str, str]] = {}
        self._reader_stopped = False
        self._handshake_timeout = handshake_timeout
        self._acknowledgement_timeout = acknowledgement_timeout
        self._reader = threading.Thread(target=self._drain_loop, name="stopwatch-serial", daemon=True)
        self._reader.start()
        try:
            self._serial.write(b"\ndebug ping\n")
            self._serial.flush()
        except serial.SerialException as exc:
            self.close()
            raise BridgeError("StopWatch serial connection failed") from exc
        try:
            self._wait_for_ping()
        except BaseException:
            self.close()
            raise

    def _drain_loop(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    raw = self._serial.readline()
                except (OSError, serial.SerialException):
                    return
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").strip()
                match = RESULT_RE.search(line)
                if match is None:
                    continue
                command, status, details = match.group(1), match.group(2), match.group(3) or ""
                with self._condition:
                    if command == "ping" and status == "PASS":
                        self._ping_passes += 1
                    elif command == "host-usage":
                        sequence_match = re.search(r"(?:^| )seq=(\d+)(?: |$)", details)
                        if sequence_match is not None:
                            self._usage_results[int(sequence_match.group(1))] = (status, details)
                    self._condition.notify_all()
        finally:
            with self._condition:
                self._reader_stopped = True
                self._condition.notify_all()

    def _wait_for_ping(self) -> None:
        deadline = time.monotonic() + self._handshake_timeout
        next_ping = time.monotonic() + 1.0
        with self._condition:
            while self._ping_passes == 0:
                if self._reader_stopped:
                    raise BridgeError("StopWatch serial reader stopped during handshake")
                now = time.monotonic()
                remaining = deadline - now
                if remaining <= 0:
                    raise BridgeError("StopWatch serial handshake timed out")
                if now >= next_ping:
                    try:
                        self._serial.write(b"debug ping\n")
                        self._serial.flush()
                    except (OSError, serial.SerialException) as exc:
                        raise BridgeError("StopWatch serial handshake failed") from exc
                    next_ping = now + 1.0
                self._condition.wait(min(remaining, max(0.05, next_ping - now)))

    def send(self, sequence: int, snapshot: UsageSnapshot) -> None:
        payload = format_host_usage_line(sequence, snapshot).encode("ascii")
        try:
            self._serial.write(payload)
            self._serial.flush()
        except (OSError, serial.SerialException) as exc:
            raise BridgeError("StopWatch serial write failed") from exc
        deadline = time.monotonic() + self._acknowledgement_timeout
        with self._condition:
            while sequence not in self._usage_results:
                if self._reader_stopped:
                    raise BridgeError("StopWatch serial reader stopped before acknowledgement")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BridgeError("StopWatch did not acknowledge the usage update")
                self._condition.wait(remaining)
            status, details = self._usage_results.pop(sequence)
        if status != "PASS":
            reason = re.search(r"(?:^| )reason=([^ ]+)", details)
            reason_text = reason.group(1) if reason is not None else "rejected"
            if reason_text == "stale_sequence":
                current = re.search(r"(?:^| )current=(\d+)(?: |$)", details)
                if current is not None:
                    current_sequence = int(current.group(1))
                    if 1 <= current_sequence <= MAX_SEQUENCE:
                        raise StaleSequenceError(current_sequence)
            raise BridgeError(f"StopWatch rejected the usage update ({reason_text})")

    def close(self) -> None:
        self._stop.set()
        try:
            self._serial.close()
        except (OSError, serial.SerialException):
            pass
        if hasattr(self, "_reader"):
            self._reader.join(timeout=1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-path", type=Path, help="explicit desktop-managed codex.exe")
    parser.add_argument("--port", help="StopWatch USB Serial/JTAG port, for example COM5")
    parser.add_argument("--once", action="store_true", help="read and send one usage snapshot")
    return parser.parse_args()


def run(args: argparse.Namespace) -> int:
    sequence = max(1, min(int(time.time()), MAX_SEQUENCE))
    failure_index = 0
    client: AppServerClient | None = None
    transport: SerialUsageTransport | None = None
    try:
        while True:
            try:
                if client is None:
                    codex_path = locate_codex(args.codex_path)
                    client = AppServerClient(codex_path)
                    client.start()
                if transport is None:
                    transport = SerialUsageTransport(discover_port(args.port))
                snapshot = client.read_usage()
                try:
                    transport.send(sequence, snapshot)
                except StaleSequenceError as exc:
                    sequence = next_sequence(exc.current_sequence)
                    transport.send(sequence, snapshot)
                print(
                    "USAGE "
                    f"remaining_bp={snapshot.remaining_basis_points} "
                    f"reset_epoch={snapshot.reset_epoch} credits={snapshot.reset_credits}"
                )
                sequence = next_sequence(sequence)
                failure_index = 0
                if args.once:
                    return 0
                time.sleep(POLL_SECONDS)
            except BridgeError as exc:
                print(f"BRIDGE ERROR {exc}", file=sys.stderr)
                if transport is not None:
                    transport.close()
                    transport = None
                if client is not None:
                    client.close()
                    client = None
                if args.once:
                    return 2
                delay = RECONNECT_DELAYS_SECONDS[
                    min(failure_index, len(RECONNECT_DELAYS_SECONDS) - 1)
                ]
                failure_index += 1
                time.sleep(delay)
    except KeyboardInterrupt:
        return 130
    finally:
        if transport is not None:
            transport.close()
        if client is not None:
            client.close()


def main() -> int:
    return run(parse_args())


if __name__ == "__main__":
    sys.exit(main())
