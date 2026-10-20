"""Offline, fail-closed scaling worksheet. No device access or executable writes.

Candidates are arithmetic hypotheses, NOT validated calibration or a deployment
plan. F4 storage arithmetic is reference-verified, not hardware calibration.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
from fractions import Fraction

import mosaico_gauge_f4 as f4

ROOT = Path(__file__).resolve().parents[1]
META = ROOT / '.artifacts/references/bqstudio/extracted/official-reader/model-metadata-003/public-model-fields-normalized.json'
RANGES = ROOT / '.artifacts/references/bqstudio/extracted/official-reader/model-metadata-004/selected-ranges-formulas.json'
BASELINE = ROOT / '.artifacts/private/mosaico/gauge97-full-values-001.json'
# Closed manually reviewed dimensional roles; no inference from names/defaults.
CAPACITY = {0x929d, 0x929f, 0x920e, 0x9210, 0x926b, 0x926d}
CURRENT = {0x91de, 0x91fb, 0x9201, 0x9217, 0x9228, 0x922a,
           0x922c, 0x923c, 0x9264}
NATIVE_CURRENT = {0x9269}  # Electronics Load: each native unit is 3 uA, NOT mA.
FLOATS = {0x9184, 0x9188}
# Closed address roles, never inferred from parameter names or saved value/unit.
PRESERVE_UNITS = {}
for _unit, _addresses in {
    'mV': {0x9282, 0x9284, 0x9288, 0x928a, 0x928e, 0x9290, 0x9294, 0x9296,
           0x92a3, 0x92a5, 0x92b4, 0x92b7, 0x92ba, 0x92bd, 0x92bf, 0x92c1,
           0x92c3, 0x92c5, 0x92c7, 0x92c9, 0x92cb, 0x92cd, 0x92cf, 0x92d1,
           0x9272, 0x9274, 0x91fd, 0x9240, 0x9243, 0x91b7},
    '%': {0x927d, 0x9286, 0x9287, 0x928c, 0x928d, 0x9292, 0x9293, 0x9298,
          0x9299, 0x926f, 0x9270, 0x920b},
    's': {0x92b6, 0x92b9, 0x92bc, 0x927c, 0x9234, 0x9239, 0x9219, 0x921e,
          0x921f, 0x922e, 0x9230, 0x9231, 0x9242},
    '0.1°C': {0x925b, 0x91f5, 0x91f7, 0x91f9, 0x9232, 0x9235, 0x9237,
              0x923a, 0x921a, 0x921c, 0x91b5, 0x91b6},
    'hex': {0x9271, 0x9206, 0x9208, 0x920c, 0x9212, 0x920d, 0x927f, 0x9281, 0x929a},
    'Counts': {0x91b4, 0x91d2, 0x9180},
    '0.1 °K': {0x9182}, '0.1%': {0x927b}, '.01 %': {0x9251},
    '0.01%/day': {0x9268},
}.items():
    PRESERVE_UNITS.update(dict.fromkeys(_addresses, _unit))


def range_number(value):
    if not isinstance(value, str):
        raise ValueError('official range must be a string')
    return Fraction(int(value, 16)) if value.lower().startswith('0x') else Fraction(value)


def normalized_unit(unit):
    return unit.replace(' ', '').replace('µ', 'u').replace('μ', 'u').lower()


def parse_full_baseline(document, metadata):
    if (document.get('complete_public131') is not True or
            document.get('not_a_full_hidden_model_or_OTP_image') is not True or
            document.get('parameter_writes') is not False or
            document.get('security_after') != 'SEALED' or
            not re.fullmatch(r'\d+', str(document.get('generation', '')))):
        raise ValueError('full backup completeness/security/generation attestation invalid')
    fields = document.get('fields', [])
    if len(fields) != 131:
        raise ValueError('expected exact complete public131 backup')
    result = {}
    for field in fields:
        address = field.get('address')
        if not isinstance(address, str) or not re.fullmatch('[0-9a-fA-F]{4}', address):
            raise ValueError('invalid full backup address')
        a = int(address, 16)
        if a not in metadata or a in result:
            raise ValueError('unknown or duplicate full backup address')
        m = metadata[a]
        if (type(field.get('width')) is not int or
                (field.get('type'), field.get('width')) != (m['type'], m['width'])):
            raise ValueError('full backup metadata type/width mismatch')
        raw = field.get('rawBE')
        if not isinstance(raw, str) or not re.fullmatch('[0-9a-fA-F]{' + str(2 * m['width']) + '}', raw):
            raise ValueError('invalid full backup rawBE')
        value = decode(raw, m['type'], m['width'])
        result[a] = {'rawBE': raw.lower(), 'value': value}
        if m['type'] == 'F':
            result[a]['exact_value'] = str(f4.decode(raw))
    if result.keys() != metadata.keys():
        raise ValueError('full backup public131 set mismatch')
    return result


def decode(raw, kind, width):
    data = bytes.fromhex(raw)
    if len(data) != width:
        raise ValueError('raw width mismatch')
    if kind == 'F':
        if width != 4:
            raise ValueError('F4 width must be four')
        return float(f4.decode(data))
    if kind not in {'I', 'U', 'B'}:
        raise ValueError('unsupported type')
    return int.from_bytes(data, 'big', signed=kind == 'I')


def parse_baseline(records, metadata):
    if len(records) != 66:
        raise ValueError('expected complete 66-field audit')
    result, generations = {}, set()
    for index, record in enumerate(records):
        d = record['details']
        if record['index'] != index or not re.search(r'\bindex=' + str(index) + r'\b', d):
            raise ValueError('audit index mismatch')
        if 'cached=1' not in d:
            raise ValueError('audit is not cached valid data')
        expected_count = 59 if index < 59 else 66
        if not re.search(r'\bcount=' + str(expected_count) + r'\b', d):
            raise ValueError('audit field count mismatch')
        generations.add(re.search(r'\bgeneration=(\d+)\b', d).group(1))
        addr = int(re.search(r'\baddress=([0-9a-fA-F]{4})\b', d).group(1), 16)
        if addr not in metadata or addr in result:
            raise ValueError('unknown or duplicate audit address')
        m = metadata[addr]
        kind, width = m['type'], m['width']
        modern = re.search(r'\btype=([IUFH])(\d+) width=(\d+) raw=([0-9a-fA-F]+)\b', d)
        if modern:
            t, w, stated_width, raw = modern.groups()
            t = 'B' if t == 'H' else t
            if (t, int(w), int(stated_width)) != (kind, width, width):
                raise ValueError('audit metadata type/width mismatch')
        else:
            legacy = re.search(r'\b\w+_([iuh])(\d+)=(-?\d+)/([0-9a-fA-F]{4})\b', d)
            if not legacy:
                raise ValueError('missing typed raw data')
            t, w, stated, raw = legacy.groups()
            t = {'i': 'I', 'u': 'U', 'h': 'B'}[t]
            if (t, int(w)) != (kind, width):
                raise ValueError('audit metadata type/width mismatch')
            raw = raw[:width * 2]  # legacy U1 includes adjacent byte, not its value
            if decode(raw, kind, width) != int(stated):
                raise ValueError('legacy decoded value disagrees with BE raw')
        value = decode(raw, kind, width)
        if modern and kind != 'F':
            stated = re.search(r'\bdecoded=(-?\d+)\b', d)
            if not stated or int(stated.group(1)) != value:
                raise ValueError('decoded value disagrees with BE raw')
        result[addr] = {'rawBE': raw.lower(), 'value': value}
    if len(generations) != 1:
        raise ValueError('mixed audit generations')
    return result


def vct_window(factor, baseline_taper, seconds=40):
    taper = baseline_taper * factor
    minimum = 0.25 * 3600 / seconds
    return {'assumption': 'strictly greater than 0.25 virtual mAh in EACH window; current below taper',
            'measured': False, 'sufficient_for_vct': False, 'seconds': seconds,
            'minimum_virtual_mA': minimum, 'minimum_physical_mA': minimum / factor,
            'baseline_taper_mA': baseline_taper, 'candidate_taper_virtual_mA': taper,
            'charge_at_taper_upper_bound_virtual_mAh': taper * seconds / 3600,
            'necessary_current_interval_nonempty': taper > minimum}


def make_plan(meta_doc, range_rows, records, factor=4):
    if type(factor) is not int or factor not in (2, 4):
        raise ValueError('factor must be 2 or 4')
    rows = meta_doc['rows']
    if len(rows) != 131:
        raise ValueError('expected 131 official metadata rows')
    metadata = {}
    for row in rows:
        a = row['address']
        if type(a) is not int or int(row['address_hex'], 16) != a or a in metadata:
            raise ValueError('invalid official metadata address')
        if (type(row['width']) is not int or row['type'] not in {'I', 'U', 'B', 'F'} or
                row['width'] not in (1, 2, 4) or
                (row['type'] == 'F') != (row['width'] == 4)):
            raise ValueError('invalid official metadata type/width')
        metadata[a] = row
    full = isinstance(records, dict)
    expected_ranges = 131 if full else 11
    ranges = {}
    for r in range_rows:
        a = int(r['address'], 16)
        if a in ranges or a not in metadata:
            raise ValueError('unknown or duplicate official range address')
        ranges[a] = r
    if len(ranges) != expected_ranges or (full and ranges.keys() != metadata.keys()):
        raise ValueError(f'expected exact {expected_ranges} official range rows')
    for a, r in ranges.items():
        m = metadata[a]
        if (type(r['length']) is not int or
                (r['parameter'], r['type'], r['length']) != (m['parameter'], m['type'], m['width'])):
            raise ValueError('official range metadata mismatch')
        if full and any(type(r.get(k + '_status')) is not int or r[k + '_status'] != 0
                        for k in ('Min', 'Max', 'Read', 'Write', 'Units')):
            raise ValueError('official range reader status failed or absent')
        if range_number(r['Min']) > range_number(r['Max']) or not isinstance(r['Units'], str):
            raise ValueError('invalid official range or unit')
    baseline = parse_full_baseline(records, metadata) if full else parse_baseline(records, metadata)
    if full:
        for a, b in baseline.items():
            value = f4.decode(b['rawBE']) if metadata[a]['type'] == 'F' else b['value']
            if not range_number(ranges[a]['Min']) <= value <= range_number(ranges[a]['Max']):
                raise ValueError(f'baseline official range exceeded at {a:04x}')
    if any(baseline.get(a, {}).get('value') != 65 for a in (0x929d, 0x929f)):
        raise ValueError('worksheet requires a 65 mAh DC/FCC baseline')
    matrix = []
    blockers = ['Host ALL current/capacity/power consumers must divide by k, not only display; implementation and unit audit incomplete.',
                'No complete rollback procedure or hardware validation in this worksheet.',
                'Reference-verified F4 arithmetic does not validate hardware calibration effects or restore.',
                'EDV compensation uses current times R0: inverse model scaling and integer quantization are unverified.',
                'Native fixed mAh learning constants change physical meaning under scaling; a unit transform is not complete algorithm invariance.',
                'Metadata range 0..32767 is input acceptance, not proof of TI product minimum capacity or physical support.',
                'Unknown metadata fields remain unreviewed; Qmax is absent and is not guessed.',
                'Max Smoothing Current official unit s conflicts with name: BLOCKED; preserve original.',
                'Not evidence of solving the 97 percent observation.']
    near_full = baseline.get(0x926b, {}).get('value')
    if near_full is not None and near_full > 65:
        blockers.append(f'Near Full baseline {near_full} mAh exceeds physical 65 mAh: parameter reasonableness unverified; no automatic reduction.')
    for a in sorted(metadata):
        m, b, r = metadata[a], baseline.get(a), ranges.get(a)
        unit = 'mAh' if a in CAPACITY else 'mA' if a in CURRENT else '3uA' if a in NATIVE_CURRENT else None
        entry = {'address': f'{a:04x}', 'parameter': m['parameter'],
                 'type': m['type'], 'width': m['width'], 'baseline': b,
                 'unit': unit, 'official_unit': r.get('Units') if r else None,
                 'unit_basis': 'closed manually reviewed dimensional role; requires confirmation' if unit else 'not inferred',
                 'action': 'PRESERVE', 'status': 'BLOCKED_UNREVIEWED', 'candidate': None}
        entry['baseline_range_status'] = 'VALIDATED_EXACT' if full else 'LEGACY_PARTIAL_RANGES'
        if a in CAPACITY | CURRENT | NATIVE_CURRENT | FLOATS:
            reasons = []
            if not b:
                reasons.append('missing baseline')
            if unit and (not r or normalized_unit(r['Units']) != normalized_unit(unit)):
                reasons.append('official unit unavailable or contradictory')
            entry['action'] = 'ARITHMETIC_CANDIDATE_ONLY'
            if b:
                if a in FLOATS:
                    parameter = 'gain' if a == 0x9184 else 'delta'
                    entry['unit'] = 'mA proportional gain' if parameter == 'gain' else 'mAh proportional gain'
                    entry['unit_basis'] = 'BQ27220 TRM 4.9.1.1/2; proportional calibration, not direct mA/mAh'
                    entry['format_status'] = 'REFERENCE_VERIFIED_XEMICS_F4'
                    entry['baseline']['exact_value'] = str(f4.decode(b['rawBE']))
                    try:
                        candidate = f4.scale_power_two(b['rawBE'], factor, parameter)
                        if r and not range_number(r['Min']) <= f4.decode(candidate) <= range_number(r['Max']):
                            raise ValueError('official range exceeded')
                        entry['candidate'] = {'rawBE': candidate.hex(),
                                              'value': float(f4.decode(candidate)),
                                              'exact_value': str(f4.decode(candidate))}
                    except ValueError as error:
                        reasons.append(str(error))
                else:
                    value = b['value'] * factor
                    bits = m['width'] * 8
                    lo, hi = (-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if m['type'] == 'I' else (0, (1 << bits) - 1)
                    if not lo <= value <= hi:
                        reasons.append('overflow')
                    elif r and not range_number(r['Min']) <= value <= range_number(r['Max']):
                        reasons.append('official range exceeded')
                    else:
                        entry['candidate'] = {'value': value}
            entry['status'] = 'BLOCKED' if reasons else 'ARITHMETIC_ONLY'
            entry['blocking_reasons'] = reasons
        elif a == 0x91df:
            entry.update(unit='raw voltage deadband', status='PRESERVE_REQUIRED',
                         unit_basis='CC Deadband is not Current Deadband at 91de')
        elif a == 0x929b:
            entry['status'] = 'PRESERVE_REQUIRED'
            entry['note'] = 'Preserve all SC/config bits and raw 112a; do not change to 1132.'
            if not b or b['rawBE'] != '112a':
                blockers.append('Configuration baseline is not expected 112a.')
        elif a == 0x9276:
            entry['note'] = 'Official s unit contradiction; no scaling guessed.'
        elif a == 0x92ab:
            entry['status'] = 'BLOCKED_EDV_MODEL'
            entry['note'] = ('BQ27220 TRM 1.1.5 uses ILOAD * R0 * FTZ; preserving R0 while scaling '
                             'current changes compensated EDV voltage. No coefficient write inferred.')
        elif a in PRESERVE_UNITS:
            expected = PRESERVE_UNITS[a]
            if r and normalized_unit(r['Units']) == normalized_unit(expected):
                entry.update(unit=expected, status='PRESERVE_REVIEWED_UNIT',
                             unit_basis='closed address role checked against official range units')
                if a == 0x9251 and b:
                    entry['physical_percent'] = b['value'] / 100
                if a == 0x927b:
                    entry['note'] = 'EOC Smooth Current is 0.1 percent, not a dimensional current.'
        matrix.append(entry)
    taper = baseline.get(0x9201, {}).get('value')
    return {'schema': 1, 'ready_for_hardware': False, 'all_no_device_changes': True,
            'factor': factor, 'physical_capacity_mAh': 65, 'virtual_capacity_mAh': 65 * factor,
            'valid66_metadata_consistent': not full,
            'complete_public131_metadata_consistent': full,
            'baseline_field_count': len(baseline), 'official_range_count': len(ranges),
            'backup_integrity': ({'generation': records['generation'],
                                  'security_after': records['security_after'],
                                  'parameter_writes': records['parameter_writes'],
                                  'public_only_not_hidden_or_OTP': True,
                                  'attestation_not_independent_device_verification': True,
                                  'upstream_sources_reported_not_rehashed': records.get('sources', {})}
                                 if full else {'mode': 'legacy_cached66_partial_ranges'}),
            'blockers': blockers,
            'preservation_policy': 'Percent, voltage, temperature, time, ratio, flags, CCDeadband and SC/EDV configuration bits remain unchanged. Dimensional candidates scale raw thresholds/taper/NearFull only to preserve their physical values, not to tune them.',
            'host_consumer_contract': 'ALL current, capacity and power consumers divide virtual values by k; no consumer changes made.',
            'vct_necessary_not_sufficient': vct_window(factor, taper) if taper is not None else {'status': 'BLOCKED missing taper'},
            'matrix': matrix}


def exclusive_json(path, plan):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(plan, stream, indent=2, ensure_ascii=False)
        stream.write('\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--metadata', type=Path, default=META)
    parser.add_argument('--ranges', type=Path, default=RANGES)
    parser.add_argument('--baseline', type=Path, default=BASELINE)
    parser.add_argument('--factor', type=int, choices=(2, 4), default=4)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.suffix.lower() != '.json':
        parser.error('output must be a new .json worksheet')
    paths = {'metadata': args.metadata, 'ranges': args.ranges, 'baseline': args.baseline}
    payloads = {name: path.read_bytes() for name, path in paths.items()}
    plan = make_plan(*(json.loads(payloads[n]) for n in paths), factor=args.factor)
    plan['sources'] = {name: {'path': str(paths[name].resolve()), 'sha256': hashlib.sha256(data).hexdigest()} for name, data in payloads.items()}
    exclusive_json(args.output, plan)


if __name__ == '__main__':
    main()
