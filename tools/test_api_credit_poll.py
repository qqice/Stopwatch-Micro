"""Offline-only tests: no Mac, network, Keychain, or real credentials."""
import http.client
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import api_credit_poll as credit
from quota_service import encode_status_v2, load_config
from stopwatch_bridge import BridgeError


def grant(id_suffix, *, amount=77.25, used=0.10, effective=900, expires=2000, **fields):
    return {"id": credit.PROMO_PREFIX + id_suffix, "grant_amount": amount,
            "used_amount": used, "effective_at": effective, "expires_at": expires,
            "is_scheduled": False, "is_held_for_prepaid_transition": False, **fields}


def body(*rows, **fields):
    return json.dumps({"object": "credit_summary",
        "grants": {"object": "list", "data": list(rows), **fields}}).encode()


class GrantTests(unittest.TestCase):
    def test_active_promo_remaining_and_first_expiry(self):
        d = body(grant("a"), grant("b", amount=3.01, used=1.00, expires=1500),
                 grant("scheduled", is_scheduled=True),
                 grant("held", is_held_for_prepaid_transition=True),
                 grant("future", effective=1001),
                 grant("expired", expires=1000),
                 grant("spent", amount=10, used=10), {"id": "purchased"})
        self.assertEqual(credit.parse_grants(d, 1000), credit.Snapshot(7916, 1500, 1000))

    def test_round_only_sum_not_each_grant(self):
        d = body(grant("a", amount=0.004, used=0), grant("b", amount=0.004, used=0))
        self.assertEqual(credit.parse_grants(d, 1000).remaining_cents, 1)

    def test_no_eligible_grants_is_healthy_zero(self):
        self.assertEqual(credit.parse_grants(body({"id": "purchased"}), 1000), credit.Snapshot(0, 0, 1000))

    def test_invalid_data_never_claims_zero(self):
        malformed = [
            b"<html>not JSON</html>", b"{}", b"[]",
            body(has_more=True), body(grant("same"), grant("same")),
            body(grant("wrong_type", amount=True)), body(grant("nonfinite", amount=float("nan"))),
            body(grant("overused", amount=1, used=2)), body(grant("negative", used=-1)),
            body(grant("bad_duration", effective=2000)),
            body(grant("too_large", amount=2**32 - 1)),
            body(grant("bad_status", is_scheduled=None)), body({}),
            b"x" * (credit.MAX_BODY + 1),
        ]
        for raw in malformed:
            with self.subTest(raw=raw[:60]):
                with self.assertRaises(ValueError):
                    credit.parse_grants(raw, 1000)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.token = ("sess-" + "x" * 16).encode()
        self.fetch = Mock()
        self.session = Mock(return_value=self.token)
        self.clock = Mock(return_value=1000)
        self.lines = []
        self.collector = credit.ApiCreditCollector(
            "org-fixture", session_reader=self.session, fetch=self.fetch,
            wall_now=self.clock, log=self.lines.append)

    def test_401_keeps_snapshot_and_stops_until_replaced(self):
        self.fetch.side_effect = [(200, body(grant("a"))), (200, body(grant("b"))),
                                  (401, b"ignored"), (200, body())]
        self.assertEqual(self.collector.poll_once(), credit.POLL_SECONDS)
        self.clock.return_value = 1001
        self.collector.poll_once()
        self.clock.return_value = 1002
        self.assertEqual(self.collector.poll_once(), credit.KEYCHAIN_CHECK_SECONDS)
        self.assertEqual(self.collector.status(), {"state": "reauth", "remaining_cents": 7715,
                                                   "expires_epoch": 2000, "captured_epoch": 1001})
        self.collector.poll_once()
        self.session.return_value = None
        self.collector.poll_once()
        self.assertEqual(self.fetch.call_count, 3)
        self.assertIn("first_ok_epoch=1000 last_ok_epoch=1001 first_401_epoch=1002", self.lines[-1])
        self.session.return_value = b"other-token-test-fixture"
        self.clock.return_value = 1003
        self.collector.poll_once()
        self.assertEqual(self.fetch.call_count, 4)
        self.assertEqual(self.collector.status()["state"], "ok")
        self.assertIn("first_ok_epoch=1003 last_ok_epoch=1003 first_401_epoch=0", self.lines[-1])
        self.assertNotIn(self.token.decode(), " ".join(self.lines))

    def test_403_distinct_and_transient_failure_retains_snapshot(self):
        self.fetch.side_effect = [(200, body(grant("a"))), (403, b""),
                                  (429, b""), (200, b"<html>bad</html>"),
                                  http.client.HTTPException("secret not logged"), (200, body())]
        self.collector.poll_once()
        for state in ("forbidden", "stale", "stale", "stale"):
            delay = self.collector.poll_once()
            self.assertLessEqual(delay, credit.MAX_BACKOFF_SECONDS)
            self.assertGreater(delay, credit.POLL_SECONDS)
            self.assertEqual(self.collector.status()["state"], state)
            self.assertEqual(self.collector.status()["remaining_cents"], 7715)
        self.assertEqual(self.collector.poll_once(), credit.POLL_SECONDS)
        self.assertNotIn("secret", " ".join(self.lines))

    def test_no_session_before_first_success_and_stop_aware(self):
        self.session.return_value = None
        self.assertEqual(self.collector.poll_once(), credit.KEYCHAIN_CHECK_SECONDS)
        self.fetch.assert_not_called()
        self.assertEqual(self.collector.status(), {"state": "unavailable", "remaining_cents": 0,
                                                  "expires_epoch": 0, "captured_epoch": 0})
        stop = threading.Event()
        stop.set()
        self.collector.run(stop)
        self.session.assert_called_once()


class WireTests(unittest.TestCase):
    def test_keychain_in_memory_only(self):
        fake = b"sess-" + b"X" * 15
        with patch("api_credit_poll.subprocess.run",
                   return_value=subprocess.CompletedProcess([], 0, fake + b"\n", b"")) as run:
            self.assertEqual(credit.read_session(), fake)
            argv = run.call_args.args[0]
            self.assertIn(credit.KEYCHAIN_SERVICE, argv)
            self.assertEqual(Path(argv[-1]).parts[-3:], ("Library", "Keychains", "login.keychain-db"))
            self.assertTrue(run.call_args.kwargs["capture_output"])
            self.assertNotIn(fake.decode(), " ".join(argv))

    def test_one_fixed_tls_request_no_redirect(self):
        from contextlib import nullcontext
        conn = Mock()
        response = Mock(status=302)
        conn.getresponse.return_value = nullcontext(response)
        with patch("api_credit_poll.http.client.HTTPSConnection", return_value=conn) as https:
            self.assertEqual(credit.get_grants("org-fixture", b"sess-" + b"X" * 15), (302, b""))
        https.assert_called_once_with("api.openai.com", timeout=12)
        conn.request.assert_called_once()
        self.assertEqual(conn.request.call_args.args, ("GET", credit.PATH))
        self.assertEqual(conn.request.call_args.kwargs["headers"]["User-Agent"], credit.UA)
        response.read1.assert_not_called()
        conn.close.assert_called_once()

    def test_fixed_tls_body_bound(self):
        from contextlib import nullcontext
        token = b"sess-" + b"X" * 15
        for size in (credit.MAX_BODY, credit.MAX_BODY + 1):
            conn = Mock()
            response = Mock(status=200)
            source = __import__("io").BytesIO(b"x" * size)
            response.read1.side_effect = source.read
            conn.getresponse.return_value = nullcontext(response)
            with patch("api_credit_poll.http.client.HTTPSConnection", return_value=conn):
                if size == credit.MAX_BODY:
                    self.assertEqual(len(credit.get_grants("org-fixture", token)[1]), size)
                else:
                    with self.assertRaises(ValueError):
                        credit.get_grants("org-fixture", token)
            conn.close.assert_called_once()

    def test_optional_wire_and_limit(self):
        poller = credit.ApiCreditCollector("org-fixture", log=lambda _s: None)
        minimal = {"version": 2, "available": False}
        self.assertNotIn("api_credit", json.loads(encode_status_v2(minimal, None)))
        result = json.loads(encode_status_v2(minimal, poller))
        self.assertEqual(result["api_credit"], poller.status())
        self.assertFalse(result["available"])
        self.assertNotIn("api_credit", minimal)
        old = {"version": 2, "contents": "a" * 8100}
        self.assertEqual(encode_status_v2(old, poller), encode_status_v2(old, None))
        self.assertLessEqual(len(encode_status_v2(old, poller)), 8192)

    def test_config_opt_in_and_controls_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            config = {"device_token": "test-fixture-token-long-enough"}
            p.write_text(json.dumps(config))
            self.assertIsNone(load_config(p).api_credit_org)
            config["api_credit_org"] = "org-example123"
            p.write_text(json.dumps(config))
            self.assertEqual(load_config(p).api_credit_org, "org-example123")
            for invalid in ("org-abc\r\nX-Test: invalid", True, "", 12):
                config["api_credit_org"] = invalid
                p.write_text(json.dumps(config))
                with self.assertRaises(BridgeError):
                    load_config(p)


if __name__ == "__main__":
    unittest.main()
