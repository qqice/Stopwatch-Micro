"""Single read-only COM8 owner for actual firmware refresh anchors.
Run alongside existing iotpower_capture.py (different measurement COM port).
Never sends a command, alters settings, starts TWT, or fabricates expiry cycles.
Receipt monotonic times match analyze_iotpower_cycles.py; no wall-energy claim.
"""
import argparse
import json
import re
import time
from pathlib import Path

PATTERN = re.compile(r"DBG TWT_CYCLE phase=(\d+) device_us=(\d+) trial_id=(\d+) cycle_seq=(\d+) associated=([01]) fetch_flags=(\d+) reason=(\d+) dropped=(\d+)")
KEYS = ("phase", "device_us", "trial_id", "cycle_seq", "associated", "fetch_flags", "reason", "dropped")


def parse_event(line, receipt):
    match = PATTERN.fullmatch(line.strip())
    if not match:
        return None
    event = dict(zip(KEYS, map(int, match.groups())))
    event["receive_monotonic_s"] = receipt
    return event


def select_anchors(events, cycles, trial_id, required_fetch_flags=15):
    # Legacy quota+history remains the default. Quota-only callers explicitly
    # opt in; unexpected history/unknown bits must not be reinterpreted as QoS.
    if type(required_fetch_flags) is not int or required_fetch_flags not in (3, 15):
        raise ValueError("required_fetch_flags must be 3 or 15")
    starts = [e for e in events if e["trial_id"] == trial_id and e["phase"] == 0]
    if len(starts) != cycles + 1:
        raise ValueError("requires exactly cycles+1 real refresh starts")
    selected = [e for e in events if e["trial_id"] == trial_id and starts[0]["cycle_seq"] <= e["cycle_seq"] <= starts[-1]["cycle_seq"]]
    if required_fetch_flags == 3 and any(e["fetch_flags"] & ~3 for e in selected):
        raise ValueError("quota-only contract contains history or unknown fetch bits")
    if any(e["dropped"] for e in selected):
        raise ValueError("firmware cycle ring dropped evidence")
    if trial_id and any(not e["associated"] for e in selected):
        raise ValueError("associated arm lost association")
    fetch_qos_ok = True
    for a, b in zip(starts, starts[1:]):
        if b["cycle_seq"] != a["cycle_seq"] + 1 or not 290_000_000 <= b["device_us"] - a["device_us"] <= 310_000_000:
            raise ValueError("not consecutive actual five-minute refresh starts")
        ends = [e for e in selected if e["cycle_seq"] == a["cycle_seq"] and e["phase"] == 2]
        fetches = [e for e in selected if e["cycle_seq"] == a["cycle_seq"] and e["phase"] == 1]
        if len(ends) != 1 or not a["device_us"] <= ends[0]["device_us"] < b["device_us"]:
            raise ValueError("missing or invalid actual window end")
        if ends[0]["reason"] in (3, 6):
            raise ValueError("refresh cancelled by wake/trial cancellation")
        # Deadline/retry/no-candidate are structural endings, never QoS success.
        fetch_qos_ok = fetch_qos_ok and ends[0]["reason"] == 1 and any(
            e["fetch_flags"] == required_fetch_flags and a["device_us"] <= e["device_us"] <= ends[0]["device_us"]
            for e in fetches)
    return {"timebase": "host_monotonic_s", "anchors": [
        {"time_s": e["receive_monotonic_s"], "phase": "refresh_start", "device_us": e["device_us"],
         "trial_id": trial_id, "cycle_seq": e["cycle_seq"]} for e in starts],
        "actual_events": selected, "receipt_not_sample_timestamp": True,
        "structural_window_coverage_complete": True,
        "cycle_fetch_qos_ok": fetch_qos_ok,
        "required_fetch_flags": required_fetch_flags,
        "cycle_events_associated": all(e["associated"] for e in selected),
        "qos_passed": None if fetch_qos_ok else False,
        "trial_valid": False,  # No state/continuous-association evidence in this reader.
        "requires_state_evidence": ["same_trial_id_and_mode_throughout",
            "Baseline_or_Active_stage_throughout", "continuous_association_no_losses",
            "cleanup_before_and_after_success_no_pending_or_failed"],
        "whole_wall_time_energy_Wh": None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM8", choices=["COM8"])
    parser.add_argument("--cycles", type=int, default=4, choices=[3, 4, 5])
    parser.add_argument("--trial-id", type=int, required=True, help="0 for observe normal-radio-off; explicit firmware ID for each arm")
    parser.add_argument("--timeout-s", type=float, default=1800)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.output.is_absolute() or args.output.exists() or args.trial_id < 0 or not 1 <= args.timeout_s <= 2100:
        parser.error("requires new absolute output, nonnegative ID, timeout 1..2100s")
    import serial
    events = []
    deadline = time.monotonic() + args.timeout_s
    error = None
    try:
        # Windows pyserial CreateFile uses exclusive access; no competing COM8 reader.
        with serial.Serial(args.port, 115200, timeout=1) as port:
            while time.monotonic() < deadline:
                line = port.readline(512).decode("ascii", errors="replace")
                event = parse_event(line, time.monotonic())
                if event is None:
                    continue
                events.append(event)
                starts = sum(e["phase"] == 0 and e["trial_id"] == args.trial_id for e in events)
                if starts == args.cycles + 1:
                    break
        result = select_anchors(events, args.cycles, args.trial_id)
        result["complete"] = True
    except Exception as exc:
        error = str(exc)
        result = {"complete": False, "structural_window_coverage_complete": False, "qos_passed": False, "trial_valid": False, "error": error, "actual_events": events, "anchors": [], "timebase": "host_monotonic_s"}
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    if error:
        raise SystemExit(error)


if __name__ == "__main__":
    main()
