"""Offline GCC1 tests use ephemeral P256 keys only; no actual key/device access."""
import base64
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from ota_release import canonical_message
import mosaico_gauge_cold_capability as c

ROOT = Path(__file__).resolve().parents[1]


class ColdCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / 'artifacts/gauge-cold-capability-tests'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=self.directory)
        self.out = Path(self.tmp.name)
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.pub = self.key.public_key()
        self.images, self.pairs = [], []
        for version in sorted(c.VERSIONS):
            image, pair = self.pair(version)
            self.images.append(image)
            self.pairs.append(pair)
        self.raw = json.dumps(self.pairs, separators=(',', ':')).encode('ascii')
        self.binding = dict(mac='240102030405', device='0220', firmware='0003', build=4, recovery=1,
            source_crc='70a767be', target_crc='91336fc0', original_gct_generation=19, original_gct_epoch=19,
            nonce='01' * 16, slot_hashes=[p['image']['sha256'] for p in self.pairs], contract_digest='00' * 32,
            source131_receipt_digest='03' * 32, capture_budget_ms=30000)

    def tearDown(self):
        self.tmp.cleanup()

    def pair(self, version):
        image = bytearray(512)
        image[48:80] = version.encode().ljust(32, b'\0')
        image = bytes(image)
        m = dict(schema=2, board='esp-mosaico', chip='esp32s31', project='Stopwatch-Mosaico',
                 layout='mosaico-dual-v2', version=version, size=len(image), sha256=hashlib.sha256(image).hexdigest())
        m['signature'] = base64.b64encode(self.key.sign(canonical_message(m['size'], m['sha256'], version,
            layout=m['layout']), ec.ECDSA(hashes.SHA256()))).decode()
        message = ('MOSAICO-GAUGE-CONTRAST-CAP-v1\n' + c.GCT_DIGEST + '\n' + version + '\n' + str(m['size']) + '\n' + m['sha256'] + '\n').encode()
        signature = self.key.sign(message, ec.ECDSA(hashes.SHA256()))
        return image, dict(image=m, signature=base64.b64encode(signature).decode())

    def signed(self, raw=None):
        return c.sign_payload(self.binding, self.raw if raw is None else raw, self.images, self.key, self.pub, approved=True)

    def test_contract_message_exact_layout_and_ephemeral_roundtrip(self):
        self.assertEqual(len(c.CONTRACT.encode('ascii')), 446)
        self.assertEqual(c.CONTRACT_SHA.hex(), 'ec39cc75a18030904771a3f4fdc46c50a9564e926f450cdbc667115925dc0ba2')
        self.assertFalse(c.CONTRACT.endswith('\n'))
        wire = c.encode_binding(self.binding)
        self.assertEqual(len(wire), 188)
        self.assertEqual(wire[:8], b'GCB1' + struct.pack('<HH', 1, 188))
        message = c.SESSION_DOMAIN + c.CONTRACT_SHA + hashlib.sha256(wire).digest() + hashlib.sha256(self.raw).digest()
        self.assertEqual(c.session_message(wire, self.raw), message)
        payload = self.signed()
        self.assertEqual(payload[:4], b'GCP1')
        self.assertEqual(c.HEADER.size, 56)
        self.assertLessEqual(len(payload), 1536)
        report = c.verify_payload(payload, self.pub, self.images, self.binding)
        self.assertFalse(report['actual_SDK_verified'])
        self.assertFalse(report['actual_flash_verified'])
        self.assertFalse(report['current_source_hardware_match'])

    def test_binding_all_fields_required_canonical_and_slot_mode_only(self):
        for name in c.BINDING_KEYS:
            b = copy.deepcopy(self.binding)
            del b[name]
            with self.assertRaises(ValueError):
                c.encode_binding(b)
        for changes in ({'recovery': 2}, {'contract_digest': '01' * 32}, {'nonce': '00' * 16}, {'mac': '250102030405'},
                        {'source_crc': '70A767BE'}, {'original_gct_epoch': True}, {'capture_budget_ms': 60001},
                        {'slot_hashes': [self.binding['slot_hashes'][0]] * 2}, {'extra': 1}):
            with self.assertRaises(ValueError):
                c.encode_binding(self.binding | changes)
        self.assertEqual(c.decode_binding(c.encode_binding(self.binding)), self.binding)

    def test_binding_full_apply_restore_counter_headroom_and_raw_receipt_domain(self):
        maximums = {'original_gct_generation': 0xffffffff, 'original_gct_epoch': 0xffffffffffffffff}
        boundary = self.binding | {name: value - 4 for name, value in maximums.items()}
        self.assertEqual(c.decode_binding(c.encode_binding(boundary)), boundary)
        for name, maximum in maximums.items():
            for value in (0, maximum - 3, maximum - 2, maximum - 1, maximum):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    c.validate_binding(self.binding | {name: value})
                with self.subTest(wire_name=name, value=value), self.assertRaises(ValueError):
                    c.encode_binding(self.binding | {name: value})
        # Raw source-receipt hashing is NOT admission and retains its previous
        # full nonzero uint domains; it cannot authorize a bound session.
        digest = c.source_receipt(self.raw_source(), bytes.fromhex(self.binding['mac']),
            0x220, 3, 4, 0xffffffff, 0xffffffffffffffff)
        self.assertEqual(len(digest), 32)

    def test_raw_pair_preserved_byte_for_byte_and_whitespace_is_signed(self):
        raw = b'\n ' + self.raw + b' \n'
        payload = self.signed(raw)
        _, wire, found, signature = c.decode_payload(payload)
        self.assertEqual(found, raw)
        self.assertEqual(c.decode_pair_file(base64.b64encode(raw).decode() + '\n'), raw)
        c.verify_payload(payload, self.pub, self.images)
        modified = c.pack_payload(wire, raw + b' ', signature)
        with self.assertRaises(InvalidSignature):
            c.verify_payload(modified, self.pub, self.images)

    def test_header_size_digest_reserved_trailing_padding_and_signature_tamper(self):
        payload = self.signed()
        for offset in (0, 4, 6, 8, 10, 12, 14, 16, 48):
            bad = bytearray(payload)
            bad[offset] ^= 1
            with self.assertRaises((ValueError, InvalidSignature)):
                c.verify_payload(bytes(bad), self.pub, self.images)
        for bad in (payload + b'\0', payload[:-1], payload[:55]):
            with self.assertRaises(ValueError):
                c.decode_payload(bad)
        bad = bytearray(payload)
        bad[-1] ^= 1
        with self.assertRaises((ValueError, InvalidSignature)):
            c.verify_payload(bytes(bad), self.pub, self.images)

    def test_binding_tamper_wrong_key_wrong_BIN_order_or_descriptor_rejected(self):
        payload = self.signed()
        bad = bytearray(payload)
        bad[c.HEADER_SIZE + 8 + 32] ^= 2  # valid nonzero nonce, invalid session signature
        with self.assertRaises(InvalidSignature):
            c.verify_payload(bytes(bad), self.pub, self.images)
        with self.assertRaises(InvalidSignature):
            c.verify_payload(payload, ec.generate_private_key(ec.SECP256R1()).public_key(), self.images)
        with self.assertRaises(ValueError):
            c.verify_payload(payload, self.pub, list(reversed(self.images)))
        with self.assertRaises(ValueError):
            c.sign_payload(self.binding, self.raw, self.images, self.key, self.pub)
        wrong = ec.generate_private_key(ec.SECP256R1())
        with self.assertRaises(ValueError):
            c.sign_payload(self.binding, self.raw, self.images, wrong, self.pub, approved=True)

    def test_pair_normal_signature_GCT_signature_duplicate_and_exact_production_roster(self):
        duplicate = self.raw.replace(b'"size":512', b'"size":512,"size":512', 1)
        with self.assertRaises(ValueError):
            c.validate_pair(duplicate, self.pub, self.images)
        for version in ('0.13.95-r19-cold-draft', '0.13.95-r15-mosaico-ota', '0.13.95-r17-mosaico-ota', '0.13.95-r18-mosaico-ota', '0.13.95-r21-mosaico-ota'):
            image, pair = self.pair(version)
            raw = json.dumps([pair, self.pairs[1]], separators=(',', ':')).encode()
            with self.assertRaises(ValueError):
                c.validate_pair(raw, self.pub, [image, self.images[1]])
        for versions in (('0.13.95-r17-mosaico-ota', '0.13.95-r18-mosaico-ota'),
                         ('0.13.95-r17-mosaico-ota', '0.13.95-r20-mosaico-ota')):
            records = [self.pair(version) for version in versions]
            raw = json.dumps([p for _, p in records], separators=(',', ':')).encode()
            with self.subTest(versions=versions), self.assertRaises(ValueError):
                c.validate_pair(raw, self.pub, [image for image, _ in records])
        for field in ('signature', 'normal'):
            pairs = copy.deepcopy(self.pairs)
            if field == 'normal':
                pairs[0]['image']['sha256'] = '11' * 32
            else:
                pairs[0]['signature'] = self.pairs[1]['signature']
            with self.assertRaises((ValueError, InvalidSignature)):
                c.validate_pair(json.dumps(pairs, separators=(',', ':')).encode(), self.pub, self.images)

    def test_signed_normal_hash_cannot_hide_wrong_local_BIN_descriptor(self):
        pairs = copy.deepcopy(self.pairs)
        images = list(self.images)
        changed = bytearray(images[0])
        changed[48:80] = b'0.13.95-r19-cold-draft'.ljust(32, b'\0')
        images[0] = bytes(changed)
        m = pairs[0]['image']
        m['sha256'] = hashlib.sha256(images[0]).hexdigest()
        m['signature'] = base64.b64encode(self.key.sign(canonical_message(m['size'], m['sha256'], m['version'],
            layout=m['layout']), ec.ECDSA(hashes.SHA256()))).decode()
        endorsement = ('MOSAICO-GAUGE-CONTRAST-CAP-v1\n' + c.GCT_DIGEST + '\n' + m['version'] + '\n' +
                       str(m['size']) + '\n' + m['sha256'] + '\n').encode()
        pairs[0]['signature'] = base64.b64encode(self.key.sign(endorsement, ec.ECDSA(hashes.SHA256()))).decode()
        with self.assertRaisesRegex(ValueError, 'descriptor version'):
            c.validate_pair(json.dumps(pairs, separators=(',', ':')).encode(), self.pub, images)

    def raw_source(self):
        text = (ROOT / 'main/hal/mosaico/mosaico_gauge_contrast_model.h').read_text()
        fixture = text.split('inline constexpr Raw sourceFixture={', 1)[1].split('};', 1)[0]
        return bytes(int(x, 16) for x in re.findall(r'0x([0-9a-f]{2})', fixture))

    def test_source_receipt_exact_domain_full_raw_and_metadata_not_JSON_file_SHA(self):
        raw = self.raw_source()
        mac = bytes.fromhex(self.binding['mac'])
        digest = c.source_receipt(raw, mac, 0x220, 3, 4, 19, 19)
        self.assertEqual(digest, hashlib.sha256(c.SOURCE_DOMAIN + mac + struct.pack('<HHBIQ', 0x220, 3, 4, 19, 19) + raw).digest())
        self.assertNotEqual(digest, c.source_receipt(raw, mac, 0x220, 3, 4, 20, 19))
        changed = bytearray(raw)
        changed[-1] ^= 1
        for value in (raw[:-1], raw + b'\0', bytes(changed)):
            with self.assertRaises(ValueError):
                c.source_receipt(value, mac, 0x220, 3, 4, 19, 19)

    def cli_files(self):
        files = {name: self.out / name for name in ('binding.json', 'pair.txt', 'pub.pem', 'slot0.bin', 'slot1.bin')}
        files['binding.json'].write_text(json.dumps(self.binding))
        files['pair.txt'].write_text(base64.b64encode(self.raw).decode() + '\n')
        files['pub.pem'].write_bytes(self.pub.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
        files['slot0.bin'].write_bytes(self.images[0])
        files['slot1.bin'].write_bytes(self.images[1])
        args = ['--binding', str(files['binding.json']), '--pair-proof', str(files['pair.txt']), '--public-key-header', str(files['pub.pem']),
                '--slot0-image', str(files['slot0.bin']), '--slot1-image', str(files['slot1.bin']),
                '--trusted-source-receipt-digest', self.binding['source131_receipt_digest']]
        return args

    def test_default_dry_reads_no_private_key_even_when_path_given_and_no_output(self):
        args = self.cli_files()
        output = self.out / 'must-not-exist.bin'
        with patch.object(c.serialization, 'load_pem_private_key', side_effect=AssertionError('private key read')), \
             patch.object(c, 'atomic_output', side_effect=AssertionError('output written')):
            capture = io.StringIO()
            with contextlib.redirect_stdout(capture):
                self.assertEqual(c.main(args + ['--private-key', str(self.out / 'missing-key'), '--output', str(output)]), 0)
            summary = json.loads(capture.getvalue())
            self.assertTrue(summary['dry_run'])
            self.assertFalse(summary['private_key_read'])
            self.assertFalse(summary['source_raw_canonical_crc_checked'])
            self.assertTrue(summary['source_receipt_trusted_input'])
            self.assertFalse(output.exists())

    def test_missing_inputs_or_sign_switch_without_paths_is_fail_closed(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                c.main([])
        with self.assertRaisesRegex(ValueError, 'explicit key'):
            c.main(self.cli_files() + ['--sign'])

    def test_raw_source_CLI_labels_CRC_only_not_current_device_match(self):
        args = self.cli_files()
        raw = self.raw_source()
        receipt = c.source_receipt(raw, bytes.fromhex(self.binding['mac']), 0x220, 3, 4, 19, 19).hex()
        (self.out / 'binding.json').write_text(json.dumps(self.binding | {'source131_receipt_digest': receipt}))
        raw_path = self.out / 'canonical-source222.bin'
        raw_path.write_bytes(raw)
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture):
            c.main(args[:-2] + ['--source-raw222', str(raw_path)])
        report = json.loads(capture.getvalue())
        self.assertTrue(report['source_raw_canonical_crc_checked'])
        self.assertFalse(report['source_receipt_trusted_input'])
        self.assertFalse(report['current_source_hardware_match'])

    def test_explicit_CLI_ephemeral_sign_and_no_overwrite_before_private_access(self):
        args = self.cli_files()
        keyfile = self.out / 'EPHEMERAL-TEST-ONLY.pem'
        keyfile.write_bytes(self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        output = self.out / 'new-session.bin'
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(c.main(args + ['--sign', '--private-key', str(keyfile), '--output', str(output)]), 0)
        before = output.read_bytes()
        c.verify_payload(before, self.pub, self.images, self.binding)
        with patch.object(c.serialization, 'load_pem_private_key', side_effect=AssertionError('must reject existing before key read')):
            with self.assertRaises(ValueError):
                c.main(args + ['--sign', '--private-key', str(keyfile), '--output', str(output)])
        self.assertEqual(output.read_bytes(), before)

    def test_atomic_publish_race_and_fsync_failure_never_overwrite(self):
        output = self.out / 'raced.bin'
        real_link = os.link
        def race(source, destination):
            Path(destination).write_bytes(b'existing-racer')
            return real_link(source, destination)
        with patch.object(c.os, 'link', side_effect=race):
            with self.assertRaises(FileExistsError):
                c.atomic_output(output, b'new-data')
        self.assertEqual(output.read_bytes(), b'existing-racer')
        with patch.object(c.os, 'fsync', side_effect=OSError('disk error')), patch.object(c.os, 'link') as link:
            with self.assertRaises(OSError):
                c.atomic_output(self.out / 'never-published.bin', b'new-data')
            link.assert_not_called()
        self.assertFalse(list(self.out.glob('.*.tmp')))

    def test_python_binding_and_layout_match_production_constexpr_capsule(self):
        from test_mosaico_gauge_cold_trial import COMPILER
        def array(raw):
            return '{' + ','.join(str(x) for x in raw) + '}'
        source = self.out / 'binding.cpp'
        source.write_text('''#include "mosaico_gauge_cold_capsule.h"
#include "mosaico_gauge_recovery_model.h"
namespace m=mosaico_gauge_cold_trial;namespace k=mosaico_gauge_cold_capsule;
constexpr bool test(){m::Binding b{};b.mac=''' + array(bytes.fromhex(self.binding['mac'])) + ';b.originalGctGeneration=19;b.originalGctEpoch=19;b.nonce=' + array(bytes.fromhex(self.binding['nonce'])) +
            ';b.slotHashes[0]=' + array(bytes.fromhex(self.binding['slot_hashes'][0])) + ';b.slotHashes[1]=' + array(bytes.fromhex(self.binding['slot_hashes'][1])) +
            ';b.source131ReceiptDigest=' + array(bytes.fromhex(self.binding['source131_receipt_digest'])) + ';k::BindingBytes out{};constexpr k::BindingBytes expected=' + array(c.encode_binding(self.binding)) +
            ';if(!k::encodeBinding(b,out)||out!=expected)return false;constexpr mosaico_gauge_recovery::LayoutField layout[]={' +
            ','.join('{' + str(a) + ',' + str(w) + '}' for a, w in c.PUBLIC131_LAYOUT) + '};for(unsigned i=0;i<131;++i)if(layout[i].address!=mosaico_gauge_recovery::layout[i].address||layout[i].width!=mosaico_gauge_recovery::layout[i].width)return false;return true;}static_assert(test());')
        result = subprocess.run([str(COMPILER), '-std=c++20', '-Wall', '-Wextra', '-Werror', '-fsyntax-only',
            '-I' + str(ROOT / 'main/hal/mosaico'), str(source)], capture_output=True, text=True)
        log = self.directory / 'binding-layout-compiler.log'
        log.write_text(result.stdout + result.stderr)
        self.assertEqual(result.returncode, 0, str(log))


if __name__ == '__main__':
    unittest.main()
