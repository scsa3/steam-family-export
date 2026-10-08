import contextlib
import csv
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from steam_family_export.api import SteamAPI
from steam_family_export.cli import main
from steam_family_export.errors import ExportError, SchemaError
from steam_family_export.prices import store_apps
from steam_family_export.reviews import fetch_reviews, review_snapshot, refresh_exports

CHECKED = "2026-10-08T07:00:00Z"


def item(appid=10):
    return {"appid": appid, "item_type": 0, "success": 1, "reviews": {
        "summary_filtered": {"review_count": 32371, "percent_positive": 85,
                             "review_score": 8, "review_score_label": "極度好評"},
        "summary_language_specific": {"review_count": 329, "percent_positive": 81,
                                      "review_score_label": "不同語言的評價"},
    }}


class ReviewTests(unittest.TestCase):
    def test_overall_percentage_and_chinese_label(self):
        result = review_snapshot(item(), CHECKED)
        self.assertEqual(result["review_positive_percent"], 85)
        self.assertEqual(result["review_description"], "極度好評")
        self.assertEqual(result["review_count"], 32371)
        self.assertEqual(result["review_checked_at"], CHECKED)
        self.assertEqual(result["review_status"], "available")

    def test_no_reviews_does_not_mean_zero_percent(self):
        for summary in ({}, {"review_count": 0, "percent_positive": 0}):
            result = review_snapshot({"reviews": {"summary_filtered": summary}}, CHECKED)
            self.assertEqual(result["review_count"], 0)
            self.assertIsNone(result["review_positive_percent"])
            self.assertIsNone(result["review_description"])
            self.assertEqual(result["review_status"], "no_reviews")

    def test_missing_summary_does_not_use_language_specific(self):
        for app in (None, {}, {"reviews": {}}, {"reviews": {"summary_language_specific": item()["reviews"]["summary_language_specific"]}}):
            result = review_snapshot(app, CHECKED)
            self.assertIsNone(result["review_positive_percent"])
            self.assertIsNone(result["review_count"])
            self.assertEqual(result["review_status"], "unavailable")

    def test_zero_percent_is_valid_and_protobuf_zero_may_be_omitted(self):
        app = item()
        summary = app["reviews"]["summary_filtered"]
        summary.update(review_count=20, review_score_label="壓倒性負評")
        del summary["percent_positive"]
        self.assertEqual(review_snapshot(app, CHECKED)["review_positive_percent"], 0)
        summary["percent_positive"] = 0
        self.assertEqual(review_snapshot(app, CHECKED)["review_positive_percent"], 0)

    def test_schema_changes_are_explicit(self):
        for field, value in (("review_count", -1), ("percent_positive", 101), ("percent_positive", 85.4), ("review_score_label", None)):
            app = item()
            app["reviews"]["summary_filtered"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(SchemaError):
                review_snapshot(app, CHECKED)
        for app in ({"reviews": []}, {"reviews": {"summary_filtered": []}}):
            with self.assertRaises(SchemaError):
                review_snapshot(app, CHECKED)

    def test_failed_store_item_with_appid_zero_is_not_schema_change(self):
        payload = {"response": {"store_items": [item(), {"appid": 0, "id": 99999, "success": 15}]}}
        self.assertEqual(list(store_apps(payload)), [10])

    def test_batch_deduplication_and_missing_item(self):
        requests = []
        def fetch(method, body, name):
            requests.append((method, body, name))
            self.assertTrue(body["data_request"]["include_reviews"])
            self.assertEqual(body["context"], {"language": "tchinese", "country_code": "TW"})
            return {"response": {"store_items": [item(i["appid"]) for i in body["ids"] if i["appid"] != 101]}}
        result = fetch_reviews(list(range(1, 102)) + [10], fetch, "tchinese", "TW")
        self.assertEqual([len(r[1]["ids"]) for r in requests], [50, 50, 1])
        self.assertEqual(len({r[2] for r in requests}), 3)
        self.assertEqual(len(result), 101)
        self.assertEqual(result[101]["review_status"], "unavailable")

    def test_http_failure_stops_requests_and_keeps_all_appids(self):
        calls = []
        def fetch(method, body, name):
            calls.append(name)
            if len(calls) == 2:
                raise ExportError("GetItems HTTP 429")
            return {"response": {"store_items": [item(i["appid"]) for i in body["ids"]]}}
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            result = fetch_reviews(list(range(1, 102)), fetch, "tchinese", "TW")
        self.assertEqual(len(calls), 2)
        self.assertEqual(result[50]["review_status"], "available")
        self.assertEqual(result[51]["review_status"], "lookup_failed")
        self.assertEqual(result[101]["review_status"], "lookup_failed")
        self.assertIn("429", errors.getvalue())

    def test_refresh_cli_no_token_preserves_existing_fields_utf8_and_repeated_updates(self):
        requests = []
        def handler(request):
            requests.append(request)
            self.assertEqual(request.url.path, "/IStoreBrowseService/GetItems/v1/")
            self.assertNotIn("access_token", request.url.params)
            body = json.loads(request.url.params["input_json"])
            return httpx.Response(200, json={"response": {"store_items": [item(i["appid"]) for i in body["ids"]]}})
        real_client = httpx.Client
        def client_factory(**kwargs):
            return real_client(transport=httpx.MockTransport(handler), **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = "appid,game_name,my_playtime_hours,current_price,owned_by_me\n10,中文・日本語 🎮,87.00,206.00,false\n"
            for name in ("steam_family_library.csv", "steam_wishlist.csv", "steam_wishlist_prices.csv"):
                (root / name).write_text(original, encoding="utf-8")
            game = {"appid": 10, "my_playtime_hours": 87, "owned_by_me": False}
            for name, key in (("steam_family_library.json", "games"), ("steam_wishlist.json", "items")):
                (root / name).write_text(json.dumps({"metadata": {"exported_at": CHECKED}, key: [game]}))
            with patch.dict(os.environ, {}, clear=True), patch("steam_family_export.cli.httpx.Client", side_effect=client_factory), patch("steam_family_export.cli.getpass.getpass", side_effect=AssertionError("Must not ask for a token")), contextlib.redirect_stdout(io.StringIO()):
                for _ in range(2):
                    self.assertEqual(main(["--refresh-reviews", "--output-dir", directory]), 0)
            self.assertEqual(len(requests), 2)
            for path in root.glob("*.csv"):
                with path.open(encoding="utf-8", newline="") as handle:
                    reader = csv.DictReader(handle)
                    rows = list(reader)
                    self.assertEqual(len(reader.fieldnames), len(set(reader.fieldnames)))
                row = rows[0]
                self.assertEqual(row["game_name"], "中文・日本語 🎮")
                self.assertEqual(row["my_playtime_hours"], "87.00")
                self.assertEqual(row["current_price"], "206.00")
                self.assertEqual(row["owned_by_me"], "false")
                self.assertEqual(row["review_description"], "極度好評")
                self.assertEqual(row["review_positive_percent"], "85")
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            payload = json.loads((root / "steam_family_library.json").read_text())
            self.assertEqual(payload["games"][0]["my_playtime_hours"], 87)
            self.assertEqual(payload["metadata"]["exported_at"], CHECKED)
            self.assertEqual(payload["games"][0]["review_count"], 32371)

    def test_bad_input_fails_before_network_or_file_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "steam_family_library.csv"
            for content in ("name\nx\n", "appid,name\n10,x,extra\n", "appid,name\n0,x\n"):
                path.write_text(content)
                with self.assertRaises(ExportError):
                    refresh_exports(root, lambda *a: self.fail("Should not call API"), "tchinese", "TW")
                self.assertEqual(path.read_text(), content)

    def test_anonymous_access_error_does_not_ask_for_new_token(self):
        for response in (httpx.Response(403), httpx.Response(200, headers={"x-eresult": "15"})):
            with httpx.Client(transport=httpx.MockTransport(lambda request: response)) as client:
                with self.assertRaisesRegex(ExportError, "不需要 token"):
                    SteamAPI("", client).call("GetItems", {})

    def test_no_csv_error_is_clear(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ExportError, "找不到現有 CSV"):
                refresh_exports(Path(directory), lambda *a: self.fail("Should not call API"), "tchinese", "TW")


if __name__ == "__main__":
    unittest.main()
