"""Convert Steam store purchase prices into an attributed price snapshot."""

from decimal import Decimal
from typing import Any

from .api import response_body
from .errors import SchemaError
from .transform import integer


def store_currency(payload: dict[str, Any]) -> str:
    value = response_body(payload).get("currency_code")
    if not isinstance(value, str) or len(value) != 3 or not value.isascii() or not value.isalpha():
        raise SchemaError("API schema 改變：GetPriceStops 缺少有效 currency_code。")
    return value.upper()


def price_snapshot(
    item: dict[str, Any] | None, currency: str | None,
    country_code: str, checked_at: str | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "original_price": None, "current_price": None, "discount_percent": None,
        "currency": currency, "country_code": country_code,
        "price_checked_at": checked_at, "price_status": "unavailable",
        "purchase_option_name": None, "packageid": None, "bundleid": None,
        "formatted_original_price": None, "formatted_current_price": None,
    }
    if item is None or integer(item.get("success", 0), "store.success") != 1:
        return result
    if not currency:
        result["price_status"] = "currency_unknown"
        return result
    option = item.get("best_purchase_option")
    if option is not None and not isinstance(option, dict):
        raise SchemaError("API schema 改變：best_purchase_option 必須是 object。")
    if not option:
        if item.get("is_free") is True and item.get("is_free_temporarily") is not True:
            result.update(original_price="0.00", current_price="0.00", discount_percent=0, price_status="free")
        else:
            result["price_status"] = "no_price"
        return result
    # Use the API's selected offer, not min(purchase_options): that array can
    # include unrelated DLC, editions and bundles. Never infer missing prices.
    for field, source in (("original_price", "original_price_in_cents"), ("current_price", "final_price_in_cents")):
        value = option.get(source)
        if value is not None:
            cents = integer(value, source)
            result[field] = format(Decimal(cents) / Decimal(100), ".2f")
    discount = integer(option.get("discount_pct", 0), "discount_pct")
    if discount > 100:
        raise SchemaError("API schema 改變：discount_pct 必須介於 0 與 100。")
    if option.get("hide_discount_pct_for_compliance") is not True and option.get("should_suppress_discount_pct") is not True:
        result["discount_percent"] = discount
    for field in ("purchase_option_name", "formatted_original_price", "formatted_final_price"):
        value = option.get(field)
        if value is not None and not isinstance(value, str):
            raise SchemaError(f"API schema 改變：{field} 必須是字串。")
        destination = "formatted_current_price" if field == "formatted_final_price" else field
        result[destination] = value
    for field in ("packageid", "bundleid"):
        value = integer(option.get(field, 0), field)
        result[field] = value or None
    result["price_status"] = "priced" if result["current_price"] is not None else "no_price"
    return result


def store_apps(payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
    items = response_body(payload).get("store_items", [])
    if not isinstance(items, list):
        raise SchemaError("API schema 改變：store_items 必須是 array。")
    result: dict[int, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            raise SchemaError("API schema 改變：store item 必須是 object。")
        if integer(item.get("success", 0), "store.success") != 1:
            continue
        if integer(item.get("item_type", 0), "store.item_type") != 0:
            continue
        appid = integer(item.get("appid", item.get("id")), "store.appid")
        if not appid or appid in result:
            raise SchemaError("API schema 改變：store appid 為零或重複。")
        result[appid] = item
    return result
