"""Offline Xemics F4 decoding and bounded power-of-two calibration candidates.

No device access, arbitrary decimal encoder, or IEEE-754 interpretation.
See docs/mosaico-gauge-f4.md for evidence and remaining hardware boundaries.
"""
from fractions import Fraction
import re


CALIBRATION_RANGES = {'gain': (Fraction(1, 10), Fraction(4)),
                      'delta': (Fraction(30000), Fraction(3000000))}


def raw_bytes(raw):
    """Accept exactly four bytes or eight hexadecimal digits, in storage order."""
    if isinstance(raw, str):
        if not re.fullmatch(r'[0-9a-fA-F]{8}', raw):
            raise ValueError('F4 requires exactly eight hexadecimal digits')
        raw = bytes.fromhex(raw)
    if not isinstance(raw, (bytes, bytearray)) or len(raw) != 4:
        raise ValueError('F4 requires exactly four bytes')
    return bytes(raw)


def decode(raw):
    """Return an exact Fraction; all-zero bytes are NOT an IEEE zero."""
    data = raw_bytes(raw)
    sign = -1 if data[1] & 0x80 else 1
    mantissa = 0x800000 | (int.from_bytes(data[1:], 'big') & 0x7fffff)
    exponent = data[0] - 128 - 24
    return sign * mantissa * Fraction(2) ** exponent


def validate_calibration(raw, parameter):
    """Restrict candidates to the reviewed BQ27220 CC calibration domain."""
    if not isinstance(parameter, str) or parameter not in CALIBRATION_RANGES:
        raise ValueError('parameter must be gain or delta')
    value = decode(raw)
    low, high = CALIBRATION_RANGES[parameter]
    if not low <= value <= high:
        raise ValueError(f'{parameter} outside positive calibration range {low}..{high}')
    return value


def scale_power_two(raw, factor, parameter):
    """Return bytes for an exact x1/x2/x4 candidate, NOT a device write.

    Both input and output must be in range. Only the exponent byte changes;
    the sign and fraction bytes remain bit-for-bit unchanged.
    """
    if type(factor) is not int or factor not in (1, 2, 4):
        raise ValueError('factor must be integer 1, 2 or 4 (not bool)')
    data = raw_bytes(raw)
    exponent = data[0] + {1: 0, 2: 1, 4: 2}[factor]
    if exponent > 255:
        raise ValueError('F4 exponent overflow')
    validate_calibration(data, parameter)
    candidate = bytes([exponent]) + data[1:]
    validate_calibration(candidate, parameter)
    return candidate
