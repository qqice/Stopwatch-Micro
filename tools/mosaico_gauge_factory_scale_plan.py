"""Offline worksheet ONLY: factory3000 observation -> nominal65/virtual130 candidate.

Not an executable transaction, calibration validation, or hardware authorization.
No firmware, device transport, serial, whitelist, or recovery writer is imported.
"""
import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
import zlib

import mosaico_gauge_consistency as consistency
import mosaico_gauge_scaling_plan as worksheet
import mosaico_gauge_f4 as f4

OWNED = frozenset(int(a, 16) for a in
    '9184 9188 91de 91fb 9201 920e 9210 9217 9228 922a 922c 923c 9264 926b 929d 929f 92ab'.split())
EXPECTED_TYPES = {a: ('I', 2) for a in OWNED}
EXPECTED_TYPES.update({0x9184: ('F', 4), 0x9188: ('F', 4),
                       0x91de: ('U', 1), 0x923c: ('I', 1), 0x92ab: ('U', 2)})
EXPECTED_SOURCE_CRC = '70a767be'
EXPECTED_BOOT = 296


def tokens(text):
    if not isinstance(text, str):
        raise ValueError('missing result details')
    result = {}
    for key, value in re.findall(r'(?:^|\s)([A-Za-z0-9_]+)=([^\s]+)', text):
        if key in result:
            raise ValueError('duplicate result token')
        result[key] = value
    return result


def fresh_receipt(factory, result, generation, source_crc, expected_crc):
    if (any(result.get(k) is not True for k in
            ('accepted', 'complete131', 'attempted', 'settings_restored', 'port_closed')) or
            result.get('ack_unknown') is not False or
            result.get('parameter_writes') is not False or result.get('error') is not None):
        raise ValueError('parent result not fully accepted')
    s = factory.get('source_status', {})
    expected = {'reason': 'revalidated_existing_full131_durable_sealed_no_scale_waiver',
                'receipt_schema': '1', 'receipt_key': 'f3_rev_v1', 'state': '2',
                'source_generation': '1', 'source_crc': source_crc, 'observed_crc': source_crc,
                'fields': '131', 'attempted_this_boot': '1', 'complete': '1',
                'fresh_this_boot': '1', 'closed_verified': '1',
                'immutable_backup_unchanged': '1', 'no_scale_waiver': '1',
                'no_CFG': '1', 'no_0091': '1', 'parameter_writes': '0'}
    if (any(s.get(k) != v for k, v in expected.items()) or
            source_crc != expected_crc or generation != s.get('receipt_generation') or
            not re.fullmatch('[1-9][0-9]*', s.get('receipt_generation', '')) or
            factory.get('no_active_profile_or_unit_assertion') is not True):
        raise ValueError('fresh sealed matching receipt/source required')
    rows = result.get('rows', [])
    boots = [r for r in rows if r.get('command') == 'boot']
    if len(boots) < 2 or any(r.get('status') != 'PASS' or
            tokens(r.get('details')).get('count') != str(EXPECTED_BOOT) or
            tokens(r.get('details')).get('nvs_error') != '0' for r in boots):
        raise ValueError('same accepted current-boot attestation required')
    confirmations = [r for r in rows if r.get('command') == 'gauge-factory-audit revalidate CONFIRM']
    if len(confirmations) != 1 or confirmations[0].get('status') != 'PASS' or tokens(confirmations[0].get('details')) != s:
        raise ValueError('receipt does not match accepted parent transaction')
    for i, field in enumerate(factory['fields']):
        matches = [r for r in rows if r.get('command') == f'gauge-factory-audit revalidate-field {i}']
        if len(matches) != 1 or matches[0].get('status') != 'PASS':
            raise ValueError('parent result missing complete field observation')
        observed = tokens(matches[0].get('details'))
        if any(observed.get(k) != v for k, v in field['metadata'].items()):
            raise ValueError('parent/export field disagreement')
    return s


def canonical(image, cat):
    return b''.join(a.to_bytes(2, 'little') + bytes([cat[a]['width']]) +
                    bytes.fromhex(image[a]).ljust(4, b'\0') for a in sorted(cat))


def image_summary(image, cat):
    raw = canonical(image, cat)
    return {'encoding': 'ascending address LE16,width U8,value BE padded to4 bytes',
            'bytes': len(raw), 'crc32': f'{zlib.crc32(raw):08x}',
            'sha256': hashlib.sha256(raw).hexdigest()}


def make_plan(metadata, ranges, native_document, factory_document, parent_result,
              *, expected_source_crc=EXPECTED_SOURCE_CRC):
    cat = consistency.catalog(metadata, ranges)
    native = worksheet.parse_full_baseline(native_document, cat)
    source, generation, source_crc = consistency.factory_export(factory_document, cat)
    status = fresh_receipt(factory_document, parent_result, generation, source_crc, expected_source_crc)
    if (native[0x929b]['rawBE'] != '112a' or source[0x929b]['rawBE'] != '102a' or
            source[0x9180]['rawBE'] != 'fff0' or
            any(native[a]['value'] != 65 or source[a]['value'] != 3000 for a in (0x929d, 0x929f)) or
            native[0x92ab]['value'] != 867):
        raise ValueError('exact native65/factory3000 anchors and R0=867 required')
    # Closed B5 worksheet: the old native image is not an arbitrary threshold
    # template. All128 other fields, including calibration and R0, must exactly
    # match the authenticated factory source, not merely fit official ranges.
    reference_differences = {a for a in cat if native[a]['rawBE'] != source[a]['rawBE']}
    if reference_differences != {0x929b, 0x929d, 0x929f}:
        raise ValueError('closed B5 reference differs outside exact cfg/DC/FCC set')
    if any((cat[a]['type'], cat[a]['width']) != EXPECTED_TYPES[a] for a in OWNED):
        raise ValueError('fixed ownership type/width disagreement')
    original = {a: v['rawBE'] for a, v in source.items()}
    target = dict(original)
    for a in OWNED:
        m = cat[a]
        if a in (0x9184, 0x9188):
            target[a] = f4.scale_power_two(native[a]['rawBE'], 2,
                'gain' if a == 0x9184 else 'delta').hex()
        else:
            value = 434 if a == 0x92ab else native[a]['value'] * 2
            try:
                target[a] = value.to_bytes(m['width'], 'big', signed=m['type'] == 'I').hex()
            except OverflowError as error:
                raise ValueError(f'target raw width overflow at {a:04x}') from error
    changed = {a for a in cat if original[a] != target[a]}
    if changed != OWNED:
        raise ValueError('actual changed mask must equal fixed seventeen fields')
    fields = []
    for a, m in sorted(cat.items()):
        values = [consistency.exact_value(v, m['type']) for v in (native[a], source[a])]
        candidate = f4.decode(target[a]) if m['type'] == 'F' else Fraction(worksheet.decode(target[a], m['type'], m['width']))
        if any(not m['low'] <= value <= m['high'] for value in values + [candidate]):
            raise ValueError(f'official raw range exceeded at {a:04x}')
        fields.append({'address': f'{a:04x}', 'parameter': m['parameter'], 'type': m['type'],
                       'width': m['width'], 'official_unit': m['unit'],
                       'native65_reference_rawBE': native[a]['rawBE'],
                       'original_rawBE': original[a], 'target_rawBE': target[a],
                       'owned': a in OWNED, 'raw_range_checked': True,
                       'original_exact': str(values[1]), 'target_exact': str(candidate),
                       'minimum_exact': str(m['low']), 'maximum_exact': str(m['high'])})
    target_summary = image_summary(target, cat)
    if expected_source_crc == EXPECTED_SOURCE_CRC and target_summary['crc32'] != 'b5b0f0d6':
        raise ValueError('closed B5 target canonical CRC disagreement')
    return {'schema': 'factory3000-to-nominal65-virtual130-WORKSHEET-1',
            'offline_only': True, 'device_access': False, 'parameter_writes': False,
            'ready_for_hardware': False, 'historical_failed_waiver': False,
            'source_unit_factor': 'UNKNOWN', 'rollback_trust': 'UNTRUSTED_FACTORY3000',
            'physical_nominal_mAh': 65, 'virtual_candidate_mAh': 130,
            'gain_reference_multiplier': 2, 'whole_3000_to_130_is_x2': False,
            'source_observation_boot': EXPECTED_BOOT, 'live_current_boot_assertion': False,
            'source_generation': status['source_generation'], 'receipt_generation': generation,
            'source_crc': source_crc, 'field_count': 131, 'owned_fields': 17, 'preserved_fields': 114,
            'owned_addresses': [f'{a:04x}' for a in sorted(OWNED)],
            'original': [{'address': f'{a:04x}', 'rawBE': original[a]} for a in sorted(cat)],
            'target': [{'address': f'{a:04x}', 'rawBE': target[a]} for a in sorted(cat)],
            'canonical_original': image_summary(original, cat),
            'canonical_target': target_summary, 'fields': fields,
            'R0_quantization': {'reference': 867, 'candidate': 434,
                'relative_current_compensation_error_exact': str(Fraction(1, 867)),
                'relative_current_compensation_error_percent': str(Fraction(100, 867)),
                'assumptions': 'same FTZ, percentage and temperature terms; current doubles against native65 reference',
                'absolute_EDV_bound_proven': False},
            'remaining_gates': [
                'New typed recovery journal/profile/schema and TWO actual signed recovery images required.',
                'Factory3000 rollback remains UNTRUSTED and factor UNKNOWN; restore must NEVER publish factor1 physical units.',
                'Fixed10mAh learning constants are not invariant; complete algorithm/unit audit required.',
                'Range/hash agreement is not chemistry, real capacity, calibration, EDV or discharge validation.',
                'No runtime writer admission, oldFailed waiver, active-profile assertion or generated write commands.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('metadata', 'ranges', 'native', 'factory', 'result', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    paths = {name: getattr(args, name) for name in ('metadata', 'ranges', 'native', 'factory', 'result')}
    contents = {name: path.read_bytes() for name, path in paths.items()}
    report = make_plan(*(json.loads(contents[name]) for name in paths))
    report['sources'] = {name: {'path': str(paths[name].resolve()),
                              'sha256': hashlib.sha256(data).hexdigest()} for name, data in contents.items()}
    worksheet.exclusive_json(args.output, report)


if __name__ == '__main__':
    main()
