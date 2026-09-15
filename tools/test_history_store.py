from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from history_store import HistoryStore


def usage(lifetime: int | None, day: str = "2026-09-16", tokens: int = 7) -> dict[str, object]:
    return {"summary": {"lifetimeTokens": lifetime}, "dailyUsageBuckets": [{"startDate": day, "tokens": tokens}]}


class HistoryStoreTests(unittest.TestCase):
    def test_restart_persists_and_daily_upserts(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "private" / "history.sqlite3"
            store = HistoryStore(path, now=lambda: 1_789_700_000)
            self.assertTrue(store.record_usage(usage(10, "2026-09-01", 4), 1_789_600_000))
            self.assertTrue(store.record_usage(usage(11, "2026-09-01", 9), 1_789_600_060))
            store.close()
            restored = HistoryStore(path, now=lambda: 1_789_700_000)
            response = restored.response()
            self.assertTrue(response["available"])
            self.assertIn({"label": "2026-09-01", "tokens": 9, "quality": "official"}, response["days"])
            restored.close()

    def test_start_date_is_required_and_null_summary_keeps_daily_history(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "history.sqlite3", now=lambda: 1_789_700_000)
            self.assertTrue(store.record_usage(usage(None, "2026-09-02", 8), 1_789_600_000))
            self.assertFalse(store.record_usage({"summary": {"lifetimeTokens": None}, "dailyUsageBuckets": [{"date": "2026-09-03", "tokens": 3}]}, 1_789_600_060))
            self.assertFalse(store.record_usage(usage(None, "2026-99-99", True), 1_789_600_120))
            self.assertIn({"label": "2026-09-02", "tokens": 8, "quality": "official"}, store.response()["days"])
            store.close()

    def test_observed_zero_positive_and_baseline_not_fabricated(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            now = 1_704_110_340  # 2024-01-01 19:59 UTC+08:00
            store = HistoryStore(Path(folder) / "history.sqlite3", now=lambda: now)
            for epoch in range(now - 3540, now, 60):
                store.record_usage(usage(100), epoch)
            store.record_usage(usage(130), now)
            hours = {item["label"]: item for item in store.response()["hours"]}
            self.assertEqual(hours["2024-01-01 19:00"]["tokens"], 30)
            self.assertEqual(hours["2024-01-01 19:00"]["quality"], "observed")
            self.assertEqual(hours["2024-01-01 18:00"]["quality"], "missing")
            store.close()

    def test_negative_and_gap_are_not_presented_as_normal_usage(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            now = 1_704_110_340
            store = HistoryStore(Path(folder) / "history.sqlite3", now=lambda: now)
            store.record_usage(usage(100), now - 3700)
            store.record_usage(usage(120), now - 3500)
            store.record_usage(usage(90), now - 60)
            hours = {item["label"]: item for item in store.response()["hours"]}
            self.assertEqual(hours["2024-01-01 19:00"], {"label": "2024-01-01 19:00", "tokens": None, "quality": "correction"})
            store.close()

    def test_hour_boundary_and_long_gap_are_partial(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            now = 1_704_110_340
            store = HistoryStore(Path(folder) / "history.sqlite3", now=lambda: now)
            store.record_usage(usage(0), now - 3700)
            store.record_usage(usage(5), now - 3500)
            store.record_usage(usage(10), now - 10)
            hours = {item["label"]: item for item in store.response()["hours"]}
            self.assertEqual(hours["2024-01-01 19:00"]["quality"], "partial")
            store.close()


if __name__ == "__main__":
    unittest.main()
