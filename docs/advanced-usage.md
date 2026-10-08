# 進階使用與資料說明

[返回 README](../README.md)

一般使用只需在專案目錄執行 `uv sync`，再執行 `uv run python -m steam_family_export`，依提示隱藏輸入 token。以下說明供需要環境變數、自訂輸出或核對資料來源時查閱。

## 驗證狀態

研究日期：2026-10-08。現行 request/schema、公開來源、實際 HTTP 探測與尚未驗證的部分記錄在 [API 研究紀錄](api-research.md)。**本專案已完成 fixture/mock 測試，但未取得使用者 token，因此未完成你的帳號的登入後實測。** 不應將這份研究解讀成已確認每個帳號都能透過網頁 token 取得完整終生歷史。

## 環境變數（選用）

若希望使用環境變數，在 macOS 預設的 **zsh** 使用以下方式；輸入值不顯示、不寫入歷史：

```zsh
read -rs 'STEAM_WEBAPI_TOKEN?請貼上 Steam webapi_token（隱藏輸入）: '
printf '\n'
export STEAM_WEBAPI_TOKEN
# 可選：你已知的本人 SteamID64，用來交叉檢查 token 的帳號
export MY_STEAM_ID='7656119xxxxxxxxxx'
uv run python -m steam_family_export
unset STEAM_WEBAPI_TOKEN MY_STEAM_ID
```

`MY_STEAM_ID` **不是必要的**；預設從 token 的 JWT `sub` 取得。若設定就必須填入真實的 17 位 SteamID64，以上含 `x` 的值只是佔位字串，不能直接使用。不設定時刪除該行即可。程式也會檢查 library response 的 `owner_steamid` 必須與 token 帳號相同；不一致就拒絕輸出。

一般語法 `export STEAM_WEBAPI_TOKEN='你的值'` 也能使用，但可能留下 shell history；請優先使用隱藏輸入。環境變數會傳給子程序，使用後 `unset`。不要在 `set -x` 或會記錄環境變數的除錯環境執行。

可選的 CLI 參數：

```zsh
uv run python -m steam_family_export --output-dir ./data/export --language english
uv run python -m steam_family_export --help
```

預設遊戲名稱語言為 `tchinese`，實際名稱仍以 Steam 回應為準。輸出到指定目錄：

- `steam_family_library.csv`：UTF-8，布林值 `true/false`，擁有人 SteamID 用分號分隔。
- `steam_family_library.json`：`metadata` 與 `games`；擁有人為字串陣列，保留中文、日文及 emoji。
- `data/raw/<UTC時間-隨機識別碼>/`：各 API 回應的 JSON body。每次執行分開保存，避免混用不同帳號或不同批次的回應。

輸出檔預設權限 `0600`。不保存 request URL、headers、token endpoint 回應或 cookies。raw body 如意外含 credential 欄位或已提供的 token，會遮蔽，因此是保留內容的 JSON 回應（重新排版），不是 wire bytes。`.gitignore` 忽略預設輸出、raw、環境檔及 token 檔；自訂輸出位置請自行確認 Git 忽略規則。即使沒有 credential，這些資料仍含你和家庭成員的 SteamID，請審慎分享。

## 資料與認證

使用 `access_token=<webapi_token>`，不使用 developer Web API key。只連線 `https://api.steampowered.com`。HTTP client 不使用瀏覽器登入狀態、環境代理、`.netrc` 或自動 redirect。

| Endpoint | 方法 | 用途 |
| --- | --- | --- |
| `IFamilyGroupsService/GetFamilyGroupForUser/v1/` | GET | 目前登入帳號的家庭 ID；不指定其他人的 SteamID |
| `IFamilyGroupsService/GetSharedLibraryApps/v1/` | GET | 家庭整合 library，含自己擁有的遊戲及所有擁有人 |
| `IFamilyGroupsService/GetPlaytimeSummary/v1/` | **POST** | `entries` 中 `steamid == 本人` 的遊玩秒數與時間戳 |
| `IPlayerService/ClientGetLastPlayedTimes/v1/` | GET | 嘗試取得登入玩家的 `playtime_forever` 分鐘數及首次/末次遊玩時間 |

這四個方法都屬於 **undocumented API**；Steamworks 有官方 Web API 說明與 `GetOwnedGames` 文件，但沒有提供這些方法的公開穩定性承諾。本工具不呼叫 `GetOwnedGames`，不以「本人擁有的遊戲清單」限制遊玩紀錄。

`GetSharedLibraryApps` 設定 `include_own=true`、`include_excluded=true`、`include_non_games=false`、本人 `steamid`、家庭 `family_groupid`，不設定 `max_apps`，不使用現行協定中不存在的 `include_free`。取得完整候選清單後保留 app_type=Game，並保留本人擁有的遊戲或 `exclude_reason=0` 的共享遊戲。非共享的本人遊戲仍有自己的授權；家庭其他人的 excluded 遊戲不輸出。未回傳的遊戲無法憑空重建。

`family_owned` 定義為**至少一位其他家庭成員擁有**；因此自己與其他成員共同擁有時，`owned_by_me` 與 `family_owned` 都是 `true`。只有自己擁有時，後者為 `false`。`owner_steamids` 保留 API 回傳的所有擁有人。

## 如何確認時間屬於本人

每款遊戲另外輸出 `playtime_steamid`、`playtime_source`、`playtime_scope`、`my_playtime_seconds`，讓數字可追溯：

1. 檢查 JSON `metadata.my_steamid` 與每列 `playtime_steamid` 是你的 SteamID。
2. `ClientGetLastPlayedTimes.games` 是以**同一個已認證 token** 呼叫，request 沒有指定遊戲擁有人，也沒有提供其他玩家的 SteamID。用 `playtime_forever / 60` 得到小時；其 first/last 使用 `first_playtime`、`last_playtime`。
3. 若上述 endpoint 沒有該 appid，就使用 `GetPlaytimeSummary.entries` 中你的 SteamID、該 appid 的 `seconds_played`，分鐘=`/60`、小時=`/3600`。**完全不使用 `entries_by_owner`，也不加總其他成員的 entry。** first/last 使用 `first_played`、`latest_played`。
4. `player_lifetime` 表示選用了 `playtime_forever`；`family_summary` 表示家庭 API 報告的累積秒數，**不能保證涵蓋加入家庭之前的全部歷史**；`unknown` 表示兩個玩家來源都沒有紀錄。
5. 在 raw 中查找同一 appid 與本人 SteamID（家庭紀錄），或同一 appid 的 `playtime_forever`（本人 endpoint），並對照你在 Steam 個人頁上已知的借玩遊戲時間。測試涵蓋「別人擁有、本人玩了 87 小時」；它不會使用擁有人自己的時間或 library 的 `rt_playtime`。

缺少 entry 時，CSV 時間留空、JSON 為 `null`。**缺少資料不等於從未玩過**；只有 API 明確回傳零時才輸出零。first/last 都是 UTC ISO 8601；timestamp 為零或省略時輸出 `null`。首次日期未知時不使用購買日期代替。某款有多位擁有人時仍然只輸出一列，時間不乘以擁有人數。`player_lifetime` 的來源解析度是分鐘，`my_playtime_seconds` 是分鐘乘以 60，並非額外取得精確到秒的歷史。

## 過期與故障

- token 有 `exp` 且已過期：本機立即停止。到前述 Steam 網頁 endpoint 重新取得 token，再隱藏輸入或重設環境變數；不必安裝 Steam Client。
- HTTP 401/403 或認證相關 EResult：可能過期、登入被撤銷、token 權限不足或家庭資格有變；先在商店確認登入帳號與家庭狀態，再取得新 token。不要假設固定 24 小時有效期。
- 尚未加入家庭：清楚報錯；待接受邀請不會被當成已加入。
- HTTP 429：稍後重試。其他 HTTP error、非 JSON、欄位型別改變、玩家歸屬不一致或重複玩家/appid：報錯，避免產生錯誤統計。
- 額外的 `ClientGetLastPlayedTimes` request 若被拒絕，會顯示警告並使用家庭玩家紀錄；若回傳的 schema 有問題，轉換會停止。JSON metadata 記錄 request 失敗理由（不含 credential）。
- 核心 API 或轉換失敗時不覆寫最終 CSV/JSON；之前的輸出可能仍存在，請以成功訊息與 `metadata.exported_at` 判斷新舊。成功取得的 raw 回應會留在該次目錄供檢查。

## 已知限制

「可玩」指目前授權與家庭共享資格，不代表某一秒仍有空閒的遊戲副本，也不代表可在 macOS 執行。未額外計算同時使用副本、兒童家長控制、Steam Client 安裝狀態或 OS 相容性。Free-to-play、試玩、DLC、私人/區域授權由 Steam 回應與 exclusion reason 決定，沒有把商店所有免費遊戲當成家庭 library。

Valve 沒有承諾這些 unofficial API 的歷史範圍與完整性。`ClientGetLastPlayedTimes` 的網頁 token 可用性及借用遊戲覆蓋需要你的本機登入後實測；空 `games` 不會被當成「所有遊戲都沒玩過」。家庭紀錄不足時工具會保留所有符合資格的 library 遊戲，並明確標示未知，而不是編造完整歷史。

## 測試與專案結構

```zsh
uv run python -m unittest discover -s tests -v
```

標準 library `unittest` + `httpx.MockTransport`，不需要 token 或網路。fixture 使用虛構 SteamID；測試包含自己的遊戲、借玩 87 小時、明確未玩過、多位擁有人、缺失紀錄、Unicode，以及 HTTP/request 格式、raw 遮蔽、帳號交叉檢查與 CLI 輸出。

```text
steam_family_export/
  api.py          API request 與 HTTP/EResult 處理
  auth.py         只解析使用者明確提供的 token
  transform.py    玩家歸屬、遊戲篩選與時間轉換
  output.py       credential 遮蔽與 UTF-8/0600 檔案輸出
  cli.py          CLI 流程
  errors.py       可安全顯示的錯誤
tests/fixtures/family.json
tests/test_transform.py
tests/test_api_cli.py
docs/api-research.md
pyproject.toml
uv.lock
```


## 願望清單模式

```zsh
uv run python -m steam_family_export --wishlist
```

`--wishlist` 改為匯出本人願望清單，不會呼叫 Family API；原本沒有旗標的家庭匯出流程維持相同。沿用隱藏輸入或 `STEAM_WEBAPI_TOKEN`，本人 SteamID 由同一個 token 取得；`MY_STEAM_ID` 仍可選用交叉檢查。

使用 `IWishlistService/GetWishlist/v1/` 取得完整 `items`，以 `IStoreBrowseService/GetItems/v1/` 每批最多 50 個 appid 匿名查詢名稱；名稱與價格 request 不傳送 token。`--language` 與 `--output-dir` 同樣適用；商店查詢必須帶國家代碼，預設 `TW`，可用 `--country-code US` 改為其他商店區域。此參數不會更改你的 Steam 帳號地區。這些 API 屬於 unofficial API，格式與未登入探測結果見 [研究紀錄](api-research.md#願望清單功能補充)。

輸出 `steam_wishlist.csv` 與 `steam_wishlist.json`，不覆寫家庭匯出檔。JSON 包含 `metadata` 與 `items`；各項目欄位如下：

| 欄位 | 說明 |
| --- | --- |
| `appid` | Steam app ID |
| `game_name` | 商店回傳的名稱；未知為 null／CSV 空白 |
| `priority` | Steam 回傳的排序值，保留原值 |
| `date_added` | 加入願望清單日期，UTC ISO 8601；零或未知為 null |
| `date_added_unix` | 原始 Unix 秒數，未知預設 0 |
| `wishlist_steamid` | 此次查詢的本人 SteamID |
| `store_url` | 該 appid 的 Steam 商店連結 |

按非零 priority 升冪輸出，0 排在後面；同順位依 appid 排序。商店名稱查詢失敗或項目下架時保留 appid，不會刪除願望清單項目。HTTP／限流失敗會停止繼續查詢名稱並顯示警告，JSON `missing_name_count` 與 `name_lookup_errors` 記錄結果。schema 變更則停止匯出，避免錯誤解讀資料。

成功取得的 wishlist 與各批商店 JSON 回應分別存到 `data/raw/<本次識別碼>/GetWishlist.json`、`GetItems_wishlist_0001.json` 等檔案，套用相同 credential 遮蔽與 0600 檔案權限。願望清單輸出也已加入 `.gitignore`。

空願望清單會產生只有標頭的 CSV 與 `items: []` JSON。私人清單或權限不足的 HTTP／EResult 會明確報錯，不當成空清單；請確認 token 所屬帳號與隱私設定，不需要把 token 傳回聊天。


## 獨立願望清單價格 CSV

執行相同的 `--wishlist` 指令，會另外產生 `steam_wishlist_prices.csv`。原本願望清單 CSV／JSON 的各項目欄位不加入價格；價格檔包含原本欄位，再增加以下欄位：

| 欄位 | 說明 |
| --- | --- |
| `original_price` | Steam 主要購買方案原價，使用幣別單位，固定兩位小數 |
| `current_price` | 同一方案目前售價 |
| `discount_percent` | API 回傳的折扣百分比；要求隱藏時留空 |
| `currency` | GetPriceStops 回傳的幣別，例如 TWD；不從貨幣符號猜測 |
| `country_code` | 實際商店查詢地區，預設 TW |
| `price_checked_at` | 該批查詢完成的 UTC ISO 8601 時間；未查詢項目留空 |
| `price_status` | priced／free／no_price／unavailable／lookup_failed／currency_unknown |
| `purchase_option_name` | Steam 選出的主要購買方案名稱 |
| `packageid`、`bundleid` | 對應方案 ID；不適用時留空 |
| `formatted_original_price`、`formatted_current_price` | Steam 原始格式化價格文字，方便核對 |

例如要查美國商店：

```zsh
uv run python -m steam_family_export --wishlist --country-code US --language english
```

商店 API 的數值單位為百分之一幣別單位，原始 int64 JSON 可能是字串。本工具使用 Decimal 除以 100，避免浮點換算誤差。採 `best_purchase_option`，不從所有購買方案挑最低價，因為候選清單可能包括其他版本、套裝或不同 DLC。方案名稱及 ID 隨價格附上；實際登入結帳價格仍可能受已擁有遊戲的套裝折扣影響。

明確免費且不是暫時免費的項目可輸出 0.00。未發售且沒有預購價格、下架、地區無法取得價格等情況留空；有預購價格的未發售遊戲仍可輸出價格。幣別查詢失敗時不猜測幣別或輸出沒有幣別的數字，並以 currency_unknown 標示。

原價欄位缺失時不以售價代替。每款保留一列；沒價格不會被排除。價格是當次查詢的快照，會隨折扣與區域變動。獨立價格 CSV 採 UTF-8、0600 權限並已加入 `.gitignore`；GetPriceStops 和各批 GetItems 回應保存到當次 raw 目錄。

## 整體評價

正常匯出遊戲庫或願望清單會自動附帶評價。願望清單沿用原本的商店批次查詢；遊戲庫另以每批 50 個 appid 查詢公開商店資料，不送 token。

`uv run python -m steam_family_export --refresh-reviews` 在 `--output-dir`（預設目前目錄）尋找已有的三份 CSV 與兩份 JSON，只更新評價欄位。此模式不要求登入、不讀取 token 環境變數，也不更新原本的價格、時數或擁有資料。

| 欄位 | 意義 |
| --- | --- |
| `review_positive_percent` | Steam 回傳的整數百分比 0–100，例如 85 表示 85%；不是 0–1 比率 |
| `review_description` | Steam 的評價描述，例如「極度好評」；依 `--language` 決定描述語言 |
| `review_count` | 整體評價樣本數；沒有可用資料時留空 |
| `review_checked_at` | 此批評價實際查詢的 UTC 時間 |
| `review_status` | `available`、`no_reviews`、`unavailable` 或 `lookup_failed` |

取 `reviews.summary_filtered`，不使用 `summary_language_specific`，也不是最近 30 天評價。統計受到 Steam 商店評價篩選規則影響，與不帶篩選的所有評論可能不同。Steam 的描述同時考慮樣本數，程式不自行只按百分比猜「極度好評」或「壓倒性好評」。無評論的百分比留空；有評論但正面比例為 0% 則保留數值 0。JSON 使用 `null` 表示空值。

HTTP 或限流失敗會停止後續批次，保留全部遊戲並標成 `lookup_failed`，不把缺資料當負評。API schema 不符會明確報錯。原始商店回應保存在該次 `data/raw/` 目錄，所有輸出檔仍使用 0600 權限並受 `.gitignore` 排除。
