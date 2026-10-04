import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import mosaico_backup_regions as backup


class RegionBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "export"
        self.output.mkdir()
        self.source = self.root / "source.partial"
        self.blob = bytes(range(256)) * 1024
        self.source.write_bytes(self.blob)
        self.manifest = self.root / "plan.json"
        self.write_plan([{"offset": 0x2000, "length": 0x7000},
                         {"offset": 0x9000, "length": 0x1000},
                         {"offset": 0x10000, "length": 0x2000},
                         {"offset": 0x20000, "length": 0x2000}])
        self.close = Mock()
        self.esp = SimpleNamespace(IS_STUB=False, IMAGE_CHIP_ID=32, CHIP_NAME="ESP32-S31",
            _port=SimpleNamespace(close=self.close),
            read_mac=lambda: bytes.fromhex(backup.EXPECTED_MAC.replace(":", "")),
            get_security_info=lambda: {"flags": 0, "flash_crypt_cnt": 0},
            flash_id=lambda: 0x180000, flash_set_parameters=Mock(),
            flash_md5sum=Mock(side_effect=lambda offset, length:
                hashlib.md5(self.blob[offset:offset+length]).hexdigest()))

    def write_plan(self, regions):
        self.manifest.write_text(json.dumps({"regions": regions}), encoding="utf-8")

    def run_main(self, detect=None):
        argv = ["export", "--source", str(self.source), "--manifest", str(self.manifest),
                "--output-dir", str(self.output), "--port", "NEVER_OPEN"]
        with patch.object(backup, "PRIVATE", self.root), patch("sys.argv", argv), \
                patch.object(backup.esptool, "detect_chip", side_effect=detect,
                             return_value=self.esp) as detector, \
                patch.object(backup.esptool, "attach_flash"), \
                patch.object(backup.esptool, "run_stub") as stub:
            backup.main()
            stub.assert_not_called()
            return detector

    def test_success_exports_frozen_source_only_after_all_md5_checks(self):
        def detect(*args, **kwargs):
            self.source.write_bytes(b"changed after preflight")
            return self.esp
        self.run_main(detect)
        self.esp.flash_set_parameters.assert_called_once_with(16 * 1024 * 1024)
        self.assertEqual(self.esp.flash_md5sum.call_count, 4)
        record = json.loads((self.output / "backup-regions.json").read_text())
        self.assertTrue(record["complete"])
        self.assertFalse(record["flash_write_performed"])
        self.assertEqual(record["source_sha256"], hashlib.sha256(self.blob).hexdigest())
        for region in record["regions"]:
            data = (self.output / region["file"]).read_bytes()
            self.assertEqual(data, self.blob[region["offset"]:region["offset"]+region["length"]])
            self.assertEqual(len(data), region["length"])
            self.assertEqual(hashlib.sha256(data).hexdigest(), region["sha256"])
        self.close.assert_called_once_with()

    def test_invalid_plans_rejected_before_serial(self):
        invalid = [[], [{"offset": 0x2001, "length": 0x1000}],
                   [{"offset": 0x2000, "length": 1}],
                   [{"offset": 0x2000, "length": 0}],
                   [{"offset": True, "length": 0x1000}],
                   [{"offset": 0, "length": 0x2000}],
                   [{"offset": 0xA000, "length": 0x1000}],
                   [{"offset": 0x2000, "length": 0x8000}],
                   [{"offset": 0x7F0000, "length": 0x1000}],
                   [{"offset": 0x20000, "length": 0x30000}],
                   [{"offset": 0x2000, "length": 0x2000},
                    {"offset": 0x3000, "length": 0x1000}]]
        for regions in invalid:
            with self.subTest(regions=regions):
                self.write_plan(regions)
                with patch.object(backup.esptool, "detect_chip") as detector, \
                        patch.object(backup, "PRIVATE", self.root):
                    with self.assertRaises(ValueError):
                        backup.prepare(self.source, self.manifest, self.output)
                    detector.assert_not_called()

    def test_output_must_be_empty_private_subdirectory(self):
        existing = self.output / "offset-00002000.bin"
        existing.write_bytes(b"preserve")
        with self.assertRaises(ValueError): self.run_main()
        self.assertEqual(existing.read_bytes(), b"preserve")
        existing.unlink()
        with patch.object(backup, "PRIVATE", self.output):
            with self.assertRaises(ValueError): backup.prepare(self.source, self.manifest, self.output)

    def test_final_range_md5_failure_leaves_no_exports(self):
        self.esp.flash_md5sum.side_effect = lambda offset, length: (
            "0" * 32 if offset == 0x20000 else hashlib.md5(self.blob[offset:offset+length]).hexdigest())
        with self.assertRaisesRegex(SystemExit, "MD5 mismatch"): self.run_main()
        self.assertEqual(list(self.output.iterdir()), [])
        self.close.assert_called_once_with()

    def test_device_identity_and_security_gates(self):
        for change in [{"IS_STUB": True}, {"IMAGE_CHIP_ID": 9},
                       {"read_mac": lambda: bytes(6)},
                       {"get_security_info": lambda: {"flags": 1, "flash_crypt_cnt": 0}},
                       {"get_security_info": lambda: {"flags": 0, "flash_crypt_cnt": 1}},
                       {"flash_id": lambda: 0x170000}]:
            with self.subTest(change=change), patch.multiple(self.esp, **change):
                with self.assertRaises(SystemExit): self.run_main()
                self.assertEqual(list(self.output.iterdir()), [])
                self.esp.flash_md5sum.assert_not_called()

    def test_racing_export_filename_is_not_overwritten(self):
        def detect(*args, **kwargs):
            (self.output / "offset-00002000.bin").write_bytes(b"preserve")
            return self.esp
        with self.assertRaises(FileExistsError): self.run_main(detect)
        self.assertEqual((self.output / "offset-00002000.bin").read_bytes(), b"preserve")
        self.assertFalse((self.output / "backup-regions.json").exists())

    def test_racing_json_is_not_overwritten(self):
        def detect(*args, **kwargs):
            (self.output / "backup-regions.json").write_bytes(b"preserve")
            return self.esp
        with self.assertRaises(FileExistsError): self.run_main(detect)
        self.assertEqual((self.output / "backup-regions.json").read_bytes(), b"preserve")


if __name__ == "__main__":
    unittest.main()
