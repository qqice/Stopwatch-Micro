"""Offline, fail-closed scaling worksheet. No device access or executable writes.

Candidates are arithmetic hypotheses, NOT validated calibration or a deployment
plan. F4 storage arithmetic is reference-verified, not hardware calibration.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

import mosaico_gauge_f4 as f4

ROOT = Path(__file__).resolve().parents[1]
META = ROOT / '.artifacts/references/bqstudio/extracted/official-reader/model-metadata-003/public-model-fields-normalized.json'
RANGES = ROOT / '.artifacts/references/bqstudio/extracted/official-reader/model-metadata-004/selected-ranges-formulas.json'
BASELINE = ROOT / '.artifacts/private/mosaico/gauge97-full-values-001.json'
# Closed manually reviewed dimensional roles; no inference from names/defaults.
CAPACITY = {0x929d, 0x929f, 0x920e, 0x9210, 0x926b, 0x926d}
CURRENT = {0x91de, 0x91fb, 0x9201, 0x9217, 0x9228, 0x922a,
           0x922c, 0x923c, 0x9264, 0x9269}
FLOATS = {0x9184, 0x9188}


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
        metadata[a] = row
    ranges = {int(r['address'], 16): r for r in range_rows}
    if len(ranges) != 11:
        raise ValueError('expected 11 official range rows')
    for a, r in ranges.items():
        m = metadata[a]
        if (r['parameter'], r['type'], r['length']) != (m['parameter'], m['type'], m['width']):
            raise ValueError('official range metadata mismatch')
    baseline = parse_baseline(records, metadata)
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
        unit = 'mAh' if a in CAPACITY else 'mA' if a in CURRENT else None
        entry = {'address': f'{a:04x}', 'parameter': m['parameter'],
                 'type': m['type'], 'width': m['width'], 'baseline': b,
                 'unit': unit, 'official_unit': r.get('Units') if r else None,
                 'unit_basis': 'closed manually reviewed dimensional role; requires confirmation' if unit else 'not inferred',
                 'action': 'PRESERVE', 'status': 'BLOCKED_UNREVIEWED', 'candidate': None}
        if a in CAPACITY | CURRENT | FLOATS:
            reasons = []
            if not b:
                reasons.append('missing baseline')
            if unit and (not r or r.get('Units') != unit):
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
                    elif r and not float(r['Min']) <= value <= float(r['Max']):
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
        matrix.append(entry)
    taper = baseline.get(0x9201, {}).get('value')
    return {'schema': 1, 'ready_for_hardware': False, 'all_no_device_changes': True,
            'factor': factor, 'physical_capacity_mAh': 65, 'virtual_capacity_mAh': 65 * factor,
            'valid66_metadata_consistent': True, 'blockers': blockers,
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
