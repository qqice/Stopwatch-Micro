"""Pure projection of real official weekly remaining-quota observations."""
from __future__ import annotations

from typing import Any

WEEK_MINUTES = 10080
MAX_GAP_SECONDS = 180
OFFSET_MINUTES = 480
UINT32_MAX = 0xffffffff


def weekly_snapshot(body: Any) -> tuple[int, str, int, int] | None:
    """Only the normalized dashboard's first bucket, never a model fallback."""
    if not isinstance(body, dict):
        return None
    epoch, buckets = body.get("captured_epoch"), body.get("buckets")
    if type(epoch) is not int or not 0 < epoch <= UINT32_MAX or not isinstance(buckets, list) or not buckets:
        return None
    bucket = buckets[0]
    if not isinstance(bucket, dict):
        return None
    ident, windows = bucket.get("id"), bucket.get("windows")
    if (ident != "codex" or not isinstance(ident, str) or not 1 <= len(ident) <= 63 or
            any(not 32 <= ord(c) <= 126 for c in ident) or not isinstance(windows, list)):
        return None
    for window in windows[:2]:
        if not isinstance(window, dict) or type(window.get("duration_minutes")) is not int or window["duration_minutes"] != WEEK_MINUTES:
            continue
        bp, reset = window.get("remaining_bp"), window.get("reset_epoch")
        if type(bp) is int and 0 <= bp <= 10000 and type(reset) is int and 0 <= reset <= UINT32_MAX:
            return epoch, ident, bp, reset
    return None


def project(rows: list[tuple[int, str, int, int]], now: int, age: int = 0) -> dict[str, Any]:
    """25/8 rolling clipped buckets, each retaining its latest real capture."""
    eligible = [row for row in rows if row[0] <= now]
    newest = eligible[-1] if eligible else None
    target = newest[1] if newest else ""
    marked = []
    previous = None
    for row in eligible:
        epoch, ident, bp, reset = row
        if ident == target:
            same = previous is not None and previous[1] == ident
            reset_before = same and previous[3] != reset
            break_before = previous is None or not same or epoch - previous[0] > MAX_GAP_SECONDS or reset_before
            marked.append((epoch, bp, bool(break_before), bool(reset_before)))
        previous = row

    def slots(span: int, step: int, count: int) -> list[dict[str, Any]]:
        start = now - span
        offset = OFFSET_MINUTES * 60
        anchor = ((now + offset) // step) * step - offset
        first = anchor - (count - 1) * step
        result = [{"epoch": max(start, first + i * step), "remaining_bp": None,
                   "break_before": False, "reset_before": False} for i in range(count)]
        for epoch, bp, broken, reset in marked:
            if not start <= epoch <= now:
                continue
            index = ((epoch + offset) // step) - ((first + offset) // step)
            if 0 <= index < count:
                point = result[index]
                point.update(epoch=epoch, remaining_bp=bp,
                             break_before=point["break_before"] or broken,
                             reset_before=point["reset_before"] or reset)
        return result

    hours, days = slots(86400, 3600, 25), slots(604800, 86400, 8)
    return {"version": 1, "available": any(p["remaining_bp"] is not None for p in days),
            "captured_epoch": newest[0] if newest else 0, "age_seconds": max(0, age) if newest else 0,
            "limit_id": target, "duration_minutes": WEEK_MINUTES,
            "timezone_offset_minutes": OFFSET_MINUTES, "start_24h_epoch": now - 86400,
            "end_epoch": now, "start_7d_epoch": now - 604800,
            "hours": hours, "days": days}
