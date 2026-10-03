"""Bounded, read-only multi-bucket view of official App Server quota data."""
from __future__ import annotations

import copy
import math
import re
import threading
import time
import unicodedata
from typing import Any, Callable

MAX_BUCKETS = 8
MAX_AGE_SECONDS = 120
UINT32_MAX = 0xFFFFFFFF


def _text(value: Any, limit: int, *, ascii_only: bool = False) -> str:
    if not isinstance(value, str):
        return ""
    clean = "".join(c for c in value if not unicodedata.category(c).startswith("C")
                    and (not ascii_only or 32 <= ord(c) <= 126))
    return clean.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


def _integer(value: Any, maximum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if not 0 <= value <= maximum or int(value) != value:
        return None
    return int(value)


def _window(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    used = value.get("usedPercent")
    # Null/absent metadata is unknown (wire sentinel 0), not an invalid percentage.
    duration_value = value.get("windowDurationMins")
    reset_value = value.get("resetsAt")
    duration = 0 if duration_value is None else _integer(duration_value, UINT32_MAX)
    reset = 0 if reset_value is None else _integer(reset_value, UINT32_MAX)
    if (isinstance(used, bool) or not isinstance(used, (float, int))
            or (isinstance(used, float) and not math.isfinite(used))
            or not 0 <= used <= 100 or duration is None or reset is None):
        return None
    return {"remaining_bp": int(round((100 - used) * 100)),
            "duration_minutes": duration, "reset_epoch": reset}


def _credits(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not isinstance(value.get("unlimited"), bool):
        return None
    has = value.get("hasCredits")
    if not isinstance(has, bool):
        return None
    balance = value.get("balance")
    if (not has or not isinstance(balance, str) or len(balance) > 31
            or re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", balance) is None):
        balance = None
    return {"unlimited": value["unlimited"], "balance": balance}


def normalize_quota_dashboard(raw: Any, captured_epoch: int) -> dict[str, Any]:
    """Project quota metadata only; never retain raw responses or reset-credit IDs."""
    captured = _integer(captured_epoch, UINT32_MAX)
    if captured is None or captured == 0:
        raise ValueError("invalid dashboard capture epoch")
    if not isinstance(raw, dict):
        raise ValueError("invalid dashboard response")
    by_id = raw.get("rateLimitsByLimitId")
    if isinstance(by_id, dict) and by_id:
        entries = [(key, bucket) for key, bucket in by_id.items() if isinstance(bucket, dict)]
    elif by_id is None or by_id == {}:
        legacy = raw.get("rateLimits")
        entries = [("codex", legacy)] if isinstance(legacy, dict) else []
    else:
        entries = []  # Malformed nonempty map must not silently use legacy quota.
    normalized = []
    for key, bucket in entries:
        # Do not truncate identifiers: that can attach different windows to one ID.
        source_id = bucket.get("limitId")
        if not isinstance(source_id, str) or not source_id:
            source_id = key
        ident = _text(source_id, 64, ascii_only=True)
        if not ident or len(ident) > 63:
            continue
        normalized.append({"id": ident,
                           "name": _text(bucket.get("limitName"), 63, ascii_only=True) or ident,
                           "plan": _text(bucket.get("planType"), 23, ascii_only=True),
                           "reached": _text(bucket.get("rateLimitReachedType"), 47, ascii_only=True),
                           "credits": _credits(bucket.get("credits")),
                           "windows": [_window(bucket.get("primary")), _window(bucket.get("secondary"))]})
    # Reject every ambiguous ID, rather than selecting an arbitrary bucket's quota.
    id_counts: dict[str, int] = {}
    for bucket in normalized:
        id_counts[bucket["id"]] = id_counts.get(bucket["id"], 0) + 1
    normalized = [bucket for bucket in normalized if id_counts[bucket["id"]] == 1]
    normalized.sort(key=lambda b: (b["id"] != "codex", b["id"], b["name"]))
    resets = raw.get("rateLimitResetCredits")
    reset_count = _integer(resets.get("availableCount"), 65535) if isinstance(resets, dict) else None
    return {"version": 2, "available": bool(normalized), "captured_epoch": captured,
            "age_seconds": 0, "buckets": normalized[:MAX_BUCKETS],
            "total_buckets": len(normalized), "truncated": len(normalized) > MAX_BUCKETS,
            "reset_credits": reset_count}


class DashboardStore:
    """Independent cache, aged by monotonic time even across wall-clock rollback."""
    def __init__(self, *, wall_now: Callable[[], float] = time.time,
                 monotonic_now: Callable[[], float] = time.monotonic) -> None:
        self._wall_now = wall_now
        self._monotonic_now = monotonic_now
        self._body: dict[str, Any] | None = None
        self._saved_at = 0.0
        self._initial_age = 0
        self._lock = threading.Lock()

    def save(self, body: dict[str, Any]) -> None:
        with self._lock:
            self._body = copy.deepcopy(body)
            self._saved_at = self._monotonic_now()
            self._initial_age = max(0, int(self._wall_now()) - body["captured_epoch"])

    def status(self) -> tuple[dict[str, Any], bool]:
        with self._lock:
            if self._body is None:
                return {"version": 2, "available": False, "captured_epoch": None,
                        "age_seconds": None, "buckets": [], "total_buckets": 0,
                        "truncated": False, "reset_credits": None}, False
            body = copy.deepcopy(self._body)
            age = self._initial_age + max(0, int(self._monotonic_now() - self._saved_at))
        body["age_seconds"] = age
        body["available"] = body["available"] and age <= MAX_AGE_SECONDS
        return body, body["available"]
