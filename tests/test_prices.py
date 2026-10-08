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

from steam_family_export.cli import main
from steam_family_export.errors import SchemaError
from steam_family_export.output import write_wishlist_prices
from steam_family_export.prices import price_snapshot, store_currency
from test_api_cli import fake_token

CHECKED = "2026-10-08T07:00:00Z"


def paid_item(**changes):
    option = {
        "packageid": 123, "purchase_option_name": "遊戲 Standard Edition",
        "original_price_in_cents": "18800", "final_price_in_cents": "3700",
        "discount_pct": 80, "formatted_original_price": "NT$ 188.00",
        "formatted_final_price": "NT$ 37.00",
    }
    option.update(changes)
    return {"appid": 10, "success": 1, "name": "中文遊戲", "best_purchase_option": option}


class PriceTests(unittest.TestCase):
    def test_discounted_price_decimal_conversion_and_offer(self):
        result = price_snapshot(paid_item(), "TWD", "TW", CHECKED)
        self.assertEqual(result["original_price"], "188.00")
        self.assertEqual(result["current_price"], "37.00")
        self.assertEqual(result["discount_percent"], 80)
        self.assertEqual(result["currency"], "TWD")
        self.assertEqual(result["price_checked_at"], CHECKED)
        self.assertEqual(result["packageid"], 123)
        self.assertEqual(result["purchase_option_name"], "遊戲 Standard Edition")
        self.assertEqual(result["price_status"], "priced")

    def test_undiscounted_price_and_fractional_cents(self):
        result = price_snapshot(paid_item(original_price_in_cents=1999, final_price_in_cents=1999, discount_pct=0), "USD", "US", CHECKED)
        self.assertEqual(result["current_price"], "19.99")
        self.assertEqual(result["original_price"], "19.99")
        self.assertEqual(result["discount_percent"], 0)

    def test_free_game_and_free_temporarily_not_confused(self):
        item = {"appid": 10, "success": 1, "is_free": True}
        result = price_snapshot(item, "TWD", "TW", CHECKED)
        self.assertEqual(result["current_price"], "0.00")
        self.assertEqual(result["price_status"], "free")
        item["is_free_temporarily"] = True
        result = price_snapshot(item, "TWD", "TW", CHECKED)
        self.assertIsNone(result["current_price"])
        self.assertEqual(result["price_status"], "no_price")

    def test_coming_soon_and_unavailable_leave_price_blank(self):
        for item in (None, {"success": 15}, {"success": 1, "is_coming_soon": True}):
            with self.subTest(item=item):
                result = price_snapshot(item, "TWD", "TW", CHECKED)
                self.assertIsNone(result["current_price"])
                self.assertIsNone(result["original_price"])
                self.assertNotEqual(result["price_status"], "free")

    def test_preorder_with_offer_still_has_price(self):
        item = paid_item()
        item["is_coming_soon"] = True
        self.assertEqual(price_snapshot(item, "TWD", "TW", CHECKED)["current_price"], "37.00")

    def test_does_not_use_cheapest_other_purchase_option(self):
        item = paid_item()
        item["purchase_options"] = [{"purchase_option_name": "Unrelated DLC", "final_price_in_cents": "100"}]
        result = price_snapshot(item, "TWD", "TW", CHECKED)
        self.assertEqual(result["current_price"], "37.00")
        self.assertEqual(result["packageid"], 123)

    def test_missing_currency_does_not_label_guess(self):
        result = price_snapshot(paid_item(), None, "TW", CHECKED)
        self.assertIsNone(result["current_price"])
        self.assertIsNone(result["currency"])
        self.assertEqual(result["price_status"], "currency_unknown")
        self.assertEqual(store_currency({"response": {"currency_code": "TWD"}}), "TWD")
        for body in ({}, {"currency_code": 1}, {"currency_code": "NT$"}):
            with self.subTest(body=body), self.assertRaises(SchemaError):
                store_currency({"response": body})

    def test_missing_original_price_not_inferred(self):
        item = paid_item()
        del item["best_purchase_option"]["original_price_in_cents"]
        result = price_snapshot(item, "TWD", "TW", CHECKED)
        self.assertIsNone(result["original_price"])
        self.assertEqual(result["current_price"], "37.00")

    def test_bad_price_schema_rejected_and_hidden_discount_preserved(self):
        for changes in ({"final_price_in_cents": -1}, {"discount_pct": 101}, {"final_price_in_cents": 12.34}, {"purchase_option_name": 1}):
            with self.subTest(changes=changes), self.assertRaises(SchemaError):
                price_snapshot(paid_item(**changes), "TWD", "TW", CHECKED)
        result = price_snapshot(paid_item(hide_discount_pct_for_compliance=True), "TWD", "TW", CHECKED)
        self.assertIsNone(result["discount_percent"])

    def test_separate_utf8_csv_does_not_overwrite_wishlist(self):
        base = {"appid": 10, "game_name": "中文・日本語 🎮", "priority": 1, "date_added": None, "date_added_unix": 0, "wishlist_steamid": "76561198000000001", "store_url": "https://store.steampowered.com/app/10/"}
        row = {**base, **price_snapshot(paid_item(), "TWD", "TW", CHECKED)}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "steam_wishlist.csv").write_text("keep csv")
            (root / "steam_wishlist.json").write_text("keep json")
            write_wishlist_prices(root, [row], "fixture-secret")
            self.assertEqual((root / "steam_wishlist.csv").read_text(), "keep csv")
            self.assertEqual((root / "steam_wishlist.json").read_text(), "keep json")
            path = root / "steam_wishlist_prices.csv"
            with path.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["game_name"], base["game_name"])
            self.assertEqual(rows[0]["current_price"], "37.00")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_cli_price_requests_anonymous_and_basic_wishlist_columns_unchanged(self):
        requests = []
        def handler(request):
            requests.append(request)
            method = request.url.path.split("/")[2]
            if method == "GetWishlist":
                return httpx.Response(200, json={"response": {"items": [{"appid": 10}]}})
            self.assertNotIn("access_token", request.url.params)
            if method == "GetPriceStops":
                body = json.loads(request.url.params["input_json"])
                self.assertEqual(body["country_code"], "TW")
                return httpx.Response(200, json={"response": {"currency_code": "TWD"}})
            body = json.loads(request.url.params["input_json"])
            self.assertTrue(body["data_request"]["include_all_purchase_options"])
            self.assertTrue(body["data_request"]["include_reviews"])
            app = paid_item()
            app["reviews"] = {"summary_filtered": {"review_count": 1000, "percent_positive": 85, "review_score_label": "極度好評"}}
            return httpx.Response(200, json={"response": {"store_items": [app]}})
        real_client = httpx.Client
        def client_factory(**kwargs):
            return real_client(transport=httpx.MockTransport(handler), **kwargs)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"STEAM_WEBAPI_TOKEN": fake_token(), "MY_STEAM_ID": "76561198000000001"}), patch("steam_family_export.cli.httpx.Client", side_effect=client_factory), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--wishlist", "--output-dir", directory]), 0)
            root = Path(directory)
            with (root / "steam_wishlist_prices.csv").open(newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["current_price"], "37.00")
            self.assertEqual(row["review_positive_percent"], "85")
            self.assertEqual(row["review_description"], "極度好評")
            self.assertEqual(json.loads((root / "steam_wishlist.json").read_text())["items"][0]["review_positive_percent"], 85)
            with (root / "steam_wishlist.csv").open(newline="") as handle:
                self.assertNotIn("current_price", csv.DictReader(handle).fieldnames)
            self.assertNotIn("current_price", json.loads((root / "steam_wishlist.json").read_text())["items"][0])
            self.assertEqual(len(list((root / "data/raw").glob("*/*.json"))), 3)

    def test_currency_lookup_failure_keeps_base_wishlist(self):
        def handler(request):
            method = request.url.path.split("/")[2]
            if method == "GetWishlist":
                return httpx.Response(200, json={"response": {"items": [{"appid": 10}]}})
            if method == "GetPriceStops":
                return httpx.Response(500)
            return httpx.Response(200, json={"response": {"store_items": [paid_item()]}})
        real_client = httpx.Client
        def client_factory(**kwargs):
            return real_client(transport=httpx.MockTransport(handler), **kwargs)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"STEAM_WEBAPI_TOKEN": fake_token(), "MY_STEAM_ID": "76561198000000001"}), patch("steam_family_export.cli.httpx.Client", side_effect=client_factory), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--wishlist", "--output-dir", directory]), 0)
            root = Path(directory)
            with (root / "steam_wishlist_prices.csv").open(newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["currency"], "")
            self.assertEqual(row["current_price"], "")
            self.assertEqual(row["price_status"], "currency_unknown")
            self.assertEqual(json.loads((root / "steam_wishlist.json").read_text())["items"][0]["game_name"], "中文遊戲")


if __name__ == "__main__":
    unittest.main()
