#!/usr/bin/env python3
"""HOST MOCK ONLY: migration safety tests; never open a serial port or a device.
Run with the ESP-IDF Python environment (esptool/pyserial dependencies).
Image/bootloader/partition decoding is deliberately mocked, not hardware acceptance.
"""
import contextlib
import io
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from serial import SerialException
import mosaico_ota_migrate as migration


class MockROM:
    IS_STUB = False
    IMAGE_CHIP_ID = 32
    CHIP_NAME = 'ESP32-S31'

    def __init__(self, snapshot):
        self.flash = bytearray(snapshot)
        self.resets = self.closes = 0
        self.md5_calls = []
        self._port = self

    def close(self): self.closes += 1
    def hard_reset(self): self.resets += 1
    def read_mac(self): return bytes.fromhex(migration.EXPECTED_MAC.replace(':', ''))
    def get_security_info(self): return {'flags': 0, 'flash_crypt_cnt': 0}
    def flash_id(self): return 0x180000
    def flash_set_parameters(self, size):
        if size != migration.NOR_SIZE: raise AssertionError('unexpected NOR size')
    def flash_md5sum(self, offset, length):
        self.md5_calls.append((offset, length))
        return migration.md5(self.flash[offset:offset + length])


class MigrationHostMockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mosaico-ota-host-mock-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / 'bundle'
        self.bundle.mkdir()
        self.snapshot = b'\xff' * migration.NOR_SIZE
        self.source = self.root / 'mock-snapshot.bin'
        self.source.write_bytes(self.snapshot)
        self.old = {
            'nvs': (1, 2, 0xa000, 0x6000, 0),
            'otadata': (1, 0, 0x10000, 0x2000, 0),
            'phy_init': (1, 1, 0x12000, 0x1000, 0),
            'ui_apps': (1, 0x41, 0x7f0000, 0x320000, 0),
            'system': (1, 0x83, 0xb10000, 0x2ee000, 0),
            'factory': (0, 0, 0x20000, 0x7d0000, 0),
        }
        self.new = {k: v for k, v in self.old.items() if k != 'factory'}
        self.new.update(ota_0=(0, 16, 0x20000, 0x3e0000, 0),
                        ota_1=(0, 17, 0x400000, 0x3f0000, 0))
        self.plan = {
            'schema': 1, 'mac': migration.EXPECTED_MAC,
            'source_sha256': migration.sha(self.snapshot),
            'baseline_bytes': 1024, 'baseline_sha256': migration.sha(self.snapshot[0x20000:0x20400]),
            'flash_bytes': migration.NOR_SIZE, 'writes': [],
        }
        for name, offset, data in [
            ('candidate.bin', 0x400000, b'MOCK_APP' * 600),
            ('bootloader.bin', 0x2000, b'MOCK_LOADER' * 100),
            ('partition-table.bin', 0x9000, b'MOCK_NEW_TABLE'),
            ('otadata.bin', 0x10000, migration.otadata_image()),
        ]:
            self.add_artifact(name, offset, data)
        candidate = self.plan['writes'][0]
        self.plan['ota_acceptance'] = {key: candidate[key] for key in ['bytes', 'erase_length', 'sha256']}
        self.plan['ota_acceptance']['offset'] = 0x20000
        self.save_plan()
        self.rom = MockROM(self.snapshot)
        self.write_calls = []
        self.fail_write = False
        self.detect = self.patch(migration.esptool, 'detect_chip', return_value=self.rom)
        self.attach = self.patch(migration.esptool, 'attach_flash')
        self.patch(migration.esptool, 'write_flash', side_effect=self.write_flash)
        self.patch(migration, 'PREFIX_SHA', new=migration.sha(self.snapshot[:0x9000]))
        self.image_validator = self.patch(migration, 'validate_image')
        self.boot_validator = self.patch(migration, 'validate_bootloader')
        self.patch(migration, 'partitions', side_effect=lambda raw: dict(self.old if raw[0] == 255 else self.new))

    def patch(self, obj, name, **kw):
        p = patch.object(obj, name, **kw)
        result = p.start()
        self.addCleanup(p.stop)
        return result

    def add_artifact(self, name, offset, data):
        (self.bundle / name).write_bytes(data)
        self.plan['writes'].append({'file': name, 'offset': offset, 'bytes': len(data),
                                   'erase_length': migration.sector_size(len(data)), 'sha256': migration.sha(data)})

    def save_plan(self):
        (self.bundle / 'migration-plan.json').write_text(json.dumps(self.plan))

    def replace_artifact(self, name, data):
        p = next(p for p in self.plan['writes'] if p['file'] == name)
        (self.bundle / name).write_bytes(data)
        p.update(bytes=len(data), erase_length=migration.sector_size(len(data)), sha256=migration.sha(data))
        self.save_plan()

    def write_flash(self, esp, ranges, **kwargs):
        self.assertIs(esp, self.rom)
        self.assertEqual(esp.WRITE_FLASH_ATTEMPTS, 1)
        self.assertEqual(len(ranges), 1)
        self.assertTrue(kwargs['no_compress'])
        self.assertFalse(kwargs['erase_all'])
        self.assertFalse(kwargs['force'])
        # All five exact backups and their manifest must exist before the first write.
        manifest = json.loads((self.bundle / 'rollback/backup-regions.json').read_text())
        self.assertTrue(manifest['complete'])
        self.assertEqual(len(manifest['regions']), 5)
        self.write_calls.append(ranges[0][0])
        if self.fail_write:
            raise SerialException('HOST MOCK injected first write failure; do not reconnect')
        offset, data = ranges[0]
        length = migration.sector_size(len(data))
        esp.flash[offset:offset + length] = b'\xff' * length
        esp.flash[offset:offset + len(data)] = data

    def execute(self):
        with contextlib.redirect_stdout(io.StringIO()):
            migration.execute(self.bundle, self.source, 'HOST_MOCK_NO_DEVICE')

    def test_otadata_crc_and_initial_states(self):
        blob = migration.otadata_image()
        self.assertEqual(len(blob), 8192)
        for offset, seq, state in [(0, 1, 2), (4096, 2, 0)]:
            actual_seq, label, actual_state, crc = struct.unpack_from('<I20sII', blob, offset)
            self.assertEqual((actual_seq, actual_state), (seq, state))
            self.assertEqual(label, b'\xff' * 20)
            # Independent bitwise equivalent of ESP-IDF CRC32 LE with seed 0xffffffff.
            expected_crc = 0
            for byte in struct.pack('<I', seq):
                expected_crc ^= byte
                for _ in range(8):
                    expected_crc = (expected_crc >> 1) ^ (0xedb88320 if expected_crc & 1 else 0)
            self.assertEqual(crc, expected_crc ^ 0xffffffff)
            self.assertEqual(blob[offset + 32:offset + 4096], b'\xff' * (4096 - 32))
        self.assertEqual((1 - 1) % 2, 0)  # seq1 selects ota_0 VALID
        self.assertEqual((2 - 1) % 2, 1)  # seq2 selects ota_1 NEW (not PENDING_VERIFY)

    def test_sector_ceiling_and_full_complement_protect_app0(self):
        for n, aligned in [(0, 0), (1, 4096), (4095, 4096), (4096, 4096), (4097, 8192)]:
            self.assertEqual(migration.sector_size(n), aligned)
        gaps = migration.complement(self.plan['writes'])
        writes = [(p['offset'], p['erase_length']) for p in self.plan['writes']]
        all_regions = sorted(gaps + writes)
        cursor = 0
        for offset, size in all_regions:
            self.assertEqual(offset, cursor)
            cursor += size
        self.assertEqual(cursor, migration.NOR_SIZE)
        self.assertTrue(any(o <= 0x20000 and o + n >= 0x400000 for o, n in gaps))
        with self.assertRaisesRegex(ValueError, 'overlap'):
            migration.complement(self.plan['writes'] + [self.plan['writes'][0]])

    def test_success_order_backups_and_untouched_md5(self):
        self.execute()
        self.detect.assert_called_once_with('HOST_MOCK_NO_DEVICE', connect_mode='no-reset', connect_attempts=3)
        self.assertEqual(self.write_calls, [0x400000, 0x2000, 0x9000, 0x10000])
        self.assertEqual((self.rom.resets, self.rom.closes), (1, 1))
        backup = self.bundle / 'rollback'
        manifest = json.loads((backup / 'backup-regions.json').read_text())
        self.assertEqual(len(list(backup.glob('offset-*.bin'))), 5)
        self.assertEqual([r['offset'] for r in manifest['regions']], self.write_calls + [0x20000])
        self.assertEqual(manifest['regions'][-1]['length'], self.plan['writes'][0]['erase_length'])
        for region in manifest['regions']:
            data = (backup / region['file']).read_bytes()
            self.assertEqual(len(data), region['length'])
            self.assertEqual(migration.sha(data), region['sha256'])
        for region in manifest['untouched']:
            o, n = region['offset'], region['length']
            self.assertEqual(migration.md5(self.rom.flash[o:o+n]), region['md5'])
            self.assertGreaterEqual(self.rom.md5_calls.count((o, n)), 2)
        self.assertEqual(self.rom.flash[0x20000:0x400000], self.snapshot[0x20000:0x400000])
        result = json.loads((self.bundle / 'write-result.json').read_text())
        self.assertTrue(result['written_verified'] and result['untouched_verified'] and result['app0_untouched'])
        self.assertFalse(result['runtime_accepted'])

    def test_serial_failure_is_fail_stop_without_reset_reconnect_or_result(self):
        self.fail_write = True
        with self.assertRaisesRegex(SerialException, 'HOST MOCK'):
            self.execute()
        self.assertEqual(self.write_calls, [0x400000])
        self.assertEqual(self.rom.WRITE_FLASH_ATTEMPTS, 1)
        self.assertEqual(self.detect.call_count, 1)
        self.assertEqual((self.rom.resets, self.rom.closes), (0, 1))
        self.assertFalse((self.bundle / 'write-result.json').exists())
        self.assertEqual(len(list((self.bundle / 'rollback').glob('offset-*.bin'))), 5)

    def test_reject_bad_hash_oversize_metadata_and_wrong_layout_before_connect(self):
        original_table = (self.bundle / 'partition-table.bin').read_bytes()
        candidate = self.bundle / 'candidate.bin'
        original_candidate = candidate.read_bytes()
        candidate.write_bytes(b'CORRUPT')
        with self.assertRaisesRegex(ValueError, 'artifact changed'):
            self.execute()
        candidate.write_bytes(original_candidate)
        self.replace_artifact('partition-table.bin', b'MOCK_TOO_BIG' + b'x' * 4096)
        with self.assertRaisesRegex(ValueError, 'metadata overflow'):
            self.execute()
        self.replace_artifact('partition-table.bin', original_table)
        self.new['ota_1'] = (0, 17, 0x410000, 0x3f0000, 0)
        with self.assertRaisesRegex(ValueError, 'wrong slots'):
            self.execute()
        self.new['ota_1'] = (0, 17, 0x400000, 0x3f0000, 0)
        self.plan['ota_acceptance']['erase_length'] += 4096
        self.save_plan()
        with self.assertRaisesRegex(ValueError, 'OTA acceptance backup not bound'):
            self.execute()
        self.detect.assert_not_called()
        self.assertEqual(self.write_calls, [])
        self.assertFalse((self.bundle / 'write-result.json').exists())
        self.assertFalse((self.bundle / 'rollback').exists())


if __name__ == '__main__':
    print('HOST MOCK ONLY: no serial device, real image validation, SSH, or firmware writes.')
    unittest.main(verbosity=2)
