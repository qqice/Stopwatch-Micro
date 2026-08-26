#!/usr/bin/env python3
"""Host-side regression tests that do not require connected hardware."""

from __future__ import annotations

import json
import os
import queue
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))

import package_release  # noqa: E402
import serial_debug_test  # noqa: E402
import stopwatch_bridge  # noqa: E402


class FakeDebugClient:
    def __init__(self, statuses: dict[str, str] | None = None) -> None:
        self.statuses = statuses or {}

    def command(self, _text: str, expected: str, _timeout: float) -> serial_debug_test.Result:
        default = "SKIP" if expected == "pairing-reset" else "PASS"
        return serial_debug_test.Result(expected, self.statuses.get(expected, default), "fixture=1")


class FakeSerialPort:
    def __init__(self, *, ping: bool = True, usage: str = "pass", current: int = 0) -> None:
        self.dtr = True
        self.rts = True
        self.port: str | None = None
        self.closed = False
        self._ping = ping
        self._usage = usage
        self._current = current
        self._incoming: queue.Queue[bytes] = queue.Queue()

    def open(self) -> None:
        self.closed = False

    def write(self, payload: bytes) -> int:
        if self.closed:
            raise OSError("closed")
        if b"debug ping\n" in payload and self._ping:
            self._incoming.put(b"DBG RESULT command=ping status=PASS reply=pong\r\n")
        if payload.startswith(b"debug host-usage "):
            sequence = int(payload.split()[2])
            if self._usage == "pass":
                self._incoming.put(
                    f"DBG RESULT command=host-usage status=PASS seq={sequence}\r\n".encode()
                )
            elif self._usage == "stale":
                self._incoming.put(
                    (
                        "DBG RESULT command=host-usage status=FAIL "
                        f"seq={sequence} reason=stale_sequence current={self._current}\r\n"
                    ).encode()
                )
        return len(payload)

    def flush(self) -> None:
        return

    def readline(self) -> bytes:
        if self.closed:
            raise OSError("closed")
        try:
            return self._incoming.get(timeout=0.005)
        except queue.Empty:
            return b""

    def close(self) -> None:
        self.closed = True


class FakeHidDevice:
    def __init__(self) -> None:
        self.opened_path: bytes | str | None = None
        self.writes: list[bytes] = []
        self.closed = False

    def open_path(self, path: bytes | str) -> None:
        self.opened_path = path

    def write(self, payload: bytes) -> int:
        self.writes.append(bytes(payload))
        return len(payload)

    def close(self) -> None:
        self.closed = True


class FakeHidModule:
    def __init__(self, entries: list[dict[str, object]]) -> None:
        self.entries = entries
        self.instance = FakeHidDevice()
        self.enumerated: tuple[int, int] | None = None

    def enumerate(self, vendor_id: int, product_id: int) -> list[dict[str, object]]:
        self.enumerated = (vendor_id, product_id)
        return self.entries

    def device(self) -> FakeHidDevice:
        return self.instance


class SerialPolicyTests(unittest.TestCase):
    def test_strict_policy(self) -> None:
        _, failures = serial_debug_test.run_automated(FakeDebugClient(), allow_offline=False)
        self.assertEqual(failures, [])

    def test_offline_allows_only_connection_dependent_skips(self) -> None:
        client = FakeDebugClient({"ui-cycle": "SKIP", "transport": "SKIP", "perf": "SKIP"})
        _, failures = serial_debug_test.run_automated(client, allow_offline=True)
        self.assertEqual(failures, [])

    def test_unknown_status_and_pairing_pass_fail(self) -> None:
        client = FakeDebugClient({"status": "ERROR", "pairing-reset": "PASS"})
        _, failures = serial_debug_test.run_automated(client, allow_offline=True)
        self.assertTrue(any("status:" in failure for failure in failures))
        self.assertTrue(any("pairing-reset:" in failure for failure in failures))


class FlashPlanTests(unittest.TestCase):
    def write_plan(self, root: Path, flash_files: dict[str, str]) -> None:
        plan = {
            "write_flash_args": ["--flash_mode", "dio", "--flash_size", "16MB"],
            "flash_settings": {"flash_mode": "dio", "flash_size": "16MB", "flash_freq": "80m"},
            "flash_files": flash_files,
            "extra_esptool_args": {
                "after": "hard_reset",
                "before": "default_reset",
                "stub": True,
                "chip": "esp32s3",
            },
        }
        (root / "flasher_args.json").write_text(json.dumps(plan), encoding="utf-8")

    def test_valid_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bootloader.bin").write_bytes(b"a" * 32)
            (root / "app.bin").write_bytes(b"b" * 64)
            self.write_plan(root, {"0x0": "bootloader.bin", "0x20000": "app.bin"})
            _, entries = package_release.load_flash_plan(root)
            self.assertEqual([entry[0] for entry in entries], ["0x0", "0x20000"])

    def test_path_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "build"
            root.mkdir()
            (parent / "outside.bin").write_bytes(b"x")
            self.write_plan(root, {"0x0": "../outside.bin"})
            with self.assertRaises(SystemExit):
                package_release.load_flash_plan(root)


    def test_duplicate_basename_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a").mkdir()
            (root / "b").mkdir()
            (root / "a" / "same.bin").write_bytes(b"a")
            (root / "b" / "same.bin").write_bytes(b"b")
            self.write_plan(root, {"0x0": "a/same.bin", "0x100": "b/same.bin"})
            with self.assertRaises(SystemExit):
                package_release.load_flash_plan(root)

    def test_case_insensitive_duplicate_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "App.bin").write_bytes(b"a")
            (root / "app.bin").write_bytes(b"b")
            self.write_plan(root, {"0x0": "App.bin", "0x100": "app.bin"})
            with self.assertRaises(SystemExit):
                package_release.load_flash_plan(root)

    def test_generated_bundle_name_is_reserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.json").write_bytes(b"firmware")
            self.write_plan(root, {"0x0": "manifest.json"})
            with self.assertRaises(SystemExit):
                package_release.load_flash_plan(root)

    def test_overlap_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "first.bin").write_bytes(b"a" * 32)
            (root / "second.bin").write_bytes(b"b" * 32)
            self.write_plan(root, {"0x0": "first.bin", "0x10": "second.bin"})
            with self.assertRaises(SystemExit):
                package_release.load_flash_plan(root)


class StopwatchBridgeTests(unittest.TestCase):
    def test_normalizes_canonical_codex_bucket(self) -> None:
        result = {
            "rateLimits": {
                "limitId": "legacy",
                "primary": {"usedPercent": 99, "windowDurationMins": 5, "resetsAt": 1},
            },
            "rateLimitsByLimitId": {
                "codex": {
                    "limitId": "codex",
                    "primary": {
                        "usedPercent": 17.25,
                        "windowDurationMins": 10080,
                        "resetsAt": 1788295390,
                    },
                },
                "codex_model": {
                    "limitId": "codex_model",
                    "primary": {"usedPercent": 95, "windowDurationMins": 300, "resetsAt": 2},
                },
            },
            "rateLimitResetCredits": {"availableCount": 2},
        }
        snapshot = stopwatch_bridge.normalize_rate_limits(result, captured_epoch=1234)
        self.assertEqual(snapshot.remaining_basis_points, 8275)
        self.assertEqual(snapshot.reset_epoch, 1788295390)
        self.assertEqual(snapshot.captured_epoch, 1234)
        self.assertEqual(snapshot.reset_credits, 2)

    def test_selects_more_consumed_window_and_clamps_remaining(self) -> None:
        result = {
            "rateLimitsByLimitId": {
                "codex": {
                    "limitId": "codex",
                    "primary": {
                        "usedPercent": 80,
                        "windowDurationMins": 300,
                        "resetsAt": 100,
                    },
                    "secondary": {
                        "usedPercent": 101.5,
                        "windowDurationMins": 10080,
                        "resetsAt": 200,
                    },
                }
            }
        }
        snapshot = stopwatch_bridge.normalize_rate_limits(result, captured_epoch=300)
        self.assertEqual(snapshot.remaining_basis_points, 0)
        self.assertEqual(snapshot.reset_epoch, 200)
        self.assertEqual(snapshot.reset_credits, 0)

    def test_falls_back_to_legacy_codex_bucket(self) -> None:
        result = {
            "rateLimits": {
                "limitId": "codex",
                "primary": {
                    "usedPercent": 25,
                    "windowDurationMins": 60,
                    "resetsAt": None,
                },
            }
        }
        snapshot = stopwatch_bridge.normalize_rate_limits(result, captured_epoch=400)
        self.assertEqual(snapshot.remaining_basis_points, 7500)
        self.assertEqual(snapshot.reset_epoch, 0)

    def test_rejects_missing_canonical_bucket(self) -> None:
        with self.assertRaises(stopwatch_bridge.BridgeError):
            stopwatch_bridge.normalize_rate_limits(
                {
                    "rateLimitsByLimitId": {
                        "other": {
                            "limitId": "other",
                            "primary": {"usedPercent": 10},
                        }
                    }
                }
            )

    def test_formats_single_line_host_usage_command(self) -> None:
        snapshot = stopwatch_bridge.UsageSnapshot(8275, 1788295390, 1787740000, 1)
        self.assertEqual(
            stopwatch_bridge.format_host_usage_line(7, snapshot),
            "debug host-usage 7 8275 1788295390 1787740000 1\n",
        )

    def test_formats_fixed_size_hid_usage_report(self) -> None:
        snapshot = stopwatch_bridge.UsageSnapshot(8275, 1788295390, 1787740000, 2)
        report = stopwatch_bridge.format_hid_usage_report(0x01020304, snapshot)
        self.assertEqual(len(report), 64)
        self.assertEqual(
            report,
            bytes.fromhex(
                "06a501040302015320de38976a60bf8e6a0210afe39a"
                "000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
            ),
        )

    def test_selects_only_codex_micro_vendor_hid_collection(self) -> None:
        entries = [
            {
                "vendor_id": 0x303A,
                "product_id": 0x8360,
                "usage_page": 0x0001,
                "usage": 0x0002,
                "path": b"system-mouse",
            },
            {
                "vendor_id": 0x303A,
                "product_id": 0x8360,
                "usage_page": 0xFF00,
                "usage": 0x0001,
                "path": b"codex-micro",
            },
            {
                "vendor_id": 0x303A,
                "product_id": 0x1001,
                "usage_page": 0xFF00,
                "usage": 0x0001,
                "path": b"usb-jtag",
            },
        ]
        self.assertEqual(stopwatch_bridge.select_hid_path(entries), b"codex-micro")

    def test_hid_transport_opens_and_writes_selected_collection(self) -> None:
        fake_hid = FakeHidModule(
            [
                {
                    "vendor_id": 0x303A,
                    "product_id": 0x8360,
                    "usage_page": 0xFF00,
                    "usage": 0x0001,
                    "path": b"codex-micro",
                }
            ]
        )
        snapshot = stopwatch_bridge.UsageSnapshot(8000, 2000, 1000, 1)
        with unittest.mock.patch.object(stopwatch_bridge, "hid", fake_hid):
            transport = stopwatch_bridge.HidUsageTransport()
            try:
                transport.send(10, snapshot)
            finally:
                transport.close()
        self.assertEqual(fake_hid.enumerated, (0x303A, 0x8360))
        self.assertEqual(fake_hid.instance.opened_path, b"codex-micro")
        self.assertEqual(
            fake_hid.instance.writes,
            [stopwatch_bridge.format_hid_usage_report(10, snapshot)],
        )
        self.assertTrue(fake_hid.instance.closed)

    def test_auto_transport_falls_back_only_when_hid_is_unavailable(self) -> None:
        sentinel = object()
        with (
            unittest.mock.patch.object(
                stopwatch_bridge,
                "HidUsageTransport",
                side_effect=stopwatch_bridge.HidUnavailableError("not present"),
            ),
            unittest.mock.patch.object(stopwatch_bridge, "discover_port", return_value="COM5"),
            unittest.mock.patch.object(
                stopwatch_bridge, "SerialUsageTransport", return_value=sentinel
            ) as serial_transport,
        ):
            self.assertIs(stopwatch_bridge.create_usage_transport("auto", None), sentinel)
        serial_transport.assert_called_once_with("COM5")

    def test_rejects_out_of_range_wire_values(self) -> None:
        with self.assertRaises(stopwatch_bridge.BridgeError):
            stopwatch_bridge.format_host_usage_line(
                1,
                stopwatch_bridge.UsageSnapshot(10001, 1788295390, 1787740000, 1),
            )

    def test_locates_newest_desktop_managed_codex(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "OpenAI" / "Codex" / "bin" / "old" / "codex.exe"
            new = root / "OpenAI" / "Codex" / "bin" / "new" / "codex.exe"
            old.parent.mkdir(parents=True)
            new.parent.mkdir(parents=True)
            old.write_bytes(b"old")
            new.write_bytes(b"new")
            os.utime(old, (1, 1))
            os.utime(new, (2, 2))
            with unittest.mock.patch.dict(os.environ, {"LOCALAPPDATA": str(root)}):
                self.assertTrue(os.path.samefile(stopwatch_bridge.locate_codex(), new))

    def test_serial_ping_timeout_closes_port(self) -> None:
        fake = FakeSerialPort(ping=False)
        with unittest.mock.patch.object(stopwatch_bridge.serial, "Serial", return_value=fake):
            with self.assertRaises(stopwatch_bridge.BridgeError):
                stopwatch_bridge.SerialUsageTransport("COM5", handshake_timeout=0.02)
        self.assertTrue(fake.closed)

    def test_serial_accepts_ack_that_arrives_before_wait(self) -> None:
        fake = FakeSerialPort()
        snapshot = stopwatch_bridge.UsageSnapshot(8000, 2000, 1000, 1)
        with unittest.mock.patch.object(stopwatch_bridge.serial, "Serial", return_value=fake):
            transport = stopwatch_bridge.SerialUsageTransport("COM5", handshake_timeout=0.2)
            try:
                transport.send(10, snapshot)
            finally:
                transport.close()

    def test_serial_reports_stale_current_for_sequence_sync(self) -> None:
        fake = FakeSerialPort(usage="stale", current=123)
        snapshot = stopwatch_bridge.UsageSnapshot(8000, 2000, 1000, 1)
        with unittest.mock.patch.object(stopwatch_bridge.serial, "Serial", return_value=fake):
            transport = stopwatch_bridge.SerialUsageTransport("COM5", handshake_timeout=0.2)
            try:
                with self.assertRaises(stopwatch_bridge.StaleSequenceError) as caught:
                    transport.send(10, snapshot)
                self.assertEqual(caught.exception.current_sequence, 123)
                self.assertEqual(stopwatch_bridge.next_sequence(123), 124)
            finally:
                transport.close()


if __name__ == "__main__":
    unittest.main()
