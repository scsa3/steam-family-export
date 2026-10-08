import contextlib
import copy
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
from steam_family_export.cli import country_code, main
from steam_family_export.errors import AuthenticationError, SchemaError
from steam_family_export.output import write_wishlist_exports
from steam_family_export.wishlist import store_names, transform_wishlist
from test_api_cli import fake_token

FIXTURE = Path(__file__).parent / "fixtures/wishlist.json"


class WishlistTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.my_id = self.fixture["my_steamid"]

    def test_priorities_dates_and_identity(self):
        rows = transform_wishlist(self.fixture["wishlist"], self.my_id)
        self.assertEqual([r["appid"] for r in rows], [20, 10, 30])
        self.assertEqual(rows[0]["date_added"], "2023-11-14T23:13:20Z")
        self.assertEqual(rows[0]["date_added_unix"], 1700003600)
        self.assertIsNone(rows[-1]["date_added"])
        self.assertTrue(all(r["wishlist_steamid"] == self.my_id for r in rows))

    def test_empty_wishlist_and_omitted_repeated_field(self):
        for body in ({"items": []}, {}):
            self.assertEqual(transform_wishlist({"response": body}, self.my_id), [])

    def test_invalid_items_and_duplicates(self):
        for items in (None, {}, [None], [{"appid": 0}], [{"appid": 1}, {"appid": 1}], [{"appid": 1, "priority": -1}], [{"appid": 1, "date_added": "oops"}]):
            with self.subTest(items=items), self.assertRaises(SchemaError):
                transform_wishlist({"response": {"items": items}}, self.my_id)

    def test_optional_scalar_defaults(self):
        row = transform_wishlist({"response": {"items": [{"appid": 10}]}}, self.my_id)[0]
        self.assertEqual(row["priority"], 0)
        self.assertIsNone(row["date_added"])

    def test_unicode_names_missing_removed_and_non_app_items(self):
        payload = copy.deepcopy(self.fixture["store"])
        payload["response"]["store_items"].append({"item_type": 1, "id": 999, "success": 1, "name": "Package"})
        names = store_names(payload)
        self.assertEqual(names[10], "中文遊戲・日本語 🎮")
        self.assertNotIn(30, names)
        self.assertNotIn(999, names)
        self.assertEqual(store_names({"response": {}}), {})

    def test_changed_store_schema_fails(self):
        for items in (None, [None], [{"success": 1, "appid": 10, "name": 123}], [{"success": 1, "appid": 10, "name": "x"}] * 2):
            with self.subTest(items=items), self.assertRaises(SchemaError):
                store_names({"response": {"store_items": items}})

    def test_utf8_output_and_no_credential(self):
        rows = transform_wishlist(self.fixture["wishlist"], self.my_id)
        names = store_names(self.fixture["store"])
        for row in rows:
            row["game_name"] = names.get(row["appid"])
        token = fake_token()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_wishlist_exports(root, rows, {"my_steamid": self.my_id, "access_token": token}, token)
            payload = json.loads((root / "steam_wishlist.json").read_text(encoding="utf-8"))
            self.assertIsNone(payload["items"][-1]["game_name"])
            with (root / "steam_wishlist.csv").open(encoding="utf-8", newline="") as handle:
                csv_rows = list(csv.DictReader(handle))
            self.assertEqual(csv_rows[1]["game_name"], "中文遊戲・日本語 🎮")
            self.assertEqual(csv_rows[-1]["game_name"], "")
            for path in root.iterdir():
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertNotIn(token, path.read_text())

    def test_getwishlist_and_storebrowse_request_format(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"response": {}})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            api = SteamAPI("fixture-secret", client)
            api.call("GetWishlist", {"steamid": self.my_id})
            api.call("GetItems", {"ids": [{"appid": 10}], "context": {"language": "tchinese", "country_code": "TW"}, "data_request": {}})
        self.assertEqual(requests[0].url.path, "/IWishlistService/GetWishlist/v1/")
        self.assertEqual(requests[1].url.path, "/IStoreBrowseService/GetItems/v1/")
        self.assertTrue(all(r.method == "GET" for r in requests))
        self.assertEqual(json.loads(requests[0].url.params["input_json"])["steamid"], self.my_id)
        self.assertEqual(requests[0].url.params["access_token"], "fixture-secret")
        self.assertNotIn("access_token", requests[1].url.params)

    def test_country_code_required_by_real_service(self):
        def handler(request):
            if "/GetWishlist/" in request.url.path:
                return httpx.Response(200, json=self.fixture["wishlist"])
            context = json.loads(request.url.params["input_json"])["context"]
            if not context.get("country_code"):
                return httpx.Response(200, json={"response": {}}, headers={"x-eresult": "8"})
            self.assertEqual(context["country_code"], "TW")
            return httpx.Response(200, json=self.fixture["store"], headers={"x-eresult": "1"})
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self.run_cli(handler, directory), 0)
            payload = json.loads((Path(directory) / "steam_wishlist.json").read_text())
            self.assertEqual(payload["metadata"]["missing_name_count"], 1)
            self.assertEqual(payload["metadata"]["name_lookup_errors"], [])
            self.assertEqual(payload["items"][1]["game_name"], "中文遊戲・日本語 🎮")

    def test_country_code_validation(self):
        import argparse
        self.assertEqual(country_code("us"), "US")
        for value in ("", "USA", "台灣", "1W"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                country_code(value)

    def test_private_wishlist_is_not_reported_as_empty(self):
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"response": {}}, headers={"x-eresult": "15"}))) as client:
            with self.assertRaisesRegex(AuthenticationError, "願望清單存取遭拒"):
                SteamAPI("fixture-secret", client).call("GetWishlist", {"steamid": self.my_id})

    def run_cli(self, handler, directory):
        real_client = httpx.Client
        def client_factory(**kwargs):
            def routed_handler(request):
                if "/GetPriceStops/" in request.url.path:
                    return httpx.Response(200, json={"response": {"currency_code": "TWD"}})
                return handler(request)
            return real_client(transport=httpx.MockTransport(routed_handler), **kwargs)
        with patch.dict(os.environ, {"STEAM_WEBAPI_TOKEN": fake_token(), "MY_STEAM_ID": self.my_id}), patch("steam_family_export.cli.httpx.Client", side_effect=client_factory), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(["--wishlist", "--output-dir", directory])

    def test_cli_does_not_require_family_and_saves_each_raw_response(self):
        paths = []
        def handler(request):
            paths.append(request.url.path)
            if "/GetWishlist/" in request.url.path:
                return httpx.Response(200, json=self.fixture["wishlist"])
            self.assertEqual(request.url.path, "/IStoreBrowseService/GetItems/v1/")
            return httpx.Response(200, json=self.fixture["store"])
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self.run_cli(handler, directory), 0)
            root = Path(directory)
            payload = json.loads((root / "steam_wishlist.json").read_text())
            self.assertEqual(payload["metadata"]["item_count"], 3)
            self.assertEqual(payload["metadata"]["missing_name_count"], 1)
            self.assertEqual(len(list((root / "data/raw").glob("*/*.json"))), 3)
            self.assertFalse((root / "steam_family_library.json").exists())
            self.assertEqual(len(paths), 2)

    def test_empty_cli_only_calls_wishlist_and_writes_header(self):
        def handler(request):
            self.assertIn("/GetWishlist/", request.url.path)
            return httpx.Response(200, json={"response": {}})
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self.run_cli(handler, directory), 0)
            with (Path(directory) / "steam_wishlist.csv").open(newline="") as handle:
                self.assertEqual(list(csv.DictReader(handle)), [])

    def test_name_lookup_http_failure_preserves_wishlist(self):
        def handler(request):
            if "/GetWishlist/" in request.url.path:
                return httpx.Response(200, json=self.fixture["wishlist"])
            return httpx.Response(429, text="rate limit")
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self.run_cli(handler, directory), 0)
            payload = json.loads((Path(directory) / "steam_wishlist.json").read_text())
            self.assertEqual(len(payload["items"]), 3)
            self.assertEqual(payload["metadata"]["missing_name_count"], 3)
            self.assertTrue(payload["metadata"]["name_lookup_errors"])

    def test_batched_lookup_has_no_truncation_and_unique_raw_names(self):
        batches = []
        def handler(request):
            if "/GetWishlist/" in request.url.path:
                return httpx.Response(200, json={"response": {"items": [{"appid": i} for i in range(1, 102)]}})
            body = json.loads(request.url.params["input_json"])
            self.assertEqual(body["context"]["country_code"], "TW")
            self.assertNotIn("access_token", request.url.params)
            ids = [item["appid"] for item in body["ids"]]
            batches.append(ids)
            return httpx.Response(200, json={"response": {"store_items": [{"appid": i, "success": 1, "name": f"Game {i}"} for i in ids]}})
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self.run_cli(handler, directory), 0)
            self.assertEqual([len(batch) for batch in batches], [50, 50, 1])
            root = Path(directory)
            payload = json.loads((root / "steam_wishlist.json").read_text())
            self.assertEqual(len(payload["items"]), 101)
            self.assertEqual(payload["metadata"]["missing_name_count"], 0)
            self.assertEqual(len(list((root / "data/raw").glob("*/GetItems_wishlist_*.json"))), 3)


if __name__ == "__main__":
    unittest.main()
