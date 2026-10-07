"""Read-only IoTPower V1 native-parser capture; never changes output settings.

ABI evidence: https://gitee.com/dingjg8944/iot-power/tree/master/dynamic_library
readme.md and IoTPowerExample/Device.cs: cdecl, parse01 has 16 samples;
Program.cs labels getters mA and V. No V1 packet ID/raw packet API is documented.
ABI was also verified against the installed official Microsoft Store IoTPower
PowerAnalyzer cdecl imports and its embedded AMD64 iot_parser.dll (2026-10-07).
No native binaries are distributed by this tool.
10 kHz source: https://wiki.luatos.org/iotpower/power/index.html
and https://wiki.luatos.com/iotpower/devices.html.
10 kHz is a nominal program-stream rate, not a host-arrival clock. Energy is
integrated per received sample (rectangular rule); unobserved time is excluded.
An output-disabled capture does not measure an independently powered board.
Native DLL execution requires separate approval and a verified SHA256; this tool
does not download, discover, or silently load DLLs. --list never loads a DLL.
"""

import argparse
import csv
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import struct
import threading
import time

RATE_HZ = 10000
BATCH_SIZE = 16


class NativeBackend:
    """Whitelist bindings only: intentionally no output-setting binding."""

    def __init__(self, dll, expected_sha256):
        path = Path(dll)
        if not path.is_absolute() or not path.is_file():
            raise ValueError("DLL must be an explicit absolute file path")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256 or ""):
            raise ValueError("verified --dll-sha256 is required")
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
            source.seek(0)
            if source.read(2) != b"MZ":
                raise ValueError("not a PE DLL")
            source.seek(0x3C)
            offset_bytes = source.read(4)
            if len(offset_bytes) != 4:
                raise ValueError("truncated PE header")
            source.seek(struct.unpack("<I", offset_bytes)[0])
            header = source.read(6)
        if digest != expected_sha256.lower():
            raise ValueError("DLL SHA256 mismatch")
        if header != b"PE\0\0\x64\x86":
            raise ValueError("requires an AMD64 PE DLL")
        if os.name != "nt" or ctypes.sizeof(ctypes.c_void_p) != 8:
            raise ValueError("requires 64-bit Windows Python")
        self.dll_sha256 = digest
        self.lib = ctypes.CDLL(str(path), winmode=0x1100)
        signatures = {
            "iot_uart_open": ([ctypes.c_int32], ctypes.c_uint8),
            "iot_uart_send_initial": ([], None),
            "iot_uart_request_close": ([], None),
            "iot_parse": ([], ctypes.c_uint8),
            "iot_get_current": ([ctypes.c_int32], ctypes.c_double),
            "iot_get_voltage": ([ctypes.c_int32], ctypes.c_double),
            "iot_get_power_on": ([], ctypes.c_uint8),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.lib, name)
            function.argtypes, function.restype = args, result

    def open(self, port):
        match = re.fullmatch(r"COM([1-9][0-9]*)", port, re.IGNORECASE)
        if not match or int(match[1]) > 2147483647:
            raise ValueError("V1 requires a COM port")
        if self.lib.iot_uart_open(int(match[1])) != 1:
            raise RuntimeError("native parser could not open port")

    def initialize(self):
        self.lib.iot_uart_send_initial()

    def read(self):
        kind = self.lib.iot_parse()  # vendor API blocks; do not busy poll
        if kind == 1:
            return kind, tuple((self.lib.iot_get_voltage(i),
                                self.lib.iot_get_current(i)) for i in range(16))
        if kind == 4:
            return kind, {"output_enabled": bool(self.lib.iot_get_power_on())}
        return kind, None

    def close(self):
        self.lib.iot_uart_request_close()


class Recorder:
    """Constant-memory CSV/summary writer. Input units are V and mA."""

    def __init__(self, stream):
        self.writer = csv.writer(stream)
        self.writer.writerow(("batch_index", "sample_index", "receive_monotonic_s",
                              "nominal_in_batch_s", "voltage_V", "current_A",
                              "power_W", "valid", "host_delay_suspected"))
        self.batches = self.samples = self.valid = self.invalid = self.partial = 0
        self.delays = 0
        self.peak_i = self.peak_p = None
        self.sum_i = self.sum_p = self.wh = 0.0
        self.last_receive = None

    def batch(self, receive, values):
        if not math.isfinite(receive) or (self.last_receive is not None
                                          and receive < self.last_receive):
            raise ValueError("receive timestamp must be finite and monotonic")
        if not 1 <= len(values) <= BATCH_SIZE:
            raise ValueError("batch must contain 1..16 samples")
        delayed = self.last_receive is not None and receive - self.last_receive > 0.05
        self.delays += int(delayed)
        self.partial += int(len(values) != BATCH_SIZE)
        rows = []
        for i, (voltage, current_ma) in enumerate(values):
            current = current_ma / 1000.0
            power = voltage * current
            valid = all(math.isfinite(x) for x in (voltage, current, power))
            rows.append((self.batches, self.samples, receive, i / RATE_HZ,
                         voltage if valid else "", current if valid else "",
                         power if valid else "", int(valid), int(delayed)))
            self.samples += 1
            if valid:
                self.valid += 1
                self.sum_i += current
                self.sum_p += power
                self.wh += power / RATE_HZ / 3600
                self.peak_i = current if self.peak_i is None else max(self.peak_i, current)
                self.peak_p = power if self.peak_p is None else max(self.peak_p, power)
            else:
                self.invalid += 1
        self.writer.writerows(rows)
        self.batches += 1
        self.last_receive = receive

    def summary(self):
        return {"batches": self.batches, "samples": self.samples,
                "valid_samples": self.valid, "invalid_samples": self.invalid,
                "partial_batches": self.partial,
                "average_current_A": self.sum_i / self.valid if self.valid else None,
                "average_power_W": self.sum_p / self.valid if self.valid else None,
                "energy_received_samples_Wh": self.wh,
                "observed_sample_seconds": self.valid / RATE_HZ,
                "peak_current_A": self.peak_i, "peak_power_W": self.peak_p,
                "nominal_rate_Hz": RATE_HZ,
                "host_delay_suspicions": self.delays,
                "packet_loss": "unknown: no documented V1 packet counter",
                "raw_packets": "unavailable in documented native API",
                "timebase": "nominal 10kHz; host receive timestamps are not sample timestamps",
                "energy_scope": "rectangular sum of valid received samples at nominal 10kHz; not elapsed-time energy",
                "warning": "power-path wiring is not verified by this tool"}


def capture(backend, port, seconds, output, clock=time.monotonic,
            allow_enabled_output=False):
    """Fresh directory, bounded queue, no settings writes; close on every exit."""
    if not math.isfinite(seconds) or not 0 < seconds <= 86400:
        raise ValueError("seconds must be finite, positive, and <=86400")
    output = Path(output)
    if not output.parent.is_dir():
        raise ValueError("output parent must already exist")
    output.mkdir()  # exclusive: do not overwrite or reuse someone else's capture
    packets = queue.Queue(maxsize=64)
    stop = threading.Event()
    thread = None
    error = None
    status = None
    initialized = False
    started = clock()

    def reader():
        try:
            while not stop.is_set():
                kind, payload = backend.read()
                if kind == 0:
                    continue
                event = (clock(), kind, payload)
                while not stop.is_set():
                    try:
                        packets.put(event, timeout=0.1)
                        break
                    except queue.Full:
                        pass
                if kind == 255:
                    return
        except Exception as exc:
            while not stop.is_set():
                try:
                    packets.put((clock(), -1, str(exc)), timeout=0.1)
                    return
                except queue.Full:
                    pass

    with (output / "samples.csv").open("x", newline="", encoding="utf-8") as stream:
        recorder = Recorder(stream)
        try:
            backend.open(port)
            backend.initialize()
            thread = threading.Thread(target=reader, daemon=True)
            thread.start()
            deadline = started + seconds
            while clock() < deadline:
                try:
                    receive, kind, payload = packets.get(timeout=min(0.2, max(0.001, deadline-clock())))
                except queue.Empty:
                    if not initialized and clock() - started > 5:
                        raise RuntimeError("no initialization status within 5 seconds")
                    continue
                if kind in (255, -1):
                    raise RuntimeError("serial disconnected" if kind == 255 else payload)
                if kind == 4:
                    initialized, status = True, payload
                    if type(status.get("output_enabled")) is not bool:
                        raise RuntimeError("output status is unknown")
                    if status["output_enabled"] and not allow_enabled_output:
                        raise RuntimeError("output is enabled; refusal to change or capture it")
                elif kind == 1 and initialized:
                    recorder.batch(receive, payload)
                if not initialized and clock() - started > 5:
                    raise RuntimeError("no initialization status within 5 seconds")
            if not initialized:
                raise RuntimeError("no initialization status received")
            if not recorder.valid:
                raise RuntimeError("no valid initialized samples received")
        except BaseException as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            stop.set()
            try:
                backend.close()
            except Exception as exc:
                error = error or f"close failed: {exc}"
            if thread is not None:
                thread.join(timeout=2)
                if thread.is_alive():
                    error = error or "native reader did not stop within 2 seconds"
            summary = recorder.summary()
            elapsed = clock()-started
            summary["received_to_host_expected_ratio"] = recorder.samples / (elapsed * RATE_HZ) if elapsed > 0 else None
            summary["whole_wall_time_energy_Wh"] = None
            summary["allow_enabled_output"] = bool(allow_enabled_output)
            summary["output_enabled"] = status.get("output_enabled") if status else None
            if summary["output_enabled"] is False:
                summary["warning"] = "output-disabled capture is not board power measurement"
            elif summary["output_enabled"] is True:
                summary["warning"] = "output was already enabled; power-path wiring and exclusive DUT load must be verified separately"
            summary.update({"port": port, "device_status": status,
                            "dll_sha256": getattr(backend, "dll_sha256", None),
                            "host_elapsed_seconds": clock()-started,
                            "complete": error is None, "error": error})
            with (output / "summary.json").open("x", encoding="utf-8") as destination:
                json.dump(summary, destination, indent=2, allow_nan=False)
    if error:
        raise RuntimeError(error)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--port")
    parser.add_argument("--dll", help="approved absolute native DLL path; never auto-discovered")
    parser.add_argument("--dll-sha256", help="independently verified approved DLL SHA256")
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--output", type=Path, help="fresh private directory under existing parent")
    parser.add_argument("--allow-enabled-output", action="store_true",
                        help="read already-enabled output; never enables or changes it (manual panel setup required)")
    args = parser.parse_args(argv)
    if args.list:
        from serial.tools import list_ports
        print(json.dumps([{"port": p.device, "description": p.description,
                           "vid": p.vid, "pid": p.pid, "serial_number": p.serial_number}
                          for p in list_ports.comports()], indent=2))
        return 0
    if not all((args.port, args.dll, args.dll_sha256, args.output)):
        parser.error("capture requires --port --dll --dll-sha256 --output")
    try:
        backend = NativeBackend(args.dll, args.dll_sha256)
        summary = capture(backend, args.port, args.seconds, args.output,
                          allow_enabled_output=args.allow_enabled_output)
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(1, f"capture failed: {exc}\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

