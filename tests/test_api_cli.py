import base64
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

import httpx

from steam_family_export.api import SteamAPI, family_id
from steam_family_export.auth import token_identity
from steam_family_export.cli import main
from steam_family_export.errors import AuthenticationError, ExportError, SchemaError
from steam_family_export.output import redact, save_json
from steam_family_export.transform import transform_library

MY_ID = "76561198000000001"


def fake_token(subject=MY_ID, expires=4102444800):
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    return encode({"alg": "none"}) + "." + encode({"sub": subject, "exp": expires}) + ".fixture-signature"


class APIAndSecurityTests(unittest.TestCase):
    def test_token_subject_expiry_and_expected_identity(self):
        self.assertEqual(token_identity(fake_token()), MY_ID)
        for token, expected in [(fake_token(expires=1), None), (fake_token(), "76561198000000002"), ("not-a-jwt", None)]:
            with self.subTest(expected=expected), self.assertRaises(AuthenticationError):
                token_identity(token, expected)

    def test_request_format_and_versions(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"response": {}}, headers={"x-eresult": "1"})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            api = SteamAPI("test-secret", client)
            api.call("GetFamilyGroupForUser", {"include_family_group_response": True})
            api.call("GetSharedLibraryApps", {"family_groupid": "123", "include_own": True})
            api.call("GetPlaytimeSummary", {"family_groupid": "123"})
            api.call("ClientGetLastPlayedTimes", {"min_last_played": 0})
        self.assertEqual([r.method for r in requests], ["GET", "GET", "POST", "GET"])
        self.assertEqual(requests[2].url.path, "/IFamilyGroupsService/GetPlaytimeSummary/v1/")
        body = parse_qs(requests[2].content.decode())
        self.assertEqual(json.loads(body["input_json"][0]), {"family_groupid": "123"})
        self.assertEqual(requests[2].url.params["access_token"], "test-secret")
        self.assertEqual(requests[3].url.path, "/IPlayerService/ClientGetLastPlayedTimes/v1/")
        self.assertNotIn("steamid", json.loads(requests[0].url.params["input_json"]))

    def test_http_errors_do_not_leak_token_or_body(self):
        for status in [401, 403, 429, 500, 405]:
            def handler(request):
                return httpx.Response(status, text="test-secret")
            with self.subTest(status=status), httpx.Client(transport=httpx.MockTransport(handler)) as client:
                with self.assertRaises(ExportError) as caught:
                    SteamAPI("test-secret", client).call("GetPlaytimeSummary", {})
                self.assertNotIn("test-secret", str(caught.exception))

    def test_network_errors_and_eresult(self):
        def disconnected(request):
            raise httpx.ConnectError("URL contains test-secret", request=request)
        with httpx.Client(transport=httpx.MockTransport(disconnected)) as client:
            with self.assertRaises(ExportError) as caught:
                SteamAPI("test-secret", client).call("GetFamilyGroupForUser", {})
            self.assertNotIn("test-secret", str(caught.exception))
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"response": {}}, headers={"x-eresult": "15"}))) as client:
            with self.assertRaises(AuthenticationError):
                SteamAPI("test-secret", client).call("GetFamilyGroupForUser", {})

    def test_not_in_family_and_changed_schema(self):
        with self.assertRaisesRegex(ExportError, "沒有加入"):
            family_id({"response": {"is_not_member_of_any_group": True}})
        for payload in ({}, {"response": []}, {"response": {}}):
            with self.subTest(payload=payload), self.assertRaises(SchemaError):
                family_id(payload)

    def test_raw_redaction(self):
        token = fake_token()
        payload = {"response": {"access_token": token, "nested": [{"Cookie": "secret", "note": "echo " + token}], "other_jwt": fake_token("76561198000000002")}}
        safe = redact(payload, token)
        self.assertNotIn(token, json.dumps(safe))
        self.assertEqual(safe["response"]["nested"][0]["Cookie"], "[REDACTED]")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw.json"
            save_json(path, payload, token)
            self.assertNotIn(token, path.read_text())
            self.assertNotIn("\"secret\"", path.read_text())

    def test_lifetime_player_record_preferred_and_borrowed_not_lost(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/family.json").read_text())
        player = {"response": {"games": [{"appid": 20, "playtime_forever": 5220, "first_playtime": 1600000000, "last_playtime": 1700500000}, {"appid": 50, "playtime_forever": 600}]}}
        rows = transform_library(fixture["library"], fixture["summary"], MY_ID, player)
        by_id = {row["appid"]: row for row in rows}
        self.assertEqual(by_id[20]["my_playtime_hours"], 87)
        self.assertEqual(by_id[20]["playtime_scope"], "player_lifetime")
        self.assertFalse(by_id[20]["owned_by_me"])
        self.assertEqual(by_id[50]["my_playtime_hours"], 10)
        self.assertEqual(by_id[10]["playtime_scope"], "family_summary")

    def test_cli_end_to_end_with_mocked_steam(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/family.json").read_text())
        bodies = {"GetFamilyGroupForUser": fixture["group"], "GetSharedLibraryApps": fixture["library"], "GetPlaytimeSummary": fixture["summary"], "ClientGetLastPlayedTimes": {"response": {}}}
        def handler(request):
            method = request.url.path.split("/")[2]
            return httpx.Response(200, json=bodies[method])
        real_client = httpx.Client
        def client_factory(**kwargs):
            return real_client(transport=httpx.MockTransport(handler), **kwargs)
        token = fake_token()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"STEAM_WEBAPI_TOKEN": token, "MY_STEAM_ID": MY_ID}), patch("steam_family_export.cli.httpx.Client", side_effect=client_factory), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--output-dir", directory]), 0)
            root = Path(directory)
            output = json.loads((root / "steam_family_library.json").read_text())
            game = next(row for row in output["games"] if row["appid"] == 20)
            self.assertEqual(game["my_playtime_hours"], 87)
            self.assertEqual(output["metadata"]["my_steamid"], MY_ID)
            self.assertEqual(len(list((root / "data/raw").glob("*/*.json"))), 4)
            for path in root.rglob("*.json"):
                self.assertNotIn(token, path.read_text())


if __name__ == "__main__":
    unittest.main()
