"""Small synchronous client for Steam library, wishlist and store APIs."""

import json
from typing import Any

import httpx

from .errors import AuthenticationError, ExportError, SchemaError

BASE_URL = "https://api.steampowered.com/"
METHODS = {
    "GetFamilyGroupForUser": "GET",
    "GetSharedLibraryApps": "GET",
    "GetPlaytimeSummary": "POST",
    "ClientGetLastPlayedTimes": "GET",
    "GetWishlist": "GET",
    "GetItems": "GET",
    "GetPriceStops": "GET",
}


def response_body(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("response"), dict):
        raise SchemaError("API schema 改變：缺少 response object。")
    return payload["response"]


class SteamAPI:
    def __init__(self, token: str, client: httpx.Client):
        self._token = token
        self._client = client

    def call(self, method: str, body: dict[str, Any]) -> dict[str, Any]:
        verb = METHODS[method]
        # Steam's own web transport uses access_token in the query and input_json
        # in the query (GET) or form body (POST). Never print a request/exception.
        # Public store names, prices and reviews do not need a personal token.
        public = method in {"GetItems", "GetPriceStops"}
        params = {} if public else {"access_token": self._token}
        encoded = json.dumps(body, separators=(",", ":"))
        kwargs: dict[str, Any] = {"params": params}
        if verb == "GET":
            params.update({"input_json": encoded, "origin": "https://store.steampowered.com"})
        else:
            kwargs["data"] = {"input_json": encoded}
        try:
            service = {
                "ClientGetLastPlayedTimes": "IPlayerService",
                "GetWishlist": "IWishlistService",
                "GetItems": "IStoreBrowseService",
                "GetPriceStops": "IStoreBrowseService",
            }.get(method, "IFamilyGroupsService")
            result = self._client.request(verb, BASE_URL + service + "/" + method + "/v1/", **kwargs)
        except httpx.RequestError:
            raise ExportError(f"{method} 網路連線失敗；請檢查 DNS、網路與代理設定。") from None
        if result.status_code in (401, 403):
            if public:
                raise ExportError(f"{method} 公開商店查詢遭拒 (HTTP {result.status_code})；此查詢不需要 token，請稍後再試。")
            raise AuthenticationError(f"{method} 拒絕認證或權限不足；請更新 token 並確認帳號與資料存取權限。")
        if result.status_code == 429:
            raise ExportError(f"{method} 遭 Steam 限流 (HTTP 429)；請稍後再試。")
        if not result.is_success:
            raise ExportError(f"{method} HTTP {result.status_code}；若為 404/405，API 路徑或方法可能改變。")
        eresult = result.headers.get("x-eresult")
        if eresult is not None and not eresult.isdecimal():
            raise SchemaError(f"{method} 回傳無效的 X-EResult header。")
        if eresult is not None and eresult != "1":
            if method == "GetWishlist" and eresult == "15":
                raise AuthenticationError("願望清單存取遭拒 (EResult 15)；請確認 token 帳號與願望清單隱私設定，不將此回應當成空清單。")
            if eresult in {"5", "15", "27", "65"}:
                if public:
                    raise ExportError(f"{method} 公開商店查詢遭拒 (EResult {eresult})；此查詢不需要 token。")
                raise AuthenticationError(f"{method} Steam EResult {eresult}；token 無效、過期或權限不足。")
            raise ExportError(f"{method} Steam EResult {eresult}。")
        try:
            payload = result.json()
        except ValueError:
            raise SchemaError(f"{method} 回傳非 JSON；API schema 或服務可能改變。") from None
        if not isinstance(payload, dict):
            raise SchemaError(f"{method} 回傳的 JSON 不是 object。")
        return payload


def family_id(payload: dict[str, Any]) -> str:
    body = response_body(payload)
    if body.get("is_not_member_of_any_group") is True or str(body.get("family_groupid")) == "0":
        raise ExportError("這個帳號目前沒有加入 Steam Family（待接受邀請不算成員）。")
    value = str(body.get("family_groupid", ""))
    if not value.isdecimal() or int(value) <= 0:
        raise SchemaError("API schema 改變：缺少有效的 family_groupid。")
    return value
