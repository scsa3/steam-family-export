[English](README.en.md) | 繁體中文

# Steam Family library exporter

匯出 Steam Family 遊戲庫、你本人的遊玩紀錄或願望清單，產生 CSV 和 JSON。支援 Python 3.12+，不需要安裝 Steam Client。

## 開始使用

1. 在瀏覽器登入 [Steam 商店](https://store.steampowered.com/)，確認是你自己的帳號。
2. 在同一個瀏覽器開啟 [Steam token 頁面](https://store.steampowered.com/pointssummary/ajaxgetasyncconfig)，複製 `data.webapi_token` 的值。
3. 在專案目錄執行以下指令（需要已安裝 `uv`）：

```zsh
uv sync
uv run python -m steam_family_export
```

依程式提示貼上 token；輸入內容不會顯示。程式會自動取得你的 SteamID。

**token 只貼到你自己的終端機，不要貼到聊天、Git 或 issue。** 本工具不讀取瀏覽器 cookie、Keychain 或密碼，也不保存 token。

## 匯出願望清單

使用相同的 token 取得方式，改執行：

```zsh
uv run python -m steam_family_export --wishlist
```

會產生 `steam_wishlist.csv` 和 `steam_wishlist.json`，包含 appid、遊戲名稱、排序、加入日期與商店連結。此模式只匯出本人願望清單，不需要加入 Steam Family。查不到名稱的項目仍會保留。另產生 `steam_wishlist_prices.csv`，記錄原價、售價、折扣、幣別、商店地區與查詢時間；預設查台灣商店，未知價格留空。

## 評價

遊戲庫、願望清單與價格 CSV 都會包含整體正面評價百分比、評價描述（例如「極度好評」）、評論總數與查詢時間。百分比採用 Steam 回傳的整體評價，包含所有評論語言；預設描述為繁體中文。

已有匯出檔時，只更新評價、不需 token：

```zsh
uv run python -m steam_family_export --refresh-reviews
```

評價是查詢當下的快照。沒有評論或資料不可用時，百分比留空，原因見 `review_status`。

## 輸出

成功後會在目前目錄產生：

- `steam_family_library.csv`：UTF-8，支援中文、日文遊戲名稱。
- `steam_family_library.json`：遊戲資料及時間來源。
- `data/raw/`：各次 API 回應，credential 欄位會遮蔽。

匯出資料含家庭成員 SteamID，請審慎分享。缺少遊玩紀錄時，時間會留空／`null`，不當作零小時。

token 過期或 token 頁面沒有值時，重新登入 Steam 商店、重新載入該頁面取得 token，再執行程式。

## 更多說明

- [進階使用與資料說明](docs/advanced-usage.md)：環境變數、自訂參數、時間歸屬檢查、故障處理及測試。
- [API 研究紀錄](docs/api-research.md)：endpoint、認證方式、schema 與驗證程度。

本工具使用 unofficial API；遊玩歷史不保證完整。「可玩」指家庭共享／授權資格，不代表即時副本空閒或 macOS 相容性。詳細限制請見進階說明。
