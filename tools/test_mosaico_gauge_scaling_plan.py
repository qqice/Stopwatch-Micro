"""Host-only tests with synthetic inputs; no private artifacts or TI files needed."""
import copy
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

import mosaico_gauge_scaling_plan as s


def synthetic_inputs():
    """Use public repo address/type declarations, NOT vendor/private metadata.

    Values and unused metadata rows are invented test vectors. They make no
    claim about actual gauge defaults, measured calibration, or TI units.
    """
    header = (s.ROOT / 'main/hal/mosaico/mosaico_threshold_audit_model.h').read_text(encoding='utf-8')
    selectors = {name: int(address, 16) for name, address in
                 re.findall(r'\b(\w+)=0x([0-9A-Fa-f]+)', header.split('struct Field')[0])}
    fields = re.findall(r'\{Selector::(\w+),Type::([IUH][12]),"(\w+)"\}', header)
    if len(fields) != 59:
        raise AssertionError('public audit field schema changed')
    rows, audit = {}, []

    def metadata(address, kind, width, name):
        rows[address] = {'address': address, 'address_hex': f'{address:04x}',
                         'parameter': name, 'width': width, 'type': kind,
                         'default': '0', 'address_aux_raw': width}

    values = {0x929f: 65, 0x929d: 65, 0x929b: 0x112a,
              0x91fb: 80, 0x9201: 10, 0x926b: 200, 0x926d: 0}
    for index, (selector, typed, label) in enumerate(fields):
        address = selectors[selector]
        kind, width = ('B' if typed[0] == 'H' else typed[0]), int(typed[1])
        metadata(address, kind, width, 'Synthetic_' + selector)
        value = values.get(address, 1)
        raw = value.to_bytes(width, 'big', signed=kind == 'I').hex()
        if width == 1:
            raw += 'aa'  # adjacent byte must NOT affect U1/H1 decoding
        audit.append({'index': index, 'details':
                      f'cached=1 generation=1 index={index} count=59 address={address:04x} {label}={value}/{raw}'})

    # Invented public test vectors, intentionally unrelated to device readings.
    calibration = [(0x9180, 'I', 2, 'fff0'), (0x9184, 'F', 4, '7f000000'),
                   (0x9188, 'F', 4, '93000000'), (0x91b4, 'I', 1, '00'),
                   (0x91dd, 'U', 1, 'ef'), (0x91de, 'U', 1, '05'),
                   (0x91df, 'U', 1, '11')]
    for address, kind, width, raw in calibration:
        index = len(audit)
        metadata(address, kind, width, f'SyntheticCalibration{index}')
        decoded = 'unreported' if kind == 'F' else str(s.decode(raw, kind, width))
        audit.append({'index': index, 'details':
                      f'cached=1 generation=1 index={index} count=66 address={address:04x} type={kind}{width} width={width} raw={raw} decoded={decoded}'})
    # Closed roles without baseline must remain blocked, not receive defaults.
    for address in (0x920e, 0x9210, 0x923c, 0x9264):
        metadata(address, 'I', 1 if address == 0x923c else 2,
                 f'SyntheticMissingBaseline{address:04x}')
    metadata(0x92a1, 'U', 2, 'SyntheticOpaque92a1')
    for i in range(131 - len(rows)):
        metadata(0xa000 + i, 'U', 1, f'SyntheticName{i}')
    ranges = []
    for address in (0x926b, 0x926f, 0x9270, 0x929d, 0x929f, 0x9276,
                    0x9201, 0x9228, 0x922a, 0x9184, 0x9188):
        row = rows[address]
        unit = ('mAh' if address in s.CAPACITY else 'mA' if address in s.CURRENT
                else 's' if address == 0x9276 else '%' if address in (0x926f, 0x9270) else '-')
        ranges.append({'address': f'0x{address:04x}', 'parameter': row['parameter'],
                       'type': row['type'], 'length': row['width'], 'Units': unit,
                       'Min': '0.1' if address == 0x9184 else '30000' if address == 0x9188 else '0',
                       'Max': '4' if address == 0x9184 else '3000000' if address == 0x9188 else '32767',
                       'Read': 'x', 'Write': 'x'})
    return {'rows': list(rows.values())}, ranges, audit


def synthetic_full_inputs():
    meta, _, audit = synthetic_inputs()
    metadata = {r['address']: r for r in meta['rows']}
    legacy = s.parse_baseline(audit, metadata)
    fields, ranges = [], []
    for row in meta['rows']:
        a, kind, width = row['address'], row['type'], row['width']
        raw = legacy.get(a, {}).get('rawBE', '00' * width)
        fields.append({'address': f'{a:04x}', 'name': 'UNTRUSTED name',
                       'type': kind, 'width': width, 'rawBE': raw,
                       'value': 'UNTRUSTED value', 'unit': 'UNTRUSTED unit'})
        bits = width * 8
        low, high = ((-(1 << (bits - 1)), (1 << (bits - 1)) - 1)
                     if kind == 'I' else (0, (1 << bits) - 1))
        if kind == 'F':
            low, high = s.f4.CALIBRATION_RANGES['gain' if a == 0x9184 else 'delta']
        unit = ('mAh' if a in s.CAPACITY else 'mA' if a in s.CURRENT
                else '3 µA' if a in s.NATIVE_CURRENT else 's' if a == 0x9276
                else s.PRESERVE_UNITS.get(a, '-'))
        ranges.append({'address': f'0x{a:04x}', 'parameter': row['parameter'],
                       'type': kind, 'length': width, 'Min': str(low), 'Max': str(high),
                       'Units': unit, 'Read': 'x', 'Write': 'x',
                       **{key + '_status': 0 for key in ('Min', 'Max', 'Units', 'Read', 'Write')}})
    document = {'complete_public131': True, 'not_a_full_hidden_model_or_OTP_image': True,
                'generation': '9', 'parameter_writes': False, 'security_after': 'SEALED',
                'sources': {'synthetic': 'a' * 64}, 'fields': fields}
    return meta, ranges, document


class ScalingPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.meta, cls.ranges, cls.audit = synthetic_inputs()

    def plan(self, factor=4, audit=None):
        return s.make_plan(self.meta, self.ranges, audit or self.audit, factor)

    def test_decode_be_and_f4(self):
        self.assertEqual(s.decode('ef', 'U', 1), 239)
        self.assertEqual(s.decode('fff0', 'I', 2), -16)
        self.assertEqual(s.decode('7f000000', 'F', 4), 0.25)
        with self.assertRaises(ValueError):
            s.decode('ef00', 'U', 1)

    def test_synthetic_66_and_preserve_bits_deadband(self):
        p = self.plan()
        rows = {r['address']: r for r in p['matrix']}
        self.assertEqual(rows['929b']['baseline']['rawBE'], '112a')
        self.assertEqual(rows['929b']['action'], 'PRESERVE')
        self.assertIsNone(rows['929b']['candidate'])
        self.assertEqual(rows['91df']['baseline']['value'], 17)
        self.assertIsNone(rows['91df']['candidate'])
        self.assertEqual(rows['91de']['candidate']['value'], 20)
        self.assertEqual(rows['9184']['candidate']['rawBE'], '81000000')
        self.assertEqual(rows['9184']['candidate']['value'], 1.0)
        self.assertEqual(rows['9188']['candidate']['rawBE'], '95000000')
        self.assertEqual(rows['9184']['format_status'], 'REFERENCE_VERIFIED_XEMICS_F4')
        self.assertEqual(rows['9184']['status'], 'ARITHMETIC_ONLY')
        self.assertFalse(p['ready_for_hardware'])
        self.assertTrue(p['all_no_device_changes'])

    def test_edv_current_model_and_native_constants_remain_blocked(self):
        p = self.plan()
        rows = {r['address']: r for r in p['matrix']}
        self.assertEqual(rows['92ab']['status'], 'BLOCKED_EDV_MODEL')
        self.assertIsNone(rows['92ab']['candidate'])
        self.assertTrue(any('quantization' in b for b in p['blockers']))
        self.assertTrue(any('fixed mAh' in b for b in p['blockers']))
        self.assertFalse(p['ready_for_hardware'])

    def test_overflow_rejected_and_missing_baseline_blocked(self):
        audit = copy.deepcopy(self.audit)
        charge = next(r for r in audit if 'address=91fb' in r['details'])
        charge['details'] = charge['details'].replace('80/0050', '30000/7530')
        rows = {r['address']: r for r in self.plan(audit=audit)['matrix']}
        self.assertIsNone(rows['91fb']['candidate'])
        self.assertIn('overflow', rows['91fb']['blocking_reasons'])
        self.assertIn('missing baseline', rows['920e']['blocking_reasons'])
        self.assertIsNone(rows['920e']['candidate'])

    def test_f4_out_of_range_is_blocked_not_a_candidate(self):
        audit = copy.deepcopy(self.audit)
        gain = next(r for r in audit if 'address=9184' in r['details'])
        gain['details'] = gain['details'].replace('raw=7f000000', 'raw=83000000')
        p = self.plan(audit=audit)
        row = next(r for r in p['matrix'] if r['address'] == '9184')
        self.assertEqual(row['status'], 'BLOCKED')
        self.assertIsNone(row['candidate'])
        self.assertTrue(any('calibration range' in r for r in row['blocking_reasons']))
        self.assertFalse(p['ready_for_hardware'])
        self.assertFalse(any('opaque F4' in b for b in p['blockers']))

    def test_metadata_mismatch_and_incomplete_audit_rejected(self):
        with self.assertRaises(ValueError):
            self.plan(audit=self.audit[:-1])
        audit = copy.deepcopy(self.audit)
        audit[64]['details'] = audit[64]['details'].replace('type=U1', 'type=I1')
        with self.assertRaises(ValueError):
            self.plan(audit=audit)
        with self.assertRaises(ValueError):
            self.plan(factor=3)

    def test_no_qmax_invention_units_conflict(self):
        rows = self.plan()['matrix']
        self.assertFalse(any('qmax' in r['parameter'].lower() for r in rows))
        self.assertIsNone(next(r for r in rows if r['address'] == '92a1')['candidate'])
        r = next(r for r in rows if r['address'] == '9276')
        self.assertEqual(r['official_unit'], 's')
        self.assertIsNone(r['candidate'])
        self.assertTrue(r['status'].startswith('BLOCKED'))
        self.assertEqual(next(r for r in rows if r['address'] == '926b')['candidate']['value'], 800)

    def test_vct_arithmetic_not_measurement(self):
        x2, x4 = s.vct_window(2, 10), s.vct_window(4, 10)
        self.assertLess(x2['charge_at_taper_upper_bound_virtual_mAh'], .25)
        self.assertFalse(x2['necessary_current_interval_nonempty'])
        self.assertTrue(x4['necessary_current_interval_nonempty'])
        self.assertEqual(x4['minimum_virtual_mA'], 22.5)
        self.assertEqual(x4['minimum_physical_mA'], 5.625)
        self.assertFalse(x4['sufficient_for_vct'])
        self.assertFalse(x4['measured'])
        boundary = s.vct_window(2, 11.25)
        self.assertEqual(boundary['candidate_taper_virtual_mA'], 22.5)
        self.assertEqual(boundary['charge_at_taper_upper_bound_virtual_mAh'], .25)
        self.assertFalse(boundary['necessary_current_interval_nonempty'])
        self.assertIn('strictly greater', boundary['assumption'])

    def test_dc_fcc_must_both_be_65(self):
        for address in ('929f', '929d'):
            audit = copy.deepcopy(self.audit)
            record = next(r for r in audit if f'address={address}' in r['details'])
            record['details'] = record['details'].replace('65/0041', '64/0040')
            with self.assertRaisesRegex(ValueError, '65 mAh DC/FCC baseline'):
                self.plan(audit=audit)

    def test_no_tuning_and_input_objects_unchanged(self):
        before = copy.deepcopy((self.meta, self.ranges, self.audit))
        plan = self.plan()
        rows = {r['address']: r for r in plan['matrix']}
        for address in ('929b', '92b4', '92b7', '92ba', '91df'):
            self.assertEqual(rows[address]['action'], 'PRESERVE')
            self.assertIsNone(rows[address]['candidate'])
        for address in ('9201', '926b'):
            self.assertEqual(rows[address]['candidate']['value'], rows[address]['baseline']['value'] * 4)
        self.assertEqual((self.meta, self.ranges, self.audit), before)
        self.assertIn('not to tune', plan['preservation_policy'])
        self.assertIn('power', plan['host_consumer_contract'])
        self.assertTrue(any('Near Full baseline 200' in x for x in plan['blockers']))
        audit = copy.deepcopy(self.audit)
        near = next(r for r in audit if 'address=926b' in r['details'])
        near['details'] = near['details'].replace('200/00c8', '150/0096')
        self.assertTrue(any('Near Full baseline 150' in x for x in self.plan(audit=audit)['blockers']))

    def test_exclusive_output_and_sources(self):
        with tempfile.TemporaryDirectory(dir=s.ROOT, prefix='.scaling-test-') as d:
            self.assertEqual(Path(d).resolve().parent, s.ROOT.resolve())
            path = Path(d) / 'worksheet.json'
            argv = ['--output', str(path)]
            for option, data in (('metadata', self.meta), ('ranges', self.ranges), ('baseline', self.audit)):
                fixture = Path(d) / (option + '.json')
                fixture.write_text(json.dumps(data), encoding='utf-8')
                argv += ['--' + option, str(fixture)]
            # Simulate a clone where every private/default input path is absent.
            absent = Path(d) / 'not-present.json'
            with mock.patch.multiple(s, META=absent, RANGES=absent, BASELINE=absent):
                s.main(argv)
            original = path.read_bytes()
            self.assertEqual(len(json.loads(original)['sources']['baseline']['sha256']), 64)
            with self.assertRaises(FileExistsError):
                s.main(argv)
            self.assertEqual(path.read_bytes(), original)

    def test_full131_recompute_complete_roles_and_preservation(self):
        meta, ranges, backup = synthetic_full_inputs()
        def set_value(address, value):
            f = next(r for r in backup['fields'] if r['address'] == address)
            f['rawBE'] = value.to_bytes(f['width'], 'big', signed=f['type'] == 'I').hex()
        set_value('9251', 700)
        set_value('9269', 2)
        before = copy.deepcopy((meta, ranges, backup))
        for factor in (2, 4):
            p = s.make_plan(meta, ranges, backup, factor)
            rows = {r['address']: r for r in p['matrix']}
            self.assertEqual(len(rows), 131)
            self.assertTrue(p['complete_public131_metadata_consistent'])
            self.assertEqual(p['official_range_count'], 131)
            self.assertEqual(p['backup_integrity']['security_after'], 'SEALED')
            self.assertEqual(rows['9269']['unit'], '3uA')
            self.assertEqual(rows['9269']['candidate']['value'], 2 * factor)
            for a in ('920e', '9210', '923c', '9264'):
                self.assertIsNotNone(rows[a]['candidate'])
            self.assertEqual(rows['9251']['physical_percent'], 7)
            for a in ('9251', '927b', '91b4', '92b4'):
                self.assertIsNone(rows[a]['candidate'])
                self.assertTrue(rows[a]['status'].startswith('PRESERVE'))
            self.assertEqual(rows['92ab']['status'], 'BLOCKED_EDV_MODEL')
            self.assertIsNone(rows['92ab']['candidate'])
            self.assertTrue(rows['9276']['status'].startswith('BLOCKED'))
            self.assertEqual(rows['a000']['status'], 'BLOCKED_UNREVIEWED')
            self.assertTrue(all(r['baseline_range_status'] == 'VALIDATED_EXACT' for r in rows.values()))
            self.assertFalse(p['ready_for_hardware'])
        self.assertEqual((meta, ranges, backup), before)

    def test_full131_rejects_incomplete_duplicate_type_raw_range_or_security(self):
        for mode in ('missing', 'duplicate', 'unknown', 'type', 'width', 'raw',
                     'out_of_range', 'security', 'writes', 'generation', 'range_missing',
                     'range_duplicate', 'range_status', 'range_type', 'range_bounds'):
            meta, ranges, backup = synthetic_full_inputs()
            f = backup['fields'][0]
            if mode == 'missing': backup['fields'].pop()
            if mode == 'duplicate': backup['fields'][-1] = copy.deepcopy(f)
            if mode == 'unknown': f['address'] = 'ffff'
            if mode == 'type': f['type'] = 'F'
            if mode == 'width': f['width'] = True
            if mode == 'raw': f['rawBE'] = '00'
            if mode == 'out_of_range': ranges[0]['Max'] = '-32768'
            if mode == 'security': backup['security_after'] = 'UNSEALED'
            if mode == 'writes': backup['parameter_writes'] = True
            if mode == 'generation': backup['generation'] = None
            if mode == 'range_missing': ranges.pop()
            if mode == 'range_duplicate': ranges[-1] = copy.deepcopy(ranges[0])
            if mode == 'range_status': ranges[0]['Units_status'] = 1
            if mode == 'range_type': ranges[0]['type'] = 'B'
            if mode == 'range_bounds': ranges[0]['Min'] = 'not numeric'
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                s.make_plan(meta, ranges, backup)


if __name__ == '__main__':
    unittest.main()
