# Steam API 現況研究紀錄 — 2026-10-08

## 證據與驗證程度

使用最新公開協定、Valve 實際提供的 JavaScript，以及對正式 API host 的無 credential HTTP 探測。沒有讀取使用者瀏覽器或登入資料。**沒有登入後 response 的實測樣本**；tests/fixtures 是依据協定建立的合成資料，不是你的帳號資料。這裡區分「目前定義/HTTP 方法已核對」與「你的網頁 token 在正式服務可取得的實際範圍仍須本機驗證」。

主要來源：

1. [Steam Web API 官方總覽](https://partner.steamgames.com/doc/webapi_overview)：官方通用 Web API 說明，並不構成 Families API 文件。
2. [Valve Web UI familygroups 協定的 SteamDatabase 追蹤副本](https://raw.githubusercontent.com/SteamDatabase/Protobufs/master/webui/service_familygroups.proto)：request/response 與 `bConstMethod`/`ePrivilege` metadata。這是从 Valve 網頁擷取的協定，不是 Valve 對開發者的官方 API 支援承諾。
3. [Valve Steam client familygroups 協定追蹤副本](https://raw.githubusercontent.com/SteamDatabase/Protobufs/master/steam/steammessages_familygroups.steamclient.proto)：交叉比對欄位與 exclusion enum。
4. [Valve Web UI player 協定追蹤副本](https://raw.githubusercontent.com/SteamDatabase/Protobufs/master/webui/service_player.proto)：`ClientGetLastPlayedTimes` schema。
5. [Valve 正式 Steam Store JavaScript](https://store.akamai.steamstatic.com/public/javascript/applications/store/main.js)：當天 familymanagement 頁面引用 `v=A4nUhVT930Qn&l=english&_cdn=akamai`。其 `SendMsgAndAwaitResponse` transport 以 service/version 建構 API URL；JSON mode 使用 `input_json`，GET 放 query、POST 放 form body，`access_token` 放 query。頁面預設也可用 protobuf transport；本工具採 JSON mode，不需 protobuf dependency。
6. [Lutris 自己的 Steam Family 整合原始碼](https://raw.githubusercontent.com/lutris/lutris/master/lutris/services/steamfamily.py)：可取得 credential 的網頁 endpoint 与 `data.webapi_token` 路徑。只参考 endpoint，不採用其 cookie/credential 自動保存流程。

為避免只憑過時文章決定格式，另讀取現行 Store 頁面與公開 bundle、當天最新協定。來源檔 SHA-256（本機當次下載）：

```text
webui/service_familygroups.proto
796726e3d5ca9698423096c15121f8cec5d8669c795f3a8393340ab5ac56d604
webui/service_player.proto
f24c7fe6999dddee02ba702ba86e8f571a85db5f5385868204360df8ef773f14
Valve store/main.js?v=A4nUhVT930Qn&l=english&_cdn=akamai
15f0adcc1d1b1654095e61f000ca617efd3f31a63b6b4b11a5d57f0b8499b2c8
```

这些 hash 識別本次查到的公開來源，不是永遠固定的上游版本。公開 Web 搜尋快取与當天直接下載的行號可能不同；此處不以行號作為穩定契約。

## 實際未登入探測

2026-10-08 04:39–04:45 UTC（台北 12:39–12:45），透過 `curl` 对正式 host 發送無 token、無 cookie 的 request：

| Request | 實際結果 | 能證明的事 |
| --- | --- | --- |
| GET `/IFamilyGroupsService/GetPlaytimeSummary/v1/?family_groupid=0` | HTTP 405，`Allow: POST` | 不能把此方法當 GET；伺服器要求 POST |
| POST `/IFamilyGroupsService/GetPlaytimeSummary/v1/`，form `family_groupid=0` | HTTP 401 | 路徑/方法存在，未認證拒絕 |
| GET `/IFamilyGroupsService/GetFamilyGroupForUser/v1/` | HTTP 401 | 未認證拒絕 |
| GET `/IFamilyGroupsService/GetSharedLibraryApps/v1/` | HTTP 401 | 未認證拒絕 |
| GET `/IPlayerService/ClientGetLastPlayedTimes/v1/`，`input_json={"min_last_played":0}` | HTTP 200，`X-EResult: 1`，`{"response":{}}` | 路徑可接受 GET；無登入成功空 response 不代表有玩家資料 |
| GET Store `/pointssummary/ajaxgetasyncconfig` | `{"success":1,"data":[]}` | 未登入没有 token；不能以未登入回應驗證取得 token 的權限 |

Families 401 的通用錯誤文字提及 `key=`，**不据此推論 developer API key 能讀取家庭資料**。Web UI metadata `ePrivilege=1` 和 transport 的 `access_token` 才是用户登入認證方式的依據。網頁 token 取得途徑另以現行整合原始碼交叉比對；本研究沒有冒充登入後成功測試。

## 實作採用的 request

host 統一為 `https://api.steampowered.com`；`access_token` 为使用者明確提供的 Store `webapi_token`。不送 browser cookies、不送 `spoof_steamid`，不使用開發者 key。

GET 格式：

```text
GET /<Service>/<Method>/v1/
query: access_token=<使用者本機提供>, input_json=<JSON>, origin=https://store.steampowered.com
```

POST 格式：

```text
POST /IFamilyGroupsService/GetPlaytimeSummary/v1/
query: access_token=<使用者本機提供>
Content-Type: application/x-www-form-urlencoded
body field input_json={"family_groupid":"..."}
```

Valve 的 transport 可使用 multipart form；本程式使用 httpx 的 URL-encoded form field `input_json`。登入後此格式的接受情況未實測，任何 400/405/認證錯誤會明確報告；不靜默產生零時間結果。以下 JSON 是 schema 範例，`...` 不是可直接使用的 ID。

### GetFamilyGroupForUser v1

Web metadata：`bConstMethod=true, ePrivilege=1`。本工具 request：

```json
{"include_family_group_response": true}
```

不指定 request `steamid`，讓服務使用登入者。response 讀取 `family_groupid`、`is_not_member_of_any_group`。`family_group` 可含 `members[].steamid`、`role` 等。`latest_joined_family_groupid` 与 pending invites **不等於目前有效家庭**，不拿来當 fallback。

### GetSharedLibraryApps v1

Web metadata：`bConstMethod=true, ePrivilege=1`。本工具 request：

```json
{
  "family_groupid": "...",
  "steamid": "本人SteamID64",
  "include_own": true,
  "include_excluded": true,
  "include_non_games": false,
  "language": "tchinese"
}
```

`max_apps` 是 optional uint32，省略以避免顯式截斷。協定 field 4 沒有 `include_free`，不使用舊文章這個參數。

response：`owner_steamid` 与 `apps[]`。app 包含 `appid`、`owner_steamids[]`、`name`、`exclude_reason`、`app_type`，還可能有 `rt_time_acquired`、`rt_last_played`、`rt_playtime`。後三者在本工具不作為個人時間來源：避免把取得時間當首次遊玩、把未核實的單位/歸屬當個人歷史。

`exclude_reason` default 0 代表 Included；其他原因可能是不可共享、私人、免費遊戲、不同 app 類型、未發售、不同授權條件等。不能简化為「owner 手動 excluded」。只保留本人有授權的 Game 或家庭可共享 Game；其他人的非零 reason 排除。

### GetPlaytimeSummary v1

Web metadata：`ePrivilege=1`，没有 `bConstMethod=true`；實際 HTTP 也證明要求 POST。request 只有 `family_groupid`。

response 两个不同陣列：`entries`、`entries_by_owner`。entry 欄位：`steamid`、`appid`、`first_played`、`latest_played`、`seconds_played`。程式嚴格採 `entries` 中本人 SteamID，按 appid join library；**忽略 `entries_by_owner`**。秒數除以 60/3600；日期是 Unix 秒數轉 UTC。同玩家/同 appid 重複 entry 時停止，不猜測应该加總。

協定只说明這是家庭 playtime summary，沒有對加入前、離線、旧 Family Sharing 或全部終生歷史的完整性保證。可確定玩家歸屬的紀錄也不能被過度聲稱為完整終生時間。

### ClientGetLastPlayedTimes v1

额外發現的本人玩家紀錄来源。Web metadata：`bConstMethod=true, ePrivilege=1, eWebAPIKeyRequirement=1`。request schema 只有 `min_last_played`，没有 `steamid`；因此必須用同一個本人 token 呼叫，不可切換為 owner ID。

```json
{"min_last_played": 0}
```

response：`games[]`，各 entry 有 `appid`、`playtime_forever`、`first_playtime`、`last_playtime` 与平台分項等。工具按 Steam `playtime_forever` 分鐘慣例處理，只取總時間，不加總平台時間，也不與家庭 summary 相加。其授權來源並非 GetOwnedGames，因此没有程式側「借玩但不擁有就刪除」的問題。

`playtime_forever` 的時間單位依 Steam 同名 playtime 欄位慣例；**此 endpoint 的借玩覆蓋、網頁 token 權限与實際數值仍需登入後与已知遊戲時間核對**，schema 本身不是完整性證明。若回傳 appid，優先使用此來源；沒有就採家庭玩家 entry；兩者皆缺即 null。output 每列保留 source/scope，讓這項不確定性可見。

## 官方與 unofficial 的界線

官方 documented 的是一般 Web API 說明與 [IPlayerService/GetOwnedGames](https://partner.steamgames.com/doc/webapi/IPlayerService)。本工具使用的四個方法與 Store token configuration route 均没有公開支援契約，应视为 undocumented。SteamDatabase 是 Valve 協定的追蹤来源，Lutris 是實作者自己的整合源码，兩者均不能代表 Valve 官方保證。

本機安全與 correctness 驗證通过 unittest/mock 完成。使用者在本機第一次執行時才会取得真正登入後的 raw schema；工具提供身份交叉檢查、明確 schema error 与未知時間標示。不要为了研究將 token 或原始私密資料傳回聊天。
