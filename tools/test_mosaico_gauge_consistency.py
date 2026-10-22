import copy
import json
from pathlib import Path
import tempfile
import unittest
import zlib

import mosaico_gauge_consistency as c
from test_mosaico_gauge_scaling_plan import synthetic_full_inputs


def fixture():
    m, r, original = synthetic_full_inputs()
    fields, payload = [], bytearray()
    for index, f in enumerate(original['fields']):
        raw = f['rawBE'].ljust(8, '0')
        kind = 'F4_opaque' if f['type'] == 'F' else ('H' if f['type'] == 'B' else f['type']) + str(f['width'])
        d = {'index': str(index), 'address': f['address'], 'width': str(f['width']),
             'type': kind, 'raw': raw, 'generation': '1', 'complete': '1',
             'durable': '1', 'closed_verified': '1', 'fresh_this_boot': '1',
             'source_stage': 'complete'}
        fields.append({'index': index, 'address': f['address'], 'width': f['width'],
                       'raw': raw, 'rawBE': f['rawBE'], 'metadata': d})
        payload.extend(int(f['address'], 16).to_bytes(2, 'little') + bytes([f['width']]) + bytes.fromhex(raw))
    for f in fields:
        f['metadata']['source_crc'] = f'{zlib.crc32(payload):08x}'
    return m, r, original, {'complete': True, 'fields': fields}


class ConsistencyTests(unittest.TestCase):
    def test_same131_no_hardware_or_fresh_claim(self):
        inputs = fixture()
        before = copy.deepcopy(inputs)
        report = c.audit(*inputs)
        self.assertEqual(inputs, before)
        self.assertEqual(report['changed_addresses'], [])
        self.assertEqual(report['range_failures'], [])
        self.assertFalse(report['ready_for_hardware'])
        self.assertFalse(report['findings'][1]['fresh_current_boot'])

    def test_signed_and_f4_exact_decode_not_display_value(self):
        m, r, original, factory = fixture()
        report = c.audit(m, r, original, factory)
        rows = {f['address']: f for f in report['fields']}
        self.assertEqual(rows['9184']['native65_exact'], '1/4')
        self.assertEqual(rows['9188']['native65_exact'], '262144')

    def test_reject_31_malformed_inputs(self):
        mutations = [
            lambda m, r, o, f: m['rows'].pop(),
            lambda m, r, o, f: m['rows'].__setitem__(1, copy.deepcopy(m['rows'][0])),
            lambda m, r, o, f: r.pop(),
            lambda m, r, o, f: r.__setitem__(1, copy.deepcopy(r[0])),
            lambda m, r, o, f: r[0].update(Min_status=1),
            lambda m, r, o, f: r[0].update(Max_status=True),
            lambda m, r, o, f: r[0].update(Min='99999', Max='0'),
            lambda m, r, o, f: r[0].update(parameter='wrong'),
            lambda m, r, o, f: r[0].update(length=4),
            lambda m, r, o, f: o.update(security_after='FULL_ACCESS'),
            lambda m, r, o, f: o.update(parameter_writes=True),
            lambda m, r, o, f: o['fields'].pop(),
            lambda m, r, o, f: f.update(complete=False),
            lambda m, r, o, f: f['fields'].pop(),
            lambda m, r, o, f: f['fields'].__setitem__(1, copy.deepcopy(f['fields'][0])),
            lambda m, r, o, f: f['fields'][0].update(index=True),
            lambda m, r, o, f: f['fields'][0].update(width=True),
            lambda m, r, o, f: f['fields'][0].update(address='bad'),
            lambda m, r, o, f: f['fields'][0].update(rawBE='zz'),
            lambda m, r, o, f: f['fields'][0].update(raw='00000000'),
            lambda m, r, o, f: f['fields'][0]['metadata'].update(raw='00000000'),
            lambda m, r, o, f: f['fields'][0]['metadata'].update(index='1'),
            lambda m, r, o, f: f['fields'][0]['metadata'].update(type='U2'),
            lambda m, r, o, f: f['fields'][0]['metadata'].update(width='1'),
            lambda m, r, o, f: f['fields'][0]['metadata'].update(complete='0'),
            lambda m, r, o, f: f['fields'][0]['metadata'].update(durable='0'),
            lambda m, r, o, f: f['fields'][0]['metadata'].update(closed_verified='0'),
            lambda m, r, o, f: f['fields'][0]['metadata'].update(fresh_this_boot='0'),
            lambda m, r, o, f: f['fields'][0]['metadata'].update(generation='2'),
            lambda m, r, o, f: f['fields'][0]['metadata'].update(source_crc='00000000'),
            lambda m, r, o, f: f['fields'][0]['metadata'].update(source_stage='failed_or_partial'),
        ]
        for index, mutation in enumerate(mutations):
            inputs = fixture()
            mutation(*inputs)
            with self.subTest(index=index), self.assertRaises(ValueError):
                c.audit(*inputs)

    def test_all_fields_crc_corruption_rejected(self):
        inputs = fixture()
        for f in inputs[3]['fields']:
            f['metadata']['source_crc'] = '00000000'
        with self.assertRaisesRegex(ValueError, 'payload CRC'):
            c.audit(*inputs)

    def test_existing_report_never_overwritten(self):
        with tempfile.TemporaryDirectory(prefix='.gauge-consistency-', dir=Path(__file__).resolve().parents[1]) as td:
            path = Path(td) / 'report.json'
            c.worksheet.exclusive_json(path, {'original': True})
            with self.assertRaises(FileExistsError):
                c.worksheet.exclusive_json(path, {'original': False})
            self.assertEqual(json.loads(path.read_text()), {'original': True})


if __name__ == '__main__':
    unittest.main()
