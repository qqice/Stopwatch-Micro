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
            self.assertEqual(hours["2024-01-01 19:00"]["reported_delta"], 30)
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
            self.assertIsNone(hours["2024-01-01 19:00"]["tokens"])
            self.assertEqual(hours["2024-01-01 19:00"]["reported_delta"], -10)
            self.assertEqual(hours["2024-01-01 19:00"]["correction_delta"], -30)
            self.assertEqual(hours["2024-01-01 19:00"]["quality"], "correction")
            store.close()

    def test_hour_boundary_and_long_gap_are_partial(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            now = 1_704_110_340
            store = HistoryStore(Path(folder) / "history.sqlite3", now=lambda: now)
            store.record_usage(usage(0), now - 3700)
            store.record_usage(usage(5), now - 3500)
            store.record_usage(usage(10), now - 10)
            hours = {item["label"]: item for item in store.response()["hours"]}
            self.assertEqual(hours["2024-01-01 19:00"]["quality"], "gap")
            store.close()

    def test_delayed_backfill_correction_and_missing_buckets(self):
        with tempfile.TemporaryDirectory() as folder:
            now = 1789897440
            store = HistoryStore(Path(folder) / 'h.db', now=lambda: now)
            store.record_usage(usage(100, '2026-09-19', 10), now - 120)
            store.record_usage(usage(100, '2026-09-19', 10), now - 60)
            self.assertEqual(store.response()['last_value_change_epoch'], now - 120)
            self.assertEqual(store.response()['last_successful_poll_epoch'], now - 60)
            store.record_usage(usage(1000, '2026-09-19', 900), now)
            store.record_usage(usage(999, '2026-09-19', 899), now + 60)
            store.record_usage({'summary': {'lifetimeTokens': 999}}, now + 120)
            self.assertFalse(store.record_usage({}, now + 180))
            response = store.response()
            self.assertIn({'label': '2026-09-19', 'tokens': 899, 'quality': 'official'}, response['days'])
            self.assertIsNone(response['days'][-1]['tokens'])
            self.assertIsNone(response['upstream_data_delay_seconds'])
            self.assertEqual(store._db.execute('select count(*) from daily_revisions').fetchone()[0], 3)
            store.close()

    def test_local_heartbeat_does_not_refresh_official_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'h.db'
            store = HistoryStore(path, now=lambda: 2000)
            store.record_usage({'summary': {'lifetimeTokens': 10}}, 1000)
            store.close()
            store = HistoryStore(path, now=lambda: 2000)
            store.record_local_events([], 'mac')
            r = store.response()
            self.assertEqual(r['captured_epoch'], 1000)
            self.assertEqual(r['age_seconds'], 1000)
            self.assertEqual(r['cache_status'], 'stale_cache')
            store.close()

    def test_hourly_observations_timezone_boundaries_and_daily_labels(self):
        import datetime as dt
        epoch = int(dt.datetime(2024, 1, 1, 16, tzinfo=dt.timezone.utc).timestamp())
        with tempfile.TemporaryDirectory() as folder:
            for offset, label in ((480, '2024-01-02 00:00'), (0, '2024-01-01 16:00'), (-300, '2024-01-01 11:00')):
                store = HistoryStore(Path(folder) / str(offset), now=lambda: epoch + 60,
                                     timezone_offset_minutes=offset)
                for t, total in ((epoch - 60, 100), (epoch, 150), (epoch + 60, 180)):
                    store.record_usage(usage(total, '2024-01-01', 123), t)
                r = store.response()
                hour = next(x for x in r['hours'] if x['label'] == label)
                self.assertEqual(hour['tokens'], 80)
                self.assertEqual(hour['start_epoch'], epoch)
                self.assertIn({'label': '2024-01-01', 'tokens': 123, 'quality': 'official'}, r['days'])
                self.assertIsNone(r['daily_timezone'])
                self.assertFalse(r['hourly_actual_consumption_supported'])
                store.close()

    def test_utc_midnight_is_beijing_eight_and_delayed_jump_stays_at_arrival(self):
        import datetime as dt
        midnight = int(dt.datetime(2024, 1, 2, tzinfo=dt.timezone.utc).timestamp())
        with tempfile.TemporaryDirectory() as folder:
            s = HistoryStore(Path(folder)/'h.db', now=lambda: midnight + 60)
            for epoch in range(midnight - 3600, midnight, 60):
                s.record_usage({'summary': {'lifetimeTokens': 100}}, epoch)
            s.record_usage({'summary': {'lifetimeTokens': 1100}}, midnight)
            s.record_usage({'summary': {'lifetimeTokens': 1100}}, midnight + 60)
            hours = {x['label']: x for x in s.response()['hours']}
            self.assertEqual(hours['2024-01-02 07:00']['tokens'], 0)
            self.assertEqual(hours['2024-01-02 08:00']['tokens'], 1000)
            self.assertEqual(hours['2024-01-02 08:00']['start_epoch'], midnight)
            s.close()

    def test_zero_is_reported_zero_not_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            s = HistoryStore(Path(folder)/'h.db', now=lambda: 1789897440)
            s.record_usage({'summary': {'lifetimeTokens': 100}}, 1789897380)
            s.record_usage({'summary': {'lifetimeTokens': 100}}, 1789897440)
            r = s.response()
            self.assertEqual(r['hours'][-1]['tokens'], 0)
            self.assertIsNone(r['hours'][-2]['tokens'])
            self.assertEqual(r['hourly_semantics'], 'official_reported_delta_by_observation_time')
            s.close()

    def test_gap_not_spread_or_assigned_and_restart_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'h.db'; now = 1789897440
            s = HistoryStore(path, now=lambda: now)
            for t, total in ((now-7200,100), (now-120,1100), (now-60,1120), (now,1120)):
                s.record_usage({'summary': {'lifetimeTokens': total}}, t)
            r = s.response()['hours'][-1]
            self.assertEqual(r['tokens'], 20)
            self.assertEqual(r['quality'], 'partial')
            self.assertEqual(r['gap_delta'], 1000)
            self.assertEqual(r['reported_delta'], 1020)
            self.assertEqual(r['gap_start_epoch'], now-7200)
            s.close(); s = HistoryStore(path, now=lambda: now)
            self.assertEqual(s.response()['hours'][-1], r)
            s.close()

    def test_negative_revision_is_not_zero_or_normal_growth(self):
        with tempfile.TemporaryDirectory() as folder:
            now = 1789897440; s = HistoryStore(Path(folder)/'h.db', now=lambda: now)
            for t, total in ((now-180,100),(now-120,120),(now-60,90),(now,94)):
                s.record_usage({'summary': {'lifetimeTokens': total}}, t)
            r = s.response()['hours'][-1]
            self.assertIsNone(r['tokens'])
            self.assertEqual((r['positive_delta'],r['correction_delta'],r['reported_delta']), (24,-30,-6))
            self.assertEqual(r['quality'], 'correction')
            s.close()


if __name__ == "__main__":
    unittest.main()
