import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import analyze_iotpower_cycles as analyzer


class CycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.capture = self.root / "capture"
        self.capture.mkdir()
        self.anchors = self.root / "anchors.json"
        self.data = {"timebase": "first_receipt_relative_s", "anchors": [
            {"time_s": t, "phase": "refresh_start"} for t in (0, 300, 600, 900)]}
        self.save_anchors()
        self.rows = [(0, 1000, 5, 1, 5, 1), (1, 1299, 5, 1, 5, 1),
                     (2, 1300, 5, 2, 10, 1), (3, 1300, 5, 2, 10, 1),
                     (4, 1599, '', '', '', 0), (6, 1600, 5, 3, 15, 1),
                     (7, 1899, 5, 3, 15, 1), (8, 1900, 5, 100, 500, 1)]
        self.save_capture()

    def save_anchors(self):
        self.anchors.write_text(json.dumps(self.data))

    def save_capture(self):
        with (self.capture / 'samples.csv').open('w', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['sample_index', 'receive_monotonic_s', 'voltage_V', 'current_A', 'power_W', 'valid'])
            writer.writerows(self.rows)
        summary = {'complete': True, 'error': None, 'host_elapsed_seconds': 900,
                   'samples': len(self.rows), 'valid_samples': sum(r[-1] for r in self.rows),
                   'invalid_samples': sum(1-r[-1] for r in self.rows), 'nominal_rate_Hz': 10}
        (self.capture / 'summary.json').write_text(json.dumps(summary))

    def test_all_cycles_not_last60_energy_and_boundaries(self):
        result = analyzer.analyze(self.capture, self.anchors)
        self.assertEqual([w['valid_samples'] for w in result['cycles']], [2, 2, 2])
        overall = result['overall']
        self.assertEqual(overall['invalid_samples'], 1)
        self.assertEqual(overall['missing_sample_indices'], 1)
        self.assertAlmostEqual(overall['statistics']['current_A']['mean'], 2)
        self.assertAlmostEqual(overall['statistics']['voltage_V']['mean'], 5)
        self.assertAlmostEqual(overall['statistics']['power_W']['mean'], 10)
        self.assertAlmostEqual(overall['energy_received_samples_Wh'], 60/10/3600)
        self.assertAlmostEqual(overall['valid_received_to_nominal_expected_ratio'], .6/900)
        self.assertIsNone(overall['whole_wall_time_energy_Wh'])
        self.assertEqual(overall['maximum_receipt_gap_overlap_s'], 299)
        self.assertGreater(overall['receipt_gap_count_above_threshold'], 0)

    def test_cross_boundary_index_gap_attributed_to_subsequent_row_only(self):
        self.rows = [(0, 1000, 5, 1, 5, 1), (1, 1290, 5, 1, 5, 1),
                     (5, 1310, 5, 1, 5, 1), (6, 1600, 5, 1, 5, 1),
                     (7, 1900, 5, 1, 5, 1)]
        self.save_capture()
        result = analyzer.analyze(self.capture, self.anchors)
        self.assertEqual([w['missing_sample_indices'] for w in result['cycles']], [0, 3, 0])
        self.assertEqual(result['overall']['missing_sample_indices'], 3)
        self.assertTrue(any('subsequent received row' in c for c in result['caveats']))
        self.assertTrue(any('matching phase labels alone' in c for c in result['caveats']))

    def test_sample_weighting_not_equal_cycle_weighting(self):
        self.rows.insert(4, (4, 1301, 5, 4, 20, 1))
        # Renumber while retaining one explicit CSV omission.
        self.rows = [(i, *row[1:]) for i, row in enumerate(self.rows)]
        self.save_capture()
        result = analyzer.analyze(self.capture, self.anchors)
        self.assertEqual(result['overall']['valid_samples'], 7)
        self.assertAlmostEqual(result['overall']['statistics']['power_W']['mean'], 80/7)
        self.assertEqual(result['cycles'][1]['valid_samples'], 3)

    def test_batched_receipts_ratio_above_one_is_not_clamped(self):
        self.data['anchors'] = [{'time_s': t, 'phase': 'refresh_start'} for t in (0, 1, 2, 3)]
        self.save_anchors()
        self.rows = [(i, 1000 + i//2, 5, 1, 5, 1) for i in range(8)]
        self.save_capture()
        path = self.capture / 'summary.json'
        summary = json.loads(path.read_text())
        summary['nominal_rate_Hz'] = 1
        path.write_text(json.dumps(summary))
        result = analyzer.analyze(self.capture, self.anchors)
        self.assertEqual(result['overall']['valid_received_to_nominal_expected_ratio'], 2)
        self.assertEqual(result['overall']['missing_sample_indices'], 0)
        self.assertEqual([w['valid_samples'] for w in result['cycles']], [2, 2, 2])
        self.assertEqual(result['overall']['observed_valid_sample_seconds'], 6)
        self.assertEqual(result['overall']['duration_s'], 3)

    def test_absolute_and_relative_equivalent(self):
        relative = analyzer.analyze(self.capture, self.anchors)
        self.data['timebase'] = 'host_monotonic_s'
        for anchor in self.data['anchors']:
            anchor['time_s'] += 1000
        self.save_anchors()
        self.assertEqual(relative['overall'], analyzer.analyze(self.capture, self.anchors)['overall'])

    def test_empty_valid_window_and_unknown_rate(self):
        self.rows = [(r[0], r[1], '', '', '', 0) if 1300 <= r[1] < 1600 else r for r in self.rows]
        self.save_capture()
        path = self.capture / 'summary.json'
        summary = json.loads(path.read_text())
        summary['nominal_rate_Hz'] = None
        path.write_text(json.dumps(summary))
        result = analyzer.analyze(self.capture, self.anchors)
        self.assertIsNone(result['cycles'][1]['statistics']['power_W']['mean'])
        self.assertIsNone(result['overall']['energy_received_samples_Wh'])

    def test_invalid_anchor_contract(self):
        original = json.loads(json.dumps(self.data))
        for mutation in ('phase', 'reverse', 'few', 'nan', 'timebase', 'outside'):
            self.data = json.loads(json.dumps(original))
            if mutation == 'phase': self.data['anchors'][1]['phase'] = 'sleep'
            if mutation == 'reverse': self.data['anchors'][1]['time_s'] = 0
            if mutation == 'few': self.data['anchors'].pop()
            if mutation == 'nan': self.data['anchors'][1]['time_s'] = float('nan')
            if mutation == 'timebase': self.data['timebase'] = 'capture_start'
            if mutation == 'outside': self.data['anchors'][-1]['time_s'] = 901
            self.save_anchors()
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                analyzer.analyze(self.capture, self.anchors)

    def test_bad_rows_and_summary(self):
        original = self.rows[:]
        for row in [(1, 999, 5, 1, 5, 1), (0, 1299, 5, 1, 5, 1),
                    (1, 1299, 5, 1, 6, 1), (1, 1299, 'nan', 1, 5, 1)]:
            self.rows = original[:]
            self.rows[1] = row
            self.save_capture()
            with self.subTest(row=row), self.assertRaises(ValueError):
                analyzer.analyze(self.capture, self.anchors)
        self.rows = original
        self.save_capture()
        path = self.capture / 'summary.json'
        summary = json.loads(path.read_text())
        for key, value in [('complete', False), ('samples', 999), ('host_elapsed_seconds', 1)]:
            changed = dict(summary, **{key: value})
            path.write_text(json.dumps(changed))
            with self.subTest(key=key), self.assertRaises(ValueError):
                analyzer.analyze(self.capture, self.anchors)

    def test_five_cycles_and_changed_input(self):
        self.data['anchors'] = [{'time_s': t, 'phase': 'refresh_start'} for t in (0, 100, 200, 300, 600, 900)]
        self.save_anchors()
        self.assertEqual(len(analyzer.analyze(self.capture, self.anchors)['cycles']), 5)
        with patch.object(analyzer, 'fingerprint', side_effect=list(range(6))):
            with self.assertRaisesRegex(ValueError, 'changed'):
                analyzer.analyze(self.capture, self.anchors)

    def test_cli_new_output_only_outside_inputs(self):
        output = self.root / 'analysis.json'
        argv = ['--input', str(self.capture), '--anchors', str(self.anchors), '--output', str(output)]
        self.assertEqual(analyzer.main(argv), 0)
        with self.assertRaises(SystemExit): analyzer.main(argv)
        argv[-1] = str(self.capture / 'analysis.json')
        with self.assertRaises(SystemExit): analyzer.main(argv)
        self.assertFalse((self.capture / 'analysis.json').exists())


if __name__ == '__main__':
    unittest.main()



