"""Synthetic offline-only factory worksheet validation; no hardware access."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zlib

import mosaico_gauge_factory_scale_plan as p
from test_mosaico_gauge_consistency import fixture as base_fixture


def refresh(factory, result):
    payload = bytearray()
    for field in factory['fields']:
        field['raw'] = field['rawBE'].ljust(8, '0')
        field['metadata']['raw'] = field['raw']
        payload.extend(int(field['address'], 16).to_bytes(2, 'little') +
                       bytes([field['width']]) + bytes.fromhex(field['raw']))
    crc = f'{zlib.crc32(payload):08x}'
    for field in factory['fields']:
        field['metadata']['source_crc'] = crc
    factory['source_status']['source_crc'] = factory['source_status']['observed_crc'] = crc
    result['rows'] = [dict(command='boot', status='PASS', details='count=296 nvs_error=0') for _ in range(2)]
    result['rows'].append(dict(command='gauge-factory-audit revalidate CONFIRM', status='PASS',
                              details=' '.join(f'{k}={v}' for k, v in factory['source_status'].items())))
    for i, field in enumerate(factory['fields']):
        result['rows'].append(dict(command=f'gauge-factory-audit revalidate-field {i}', status='PASS',
                              details=' '.join(f'{k}={v}' for k, v in field['metadata'].items())))
    return crc


def fixture():
    metadata, ranges, native, factory = base_fixture()
    # Nonzero owned native values guarantee the actual changed mask is exactly17.
    for field in native['fields']:
        a = int(field['address'], 16)
        if a in p.OWNED and a not in (0x9184, 0x9188):
            value = 65 if a in (0x929d, 0x929f) else 867 if a == 0x92ab else 5
            field['rawBE'] = value.to_bytes(field['width'], 'big', signed=field['type'] == 'I').hex()
    native_fields = {f['address']: f for f in native['fields']}
    for field in factory['fields']:
        a = int(field['address'], 16)
        field['rawBE'] = native_fields[field['address']]['rawBE']
        if a in (0x929d, 0x929f):field['rawBE'] = '0bb8'
        if a == 0x929b:field['rawBE'] = '102a'
        if a == 0x9180:field['rawBE'] = 'fff0'
    factory['no_active_profile_or_unit_assertion'] = True
    factory['source_status'] = dict(reason='revalidated_existing_full131_durable_sealed_no_scale_waiver',
        receipt_schema='1', receipt_key='f3_rev_v1', state='2', receipt_generation='1',
        source_generation='1', fields='131', attempted_this_boot='1', complete='1', fresh_this_boot='1',
        closed_verified='1', immutable_backup_unchanged='1', no_scale_waiver='1', no_CFG='1',
        no_0091='1', parameter_writes='0')
    result = dict(accepted=True, complete131=True, attempted=True, settings_restored=True,
                  port_closed=True, ack_unknown=False, parameter_writes=False, error=None)
    crc = refresh(factory, result)
    return [metadata, ranges, native, factory, result], crc


class FactoryScalePlanTests(unittest.TestCase):
    def test_fixed_expected_mask_not_inferred_from_names_or_values(self):
        expected = {0x9184, 0x9188, 0x91de, 0x91fb, 0x9201, 0x920e, 0x9210,
                    0x9217, 0x9228, 0x922a, 0x922c, 0x923c, 0x9264, 0x926b,
                    0x929d, 0x929f, 0x92ab}
        self.assertEqual(p.OWNED, expected)
        self.assertNotIn(0x929b, p.OWNED)
        self.assertNotIn(0x9180, p.OWNED)

    def test_exact_mask_preservation_crc_and_nominal_boundaries(self):
        inputs, crc = fixture();before = copy.deepcopy(inputs)
        report = p.make_plan(*inputs, expected_source_crc=crc)
        self.assertEqual(before, inputs)
        self.assertFalse(report['ready_for_hardware'])
        self.assertFalse(report['whole_3000_to_130_is_x2'])
        self.assertFalse(report['live_current_boot_assertion'])
        self.assertEqual(report['source_unit_factor'], 'UNKNOWN')
        fields = {int(f['address'], 16): f for f in report['fields']}
        changed = {a for a, f in fields.items() if f['original_rawBE'] != f['target_rawBE']}
        self.assertEqual(changed, p.OWNED)
        self.assertEqual(len(fields) - len(changed), 114)
        self.assertEqual(fields[0x929b]['target_rawBE'], '102a')
        self.assertEqual(fields[0x9180]['target_rawBE'], 'fff0')
        self.assertEqual(fields[0x929f]['target_rawBE'], '0082')
        self.assertEqual(fields[0x92ab]['target_rawBE'], '01b2')
        for a in (0x9184, 0x9188):
            self.assertEqual(p.f4.decode(fields[a]['target_rawBE']), 2*p.f4.decode(fields[a]['native65_reference_rawBE']))
        cat = p.consistency.catalog(inputs[0], inputs[1])
        for name in ('original', 'target'):
            image = {int(row['address'], 16): row['rawBE'] for row in report[name]}
            raw = p.canonical(image, cat)
            self.assertEqual(len(raw), 917)
            self.assertEqual(report['canonical_'+name]['crc32'], f'{zlib.crc32(raw):08x}')
            self.assertEqual(report['canonical_'+name]['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(report['R0_quantization']['relative_current_compensation_error_exact'], '1/867')
        self.assertFalse(report['R0_quantization']['absolute_EDV_bound_proven'])

    def test_mutated_nonowned_observation_is_preserved_not_native_reference(self):
        inputs, _ = fixture()
        field = next(f for f in inputs[3]['fields'] if int(f['address'], 16) not in p.OWNED | {0x9180, 0x929b})
        field['rawBE'] = '01'.rjust(2*field['width'], '0')
        # A coherent synthetic pair may differ from the real pinned source; 
        # a native-only nonowned change is rejected by the closure tests below.
        next(f for f in inputs[2]['fields'] if f['address'] == field['address'])['rawBE'] = field['rawBE']
        crc = refresh(inputs[3], inputs[4]);report = p.make_plan(*inputs, expected_source_crc=crc)
        row = next(f for f in report['fields'] if f['address'] == field['address'])
        self.assertEqual(row['target_rawBE'], field['rawBE'])

    def test_fail_closed_negative_inputs(self):
        mutations = [
            lambda v: v[4].update(accepted=False),
            lambda v: v[4].update(ack_unknown=True),
            lambda v: v[4].update(parameter_writes=True),
            lambda v: v[4]['rows'][0].update(details='count=295 nvs_error=0'),
            lambda v: v[4]['rows'].pop(),
            lambda v: v[4]['rows'][3].update(status='FAIL'),
            lambda v: v[3]['source_status'].update(fresh_this_boot='0'),
            lambda v: v[3]['source_status'].update(closed_verified='0'),
            lambda v: v[3]['source_status'].update(state='3'),
            lambda v: v[3]['source_status'].update(source_generation='2'),
            lambda v: v[3]['source_status'].update(observed_crc='00000000'),
            lambda v: v[3]['fields'][0].update(rawBE='0000'),
            lambda v: v[3]['fields'].pop(),
            lambda v: v[2].update(security_after='FULL_ACCESS'),
            lambda v: v[1][0].update(Min_status=True),
            lambda v: v[1][0].update(Min='999999999'),
            lambda v: v[0]['rows'].__setitem__(0, copy.deepcopy(v[0]['rows'][1])),
        ]
        for index, mutate in enumerate(mutations):
            inputs, crc = fixture();mutate(inputs)
            with self.subTest(index=index), self.assertRaises(ValueError):
                p.make_plan(*inputs, expected_source_crc=crc)
        inputs, _ = fixture()
        with self.assertRaises(ValueError):p.make_plan(*inputs) # unpinned synthetic source CRC

    def test_closed_native_reference_rejects_in_range_threshold_gain_and_r0_changes(self):
        # Exactly the review counterexample:60->61 remains in range but cannot
        # become a new122 target in the fixed B5 worksheet.
        for address, raw in ((0x9228, '003d'), (0x9184, '80000000'),
                             (0x9188, '94000000'), (0x92ab, '0364')):
            inputs, crc = fixture()
            if address == 0x9228:
                next(f for f in inputs[2]['fields'] if f['address'] == '9228')['rawBE'] = '003c'
                next(f for f in inputs[3]['fields'] if f['address'] == '9228')['rawBE'] = '003c'
                crc = refresh(inputs[3], inputs[4])
            next(f for f in inputs[2]['fields'] if f['address'] == f'{address:04x}')['rawBE'] = raw
            with self.subTest(address=address), self.assertRaises(ValueError):
                p.make_plan(*inputs, expected_source_crc=crc)

    def test_raw_overflow_range_and_mask_rejected(self):
        for address, value in ((0x923c, '7f'), (0x929f, '0042'), (0x92ab, '0364')):
            inputs, crc = fixture()
            next(f for f in inputs[2]['fields'] if f['address'] == f'{address:04x}')['rawBE'] = value
            with self.subTest(address=address), self.assertRaises(ValueError):
                p.make_plan(*inputs, expected_source_crc=crc)
        inputs, crc = fixture()
        next(r for r in inputs[1] if r['address'] == '0x929f')['Max'] = '100'
        with self.assertRaises(ValueError):p.make_plan(*inputs, expected_source_crc=crc)
        inputs, _ = fixture()
        field = next(f for f in inputs[3]['fields'] if f['address'] == '91de')
        field['rawBE'] = '0a' # target already equal -> must reject changed mask !=17
        crc = refresh(inputs[3], inputs[4])
        with self.assertRaises(ValueError):p.make_plan(*inputs, expected_source_crc=crc)

    def test_exclusive_output_never_overwrites(self):
        with tempfile.TemporaryDirectory(prefix=".factory-plan-test-", dir=Path(__file__).resolve().parents[1]) as td:
            path = Path(td)/'worksheet.json'
            p.worksheet.exclusive_json(path, {'sentinel': True})
            with self.assertRaises(FileExistsError):p.worksheet.exclusive_json(path, {'sentinel': False})
            self.assertEqual(json.loads(path.read_text()), {'sentinel': True})


if __name__ == '__main__':unittest.main()

