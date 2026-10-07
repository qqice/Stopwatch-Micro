import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import analyze_iotpower_capture as analyzer


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.capture = self.root / "capture"
        self.capture.mkdir()
        self.write_capture()

    def write_capture(self, current=.1, rate=10000):
        # One row per host second: intentionally NOT a 10kHz sample timeline.
        with (self.capture / "samples.csv").open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["sample_index", "receive_monotonic_s", "voltage_V",
                             "current_A", "power_W", "valid"])
            for i in range(181):
                writer.writerow([i, 1000 + i, 5, current, 5 * current, 1])
        self.summary = {"complete": True, "error": None, "samples": 181,
                        "valid_samples": 181, "invalid_samples": 0,
                        "host_elapsed_seconds": 180, "nominal_rate_Hz": rate,
                        "dll_sha256": "a" * 64,
                        "packet_loss": "unknown: no documented V1 packet counter"}
        self.save_summary()

    def save_summary(self):
        (self.capture / "summary.json").write_text(json.dumps(self.summary))

    def test_host_selection_nominal_energy_and_provenance(self):
        result = analyzer.analyze(self.capture)
        self.assertEqual(result["selection"]["samples"], 61)
        self.assertEqual(result["selection"]["start_monotonic_s"], 1120)
        self.assertEqual([w["samples"] for w in result["ten_second_windows"]], [10]*5+[11])
        self.assertEqual(result["statistics"]["voltage_V"]["mean"], 5)
        self.assertAlmostEqual(result["statistics"]["current_A"]["p50"], .1)
        self.assertAlmostEqual(result["energy_received_samples_Wh"], 61 * .5 / 10000 / 3600)
        self.assertIsNone(result["whole_wall_time_energy_Wh"])
        self.assertEqual(result["provenance"]["dll_sha256"], "a"*64)
        self.assertIn("unknown", result["provenance"]["packet_loss"])
        self.assertIn("not charging or packet-loss proof", result["stability_heuristic"]["scope"])

    def test_unknown_rate(self):
        self.summary["nominal_rate_Hz"] = None
        self.save_summary()
        result = analyzer.analyze(self.capture)
        self.assertIsNone(result["energy_received_samples_Wh"])
        self.assertIsNone(result["observed_sample_seconds"])

    def test_two_minute_observation(self):
        path = self.capture / "samples.csv"
        lines=path.read_text().splitlines()
        path.write_text('\n'.join(lines[:122])+'\n')
        self.summary.update(samples=121,valid_samples=121,host_elapsed_seconds=120)
        self.save_summary()
        result=analyzer.analyze(self.capture,120)
        self.assertEqual(result['selection']['start_monotonic_s'],1060)
        self.assertEqual(result['selection']['samples'],61)
        with self.assertRaises(ValueError):analyzer.analyze(self.capture,180)
        with self.assertRaises(ValueError):analyzer.analyze(self.capture,60)

    def test_partial_and_error_capture_rejected(self):
        for key, value in (("complete", False), ("error", "disconnected"),
                           ("host_elapsed_seconds", 170), ("samples", 182)):
            with self.subTest(key=key):
                original = self.summary[key]
                self.summary[key] = value
                self.save_summary()
                with self.assertRaises(ValueError):
                    analyzer.analyze(self.capture)
                self.summary[key] = original

    def test_nan_and_reversed_timestamp_rejected(self):
        path = self.capture / "samples.csv"
        original = path.read_text()
        for text in (original.replace("5,0.1,0.5", "nan,0.1,0.5", 1),
                     original.replace("1,1001,", "1,999,", 1)):
            path.write_text(text)
            with self.assertRaises(ValueError):
                analyzer.analyze(self.capture)

    def test_short_receipt_span_rejected_despite_summary(self):
        path = self.capture / "samples.csv"
        path.write_text(path.read_text().replace("1180", "1178"))
        with self.assertRaises(ValueError):
            analyzer.analyze(self.capture)

    def test_memory_bound(self):
        with patch.object(analyzer, "MAX_WINDOW_SAMPLES", 60):
            with self.assertRaisesRegex(ValueError, "bounded memory"):
                analyzer.analyze(self.capture)

    def test_comparison_and_zero_baseline(self):
        baseline = analyzer.analyze(self.capture)
        self.write_capture(.08)
        trial = analyzer.analyze(self.capture)
        result = analyzer.compare(baseline, trial)
        self.assertAlmostEqual(result["percent_change_average_power"], -20)
        self.assertAlmostEqual(result["percent_change_median_current"], -20)
        self.write_capture(0)
        zero = analyzer.analyze(self.capture)
        self.assertIsNone(analyzer.compare(zero, trial)["percent_change_average_power"])
        self.assertIsNone(zero["stability_heuristic"]["within_5_percent"])

    def test_exclusive_output_and_no_input_write(self):
        output = self.root / "analysis.json"
        original = (self.capture / "samples.csv").read_bytes()
        args = ["--input", str(self.capture), "--output", str(output)]
        self.assertEqual(analyzer.main(args), 0)
        with self.assertRaises(SystemExit):
            analyzer.main(args)
        with self.assertRaises(SystemExit):
            analyzer.main(["--input", str(self.capture), "--output", str(self.capture / "analysis.json")])
        self.assertEqual((self.capture / "samples.csv").read_bytes(), original)
        self.assertFalse((self.capture / "analysis.json").exists())

    def test_percentile_interpolation(self):
        self.assertEqual(analyzer.stats([0, 10])["p10"], 1)
        self.assertEqual(analyzer.stats([0, 10])["p90"], 9)


if __name__ == "__main__":
    unittest.main()
