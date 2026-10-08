"""Private, atomic exports with credential redaction as defense in depth."""

import csv
import io
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any
from urllib.parse import quote, quote_plus

CREDENTIAL_KEY = re.compile(r"token|cookie|password|authorization|credential|secret|api.?key", re.I)
JWT = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")


def redact(value: Any, token: str) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if CREDENTIAL_KEY.search(key) else redact(item, token)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item, token) for item in value]
    if isinstance(value, str):
        for secret in {token, quote(token, safe=""), quote_plus(token)}:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return JWT.sub("[REDACTED]", value)
    return value


def private_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # mkstemp creates mode 0600; replacing an existing file preserves that mode.
    fd, temporary = tempfile.mkstemp(prefix=".steam-export-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save_json(path: Path, payload: Any, token: str) -> None:
    private_write(path, json.dumps(redact(payload, token), ensure_ascii=False, indent=2) + "\n")


def write_exports(output_dir: Path, rows: list[dict[str, Any]], metadata: dict[str, Any], token: str) -> None:
    safe_rows = redact(rows, token)
    fields = [
        "appid", "game_name", "owned_by_me", "family_owned", "owner_steamids",
        "my_playtime_minutes", "my_playtime_hours", "first_played", "last_played",
        "playtime_steamid", "my_playtime_seconds", "playtime_source", "playtime_scope", "exclude_reason",
        "review_positive_percent", "review_description", "review_count", "review_checked_at", "review_status",
    ]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in safe_rows:
        csv_row = dict(row)
        csv_row["owner_steamids"] = ";".join(row["owner_steamids"])
        for flag in ("owned_by_me", "family_owned"):
            csv_row[flag] = str(row[flag]).lower()
        writer.writerow(csv_row)
    private_write(output_dir / "steam_family_library.csv", stream.getvalue())
    save_json(output_dir / "steam_family_library.json", {"metadata": metadata, "games": safe_rows}, token)


def write_wishlist_exports(
    output_dir: Path, rows: list[dict[str, Any]], metadata: dict[str, Any], token: str,
) -> None:
    safe_rows = redact(rows, token)
    fields = [
        "appid", "game_name", "priority", "date_added", "date_added_unix",
        "wishlist_steamid", "store_url",
        "review_positive_percent", "review_description", "review_count", "review_checked_at", "review_status",
    ]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows(safe_rows)
    private_write(output_dir / "steam_wishlist.csv", stream.getvalue())
    save_json(output_dir / "steam_wishlist.json", {"metadata": metadata, "items": safe_rows}, token)


def write_wishlist_prices(output_dir: Path, rows: list[dict[str, Any]], token: str) -> None:
    fields = [
        "appid", "game_name", "priority", "date_added", "date_added_unix",
        "wishlist_steamid", "store_url", "original_price", "current_price",
        "discount_percent", "currency", "country_code", "price_checked_at",
        "price_status", "purchase_option_name", "packageid", "bundleid",
        "formatted_original_price", "formatted_current_price",
        "review_positive_percent", "review_description", "review_count", "review_checked_at", "review_status",
    ]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows(redact(rows, token))
    private_write(output_dir / "steam_wishlist_prices.csv", stream.getvalue())
