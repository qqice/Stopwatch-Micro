"""Offline public131 comparison; no hardware access or executable write plan.

The factory export is a historical observation, never promoted to current-boot
evidence. Range acceptance is not evidence of a correct cell/chemistry model.
"""
import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
import zlib

import mosaico_gauge_scaling_plan as worksheet
import mosaico_gauge_f4 as f4


def catalog(metadata, ranges):
    rows = metadata.get('rows', [])
    if len(rows) != 131 or len(ranges) != 131:
        raise ValueError('exact public131 metadata and ranges required')
    result = {}
    for row in rows:
        a = row['address']
        if (type(a) is not int or a in result or
                row['address_hex'] != f'{a:04x}' or
                row['type'] not in {'I', 'U', 'B', 'F'} or
                type(row['width']) is not int or row['width'] not in (1, 2, 4) or
                (row['type'] == 'F') != (row['width'] == 4)):
            raise ValueError('invalid metadata')
        result[a] = dict(row)
    seen = set()
    for r in ranges:
        a = int(r['address'], 16)
        if a not in result or a in seen:
            raise ValueError('unknown or duplicate range')
        seen.add(a)
        m = result[a]
        if ((r['parameter'], r['type'], r['length']) !=
                (m['parameter'], m['type'], m['width']) or
                any(type(r.get(k + '_status')) is not int or r[k + '_status'] != 0
                    for k in ('Min', 'Max', 'Read', 'Write', 'Units')) or
                not isinstance(r['Units'], str)):
            raise ValueError('range schema/status disagreement')
        low, high = map(worksheet.range_number, (r['Min'], r['Max']))
        if low > high:
            raise ValueError('inverted official range')
        m.update(low=low, high=high, unit=r['Units'])
    return result


def factory_export(document, metadata):
    if document.get('complete') is not True or len(document.get('fields', [])) != 131:
        raise ValueError('complete historical factory131 export required')
    result, generations, crcs, payload = {}, set(), set(), bytearray()
    for index, field in enumerate(document['fields']):
        address = field.get('address', '')
        if not isinstance(address, str) or not re.fullmatch('[0-9a-fA-F]{4}', address):
            raise ValueError('invalid factory address')
        a = int(address, 16)
        if a not in metadata or a in result or type(field.get('index')) is not int or field['index'] != index:
            raise ValueError('duplicate/unknown factory field or bad index')
        m, d = metadata[a], field.get('metadata', {})
        if (type(field.get('width')) is not int or field['width'] != m['width'] or
                d.get('index') != str(index) or d.get('address') != address or
                d.get('width') != str(m['width']) or
                d.get('type') != ('F4_opaque' if m['type'] == 'F' else
                                  ('H' if m['type'] == 'B' else m['type']) + str(m['width'])) or
                any(d.get(k) != '1' for k in ('complete', 'durable', 'closed_verified', 'fresh_this_boot')) or
                d.get('source_stage') != 'complete' or
                not re.fullmatch('[1-9][0-9]*', d.get('generation', '')) or
                not re.fullmatch('[0-9a-fA-F]{8}', d.get('source_crc', ''))):
            raise ValueError('factory field metadata/completeness disagreement')
        raw = field.get('rawBE', '')
        if (not isinstance(raw, str) or not re.fullmatch('[0-9a-fA-F]{' + str(2*m['width']) + '}', raw) or
                field.get('raw', '').lower() != raw.lower().ljust(8, '0') or
                d.get('raw', '').lower() != field.get('raw', '').lower()):
            raise ValueError('factory raw bytes/width disagreement')
        generations.add(d['generation']); crcs.add(d['source_crc'].lower())
        result[a] = {'rawBE': raw.lower(), 'value': worksheet.decode(raw, m['type'], m['width'])}
        payload.extend(a.to_bytes(2, 'little') + bytes([m['width']]) + bytes.fromhex(raw).ljust(4, b'\0'))
    if len(generations) != 1 or len(crcs) != 1:
        raise ValueError('mixed factory generations or source CRCs')
    if f'{zlib.crc32(payload):08x}' != next(iter(crcs)):
        raise ValueError('factory source payload CRC disagreement')
    return result, next(iter(generations)), next(iter(crcs))


def exact_value(field, kind):
    return f4.decode(field['rawBE']) if kind == 'F' else Fraction(field['value'])


def audit(metadata, ranges, original, factory):
    cat = catalog(metadata, ranges)
    native = worksheet.parse_full_baseline(original, cat)
    observed, generation, source_crc = factory_export(factory, cat)
    rows, changes, range_failures = [], [], []
    for a, m in sorted(cat.items()):
        old, new = native[a], observed[a]
        old_value, new_value = exact_value(old, m['type']), exact_value(new, m['type'])
        valid = m['low'] <= old_value <= m['high'] and m['low'] <= new_value <= m['high']
        if not valid:
            range_failures.append(f'{a:04x}')
        changed = old['rawBE'] != new['rawBE']
        if changed:
            changes.append(f'{a:04x}')
        rows.append({'address': f'{a:04x}', 'name': m['parameter'], 'type': m['type'],
                     'width': m['width'], 'official_unit': m['unit'],
                     'native65_rawBE': old['rawBE'], 'factory_rawBE': new['rawBE'],
                     'native65_exact': str(old_value), 'factory_exact': str(new_value),
                     'minimum_exact': str(m['low']), 'maximum_exact': str(m['high']),
                     'both_in_official_range': valid, 'changed': changed})
    findings = [
        {'fact': 'The complete public131 historical images differ only at the listed byte addresses.',
         'changed_addresses': changes, 'unchanged_fields': 131-len(changes)},
        {'fact': 'Factory export is historical, not a current-boot hardware observation.',
         'persisted_generation': generation, 'source_crc': source_crc,
         'fresh_current_boot': False},
        {'fact': 'Public131 does not cover hidden firmware, OTP or other profile banks.',
         'active_profile_proven': False},
        {'fact': 'A numeric range pass alone does not validate chemistry, thresholds or physical capacity.'},
    ]
    if native.get(0x926b, {}).get('value', 0) > 65:
        findings.append({'fact': 'Native65 Near Full exceeds 65mAh.',
                         'near_full_mAh': native[0x926b]['value'],
                         'cause_proven': False, 'auto_retune': False})
    if native[0x929b]['rawBE'] == '112a' and observed[0x929b]['rawBE'] == '102a':
        findings.append({'fact': 'Gauging Configuration differs only in bit8 FCC_LIMIT.',
                         'bit_xor': '0100', 'other_bits_unchanged': True})
    return {'schema': 1, 'offline_only': True, 'device_access': False,
            'parameter_writes': False, 'ready_for_hardware': False,
            'field_count': 131, 'range_failures': range_failures,
            'changed_addresses': changes, 'findings': findings, 'fields': rows,
            'remaining_gates': ['Fresh complete double-authenticated current131 and reseal proof.',
                               'Durable access intent and exact rollback bytes without NVS exhaustion.',
                               'Current3000 baseline is not admitted by the native65-only x2 writer.',
                               'Both retained images must independently understand the exact scale recovery model.',
                               'EDV compensation, fixed learning constants and physical-unit consumers remain verified separately.',
                               'USB charging observations alone cannot prove battery discharge accuracy or resolve the plateau.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('metadata', 'ranges', 'original', 'factory', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    paths = {name: getattr(args, name) for name in ('metadata', 'ranges', 'original', 'factory')}
    payloads = {name: path.read_bytes() for name, path in paths.items()}
    report = audit(*(json.loads(payloads[name]) for name in paths))
    report['sources'] = {name: {'path': str(paths[name].resolve()),
                              'sha256': hashlib.sha256(data).hexdigest()}
                         for name, data in payloads.items()}
    worksheet.exclusive_json(args.output, report)


if __name__ == '__main__':
    main()
