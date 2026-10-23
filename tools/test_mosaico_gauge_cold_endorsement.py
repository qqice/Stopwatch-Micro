"""Offline cold-pair endorsements: ephemeral P256 only; no files/actual keys/devices."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from ota_release import canonical_message

import mosaico_gauge_cold_capability as cap
import mosaico_gauge_cold_fragment as fragment
import mosaico_gauge_contrast_capability as old_gct
import test_mosaico_gauge_cold_capability as fixtures

ROOT = Path(__file__).resolve().parents[1]


class ColdEndorsementTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ColdCapabilityTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.private = self.fixture.key
        self.public = self.fixture.pub
        self.manifests = [copy.deepcopy(pair['image']) for pair in self.fixture.pairs]
        self.images = list(self.fixture.images)

    def endorse(self, manifests=None, images=None, private=None, public=None, audited=True):
        return cap.endorse_pair(self.manifests if manifests is None else manifests,
            self.images if images is None else images, self.private if private is None else private,
            self.public if public is None else public, audited=audited)

    def normal(self, version, image=None, schema=2, layout='mosaico-dual-v2'):
        if image is None:
            image = bytearray(512)
            image[48:80] = version.encode().ljust(32, b'\0')
            image = bytes(image)
        manifest = dict(schema=schema, board='esp-mosaico', chip='esp32s31', project='Stopwatch-Mosaico',
            layout=layout, size=len(image), sha256=hashlib.sha256(image).hexdigest())
        if schema == 2:
            manifest['version'] = version
        message = canonical_message(manifest['size'], manifest['sha256'], version if schema == 2 else None, layout=layout)
        manifest['signature'] = base64.b64encode(self.private.sign(message, ec.ECDSA(hashes.SHA256()))).decode('ascii')
        return manifest, image

    def test_audit_must_be_literal_true_before_key_access(self):
        for audited in (False, None, 0, 1, 'True', [], object()):
            with self.subTest(audited=type(audited).__name__), patch.object(cap, 'p256_public') as public_check:
                with self.assertRaisesRegex(ValueError, 'audit'):
                    cap.endorse_pair(self.manifests, self.images, object(), object(), audited=audited)
                public_check.assert_not_called()
        with self.assertRaises(ValueError):
            cap.endorse_pair(self.manifests, self.images, self.private, self.public)

    def test_matching_P256_signer_and_verified_public_required(self):
        wrong = ec.generate_private_key(ec.SECP256R1())
        for private, public in ((wrong, self.public), (self.private, wrong.public_key()),
            (ec.generate_private_key(ec.SECP384R1()), self.public),
            (self.private, ec.generate_private_key(ec.SECP384R1()).public_key()), (object(), self.public)):
            with self.subTest(private=type(private).__name__), self.assertRaises(ValueError):
                self.endorse(private=private, public=public)

    def test_both_legal_orders_preserved_and_old_GCT_roster_untouched(self):
        old_digest, old_versions = old_gct.DIGEST, set(old_gct.VERSIONS)
        for reverse in (False, True):
            manifests = list(reversed(self.manifests)) if reverse else self.manifests
            images = list(reversed(self.images)) if reverse else self.images
            original = copy.deepcopy(manifests)
            raw = self.endorse(manifests, images)
            pairs = cap.validate_pair(raw, self.public, images)
            self.assertEqual([p['image'] for p in pairs], manifests)
            self.assertEqual(manifests, original)
            self.assertEqual(raw, json.dumps(pairs, separators=(',', ':'), allow_nan=False).encode('ascii'))
            self.assertLessEqual(len(raw), cap.PAIR_RAW_MAX)
            for pair in pairs:
                manifest = pair['image']
                message = ('MOSAICO-GAUGE-CONTRAST-CAP-v1\n' + old_digest + '\n' + manifest['version'] + '\n' +
                    str(manifest['size']) + '\n' + manifest['sha256'] + '\n').encode('ascii')
                self.public.verify(base64.b64decode(pair['signature'], validate=True), message, ec.ECDSA(hashes.SHA256()))
        self.assertEqual(old_gct.DIGEST, old_digest)
        self.assertEqual(old_gct.VERSIONS, old_versions)
        self.assertEqual(old_versions, {'0.13.95-r15-mosaico-ota', '0.13.95-r16-mosaico-ota'})

    def test_old_mixed_draft_duplicate_versions_rejected(self):
        for versions in (('0.13.95-r15-mosaico-ota', '0.13.95-r16-mosaico-ota'),
            ('0.13.95-r17-mosaico-ota', '0.13.95-r18-mosaico-ota'),
            ('0.13.95-r19-mosaico-ota', '0.13.95-r18-mosaico-ota'),
            ('0.13.95-r17-mosaico-ota', '0.13.95-r20-mosaico-ota'),
            ('0.13.95-r19-mosaico-ota', '0.13.95-r16-mosaico-ota'),
            ('0.13.95-r19-cold-draft', '0.13.95-r20-mosaico-ota'),
            ('0.13.95-r19-mosaico-ota', '0.13.95-r19-mosaico-ota')):
            records = [self.normal(version) for version in versions]
            with self.subTest(versions=versions), self.assertRaises(ValueError):
                self.endorse([m for m, _ in records], [b for _, b in records])

    def test_ordered_full_BIN_changes_size_and_descriptor_mismatch_rejected(self):
        changed = bytearray(self.images[0])
        changed[-1] ^= 1
        for images in ([bytes(changed), self.images[1]], [self.images[0][:-1], self.images[1]], list(reversed(self.images))):
            with self.subTest(size=len(images[0])), self.assertRaises(ValueError):
                self.endorse(images=images)
        # Fully normal-signed hash/size still cannot substitute a wrong embedded
        # descriptor version. Both signatures are valid in this negative test.
        m, image = self.normal('0.13.95-r19-mosaico-ota', self.images[1])
        with self.assertRaisesRegex(ValueError, 'distinct'):
            self.endorse([m, self.manifests[1]], [image, self.images[1]])
        other = bytearray(self.images[1])
        other[-1] ^= 1
        m, image = self.normal('0.13.95-r19-mosaico-ota', bytes(other))
        with self.assertRaisesRegex(ValueError, 'descriptor'):
            self.endorse([m, self.manifests[1]], [image, self.images[1]])

    def test_normal_signature_schema_layout_and_manifest_fields_rejected(self):
        wrong = ec.generate_private_key(ec.SECP256R1())
        m = copy.deepcopy(self.manifests)
        m[0]['signature'] = base64.b64encode(wrong.sign(canonical_message(m[0]['size'], m[0]['sha256'],
            m[0]['version'], layout=m[0]['layout']), ec.ECDSA(hashes.SHA256()))).decode('ascii')
        with self.assertRaises(InvalidSignature):
            self.endorse(m)
        for schema, layout in ((1, 'mosaico-dual-v2'), (2, 'mosaico-dual-v1')):
            first, image = self.normal('0.13.95-r19-mosaico-ota', schema=schema, layout=layout)
            with self.subTest(schema=schema, layout=layout), self.assertRaises(ValueError):
                self.endorse([first, self.manifests[1]], [image, self.images[1]])
        m = copy.deepcopy(self.manifests)
        m[0]['unexpected'] = 'no'
        with self.assertRaises(ValueError):
            self.endorse(m)
        for manifests, images in (([], self.images), (self.manifests[:1], self.images),
            (self.manifests, self.images[:1]), (self.manifests, [bytearray(self.images[0]), self.images[1]])):
            with self.assertRaises(ValueError):
                self.endorse(manifests, images)

    def test_raw_pair_supports_session_sign_and_exact_fragment_rebuild_without_hardware_claim(self):
        # Production-relevant reversed slot order R20/ota0, R19/ota1 is legal.
        manifests, images = list(reversed(self.manifests)), list(reversed(self.images))
        raw = self.endorse(manifests, images)
        binding = self.fixture.binding | {'slot_hashes': [m['sha256'] for m in manifests]}
        payload = cap.sign_payload(binding, raw, images, self.private, self.public, approved=True)
        encoded, report = fragment.build_fragment(payload, self.public, images, raw, binding)
        wire = base64.b64decode(encoded, validate=True)
        self.assertEqual(wire[:4], b'GCS1')
        signature_length = struct.unpack_from('<H', wire, 192)[0]
        self.assertEqual(len(wire), 194 + signature_length)
        self.assertLessEqual(len(encoded), 356)
        rebuilt = cap.pack_payload(wire[4:192], raw, wire[194:])
        self.assertEqual(rebuilt, payload)
        checked = cap.verify_payload(rebuilt, self.public, images, binding)
        for key in ('actual_SDK_verified', 'actual_flash_verified', 'current_source_hardware_match', 'nonce_uniqueness_verified'):
            self.assertIs(checked[key], False)
        self.assertEqual(report['ports_opened'], 0)
        self.assertIs(report['private_key_read'], False)


if __name__ == '__main__':
    unittest.main()
