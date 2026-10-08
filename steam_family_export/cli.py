import argparse
from datetime import datetime, timezone
import getpass
import logging
import os
from pathlib import Path
import sys
import uuid
from typing import Any

import httpx

from .api import SteamAPI, family_id
from .auth import token_identity
from .errors import ExportError
from .output import redact, save_json, write_exports
from .transform import transform_library


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="匯出 Steam Family 遊戲及本人遊玩時間（不讀取瀏覽器資料）。")
    parser.add_argument("--output-dir", type=Path, default=Path("."), help="CSV/JSON 輸出位置，預設目前目錄")
    parser.add_argument("--language", default="tchinese", help="Steam 遊戲名稱語言，預設 tchinese")
    args = parser.parse_args(argv)
    # httpx INFO messages include full URLs. Explicitly silence HTTP libraries.
    for logger in ("httpx", "httpcore"):
        logging.getLogger(logger).disabled = True
    try:
        token = os.environ.get("STEAM_WEBAPI_TOKEN", "").strip()
        if not token:
            if not sys.stdin.isatty():
                raise ExportError("請設定 STEAM_WEBAPI_TOKEN，或在互動終端機執行以隱藏輸入 token。")
            token = getpass.getpass("Steam webapi_token（隱藏輸入；不要貼到聊天）: ").strip()
        my_id = token_identity(token, os.environ.get("MY_STEAM_ID") or None)
        now = datetime.now(timezone.utc)
        run_id = now.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        raw_dir = args.output_dir / "data" / "raw" / run_id
        with httpx.Client(timeout=30.0, follow_redirects=False, trust_env=False) as client:
            api = SteamAPI(token, client)

            def fetch(method: str, body: dict[str, Any]) -> dict[str, Any]:
                # Persist response body only: no URL, request, cookie or headers.
                payload = redact(api.call(method, body), token)
                save_json(raw_dir / (method + ".json"), payload, token)
                return payload

            group = fetch("GetFamilyGroupForUser", {"include_family_group_response": True})
            group_id = family_id(group)
            library = fetch("GetSharedLibraryApps", {
                "family_groupid": group_id,
                "steamid": my_id,
                "include_own": True,
                "include_excluded": True,
                "include_non_games": False,
                "language": args.language,
                # No max_apps: avoid a client-side truncation cap.
            })
            summary = fetch("GetPlaytimeSummary", {"family_groupid": group_id})
            player_times = None
            player_times_error = None
            try:
                player_times = fetch("ClientGetLastPlayedTimes", {"min_last_played": 0})
            except ExportError as exc:
                player_times_error = str(exc)
                print(f"注意：本人終生時間 API 無法取得；改用家庭玩家紀錄。{exc}", file=sys.stderr)
        rows = transform_library(library, summary, my_id, player_times)
        unknown = sum(row["my_playtime_seconds"] is None for row in rows)
        metadata = {
            "my_steamid": my_id,
            "family_groupid": group_id,
            "exported_at": now.isoformat().replace("+00:00", "Z"),
            "raw_run": run_id,
            "game_count": len(rows),
            "missing_player_entry_count": unknown,
            "playtime_attribution": "self-scoped ClientGetLastPlayedTimes, then GetPlaytimeSummary.entries filtered by steamid == my_steamid; entries_by_owner ignored",
            "playtime_scope": "see per-game playtime_scope; family_summary is not guaranteed to cover lifetime",
            "player_times_error": player_times_error,
            "library_scope": "owned games plus share-eligible family games; not live copy availability, parental permissions or macOS compatibility",
        }
        write_exports(args.output_dir, rows, metadata, token)
        print(f"已匯出 {len(rows)} 款遊戲；遊玩者 SteamID：{my_id}。")
        print("steam_family_library.csv / steam_family_library.json；原始回應：data/raw/" + run_id)
        if unknown:
            print(f"注意：{unknown} 款缺少你的 playtime entry，時間留空/null，不能當作未玩過。")
        family_only = sum(row["playtime_scope"] == "family_summary" for row in rows)
        if family_only:
            print(f"注意：{family_only} 款使用 family_summary；無法保證包含加入家庭前的全部歷史。")
        return 0
    except ExportError as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1
    except OSError:
        print("錯誤：無法讀寫輸出檔，請檢查目錄權限與可用空間。", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("已取消。", file=sys.stderr)
        return 130
