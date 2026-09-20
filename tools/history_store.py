"""Private SQLite retention and honest aggregation for Codex usage observations."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


TIMEZONE_OFFSET_MINUTES = 480
RETENTION_DAYS = 90
UI_DAYS = 30
UI_HOURS = 24
MAX_TOKENS = (1 << 53) - 1


class HistoryStore:
    def __init__(self, path: Path, *, now=time.time, monotonic_now=time.monotonic,
                 timezone_offset_minutes: int = TIMEZONE_OFFSET_MINUTES) -> None:
        self._now = now
        self._monotonic_now = monotonic_now
        self._offset = timezone_offset_minutes
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS samples (epoch INTEGER PRIMARY KEY, lifetime_tokens INTEGER NOT NULL)"
        )
        self._db.execute("CREATE TABLE IF NOT EXISTS daily (label TEXT PRIMARY KEY, tokens INTEGER NOT NULL)")
        self._db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL)")
        self._db.execute("CREATE TABLE IF NOT EXISTS local_events (id TEXT PRIMARY KEY, epoch INTEGER NOT NULL, tokens INTEGER NOT NULL, source TEXT NOT NULL)")
        self._db.execute("CREATE INDEX IF NOT EXISTS local_events_epoch ON local_events(epoch)")
        self._db.execute("CREATE TABLE IF NOT EXISTS official_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self._db.execute("CREATE TABLE IF NOT EXISTS daily_revisions (label TEXT, observed_epoch INTEGER, tokens INTEGER, PRIMARY KEY(label,observed_epoch))")
        self._db.commit()
        self._live_capture: int | None = None
        self._live_saved_monotonic: float | None = None

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def record_usage(self, result: Any, captured_epoch: int) -> bool:
        if not isinstance(result, dict) or not isinstance(captured_epoch, int) or captured_epoch <= 0:
            return False
        summary = result.get("summary")
        lifetime = summary.get("lifetimeTokens") if isinstance(summary, dict) else None
        valid_lifetime = isinstance(lifetime, int) and not isinstance(lifetime, bool) and 0 <= lifetime <= MAX_TOKENS
        daily_rows: list[tuple[str, int]] = []
        buckets = result.get("dailyUsageBuckets")
        if isinstance(buckets, list):
            for bucket in buckets:
                if not isinstance(bucket, dict):
                    continue
                label = bucket.get("startDate")
                tokens = bucket.get("tokens", bucket.get("totalTokens"))
                if isinstance(label, str) and isinstance(tokens, int) and not isinstance(tokens, bool) and 0 <= tokens <= MAX_TOKENS:
                    try:
                        if dt.date.fromisoformat(label).isoformat() == label:
                            daily_rows.append((label, tokens))
                    except ValueError:
                        continue
        if not valid_lifetime and not daily_rows:
            return False
        fingerprint = hashlib.sha256(json.dumps([lifetime if valid_lifetime else None, sorted(daily_rows)], separators=(",", ":")).encode()).hexdigest()
        with self._lock:
            previous = self._db.execute("SELECT value FROM official_state WHERE key='fingerprint'").fetchone()
            if previous is None or previous[0] != fingerprint:
                self._db.execute("INSERT OR REPLACE INTO official_state VALUES('fingerprint',?)", (fingerprint,))
                self._db.execute("INSERT OR REPLACE INTO meta VALUES('last_change',?)", (captured_epoch,))
            for label, tokens in daily_rows:
                old = self._db.execute("SELECT tokens FROM daily WHERE label=?", (label,)).fetchone()
                if old is None or old[0] != tokens:
                    self._db.execute("INSERT OR REPLACE INTO daily_revisions VALUES(?,?,?)", (label,captured_epoch,tokens))
            self._db.execute("DELETE FROM daily_revisions WHERE observed_epoch < ?", (captured_epoch - RETENTION_DAYS * 86400,))
            if valid_lifetime:
                self._db.execute("INSERT OR REPLACE INTO samples(epoch,lifetime_tokens) VALUES(?,?)", (captured_epoch, lifetime))
            self._db.executemany("INSERT OR REPLACE INTO daily(label,tokens) VALUES(?,?)", daily_rows)
            self._db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('last_capture',?)", (captured_epoch,))
            self._db.execute("DELETE FROM samples WHERE epoch < ?", (captured_epoch - RETENTION_DAYS * 86400,))
            self._db.commit()
        self._live_capture = captured_epoch
        self._live_saved_monotonic = self._monotonic_now()
        return True

    def record_local_events(self, events: Any, source: str) -> None:
        if source not in ("windows", "mac") or not isinstance(events, list) or len(events) > 1000:
            raise ValueError("invalid local usage batch")
        now = int(self._now())
        rows = []
        for event in events:
            if not isinstance(event, dict): raise ValueError("invalid event")
            key, epoch, tokens = event.get("id"), event.get("epoch"), event.get("tokens")
            if (not isinstance(key, str) or len(key) != 64 or any(c not in "0123456789abcdef" for c in key)
                or type(epoch) is not int or not now - RETENTION_DAYS * 86400 <= epoch <= now + 300
                or type(tokens) is not int or not 0 < tokens <= MAX_TOKENS):
                raise ValueError("invalid local usage event")
            rows.append((key, epoch, tokens, source))
        with self._lock, self._db:
            self._db.executemany("INSERT OR IGNORE INTO local_events VALUES(?,?,?,?)", rows)
            self._db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", ("local_capture_" + source, now))
            self._db.execute("DELETE FROM local_events WHERE epoch < ?", (now - RETENTION_DAYS * 86400,))

    def response(self) -> dict[str, Any]:
        with self._lock:
            meta = self._db.execute("SELECT value FROM meta WHERE key='last_capture'").fetchone()
            newest = meta[0] if meta else None
            changed = self._db.execute("SELECT value FROM meta WHERE key='last_change'").fetchone()
            daily = dict(self._db.execute("SELECT label,tokens FROM daily").fetchall())
            cutoff = int(self._now()) - (UI_HOURS + 1) * 3600
            baseline = self._db.execute(
                "SELECT epoch,lifetime_tokens FROM samples WHERE epoch < ? ORDER BY epoch DESC LIMIT 1", (cutoff,)
            ).fetchone()
            samples = self._db.execute("SELECT epoch,lifetime_tokens FROM samples WHERE epoch >= ? ORDER BY epoch", (cutoff,)).fetchall()
            if baseline is not None:
                samples.insert(0, baseline)
        if newest is None:
            return {"version": 1, "available": False, "captured_epoch": 0, "age_seconds": 0,
                    "days": self._days({}, int(self._now())), "hours": self._hours([], int(self._now())),
                    "timezone_offset_minutes": self._offset,
                    "source": "official_account_api", "hourly_supported": False,
                    "last_successful_poll_epoch": None, "last_value_change_epoch": None,
                    "cache_status": "unavailable", "upstream_data_delay_seconds": None,
                    "finality": "not_provided_by_upstream"}
        now = int(self._now())
        if newest == self._live_capture and self._live_saved_monotonic is not None:
            age = max(0, int(self._monotonic_now() - self._live_saved_monotonic))
        else:
            age = max(0, now - newest)
        return {"version": 1, "available": True, "captured_epoch": newest, "age_seconds": age,
                "days": self._days(daily, now), "hours": self._hours(samples, now),
                "timezone_offset_minutes": self._offset,
                "source": "official_account_api", "hourly_supported": False,
                "last_successful_poll_epoch": newest,
                "last_value_change_epoch": changed[0] if changed else None,
                "cache_status": "fresh_poll" if age <= 180 else "stale_cache",
                "upstream_data_delay_seconds": None,
                "finality": "not_provided_by_upstream"}

    def _local(self, epoch: int) -> dt.datetime:
        return dt.datetime.fromtimestamp(epoch, tz=dt.timezone(dt.timedelta(minutes=self._offset)))

    def _days(self, daily: dict[str, int], now: int) -> list[dict[str, Any]]:
        today = self._local(now).date()
        return [
            {"label": (today - dt.timedelta(days=UI_DAYS - 1 - index)).isoformat(),
             "tokens": daily.get((today - dt.timedelta(days=UI_DAYS - 1 - index)).isoformat()),
             "quality": "official" if (today - dt.timedelta(days=UI_DAYS - 1 - index)).isoformat() in daily else "pending"}
            for index in range(UI_DAYS)
        ]

    def _hours(self, samples: list[tuple[int, int]], now: int) -> list[dict[str, Any]]:
        current_hour = self._local(now).replace(minute=0, second=0, microsecond=0)
        starts = [current_hour - dt.timedelta(hours=UI_HOURS - 1 - index) for index in range(UI_HOURS)]
        values: dict[str, int] = {}
        correction: set[str] = set()
        for (previous_epoch, previous_tokens), (epoch, tokens) in zip(samples, samples[1:]):
            if epoch <= previous_epoch:
                continue
            later = self._local(epoch).replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:00")
            delta = tokens - previous_tokens
            if delta < 0:
                correction.add(later)
            else:
                values[later] = values.get(later, 0) + delta
        # A poll timestamp is not an event timestamp. The upstream lifetime
        # counter can lag for hours then catch up in a batch. Never label its
        # derivative as tokens consumed in the hour (including false zeroes).
        result: list[dict[str, Any]] = []
        for hour in starts:
            label = hour.strftime("%Y-%m-%d %H:00")
            result.append({"label": label, "tokens": None,
                           "quality": "pending",
                           "reported_delta": None if label in correction else values.get(label),
                           "source": "delayed_account_counter"})
        return result


def unavailable_response(now: int, offset: int = TIMEZONE_OFFSET_MINUTES) -> dict[str, Any]:
    store = HistoryStore(Path(":memory:"), now=lambda: now, timezone_offset_minutes=offset)
    try:
        return store.response()
    finally:
        store.close()
