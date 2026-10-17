"""Offline geometry checks only: never program or select the proposal."""
import csv
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def table(name):
    path = ROOT / "boards" / "mosaico" / name
    rows = csv.reader(line for line in path.read_text().splitlines()
                      if line.strip() and not line.lstrip().startswith("#"))
    return {r[0]: (r[1], r[2], int(r[3], 0), int(r[4], 0)) for r in rows}


class ProposalTests(unittest.TestCase):
    def test_geometry_and_preserved_small_partitions(self):
        current = table("partitions.ota.csv")
        proposed = table("partitions.ota6m.proposal.csv")
        for name in ("nvs", "otadata", "phy_init"):
            self.assertEqual(current[name], proposed[name])
        ordered = sorted(proposed.values(), key=lambda r: r[2])
        end = 0xA000
        for kind, subtype, start, size in ordered:
            self.assertGreaterEqual(start, end)
            self.assertEqual(start % (0x10000 if kind == "app" else 0x1000), 0)
            self.assertEqual(size % 0x1000, 0)
            end = start + size
        self.assertEqual(end, 16 * 1024 * 1024)
        self.assertEqual(proposed["ota_0"][3], 6 * 1024 * 1024)
        self.assertEqual(proposed["ota_1"][3], 6 * 1024 * 1024)
        self.assertEqual(proposed["reserve_0"][3] + proposed["reserve_1"][3],
                         0x3E0000)

    def test_migration_is_not_an_app_only_update(self):
        current = table("partitions.ota.csv")
        proposed = table("partitions.ota6m.proposal.csv")
        # The expanded first slot overlaps the old second slot: both cannot be
        # assumed healthy simultaneously during conversion. This is intentional
        # evidence for requiring a dedicated migration, not a flash recipe.
        first = proposed["ota_0"]
        old_second = current["ota_1"]
        self.assertLess(old_second[2], first[2] + first[3])
        self.assertGreater(old_second[2] + old_second[3], first[2])
        # The new second image can, however, be staged without touching either
        # old app. This deliberately trades contiguous reserve space for a
        # less destructive migration path; it is not a power-fail-safe table.
        new_second = proposed["ota_1"]
        for name in ("ota_0", "ota_1"):
            old = current[name]
            self.assertGreaterEqual(new_second[2], old[2] + old[3])
        defaults = (ROOT / "boards/mosaico/sdkconfig.defaults").read_text()
        self.assertNotIn("partitions.ota6m.proposal.csv", defaults)


if __name__ == "__main__":
    unittest.main()
