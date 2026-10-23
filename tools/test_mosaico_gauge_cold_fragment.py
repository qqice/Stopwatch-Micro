"""Ephemeral signatures only; no production key, ports, or parameter writes."""
import base64
import unittest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ec
import mosaico_gauge_cold_capability as cap
import mosaico_gauge_cold_fragment as fragment
import test_mosaico_gauge_cold_capability as fixture


class ColdFragmentTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.ColdCapabilityTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.payload = self.fixture.signed()

    def build(self, **kw):
        return fragment.build_fragment(kw.get('payload', self.payload), kw.get('key', self.fixture.pub),
            kw.get('images', self.fixture.images), kw.get('pair', self.fixture.raw), kw.get('binding', self.fixture.binding))

    def test_fragment_rebuild_is_exact_full_signed_package(self):
        text, report = self.build()
        wire = base64.b64decode(text, validate=True)
        self.assertEqual(wire[:4], b'GCS1')
        self.assertLessEqual(len(text), 356)
        size = int.from_bytes(wire[192:194], 'little')
        self.assertEqual(len(wire), 194 + size)
        self.assertEqual(cap.pack_payload(wire[4:192], self.fixture.raw, wire[194:]), self.payload)
        self.assertFalse(report['actual_SDK_verified'])
        self.assertFalse(report['actual_flash_verified'])
        self.assertFalse(report['current_source_hardware_match'])
        self.assertFalse(report['private_key_read'])
        self.assertEqual(report['ports_opened'], 0)

    def test_equivalent_JSON_or_different_receiver_pair_cannot_rebind_signature(self):
        for raw in (b' ' + self.fixture.raw, self.fixture.raw + b'\n', b'[]'):
            with self.assertRaisesRegex(ValueError, 'byte-identical'):
                self.build(pair=raw)

    def test_wrong_key_image_binding_and_payload_are_rejected(self):
        with self.assertRaises(InvalidSignature):
            self.build(key=ec.generate_private_key(ec.SECP256R1()).public_key())
        images = list(self.fixture.images)
        images[0] = images[0][:-1] + b'x'
        with self.assertRaises(ValueError):
            self.build(images=images)
        binding = self.fixture.binding | {'original_gct_epoch': 20}
        with self.assertRaisesRegex(ValueError, 'Binding mismatch'):
            self.build(binding=binding)
        with self.assertRaises(ValueError):
            self.build(payload=self.payload + b'\0')
        changed = bytearray(self.payload)
        changed[-1] ^= 1
        with self.assertRaises((InvalidSignature, ValueError)):
            self.build(payload=bytes(changed))


if __name__ == '__main__':
    unittest.main()
