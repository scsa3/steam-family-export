"""Anonymous, batched overall Steam store review summaries."""

import csv
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys
from typing import Any, Callable

from .errors import ExportError, SchemaError
from .output import private_write, save_json
from .prices import store_apps
from .transform import integer

REVIEW_FIELDS = [
    "review_positive_percent", "review_description", "review_count",
    "review_checked_at", "review_status",
]
REVIEW_SCOPE = "IStoreBrowseService/GetItems/v1 reviews.summary_filtered (overall, all languages, Steam store filtering)"
Fetch = Callable[[str, dict[str, Any], str | None], dict[str, Any]]


def review_snapshot(item: dict[str, Any] | None, checked_at: str | None) -> dict[str, Any]:
    result: dict[str, Any] = dict.fromkeys(REVIEW_FIELDS)
    result.update(review_checked_at=checked_at, review_status="unavailable")
    if item is None:
        return result
    reviews = item.get("reviews")
    if reviews is None:
        return result
    if not isinstance(reviews, dict):
        raise SchemaError("API schema 改變：reviews 必須是 object。")
    # Do not use summary_language_specific, which describes a different sample.
    summary = reviews.get("summary_filtered")
    if summary is None:
        return result
    if not isinstance(summary, dict):
        raise SchemaError("API schema 改變：reviews.summary_filtered 必須是 object。")
    count = integer(summary.get("review_count", 0), "reviews.review_count")
    result["review_count"] = count
    if count == 0:
        result["review_status"] = "no_reviews"
        return result
    # Protobuf JSON may omit a scalar zero; zero percent is valid when count > 0.
    percent = integer(summary.get("percent_positive", 0), "reviews.percent_positive")
    label = summary.get("review_score_label")
    if percent > 100 or not isinstance(label, str) or not label.strip():
        raise SchemaError("API schema 改變：評價百分比需介於 0–100，且需有 review_score_label。")
    result.update(review_positive_percent=percent, review_description=label,
                  review_status="available")
    return result


def fetch_reviews(appids: list[int], fetch: Fetch, language: str, country_code: str) -> dict[int, dict[str, Any]]:
    ids = sorted(set(appids))
    results = {appid: {**review_snapshot(None, None), "review_status": "lookup_failed"} for appid in ids}
    for start in range(0, len(ids), 50):
        batch = ids[start:start + 50]
        try:
            payload = fetch("GetItems", {
                "ids": [{"appid": appid} for appid in batch],
                "context": {"language": language, "country_code": country_code},
                "data_request": {"include_reviews": True},
            }, f"GetItems_reviews_{start // 50 + 1:04d}")
        except SchemaError:
            raise
        except ExportError as exc:
            print(f"注意：評價查詢失敗，未取得的評價留空（review_status=lookup_failed）。{exc}", file=sys.stderr)
            break  # Stop immediately after rate limits or transport failures.
        apps = store_apps(payload)
        checked = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        for appid in batch:
            results[appid] = review_snapshot(apps.get(appid), checked)
    return results


def refresh_exports(output_dir: Path, fetch: Fetch, language: str, country_code: str) -> None:
    """Append reviews without changing existing ownership, playtime or prices."""
    csv_files: list[tuple[Path, list[str], list[dict[str, Any]]]] = []
    json_files: list[tuple[Path, dict[str, Any], str]] = []
    ids: set[int] = set()
    try:
        for name in ("steam_family_library.csv", "steam_wishlist.csv", "steam_wishlist_prices.csv"):
            path = output_dir / name
            if not path.exists():
                continue
            with path.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                fields = reader.fieldnames
                if not fields or "appid" not in fields or len(set(fields)) != len(fields):
                    raise ExportError(f"{name} 缺少 appid 欄位或有重複欄位；請重新匯出。")
                rows = list(reader)
                if any(None in row or any(value is None for value in row.values()) for row in rows):
                    raise ExportError(f"{name} 有不完整的 CSV 列；請重新匯出。")
                ids.update(integer(row["appid"], "CSV.appid") for row in rows)
                csv_files.append((path, fields, rows))
        for name, key in (("steam_family_library.json", "games"), ("steam_wishlist.json", "items")):
            path = output_dir / name
            if not path.exists():
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(payload, dict) or not isinstance(payload.get(key), list)
                    or not isinstance(payload.get("metadata"), dict)
                    or any(not isinstance(row, dict) for row in payload[key])):
                raise ExportError(f"{name} 格式不符；請重新匯出。")
            ids.update(integer(row.get("appid"), "JSON.appid") for row in payload[key])
            json_files.append((path, payload, key))
    except (ValueError, UnicodeError, csv.Error):
        raise ExportError("現有匯出檔不是有效的 UTF-8 CSV/JSON；請重新匯出。") from None
    if not csv_files:
        raise ExportError("找不到現有 CSV；請先匯出遊戲庫或願望清單，或用 --output-dir 指定資料目錄。")
    if 0 in ids:
        raise ExportError("現有匯出檔含無效 appid 0；請重新匯出。")
    reviews = fetch_reviews(list(ids), fetch, language, country_code)
    for path, fields, rows in csv_files:
        for row in rows:
            row.update(reviews[int(row["appid"])])
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=fields + [field for field in REVIEW_FIELDS if field not in fields])
        writer.writeheader()
        writer.writerows(rows)
        private_write(path, stream.getvalue())
    for path, payload, key in json_files:
        for row in payload[key]:
            row.update(reviews[int(row["appid"])])
        payload["metadata"]["review_scope"] = REVIEW_SCOPE
        save_json(path, payload, "")
    available = sum(row["review_status"] == "available" for row in reviews.values())
    print(f"已更新 {len(csv_files)} 份 CSV 與 {len(json_files)} 份 JSON 的評價：{available} / {len(ids)} 款有整體評價。")
    print("原本的遊玩時間與價格保持原值；評價查詢時間見 review_checked_at。")
