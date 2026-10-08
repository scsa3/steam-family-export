"""Read only explicitly supplied credentials; never inspect browser storage."""

import base64
import json
import re
import time
from typing import Any

from .errors import AuthenticationError, ExportError


def steamid(value: Any) -> str:
    result = str(value)
    if not re.fullmatch(r"7656119\d{10}", result):
        raise ExportError("SteamID 必須是個人帳號的 17 位 SteamID64。")
    return result


def token_identity(token: str, expected_id: str | None = None) -> str:
    """Decode JWT subject locally. This is NOT signature verification.

    Steam authenticates the token on each request; the library response must
    independently agree with this subject before any playtime is exported.
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        if not isinstance(claims, dict):
            raise ValueError
        identity = steamid(claims.get("sub", ""))
        if "exp" in claims and float(claims["exp"]) <= time.time():
            raise AuthenticationError("Steam token 已過期；請在已登入的 Steam 網站取得新 webapi_token。")
    except (ValueError, TypeError, KeyError, ExportError) as exc:
        if isinstance(exc, AuthenticationError):
            raise
        raise AuthenticationError("不是有效的 Steam 網頁 JWT；請只輸入 webapi_token 的值。") from None
    if expected_id and steamid(expected_id) != identity:
        raise AuthenticationError("MY_STEAM_ID 與 token 帳號不一致；請確認瀏覽器登入的是你的帳號。")
    return identity
