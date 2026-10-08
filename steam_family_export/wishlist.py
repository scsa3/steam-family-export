"""Wishlist conversion and batched name enrichment, independent of Families."""

from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any, Callable

from .api import response_body
from .errors import ExportError, SchemaError
from .output import write_wishlist_exports, write_wishlist_prices
from .prices import price_snapshot, store_apps, store_currency
from .transform import integer, timestamp
from .reviews import REVIEW_SCOPE, review_snapshot

BATCH_SIZE = 50
Fetch = Callable[[str, dict[str, Any], str | None], dict[str, Any]]


def transform_wishlist(payload: dict[str, Any], my_id: str) -> list[dict[str, Any]]:
    # An empty repeated protobuf field may be omitted. Access-denied responses
    # are rejected by the API client using HTTP status and X-EResult first.
    items = response_body(payload).get("items", [])
    if not isinstance(items, list):
        raise SchemaError("API schema 改變：願望清單 items 必須是 array。")
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in items:
        if not isinstance(item, dict):
            raise SchemaError("API schema 改變：wishlist item 必須是 object。")
        appid = integer(item.get("appid"), "wishlist.appid")
        if not appid or appid in seen:
            raise SchemaError("API schema 改變：願望清單 appid 為零或重複。")
        seen.add(appid)
        added = integer(item.get("date_added", 0), "date_added")
        rows.append({
            "appid": appid,
            "game_name": None,
            "priority": integer(item.get("priority", 0), "priority"),
            "date_added": timestamp(added, "date_added"),
            "date_added_unix": added,
            "wishlist_steamid": my_id,
            "store_url": f"https://store.steampowered.com/app/{appid}/",
        })
    # Nonzero Steam priorities are ordered first; 0 is left unranked.
    return sorted(rows, key=lambda row: (row["priority"] == 0, row["priority"], row["appid"]))


def store_names(payload: dict[str, Any]) -> dict[int, str]:
    items = response_body(payload).get("store_items", [])
    if not isinstance(items, list):
        raise SchemaError("API schema 改變：store_items 必須是 array。")
    names: dict[int, str] = {}
    for item in items:
        if not isinstance(item, dict):
            raise SchemaError("API schema 改變：store item 必須是 object。")
        if integer(item.get("success", 0), "store.success") != 1:
            continue
        if integer(item.get("item_type", 0), "store.item_type") != 0:
            continue
        appid = integer(item.get("appid", item.get("id")), "store.appid")
        name = item.get("name")
        if not appid or not isinstance(name, str):
            raise SchemaError("API schema 改變：商店 app 缺少有效 appid/name。")
        if appid in names:
            raise SchemaError("API schema 改變：商店回傳重複 appid。")
        if name:
            names[appid] = name
    return names


def export_wishlist(
    fetch: Fetch, my_id: str, language: str, output_dir: Path,
    token: str, exported_at: str, run_id: str, country_code: str = "TW",
) -> None:
    payload = fetch("GetWishlist", {"steamid": my_id}, None)
    rows = transform_wishlist(payload, my_id)
    name_errors: list[str] = []
    currency = None
    currency_error = None
    if rows:
        try:
            currency = store_currency(fetch("GetPriceStops", {"country_code": country_code}, None))
        except SchemaError:
            raise
        except ExportError as exc:
            currency_error = str(exc)
            print(f"注意：無法取得商店幣別；價格留空以免標示錯誤。{exc}", file=sys.stderr)
    for row in rows:
        row.update(review_snapshot(None, None))
        row["review_status"] = "lookup_failed"
    price_rows: list[dict[str, Any]] = []
    for row in rows:
        snapshot = price_snapshot(None, currency, country_code, None)
        snapshot["price_status"] = "lookup_failed"
        price_rows.append({**row, **snapshot})
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start:start + BATCH_SIZE]
        try:
            details = fetch("GetItems", {
                "ids": [{"appid": row["appid"]} for row in batch],
                "context": {"language": language, "country_code": country_code},
                "data_request": {"include_all_purchase_options": True, "include_reviews": True},
            }, f"GetItems_wishlist_{start // BATCH_SIZE + 1:04d}")
        except SchemaError:
            raise
        except ExportError as exc:
            name_errors.append(str(exc))
            print(f"注意：願望清單已取得，但商店名稱查詢失敗；保留所有 appid。{exc}", file=sys.stderr)
            # Do not keep sending requests after an HTTP/rate-limit failure.
            break
        names = store_names(details)
        apps = store_apps(details)
        checked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        for offset, row in enumerate(batch):
            row["game_name"] = names.get(row["appid"])
            row.update(review_snapshot(apps.get(row["appid"]), checked_at))
            price_rows[start + offset] = {
                **row, **price_snapshot(apps.get(row["appid"]), currency, country_code, checked_at),
            }
    missing = sum(row["game_name"] is None for row in rows)
    metadata = {
        "my_steamid": my_id,
        "exported_at": exported_at,
        "raw_run": run_id,
        "item_count": len(rows),
        "missing_name_count": missing,
        "name_lookup_errors": name_errors,
        "source": "IWishlistService/GetWishlist/v1",
        "name_source": "IStoreBrowseService/GetItems/v1",
        "review_scope": REVIEW_SCOPE,
        "name_context": {"language": language, "country_code": country_code},
        "price_currency_error": currency_error,
        "missing_price_count": sum(row["current_price"] is None for row in price_rows),
    }
    write_wishlist_exports(output_dir, rows, metadata, token)
    write_wishlist_prices(output_dir, price_rows, token)
    print(f"已匯出 {len(rows)} 個願望清單項目。")
    print("steam_wishlist.csv / steam_wishlist.json / steam_wishlist_prices.csv；原始回應：data/raw/" + run_id)
    missing_prices = sum(row["current_price"] is None for row in price_rows)
    if missing_prices:
        print(f"注意：{missing_prices} 個項目沒有可用價格；價格留空，原因見 price_status。")
    if missing:
        print(f"注意：{missing} 個項目查不到名稱，保留 appid；名稱留空/null。")
