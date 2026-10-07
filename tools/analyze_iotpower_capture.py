"""Read-only, bounded analysis of the last 60 host-receipt seconds of a capture.

Receipt timestamps select rows only; they are NOT sample timestamps or an energy
timebase. No V1 packet counter exists: this cannot certify loss-free acquisition,
charging state, wiring, or matching CPU/brightness/radio conditions.
"""

import argparse
from array import array
import csv
import json
import math
from pathlib import Path

MAX_WINDOW_SAMPLES = 650000  # ~600k nominal samples, with bounded arrival headroom


def rows(path):
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        required = {"sample_index", "receive_monotonic_s", "voltage_V",
                    "current_A", "power_W", "valid"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("CSV is missing required columns")
        previous = None
        for index, row in enumerate(reader):
            try:
                timestamp = float(row["receive_monotonic_s"])
                values = tuple(float(row[key]) for key in
                               ("voltage_V", "current_A", "power_W"))
                if (int(row["sample_index"]) != index or row["valid"] != "1"
                        or not all(math.isfinite(v) for v in (timestamp, *values))
                        or (previous is not None and timestamp < previous)):
                    raise ValueError("invalid sample/index/timestamp")
                if not math.isclose(values[2], values[0] * values[1],
                                    rel_tol=1e-6, abs_tol=1e-9):
                    raise ValueError("power does not match voltage * current")
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid CSV sample {index}: {exc}") from exc
            previous = timestamp
            yield timestamp, values


def stats(values):
    if not values:
        raise ValueError("empty receipt window")
    ordered = sorted(values)

    def percentile(fraction):
        position = (len(ordered) - 1) * fraction
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)

    return {"mean": math.fsum(values) / len(values),
            "p10": percentile(.1), "p50": percentile(.5),
            "p90": percentile(.9), "peak": ordered[-1]}


def fingerprint(path):
    info = path.stat()
    return info.st_size, info.st_mtime_ns


def analyze(directory):
    directory = Path(directory)
    if not directory.is_absolute() or not directory.is_dir():
        raise ValueError("input must be an existing absolute capture directory")
    directory = directory.resolve()
    csv_path, summary_path = directory / "samples.csv", directory / "summary.json"
    before = (fingerprint(csv_path), fingerprint(summary_path))
    with summary_path.open(encoding="utf-8") as source:
        summary = json.load(source)
    if not isinstance(summary, dict):
        raise ValueError("capture summary must be a JSON object")
    if summary.get("complete") is not True or summary.get("error") is not None:
        raise ValueError("capture is incomplete or has an error")
    elapsed = summary.get("host_elapsed_seconds")
    if (not isinstance(elapsed, (int, float)) or isinstance(elapsed, bool)
            or not math.isfinite(elapsed) or elapsed < 179):
        raise ValueError("requires a completed three-minute capture (1s tolerance)")
    count, first, last = 0, None, None
    for timestamp, _ in rows(csv_path):
        first = timestamp if first is None else first
        last = timestamp
        count += 1
    if not count or last - first < 179:
        raise ValueError("receipt coverage is shorter than three minutes (1s tolerance)")
    if (summary.get("samples") != count or summary.get("valid_samples") != count
            or summary.get("invalid_samples") != 0):
        raise ValueError("CSV counts do not match capture summary or invalid data exists")
    start = last - 60
    metrics = [array("d") for _ in range(3)]
    bin_counts = [0] * 6
    selected = 0
    for timestamp, values in rows(csv_path):
        if timestamp < start:
            continue
        selected += 1
        if selected > MAX_WINDOW_SAMPLES:
            raise ValueError("last-minute sample count exceeds bounded memory limit")
        slot = min(5, int((timestamp - start) / 10))
        bin_counts[slot] += 1
        for metric, value in zip(metrics, values):
            metric.append(value)
    if before != (fingerprint(csv_path), fingerprint(summary_path)):
        raise ValueError("input changed while being analyzed")
    names = ("voltage_V", "current_A", "power_W")
    aggregate = {name: stats(values) for name, values in zip(names, metrics)}
    windows, offset = [], 0
    for i, size in enumerate(bin_counts):
        windows.append({"start_offset_s": i * 10, "end_offset_s": (i + 1) * 10,
                        "samples": size,
                        **{name: stats(values[offset:offset + size])
                           for name, values in zip(names, metrics)}})
        offset += size
    power_means = [window["power_W"]["mean"] for window in windows]
    denominator = abs(aggregate["power_W"]["mean"])
    spread = ((max(power_means) - min(power_means)) / denominator
              if denominator else None)
    rate = summary.get("nominal_rate_Hz")
    known_rate = (isinstance(rate, (int, float)) and not isinstance(rate, bool)
                  and math.isfinite(rate) and rate > 0)
    provenance_keys = ("dll_sha256", "nominal_rate_Hz", "timebase", "energy_scope",
                       "packet_loss", "output_enabled", "warning", "port",
                       "host_elapsed_seconds", "received_to_host_expected_ratio",
                       "partial_batches", "host_delay_suspicions")
    return {"source_directory": str(directory), "capture_complete": True,
            "provenance": {key: summary.get(key) for key in provenance_keys},
            "selection": {"timebase": "host receipt seconds, not sample seconds",
                          "start_monotonic_s": start, "end_monotonic_s": last,
                          "bounds": "[start,end]; internal 10s bins left-inclusive",
                          "samples": selected, "receipt_span_s": last - first},
            "statistics": aggregate, "ten_second_windows": windows,
            "stability_heuristic": {"power_mean_relative_range": spread,
                                    "within_5_percent": spread <= .05 if spread is not None else None,
                                    "scope": "descriptive heuristic, not charging or packet-loss proof"},
            "energy_received_samples_Wh": math.fsum(metrics[2]) / rate / 3600 if known_rate else None,
            "observed_sample_seconds": selected / rate if known_rate else None,
            "whole_wall_time_energy_Wh": None,
            "caveats": ["Energy covers received samples only at the source nominal rate; unknown rate means unknown energy.",
                        "V1 packet loss is unknown; receipt density is not packet-loss proof.",
                        "First two minutes are excluded by last-minute receipt selection, not charging-state verification.",
                        "Comparison requires independently matched 5V wiring/load, brightness 30, radio frequency/activity, CPU setting and charging state."]}


def compare(baseline, trial):
    def change(old, new):
        return (new - old) / abs(old) * 100 if old else None
    return {"baseline": baseline, "trial": trial,
            "percent_change_average_power": change(baseline["statistics"]["power_W"]["mean"],
                                                   trial["statistics"]["power_W"]["mean"]),
            "percent_change_median_current": change(baseline["statistics"]["current_A"]["p50"],
                                                     trial["statistics"]["current_A"]["p50"]),
            "caveat": "Descriptive only; physical conditions must match independently. Zero baseline yields null change."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="trial absolute capture directory")
    parser.add_argument("--baseline", type=Path, help="optional absolute baseline capture directory")
    parser.add_argument("--output", type=Path, required=True, help="new JSON file under an existing parent, outside inputs")
    args = parser.parse_args(argv)
    try:
        output = args.output.resolve()
        if not args.output.is_absolute() or not output.parent.is_dir():
            raise ValueError("output must be absolute with an existing parent")
        for source in (args.input, args.baseline):
            if source is not None and output.is_relative_to(source.resolve()):
                raise ValueError("output must be outside input directories")
        trial = analyze(args.input)
        result = compare(analyze(args.baseline), trial) if args.baseline else trial
        # Serialize before creating a file, rejecting NaN/overflow without partial output.
        encoded = json.dumps(result, indent=2, allow_nan=False) + "\n"
        with output.open("x", encoding="utf-8") as destination:
            destination.write(encoded)
    except (ValueError, OSError, OverflowError) as exc:
        parser.exit(1, f"analysis failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
