"""Join library apps with PLAYER entries, never with entries_by_owner."""

from datetime import datetime, timezone
from typing import Any

from .api import response_body
from .auth import steamid
from .errors import ExportError, SchemaError


def integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise SchemaError(f"API schema 改變：{field} 必須是非負整數。")
    try:
        result = int(value)
    except ValueError:
        raise SchemaError(f"API schema 改變：{field} 必須是非負整數。") from None
    if result < 0:
        raise SchemaError(f"API schema 改變：{field} 不可為負數。")
    return result


def timestamp(value: Any, field: str) -> str | None:
    seconds = integer(value, field)
    if seconds == 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        raise SchemaError(f"API schema 改變：{field} 不是有效 timestamp。") from None


def transform_library(
    library: dict[str, Any], summary: dict[str, Any], my_id: str,
    player_times: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    body = response_body(library)
    if str(body.get("owner_steamid", "")) != my_id:
        raise SchemaError("GetSharedLibraryApps.owner_steamid 與你的 SteamID 不一致或缺失；停止匯出避免錯誤歸屬。")
    apps = body.get("apps")
    if not isinstance(apps, list):
        raise SchemaError("API schema 改變：缺少 apps array；不將空 response 當成空遊戲庫。")
    play_body = response_body(summary)
    # Protobuf JSON may omit an empty repeated field. A response with only
    # entries_by_owner is NOT evidence that the current player has zero hours.
    entries = play_body.get("entries", [])
    if not isinstance(entries, list):
        raise SchemaError("API schema 改變：entries 必須是 array。")
    mine: dict[int, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or "steamid" not in entry or "appid" not in entry:
            raise SchemaError("API schema 改變：playtime entry 缺少 steamid/appid。")
        try:
            player = steamid(entry["steamid"])
        except ExportError:
            raise SchemaError("API schema 改變：playtime entry 的 steamid 無效。") from None
        appid = integer(entry["appid"], "appid")
        if player != my_id:
            continue
        if appid in mine:
            raise SchemaError("同一玩家/appid 有重複 playtime entries；停止匯出以免重複加總。")
        # Missing scalar seconds_played is ambiguous; don't silently default.
        if "seconds_played" not in entry:
            raise SchemaError("API schema 改變：你的 playtime entry 缺少 seconds_played。")
        integer(entry["seconds_played"], "seconds_played")
        mine[appid] = entry
    # This self-scoped endpoint has NO steamid request parameter: Steam selects
    # the player from the authenticated token. Do not query any library owner.
    lifetime: dict[int, dict[str, Any]] = {}
    if player_times is not None:
        games = response_body(player_times).get("games", [])
        if not isinstance(games, list):
            raise SchemaError("API schema 改變：ClientGetLastPlayedTimes.games 必須是 array。")
        for game in games:
            if not isinstance(game, dict) or "playtime_forever" not in game:
                raise SchemaError("API schema 改變：玩家紀錄缺少 playtime_forever。")
            appid = integer(game.get("appid"), "appid")
            if not appid or appid in lifetime:
                raise SchemaError("API schema 改變：玩家紀錄 appid 為零或重複。")
            integer(game["playtime_forever"], "playtime_forever")
            lifetime[appid] = game
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for app in apps:
        if not isinstance(app, dict):
            raise SchemaError("API schema 改變：app 必須是 object。")
        appid = integer(app.get("appid"), "appid")
        if not appid or appid in seen:
            raise SchemaError("API schema 改變：appid 為零或重複。")
        seen.add(appid)
        owners = app.get("owner_steamids")
        if not isinstance(owners, list) or not owners:
            raise SchemaError("API schema 改變：app 缺少 owner_steamids。")
        try:
            owners = sorted({steamid(owner) for owner in owners})
        except ExportError:
            raise SchemaError("API schema 改變：owner_steamids 無效。") from None
        if not isinstance(app.get("name"), str):
            raise SchemaError("API schema 改變：app 缺少 name。")
        owned = my_id in owners
        reason = integer(app.get("exclude_reason", 0), "exclude_reason")
        app_type = integer(app.get("app_type", 1), "app_type")
        # Own licenses remain playable even if they are not shareable. This is
        # license eligibility, not live copy availability or macOS compatibility.
        if app_type != 1 or (not owned and reason != 0):
            continue
        entry = mine.get(appid)
        seconds = integer(entry["seconds_played"], "seconds_played") if entry else None
        source = "GetPlaytimeSummary.entries" if entry else "missing_player_entry"
        first = timestamp(entry.get("first_played", 0), "first_played") if entry else None
        last = timestamp(entry.get("latest_played", 0), "latest_played") if entry else None
        game = lifetime.get(appid)
        if game is not None:
            seconds = integer(game["playtime_forever"], "playtime_forever") * 60
            source = "ClientGetLastPlayedTimes.games"
            # Do not mix first/last timestamps from a narrower summary when the
            # lifetime response omits them; absent timestamps remain unknown.
            first = timestamp(game.get("first_playtime", 0), "first_playtime")
            last = timestamp(game.get("last_playtime", 0), "last_playtime")
        rows.append({
            "appid": appid,
            "game_name": app["name"],
            "owned_by_me": owned,
            "family_owned": any(owner != my_id for owner in owners),
            "owner_steamids": owners,
            "my_playtime_minutes": round(seconds / 60, 6) if seconds is not None else None,
            "my_playtime_hours": round(seconds / 3600, 6) if seconds is not None else None,
            "first_played": first,
            "last_played": last,
            "playtime_steamid": my_id,
            "my_playtime_seconds": seconds,
            "playtime_source": source,
            "playtime_scope": "player_lifetime" if game is not None else "family_summary" if entry else "unknown",
            "exclude_reason": reason,
        })
    return sorted(rows, key=lambda row: (row["game_name"].casefold(), row["appid"]))
