#!/usr/bin/env python3
"""Host-side regression tests that do not require connected hardware."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))

import package_release  # noqa: E402
import serial_debug_test  # noqa: E402


class FakeDebugClient:
    def __init__(self, statuses: dict[str, str] | None = None) -> None:
        self.statuses = statuses or {}

    def command(self, _text: str, expected: str, _timeout: float) -> serial_debug_test.Result:
        default = "SKIP" if expected == "pairing-reset" else "PASS"
        return serial_debug_test.Result(expected, self.statuses.get(expected, default), "fixture=1")


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


if __name__ == "__main__":
    unittest.main()
