import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest

from steam_family_export.errors import SchemaError
from steam_family_export.output import write_exports
from steam_family_export.transform import transform_library

FIXTURE = Path(__file__).parent / "fixtures" / "family.json"


class TransformTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.my_id = self.fixture["my_steamid"]
        self.rows = transform_library(self.fixture["library"], self.fixture["summary"], self.my_id)
        self.by_id = {row["appid"]: row for row in self.rows}

    def test_own_game(self):
        row = self.by_id[10]
        self.assertTrue(row["owned_by_me"])
        self.assertFalse(row["family_owned"])
        self.assertEqual(row["my_playtime_minutes"], 120)
        self.assertEqual(row["first_played"], "2023-11-14T22:13:20Z")
        self.assertEqual(row["last_played"], "2023-11-14T23:13:20Z")

    def test_borrowed_game_87_hours_belongs_to_player(self):
        row = self.by_id[20]
        self.assertFalse(row["owned_by_me"])
        self.assertTrue(row["family_owned"])
        self.assertEqual(row["owner_steamids"], ["76561198000000002"])
        self.assertEqual(row["my_playtime_hours"], 87)
        self.assertEqual(row["my_playtime_minutes"], 5220)
        self.assertEqual(row["playtime_steamid"], self.my_id)

    def test_explicit_never_played(self):
        row = self.by_id[30]
        self.assertEqual(row["my_playtime_hours"], 0)
        self.assertIsNone(row["first_played"])
        self.assertIsNone(row["last_played"])

    def test_multiple_owners_do_not_multiply_playtime(self):
        row = self.by_id[40]
        self.assertEqual(len(row["owner_steamids"]), 2)
        self.assertEqual(row["my_playtime_seconds"], 3601)

    def test_missing_entry_is_unknown_even_with_owner_entry(self):
        row = self.by_id[50]
        self.assertIsNone(row["my_playtime_minutes"])
        self.assertIsNone(row["my_playtime_hours"])
        self.assertEqual(row["playtime_source"], "missing_player_entry")

    def test_unicode_round_trip_and_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_exports(root, self.rows, {"my_steamid": self.my_id}, "fixture-secret")
            data = json.loads((root / "steam_family_library.json").read_text(encoding="utf-8"))
            self.assertIn("中文遊戲・日本語 🎮", [r["game_name"] for r in data["games"]])
            with (root / "steam_family_library.csv").open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertIn("中文遊戲・日本語 🎮", [r["game_name"] for r in rows])
            self.assertEqual((root / "steam_family_library.csv").stat().st_mode & 0o777, 0o600)

    def test_library_filtering(self):
        self.assertNotIn(70, self.by_id)
        self.assertIn(80, self.by_id)
        self.assertNotIn(90, self.by_id)
        self.assertTrue(self.by_id[60]["family_owned"])

    def test_wrong_library_identity_fails(self):
        self.fixture["library"]["response"]["owner_steamid"] = "76561198000000002"
        with self.assertRaises(SchemaError):
            transform_library(self.fixture["library"], self.fixture["summary"], self.my_id)

    def test_schema_changes_and_duplicate_entries_fail(self):
        for changed in (None, {}, "unexpected"):
            library = copy.deepcopy(self.fixture["library"])
            library["response"]["apps"] = changed
            with self.subTest(changed=changed), self.assertRaises(SchemaError):
                transform_library(library, self.fixture["summary"], self.my_id)
        summary = copy.deepcopy(self.fixture["summary"])
        summary["response"]["entries"].append(summary["response"]["entries"][0])
        with self.assertRaises(SchemaError):
            transform_library(self.fixture["library"], summary, self.my_id)

    def test_empty_summary_keeps_library_with_unknown_times(self):
        rows = transform_library(self.fixture["library"], {"response": {}}, self.my_id)
        self.assertEqual(len(rows), len(self.rows))
        self.assertTrue(all(row["my_playtime_hours"] is None for row in rows))


if __name__ == "__main__":
    unittest.main()
