import csv
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import iotpower_capture as capture


class FakeBackend:
    def __init__(self, events):
        self.events = iter(events)
        self.closed = threading.Event()
        self.calls = []

    def open(self, port):
        self.calls.append(("open", port))

    def initialize(self):
        self.calls.append(("initialize",))

    def read(self):
        try:
            return next(self.events)
        except StopIteration:
            self.closed.wait()
            return 255, None

    def close(self):
        self.calls.append(("close",))
        self.closed.set()


class RecorderTests(unittest.TestCase):
    def test_batch16_units_and_rectangular_energy(self):
        stream = io.StringIO()
        recorder = capture.Recorder(stream)
        recorder.batch(10, [(5, 200)] * 16)
        recorder.batch(11, [(5, 200)] * 16)
        summary = recorder.summary()
        self.assertAlmostEqual(summary["average_current_A"], 0.2)
        self.assertEqual(summary["average_power_W"], 1)
        self.assertAlmostEqual(summary["energy_received_samples_Wh"], 32/10000/3600)
        self.assertEqual(summary["observed_sample_seconds"], 32/10000)
        self.assertEqual(summary["host_delay_suspicions"], 1)
        self.assertEqual(summary["peak_current_A"], 0.2)
        rows = list(csv.DictReader(io.StringIO(stream.getvalue())))
        self.assertEqual(len(rows), 32)
        self.assertEqual(rows[-1]["sample_index"], "31")
        self.assertEqual(rows[-1]["nominal_in_batch_s"], "0.0015")

    def test_invalid_partial_and_no_gap_energy_fabrication(self):
        recorder = capture.Recorder(io.StringIO())
        recorder.batch(0, [(5, 1000), (float("nan"), 1000), (5, float("inf"))])
        recorder.batch(100, [(5, 1000)])
        summary = recorder.summary()
        self.assertEqual(summary["invalid_samples"], 2)
        self.assertEqual(summary["partial_batches"], 2)
        self.assertAlmostEqual(summary["energy_received_samples_Wh"], 10/10000/3600)
        json.dumps(summary, allow_nan=False)

    def test_validation(self):
        recorder = capture.Recorder(io.StringIO())
        for receive, batch in [(float("nan"), [(1, 1)]), (0, []), (0, [(1, 1)]*17)]:
            with self.assertRaises(ValueError):
                recorder.batch(receive, batch)
        recorder.batch(10, [(1, 1)])
        with self.assertRaises(ValueError):
            recorder.batch(9, [(1, 1)])


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "private"

    def test_capture_and_cleanup(self):
        backend = FakeBackend([(1, [(5, 900)]*16),
                               (4, {"output_enabled": False}),
                               (1, [(5, 200)]*16)])
        result = capture.capture(backend, "COM21", 0.04, self.output)
        self.assertTrue(result["complete"])
        self.assertEqual(result["samples"], 16)  # pre-init data never trusted
        self.assertFalse(result["device_status"]["output_enabled"])
        self.assertIsNone(result["whole_wall_time_energy_Wh"])
        self.assertTrue(backend.closed.is_set())
        self.assertEqual(backend.calls, [("open", "COM21"), ("initialize",), ("close",)])
        with self.assertRaises(FileExistsError):
            capture.capture(backend, "COM21", 0.01, self.output)

    def test_disconnected_partial_artifacts_and_cleanup(self):
        backend = FakeBackend([(4, {"output_enabled": False}), (255, None)])
        with self.assertRaisesRegex(RuntimeError, "disconnected"):
            capture.capture(backend, "COM21", 0.05, self.output)
        summary = json.loads((self.output / "summary.json").read_text())
        self.assertFalse(summary["complete"])
        self.assertTrue(backend.closed.is_set())

    def test_no_status_and_bad_duration(self):
        backend = FakeBackend([])
        with self.assertRaisesRegex(RuntimeError, "initialization"):
            capture.capture(backend, "COM21", 0.01, self.output)
        self.assertTrue(backend.closed.is_set())
        with self.assertRaises(ValueError):
            capture.capture(backend, "COM21", float("inf"), self.output)

    def test_loader_refuses_unverified_path_before_execution(self):
        with patch.object(capture.ctypes, "CDLL") as loader:
            with self.assertRaises(ValueError):
                capture.NativeBackend("iot_parser.dll", "0"*64)
            loader.assert_not_called()

    def test_enabled_output_refused_without_settings_write(self):
        backend = FakeBackend([(4, {"output_enabled": True})])
        with self.assertRaisesRegex(RuntimeError, "output is enabled"):
            capture.capture(backend, "COM21", 0.02, self.output)
        self.assertTrue(backend.closed.is_set())
        self.assertEqual(backend.calls, [("open", "COM21"), ("initialize",), ("close",)])

    def test_enabled_output_explicitly_allowed_read_only(self):
        backend = FakeBackend([(4, {"output_enabled": True}),
                               (1, [(5, 200)]*16)])
        result = capture.capture(backend, "COM21", 0.03, self.output,
                                 allow_enabled_output=True)
        self.assertTrue(result["complete"])
        self.assertTrue(result["output_enabled"])
        self.assertTrue(result["allow_enabled_output"])
        self.assertNotIn("output-disabled", result["warning"])
        self.assertIn("verified separately", result["warning"])
        self.assertEqual(backend.calls, [("open", "COM21"), ("initialize",), ("close",)])

    def test_wrong_hash_never_loads_native_library(self):
        dll = Path(self.temp.name) / "fake.dll"
        image = bytearray(256)
        image[:2] = b"MZ"
        image[0x3C:0x40] = (128).to_bytes(4, "little")
        image[128:134] = b"PE\0\0\x64\x86"
        dll.write_bytes(image)
        with patch.object(capture.ctypes, "CDLL") as loader:
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                capture.NativeBackend(dll, "0"*64)
            loader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
