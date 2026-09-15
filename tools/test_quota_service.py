#!/usr/bin/env python3
"""Unit tests for the LAN quota service; no Codex login or hardware required."""

from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))

import quota_service  # noqa: E402
from stopwatch_bridge import UsageSnapshot, normalize_rate_limits  # noqa: E402


class FakeClient:
    def __init__(self, snapshot: UsageSnapshot) -> None:
        self.snapshot = snapshot
        self.started = False

    def start(self) -> None:
        self.started = True

    def read_usage(self) -> UsageSnapshot:
        return self.snapshot

    def close(self) -> None:
        return


class QuotaServiceTests(unittest.TestCase):
    token = "0123456789abcdef"

    def setUp(self) -> None:
        self.wall_now = 2000
        self.monotonic_now = 10.0
        self.store = quota_service.SnapshotStore(
            wall_now=lambda: self.wall_now,
            monotonic_now=lambda: self.monotonic_now,
        )
        self.server = quota_service.ThreadingHTTPServer(("127.0.0.1", 0), quota_service.make_handler(self.store, self.token))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1/status"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)

    def request(self, token: str | None = None, path: str | None = None) -> tuple[int, dict[str, object]]:
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        request = urllib.request.Request(path or self.url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read()) if error.headers.get_content_type() == "application/json" else {}

    def test_auth_and_exact_path_are_required(self) -> None:
        self.assertEqual(self.request()[0], 401)
        self.assertEqual(self.request("wrong")[0], 401)
        self.assertEqual(self.request(self.token, self.url + "?x=1")[0], 404)
        self.assertFalse(quota_service.is_authorized("Bearer caf\u00e9", self.token))

    def test_unavailable_snapshot_is_not_fabricated(self) -> None:
        status, body = self.request(self.token)
        self.assertEqual(status, 503)
        self.assertEqual(body, {"version": 1, "captured_epoch": None, "remaining_bp": None, "reset_epoch": None, "reset_credits": None, "age_seconds": None, "available": False})

    def test_stale_timestamp_returns_unavailable_with_original_values(self) -> None:
        self.store.save(UsageSnapshot(8123, 3000, 1980, 2))
        self.monotonic_now += quota_service.MAX_AGE_SECONDS - 20 + 1
        status, body = self.request(self.token)
        self.assertEqual(status, 503)
        self.assertFalse(body["available"])
        self.assertEqual(body["age_seconds"], quota_service.MAX_AGE_SECONDS + 1)
        self.assertEqual(body["remaining_bp"], 8123)

    def test_wall_clock_rollback_cannot_extend_snapshot_lifetime(self) -> None:
        self.store.save(UsageSnapshot(8123, 3000, 1900, 2))
        self.wall_now = 1000  # Simulate a manual/system clock rollback after saving.
        self.monotonic_now += 21
        status, body = self.request(self.token)
        self.assertEqual(status, 503)
        self.assertEqual(body["age_seconds"], 121)
        self.assertFalse(body["available"])

    def test_collector_uses_normalized_snapshot(self) -> None:
        client = FakeClient(UsageSnapshot(9000, 3000, 1999, 0))
        collector = quota_service.QuotaCollector(lambda: client, self.store)
        collector.poll_once()
        status, body = self.request(self.token)
        self.assertEqual(status, 200)
        self.assertTrue(client.started)
        self.assertEqual(body["remaining_bp"], 9000)

    def test_rate_limit_parser_preserves_canonical_codex_window(self) -> None:
        snapshot = normalize_rate_limits(
            {
                "rateLimitsByLimitId": {
                    "codex": {
                        "limitId": "codex",
                        "primary": {
                            "usedPercent": 25.5,
                            "windowDurationMins": 300,
                            "resetsAt": 4321,
                        }
                    }
                },
                "rateLimitResetCredits": {"availableCount": 3},
            },
            captured_epoch=1234,
        )
        self.assertEqual(snapshot, UsageSnapshot(7450, 4321, 1234, 3))


if __name__ == "__main__":
    unittest.main()
