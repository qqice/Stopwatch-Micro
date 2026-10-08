"""Analyze 3-5 actual consecutive refresh cycles, never nominal TWT duty cycles.

CLI: --input ABS_CAPTURE_DIR --anchors ABS_JSON --output ABS_NEW_JSON
Anchors: {"timebase":"host_monotonic_s", "anchors":[
  {"time_s":1000,"phase":"refresh_start"}, ...]}
Use 4-6 increasing same-phase boundaries, certified as actual refresh events by
caller evidence; matching phase strings alone do not verify events. Alternatively use
"first_receipt_relative_s"; zero means the first CSV receipt, NOT capture start.
Selection is [start,end). Receipt times are not sample timestamps.
"""
import argparse
import csv
import json
import math
from pathlib import Path

from analyze_iotpower_capture import fingerprint


class Sum:
    def __init__(self):
        self.value = self.correction = 0.0

    def add(self, value):
        adjusted = value - self.correction
        total = self.value + adjusted
        self.correction = (total - self.value) - adjusted
        self.value = total


class Window:
    def __init__(self, start, end):
        self.start, self.end = start, end
        self.valid = self.invalid = self.index_gaps = self.receipt_gaps = 0
        self.max_gap = 0.0
        self.first = self.last = None
        self.sums = [Sum() for _ in range(3)]

    def result(self, rate):
        duration = self.end - self.start
        observed = self.valid / rate if rate else None
        return {"start_monotonic_s": self.start, "end_monotonic_s": self.end,
                "duration_s": duration, "valid_samples": self.valid,
                "invalid_samples": self.invalid, "missing_sample_indices": self.index_gaps,
                "receipt_gap_count_above_threshold": self.receipt_gaps,
                "maximum_receipt_gap_overlap_s": self.max_gap,
                "first_selected_receipt_s": self.first, "last_selected_receipt_s": self.last,
                "statistics": {name: {"mean": total.value / self.valid if self.valid else None}
                               for name, total in zip(("voltage_V", "current_A", "power_W"), self.sums)},
                "observed_valid_sample_seconds": observed,
                "valid_received_to_nominal_expected_ratio": observed / duration if rate else None,
                "all_received_to_nominal_expected_ratio": (self.valid + self.invalid) / rate / duration if rate else None,
                "energy_received_samples_Wh": self.sums[2].value / rate / 3600 if rate else None,
                "whole_wall_time_energy_Wh": None}


def read_anchors(path):
    with path.open(encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict) or data.get("timebase") not in ("host_monotonic_s", "first_receipt_relative_s"):
        raise ValueError("anchors require an explicit supported timebase")
    anchors = data.get("anchors")
    if not isinstance(anchors, list) or not 4 <= len(anchors) <= 6:
        raise ValueError("requires 4-6 boundaries for 3-5 complete cycles")
    times, phases = [], []
    for anchor in anchors:
        if not isinstance(anchor, dict):
            raise ValueError("anchor must be an object")
        value, phase = anchor.get("time_s"), anchor.get("phase")
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not isinstance(phase, str) or not phase.strip()):
            raise ValueError("anchors require finite time_s and nonempty phase")
        times.append(value)
        phases.append(phase)
    if any(b <= a for a, b in zip(times, times[1:])) or len(set(phases)) != 1:
        raise ValueError("boundaries must increase and share the same observed cycle phase")
    if times[-1] - times[0] > 86400:
        raise ValueError("cycle selection exceeds 86400 seconds")
    return data, times


def analyze(directory, anchors_path, gap_threshold_s=1.0):
    directory, anchors_path = Path(directory), Path(anchors_path)
    if not directory.is_absolute() or not directory.is_dir() or not anchors_path.is_absolute():
        raise ValueError("input directory and anchors path must be absolute")
    if not math.isfinite(gap_threshold_s) or gap_threshold_s <= 0:
        raise ValueError("receipt gap threshold must be finite and positive")
    csv_path, summary_path = directory / "samples.csv", directory / "summary.json"
    paths = (csv_path, summary_path, anchors_path)
    before = tuple(fingerprint(path) for path in paths)
    with summary_path.open(encoding="utf-8") as stream:
        summary = json.load(stream)
    if not isinstance(summary, dict) or summary.get("complete") is not True or summary.get("error") is not None:
        raise ValueError("capture is incomplete or has an error")
    anchors, times = read_anchors(anchors_path)
    rate = summary.get("nominal_rate_Hz")
    rate = rate if isinstance(rate, (int, float)) and not isinstance(rate, bool) and math.isfinite(rate) and rate > 0 else None
    count = valid_count = invalid_count = 0
    first = previous_time = previous_index = None
    windows = None
    with csv_path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        required = {"sample_index", "receive_monotonic_s", "voltage_V", "current_A", "power_W", "valid"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("CSV is missing required columns")
        for row in reader:
            try:
                timestamp, index = float(row["receive_monotonic_s"]), int(row["sample_index"])
                if (not math.isfinite(timestamp) or index < 0 or row["valid"] not in ("0", "1")
                        or (previous_time is not None and timestamp < previous_time)
                        or (previous_index is not None and index <= previous_index)):
                    raise ValueError("invalid timestamp/index/valid flag")
                values = None
                if row["valid"] == "1":
                    values = [float(row[key]) for key in ("voltage_V", "current_A", "power_W")]
                    if not all(math.isfinite(v) for v in values) or not math.isclose(values[2], values[0] * values[1], rel_tol=1e-6, abs_tol=1e-9):
                        raise ValueError("invalid values or inconsistent power")
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid CSV row {count}: {exc}") from exc
            if first is None:
                first = timestamp
                if anchors["timebase"] == "first_receipt_relative_s":
                    times = [t + first for t in times]
                windows = [Window(a, b) for a, b in zip(times, times[1:])]
                overall = Window(times[0], times[-1])
            count += 1
            valid_count += values is not None
            invalid_count += values is None
            for window in [*windows, overall]:
                if previous_time is not None:
                    overlap = max(0.0, min(timestamp, window.end) - max(previous_time, window.start))
                    window.max_gap = max(window.max_gap, overlap)
                    if overlap and timestamp - previous_time > gap_threshold_s:
                        window.receipt_gaps += 1
                if window.start <= timestamp < window.end:
                    window.first = timestamp if window.first is None else window.first
                    window.last = timestamp
                    window.index_gaps += index - previous_index - 1 if previous_index is not None else index
                    if values is None:
                        window.invalid += 1
                    else:
                        window.valid += 1
                        for total, value in zip(window.sums, values):
                            total.add(value)
            previous_time, previous_index = timestamp, index
    if windows is None or first > times[0] or previous_time < times[-1]:
        raise ValueError("CSV receipt span does not enclose all complete cycle boundaries")
    elapsed = summary.get("host_elapsed_seconds")
    if (isinstance(elapsed, bool) or not isinstance(elapsed, (int, float))
            or not math.isfinite(elapsed) or elapsed < times[-1] - times[0]):
        raise ValueError("capture elapsed duration is shorter than selected cycles")
    if any(summary.get(key) != value for key, value in (("samples", count), ("valid_samples", valid_count), ("invalid_samples", invalid_count))):
        raise ValueError("CSV counts do not match capture summary")
    if before != tuple(fingerprint(path) for path in paths):
        raise ValueError("input changed while being analyzed")
    return {"source_directory": str(directory.resolve()), "anchors": anchors,
            "selection": {"bounds": "[start,end)", "cycle_count": len(windows),
                          "timebase": "host receipt; not device sample time", "receipt_gap_threshold_s": gap_threshold_s},
            "cycles": [w.result(rate) for w in windows], "overall": overall.result(rate),
            "provenance": {key: summary.get(key) for key in ("dll_sha256", "nominal_rate_Hz", "packet_loss", "timebase", "energy_scope", "output_enabled", "warning", "host_elapsed_seconds", "partial_batches", "host_delay_suspicions")},
            "caveats": ["Means are weighted by valid received sample count, not equal cycle weights or host receipt intervals.",
                        "Received-sample energy uses nominal source rate only; whole wall-clock energy is unknown.",
                        "Coverage ratios may exceed one due to arrival batching and are not packet-loss proof.",
                        "V1 has no documented packet counter; missing_sample_indices describes CSV index omissions only, not meter packet loss or true per-cycle lost samples.",
                        "Index gaps are attributed to the window containing the subsequent received row; a cross-boundary gap may originate in earlier windows and cannot be localized from receipts.",
                        "Receipt gaps describe host arrival only; boundary attribution is uncertain due to batching and delay.",
                        "Caller evidence must certify anchors as actual same-phase refresh boundaries; matching phase labels alone do not prove actual refresh events. Nominal TWT duty cycle cannot predict power.",
                        "Complete means boundary enclosure only, not loss-free sampling or verified physical conditions."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--anchors", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt-gap-threshold-s", type=float, default=1.0)
    args = parser.parse_args(argv)
    try:
        output = args.output.resolve()
        if not args.output.is_absolute() or not output.parent.is_dir() or output.is_relative_to(args.input.resolve()) or output == args.anchors.resolve():
            raise ValueError("output must be absolute, new, outside capture and distinct from anchors")
        result = analyze(args.input, args.anchors, args.receipt_gap_threshold_s)
        encoded = json.dumps(result, indent=2, allow_nan=False) + "\n"
        with output.open("x", encoding="utf-8") as stream:
            stream.write(encoded)
    except (ValueError, OSError, OverflowError) as exc:
        parser.exit(1, f"analysis failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

