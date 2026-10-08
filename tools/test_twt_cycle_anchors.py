import unittest
from collect_twt_cycle_anchors import parse_event, select_anchors


class AnchorTests(unittest.TestCase):
    def events(self, cycles=4):
        events = []
        for seq in range(1, cycles + 2):
            us = seq * 300_000_000
            for phase, flags, reason, delta in [(0, 0, 0, 0), (1, 15, 0, 20), (2, 0, 1, 21)]:
                if seq == cycles + 1 and phase:
                    continue
                events.append(parse_event(f"DBG TWT_CYCLE phase={phase} device_us={us+delta} trial_id=8 cycle_seq={seq} associated=1 fetch_flags={flags} reason={reason} dropped=0", us/1e6+delta/1e6))
        return events

    def test_four_actual_cycles(self):
        for cycles in (3, 4, 5):
            result = select_anchors(self.events(cycles), cycles, 8)
            self.assertEqual(len(result["anchors"]), cycles + 1)
            self.assertTrue(result["structural_window_coverage_complete"])
            self.assertTrue(result["cycle_fetch_qos_ok"])
            self.assertIsNone(result["qos_passed"])
            self.assertFalse(result["trial_valid"])
            self.assertTrue(result["requires_state_evidence"])
        self.assertEqual(result["timebase"], "host_monotonic_s")
        self.assertIsNone(result["whole_wall_time_energy_Wh"])
        self.assertIsNone(parse_event("DBG twt status lease_s=1800", 0))

    def test_structural_completion_never_means_qos_pass(self):
        for cycles in (3, 4, 5):
            for reason in (2, 4, 5):
                events = self.events(cycles)
                events[1]["fetch_flags"] = 5
                events[2]["reason"] = reason
                result = select_anchors(events, cycles, 8)
                self.assertTrue(result["structural_window_coverage_complete"])
                self.assertFalse(result["cycle_fetch_qos_ok"])
                self.assertFalse(result["qos_passed"])
                self.assertFalse(result["trial_valid"])
            events = self.events(cycles)
            events[1]["fetch_flags"] = 5  # success label without successful fetch remains invalid QoS
            self.assertFalse(select_anchors(events, cycles, 8)["qos_passed"])

    def test_rejects_missing_failed_or_fabricated_evidence(self):
        for field, value in [("dropped", 1), ("associated", 0), ("reason", 6)]:
            events = self.events()
            index = 1 if field == "fetch_flags" else 2
            events[index][field] = value
            with self.assertRaises(ValueError):
                select_anchors(events, 4, 8)
        events = self.events()
        events[-1]["device_us"] += 30_000_000
        with self.assertRaises(ValueError):
            select_anchors(events, 4, 8)
        with self.assertRaises(ValueError):
            select_anchors(self.events()[:-1], 4, 8)


if __name__ == "__main__":
    unittest.main()
