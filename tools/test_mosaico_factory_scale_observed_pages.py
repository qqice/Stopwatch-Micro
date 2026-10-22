"""Offline parser tests; generated pages are not device/signature proof."""
import base64
import copy
import hashlib
import json
import unittest
import zlib
from mosaico_factory_scale_observed_pages import decode, ENCODING
from test_mosaico_factory_scale_model import PRIVATE, EXPECTED


class ObservedPagesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PRIVATE.exists():
            raise unittest.SkipTest('private complete131 reference unavailable')
        data = PRIVATE.read_bytes()
        assert hashlib.sha256(data).hexdigest() == EXPECTED
        cls.expected = [dict(index=i, address=r['address'].lower(), width=r['width'],
                             raw=bytes.fromhex(r['rawBE']).ljust(4, b'\0').hex())
                        for i, r in enumerate(json.loads(data)['fields'])]
        cls.whole = b''.join(int(r['address'], 16).to_bytes(2, 'little') + bytes([r['width']]) +
                             bytes.fromhex(r['raw']) for r in cls.expected)
        assert len(cls.whole) == 917 and zlib.crc32(cls.whole) == 0x70a767be
        cls.pages = []
        for i in range(5):
            first, count = i * 32, min(32, 131 - i * 32)
            raw = cls.whole[first * 7:(first + count) * 7]
            cls.pages.append(dict(version='1', cached='1', cached_only='1', no_i2c='1', no_nvs='1',
                                  total='131', authenticated='1', encoding=ENCODING, page=str(i),
                                  first=str(first), count=str(count), generation='4', epoch='4',
                                  read_ms='1000', cache_phase='3', stage='restore_verified',
                                  observed_crc='70a767be', page_crc=f'{zlib.crc32(raw):08x}',
                                  data=base64.b64encode(raw).decode()))

    def test_full_actual_image_and_inputs_unchanged(self):
        before = copy.deepcopy(self.pages)
        result = decode(self.pages, self.expected)
        self.assertEqual(result['fields'], self.expected)
        self.assertEqual(result['observed_crc'], '70a767be')
        self.assertEqual(self.pages, before)

    def test_page_context_and_schema_rejections(self):
        for key, value in [('version', '2'), ('authenticated', '0'), ('no_i2c', '0'),
                           ('no_nvs', '0'), ('page', '0'), ('first', '0'), ('count', '33'),
                           ('generation', '5'), ('epoch', str(2**64)), ('read_ms', '0'),
                           ('cache_phase', '4'), ('stage', 'mixed'), ('encoding', 'unknown')]:
            with self.subTest(key=key):
                pages = copy.deepcopy(self.pages)
                pages[1][key] = value
                with self.assertRaises(ValueError):
                    decode(pages, self.expected)
        for pages in [self.pages[:-1], self.pages + self.pages[:1], list(reversed(self.pages))]:
            with self.assertRaises(ValueError):
                decode(pages, self.expected)
        pages = copy.deepcopy(self.pages)
        pages[0]['extra'] = 'unreviewed'
        with self.assertRaises(ValueError):
            decode(pages, self.expected)

    def test_corruption_actual_crc_value_and_padding_rejections(self):
        for kind in ('base64', 'short', 'crc', 'actual_crc', 'value', 'padding'):
            with self.subTest(kind=kind):
                pages = copy.deepcopy(self.pages)
                raw = bytearray(base64.b64decode(pages[0]['data']))
                if kind == 'base64':
                    pages[0]['data'] = '!'
                elif kind == 'short':
                    pages[0]['data'] = base64.b64encode(raw[:-1]).decode()
                elif kind == 'crc':
                    pages[0]['page_crc'] = '00000000'
                elif kind == 'actual_crc':
                    for page in pages:
                        page['observed_crc'] = '00000000'
                else:
                    raw[3 if kind == 'value' else 6] ^= 1
                    pages[0]['data'] = base64.b64encode(raw).decode()
                    pages[0]['page_crc'] = f'{zlib.crc32(raw):08x}'
                with self.assertRaises(ValueError):
                    decode(pages, self.expected)

    def test_nontext_and_oversized_payloads_rejected(self):
        for key, value in [('data', 'A' * 304), ('data', None), ('stage', 4),
                           ('page_crc', False), ('generation', '+4')]:
            with self.subTest(key=key):
                pages = copy.deepcopy(self.pages)
                pages[0][key] = value
                with self.assertRaises(ValueError):
                    decode(pages, self.expected)


if __name__ == '__main__':
    unittest.main()
