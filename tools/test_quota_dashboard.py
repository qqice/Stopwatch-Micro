"""Quota v2 parser tests: bounded official metadata, no account interaction."""
import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from quota_dashboard import normalize_quota_dashboard, DashboardStore


def window(used=25, duration=300, reset=4000):
    return {"usedPercent": used, "windowDurationMins": duration, "resetsAt": reset}


class DashboardTests(unittest.TestCase):
    def normalize(self, raw):
        return normalize_quota_dashboard(raw, 2000)

    def test_multiple_buckets_order_and_metadata(self):
        raw = {"rateLimitsByLimitId": {
            "z": {"limitId": "z", "limitName": "Review", "primary": window(), "secondary": window(50, 10080),
                  "planType": "pro", "rateLimitReachedType": "primary", "credits": {"hasCredits": True, "unlimited": False, "balance": "12.50"}},
            "codex": {"primary": window(80)}, "a": {}},
            "rateLimits": {"primary": window(0)}, "rateLimitResetCredits": {"availableCount": 3, "ids": ["secret"]}}
        body = self.normalize(raw)
        self.assertEqual([b["id"] for b in body["buckets"]], ["codex", "a", "z"])
        self.assertEqual(body["buckets"][2]["windows"][1]["remaining_bp"], 5000)
        self.assertEqual(body["buckets"][2]["credits"]["balance"], "12.50")
        self.assertEqual(body["reset_credits"], 3)
        self.assertNotIn("secret", json.dumps(body))

    def test_missing_legacy_empty_and_malformed_map(self):
        self.assertFalse(self.normalize({})["available"])
        for raw in ({"rateLimits": {"primary": window()}}, {"rateLimitsByLimitId": {}, "rateLimits": {"primary": window()}}):
            self.assertEqual(self.normalize(raw)["buckets"][0]["id"], "codex")
        body = self.normalize({"rateLimitsByLimitId": {"bad": None}, "rateLimits": {"primary": window()}})
        self.assertEqual(body["buckets"], [])
        with self.assertRaises(ValueError):
            self.normalize(None)

    def test_unknown_and_malformed_windows_are_null(self):
        for bad in (None, {}, window(None), window(True), window(float("nan")), window(float("inf")),
                    window(-1), window(101), window(0, True), window(0, -1), window(0, 1.5),
                    window(0, 0x100000000), window(0, 300, False), window(0, 300, 0x100000000)):
            body = self.normalize({"rateLimits": {"primary": bad}})
            self.assertEqual(body["buckets"][0]["windows"], [None, None])
            json.dumps(body, allow_nan=False)
        self.assertEqual(self.normalize({"rateLimits": {"primary": window(100, 0, 0)}})["buckets"][0]["windows"][0]["remaining_bp"], 0)

    def test_unknown_window_metadata_preserves_valid_percentage(self):
        for value in ({"usedPercent": 25}, window(25, None, None),
                      {"usedPercent": 25, "resetsAt": 4000},
                      {"usedPercent": 25, "windowDurationMins": 300}):
            body = self.normalize({"rateLimits": {"primary": value}})
            actual = body["buckets"][0]["windows"][0]
            self.assertEqual(actual["remaining_bp"], 7500)
            self.assertEqual(actual["duration_minutes"], value.get("windowDurationMins") or 0)
            self.assertEqual(actual["reset_epoch"], value.get("resetsAt") or 0)
        for value in (window(25, False, None), window(25, None, True),
                      window(25, -1, None), window(25, None, -1),
                      window(25, float("nan"), None), window(25, None, float("nan"))):
            self.assertIsNone(self.normalize({"rateLimits": {"primary": value}})["buckets"][0]["windows"][0])

    def test_reset_unknown_and_integer_bounds(self):
        for count in (None, True, -1, 65536, float("nan"), 1.5, "3"):
            self.assertIsNone(self.normalize({"rateLimitResetCredits": {"availableCount": count}})["reset_credits"])
        self.assertEqual(self.normalize({"rateLimitResetCredits": {"availableCount": 65535}})["reset_credits"], 65535)

    def test_credits_decimal_only_and_no_invented_balance(self):
        for balance in (None, 10, "NaN", "inf", "1e4", "-1", "+1", " 1", "1\\n", "1" * 32, "secret"):
            body = self.normalize({"rateLimits": {"credits": {"hasCredits": True, "unlimited": False, "balance": balance}}})
            self.assertIsNone(body["buckets"][0]["credits"]["balance"])
        body = self.normalize({"rateLimits": {"credits": {"hasCredits": False, "unlimited": True, "balance": "99"}}})
        self.assertEqual(body["buckets"][0]["credits"], {"unlimited": True, "balance": None})
        self.assertIsNone(self.normalize({"rateLimits": {"credits": {"balance": "1"}}})["buckets"][0]["credits"])

    def test_sanitized_byte_limits_payload_bound_and_truncation(self):
        raw = {"rateLimitsByLimitId": {str(i): {"limitId": str(i) + "x" * 60,
             "limitName": "漢" * 100 + "\\x00", "planType": "p" * 100,
             "rateLimitReachedType": "r" * 100, "primary": window(0, 0xFFFFFFFF, 0xFFFFFFFF),
             "secondary": window(0, 0xFFFFFFFF, 0xFFFFFFFF),
             "credits": {"hasCredits": True, "unlimited": True, "balance": "1" * 31}} for i in range(20)}}
        raw["rateLimitsByLimitId"]["codex"] = {"limitId": "codex", "limitName": "Codex\\x00\\n"}
        body = self.normalize(raw)
        self.assertEqual((body["total_buckets"], len(body["buckets"]), body["truncated"]), (21, 8, True))
        self.assertEqual(body["buckets"][0]["id"], "codex")
        for bucket in body["buckets"]:
            for key, limit in (("id", 63), ("name", 63), ("plan", 23), ("reached", 47)):
                self.assertLessEqual(len(bucket[key].encode("utf-8")), limit)
        self.assertLessEqual(len(json.dumps(body, separators=(",", ":"), allow_nan=False).encode()), 8192)

    def test_wire_ascii_names_and_balance_31_byte_boundary(self):
        body = self.normalize({"rateLimitsByLimitId": {
            "codex": {"limitName": "中文\x00\n"},
            "review": {"limitName": "审查 Review", "credits": {
                "hasCredits": True, "unlimited": False, "balance": "1" * 31}}}})
        self.assertEqual(body["buckets"][0]["name"], "codex")
        self.assertEqual(body["buckets"][1]["name"], " Review")
        self.assertEqual(body["buckets"][1]["credits"]["balance"], "1" * 31)
        for bucket in body["buckets"]:
            self.assertTrue(all(32 <= ord(c) <= 126 for c in bucket["name"]))

    def test_ambiguous_and_overlong_ids_are_rejected_not_merged(self):
        raw = {"rateLimitsByLimitId": {
            "codex": {"primary": window(30)},
            "one": {"limitId": "duplicate", "primary": window(10)},
            "two": {"limitId": "duplicate", "primary": window(90)},
            "longone": {"limitId": "x" * 63 + "1", "primary": window(20)},
            "longtwo": {"limitId": "x" * 63 + "2", "primary": window(80)},
            "a": {"limitId": "safe\x00", "primary": window(10)},
            "b": {"limitId": "safe", "primary": window(90)},
            "valid": {"limitId": "v" * 63, "primary": window(40)}}}
        body = self.normalize(raw)
        self.assertEqual([b["id"] for b in body["buckets"]], ["codex", "v" * 63])
        self.assertEqual([b["windows"][0]["remaining_bp"] for b in body["buckets"]], [7000, 6000])
        raw["rateLimitsByLimitId"] = dict(reversed(list(raw["rateLimitsByLimitId"].items())))
        self.assertEqual(self.normalize(raw), body)

    def test_store_age_boundary_and_defensive_copy(self):
        clock = [2000, 10]
        store = DashboardStore(wall_now=lambda: clock[0], monotonic_now=lambda: clock[1])
        self.assertIsNone(store.status()[0]["captured_epoch"])
        body = self.normalize({"rateLimits": {}})
        store.save(body)
        body["buckets"].clear()
        clock[:] = [1000, 130]
        self.assertTrue(store.status()[1])
        clock[1] += 1
        saved, available = store.status()
        self.assertFalse(available)
        self.assertEqual(saved["age_seconds"], 121)
        self.assertEqual(len(saved["buckets"]), 1)


if __name__ == "__main__":
    unittest.main()
