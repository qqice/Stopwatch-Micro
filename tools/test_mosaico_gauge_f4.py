"""Portable offline tests: no private fixtures, SDK build, or serial access."""
from fractions import Fraction
import struct
import unittest

import mosaico_gauge_f4 as f


class F4Tests(unittest.TestCase):
    def test_primary_reference_vectors_display_rounding(self):
        self.assertAlmostEqual(float(f.decode('7f306c4d')), 0.3445762694, places=9)
        self.assertAlmostEqual(float(f.decode('7c0d2371')), 0.03445762694, delta=2e-9)

    def test_known_nonsecret_gain_exact(self):
        self.assertEqual(f.decode('7e73b645'), Fraction(15971909, 67108864))
        self.assertEqual(float(f.decode('7e73b645')), 0.23799999058246613)

    def test_synthetic_sign_extremes_and_zero_pattern(self):
        self.assertEqual(f.decode('80000000'), Fraction(1, 2))
        self.assertEqual(f.decode('80800000'), Fraction(-1, 2))
        self.assertEqual(f.decode('807fffff'), Fraction(16777215, 16777216))
        self.assertEqual(f.decode('00000000'), Fraction(1, 2 ** 129))
        self.assertEqual(f.decode('ff000000'), Fraction(2 ** 126))
        self.assertEqual(f.decode('00800000'), -Fraction(1, 2 ** 129))

    def test_not_ieee754_or_little_endian(self):
        raw = bytes.fromhex('7f000000')
        self.assertEqual(f.decode(raw), Fraction(1, 4))
        self.assertNotEqual(float(f.decode(raw)), struct.unpack('>f', raw)[0])
        self.assertNotEqual(f.decode(raw), f.decode(raw[::-1]))

    def test_exact_scaling_inverse_decode_and_dual_gain_ratio(self):
        gain, delta = bytes.fromhex('7f123456'), bytes.fromhex('93010203')
        for factor in (1, 2, 4):
            with self.subTest(factor=factor):
                cg = f.scale_power_two(gain, factor, 'gain')
                cd = f.scale_power_two(delta, factor, 'delta')
                self.assertEqual(cg[1:], gain[1:])
                self.assertEqual(cd[1:], delta[1:])
                self.assertEqual(f.decode(cg), factor * f.decode(gain))
                self.assertEqual(f.decode(cd), factor * f.decode(delta))
                self.assertEqual(f.decode(cg) / factor, f.decode(gain))
                self.assertEqual(f.decode(cd) / f.decode(cg), f.decode(delta) / f.decode(gain))

    def test_bad_raw_factor_and_parameter(self):
        for raw in ('', '00', '0000000000', 'gg000000', ' 7f000000', '7f 000000',
                    b'123', b'12345', None, 4, [127, 0, 0, 0]):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                f.decode(raw)
        for factor in (True, False, 0, -1, 3, 8, 2.0, '2', None):
            with self.subTest(factor=factor), self.assertRaises(ValueError):
                f.scale_power_two('7f000000', factor, 'gain')
        for parameter in ('other', None, []):
            with self.assertRaises(ValueError):
                f.scale_power_two('7f000000', 1, parameter)

    def test_calibration_domain_and_candidate_boundaries(self):
        # Exact synthetic powers of two exercise both input and output gates.
        self.assertEqual(f.validate_calibration('83000000', 'gain'), 4)
        self.assertEqual(f.validate_calibration('90000000', 'delta'), 32768)
        self.assertEqual(f.validate_calibration('96000000', 'delta'), 2097152)
        for raw, parameter in (('00000000', 'gain'), ('7f800000', 'gain'),
                               ('7c000000', 'gain'), ('84000000', 'gain'),
                               ('8f000000', 'delta'), ('97000000', 'delta')):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                f.scale_power_two(raw, 1, parameter)
        for raw, parameter in (('83000000', 'gain'), ('96000000', 'delta')):
            with self.assertRaisesRegex(ValueError, 'outside positive calibration range'):
                f.scale_power_two(raw, 2, parameter)
        with self.assertRaisesRegex(ValueError, 'exponent overflow'):
            f.scale_power_two('ff000000', 2, 'gain')


if __name__ == '__main__':
    unittest.main()
