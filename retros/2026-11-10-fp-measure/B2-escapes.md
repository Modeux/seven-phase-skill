# B2：流出缺陷取證（候選 2026-10-27～11-10）

範圍：brief 列的 9 個 commit／任務＋進行中的 `home-loading-speed`。只讀；原始輸出在 `tmp/retro-1110-fp/B2-src/`（`stats.txt`、各 slug 的 `*.PROGRESS.md`／`SPEC.md`／`RESEARCH.md`／`REVIEW.md`、`a88a5ed20.diff`）。

定義（本檔一律照這幾條）：
- **潛伏天數**＝引入 commit 日期 → 第一次被發現的日期（不是到修好的日期）。發現日只寫得出上界時標 `≤`。
- **流出**＝第一個發現者不是審查／走一趟／測試／agent 研究。第一個發現者是審查或測試的列進「非流出」，即使該缺陷當時已在 prod。
- 「引入時有沒有走審查」照 brief：引入日早於 2026-09-01 →「流程前」；前後 3 天內有同主題 `docs(prps): … 七階段記錄` →「有」；其他 →「查不到」。

## 逐列表（流出＋發現者查不到）

| # | 修正 commit／任務 | 症狀（白話） | 誰先發現 | 根因一句 | 引入的 commit 與日期 | 引入時走審查 | 潛伏天數 | 漏網類別 | 最早能攔的閘 | 證據 |
|---|---|---|---|---|---|---|---|---|---|---|
| E1 | `68a9bd791`／expression-reaction-gates（包 A） | 使用者花鑽石買的反應動畫，mature 角色（startle／blush_away／pout）與 gentle 角色（pout）永遠不播；prod 已套用 17 支、19 鑽石、≤3 位使用者 | 查不到（記錄只寫「外部回饋五點」，沒寫是誰；查證日 10-29） | 人設門檻表把這幾個 key 改成 `none`，但購買路徑（`GenerateOneshots`／`validateOneshotKeys`）與工作台「补齐全部反应」都不看人設：賣的那一面和播的那一面沒有互相對照 | `62f234059` 2026-08-04（門檻表；購買入口 `4a2640959` 08-03 已存在） | 流程前 | ≤86 | ④ 跨層接縫 | PLAN 測試矩陣 | `git log --diff-filter=A -- reaction_threshold.go` → `62f234059`；`tmp/expression-feedback/findings.md` 第 5 點；REVIEW 流出分析「販售面與播放面之間沒有任何測試釘『已套用的素材必可播』」；RESEARCH §2「測後同判準檢驗」查無紀錄 |
| E2 | `68a9bd791`（B1①） | 角色「緊張」退場後，臉多插 8 秒「剛哭過、眼睛還濕」 | 查不到（外部回饋，10-29） | `needsRecoveringWindow` 的觸發條件是整個 `NEGATIVE_EXPRESSION_BINS`（hurt 加 tense），不只 hurt | `8fb188710` 2026-08-04 | 流程前 | ≤86 | ① 狀態組合沒列進測試 | PLAN 測試矩陣 | `git log -S'needsRecoveringWindow'` → `8fb188710`；RESEARCH B1「沒有任何 spec 釘 tense→recovering（D2 區塊全從 `atHurt` 出發）」 |
| E3 | `68a9bd791`（B2） | 關係否決讓臉停在受傷時，同一句仍播 brighten 這類正向反應 | 查不到（外部回饋，10-29） | `coerceReactionCoherence` 只看這句的語氣，不看否決之後真正顯示的臉；它的註解前提「the ambient bin already follows it」在否決時不成立 | `4a2640959` 2026-08-03（`ResolveExpressionBin` 的否決臂與 coerce 都在這支，blame 確認） | 流程前 | ≤87 | ① 狀態組合沒列進測試（既有測試反而把 `wantReaction: brighten` 釘成期望值） | PLAN 測試矩陣 | `git blame 68a9bd791^ -- expression_delivery.go` L70–78 → `4a2640959`；RESEARCH B2 引 `inline_tag_resolve_test.go:186-192`、`reaction_classifier_test.go:442-444` |
| E4 | `68a9bd791`（B3） | 角色說一句語氣平淡的話，臉就從溫暖／親密掉回 neutral；gentle／energetic 的常駐暖臉被蓋掉；WS 推來的臉和重整後的臉不同 | 查不到（外部回饋，10-29；原回報只講人設，影響全站是 research 補的） | `ResolveExpressionBin` 只要語氣臉非空就取代關係臉，三條路又都把缺值補成 `"neutral"`；state GET 對同一個缺值回關係臉 | `4a2640959` 2026-08-03（人設 override 是 `62f234059` 08-04 加的，inline 路是 `72248cdd2` 08-07 加的） | 流程前 | ≤87 | ④ 跨層接縫（inline／classifier／state GET 三處各自算臉） | PLAN 測試矩陣 | blame 同 E3；REVIEW 流出分析「三處各算各的臉，沒有跨 lane 對同一輸入比結果的守衛」 |
| E5 | `23e03b246`（止血）＋`ef2d435cb`（救回）／message-retention-restore | 付費用戶 90 天前的聊天訊息每天 UTC 00:00 被排程藏掉，免費用戶反而不清；19 人、357 間房、21,135 則（要救的 19,102 則） | prod 使用者或 prod 資料：10-30 chat-history-loading 第 1 階段做 prod 唯讀聚合時發現（這次查詢的起因是 PM 在正式站回報 E6） | credits v1.4 重構拿掉了各方案的保留天數，查詢改成「有效訂閱者、寫死 90 天、沒訂閱就跳過」，和 `000055` 的原設計（free 7 天／vip 永久）相反；`TestCleanupHistoryWorker_Integration` 的 `NoSubscription_Skipped` 照實作寫，把錯的行為釘成規格 | `aed925e63` 2026-04-16（`ListUsersWithMessageRetentionCursor`：`JOIN user_subscriptions … status='active'`、`CAST(90 AS INT)`，註解寫「Non-subscribed users are skipped」） | 流程前 | 197（從第一筆被藏的 08-06 算起實際藏了 85 天） | ④ 跨任務接縫（credits 重構順手改了保留期；也可算 ⑥「測試照實作寫」，REVIEW 落點寫「政策意圖沒有成文來源」） | PLAN 測試矩陣（以 `000055` 的設計當期望值；10-12 prod 已看得到「軟刪 8,097 列」，見下方註 1） | `git show aed925e63 -- messages.sql.go`；`git log -S'cleanup_history_daily'` → `937e0a4a5`（03-13 建 worker）；message-retention RESEARCH §1（prod 數字）；chat-history-loading 第 2 階段紀錄「`aed925e63`（04-16）改成付費 90 天暫代寫法」；REVIEW 流出分析第 1 列 |
| E6 | `a120c63fb`／chat-history-loading（對話列表） | 對話列表按「加载更多」一直回同一批 20 間、按鈕不會消失，後面的房永遠載不到（PM 帳號 140 間房，其中 91 間沒有最後訊息） | prod 使用者或 prod 資料（PM 在正式站實測；日期未記，任務 10-30 開工） | `loadMoreConversations` 拿「前端重新排序後的最後一筆」當書籤；排序鍵是 `last_message_at \|\| updated_at`、沒有 id 第二鍵；也沒有「這頁零新增就停」 | `e4a297608` 2026-05-12（tk；commit 訊息寫「Update commit message guidance」，實際改了 store 319 行，見註 2）；排序鍵本身是 `4f142d9e6` 03-17 | 流程前 | ≤171 | ③ prod 資料形狀（要超過 20 間房，而且有 `updated_at` 被刷到中間的 NULL 房；這種房是 E5 每晚製造出來的。本機 creator.demo 53 間房沒有這種形狀） | PLAN 測試矩陣 | `git blame a120c63fb^ -- chat-workspace.store.ts` L7108–7130 → `e4a297608`，L7768–7774 → `4f142d9e6`；chat-history-loading PROGRESS「開工前已確定的證據」（正式站卡在同一 cursor `019fcad0…@2026-08-05`）；RESEARCH §一、§二 |
| E7 | `a120c63fb`（batch） | 聊天列表缺的角色累積超過 50 個時，`/characters/batch` 回 400，錯誤被 `error: () => undefined` 吞掉，之後角色名永遠補不進來（列表沒名字、搜不到角色名） | **查不到**（列在「開工前已確定的證據」，RESEARCH 來源欄寫 grep，是 PM 回報還是 agent 讀碼查不到） | 前端一次送出全部缺的 id，後端上限 50 | `8ffd3a419` 2026-03-30（後端 `maxBatchSize`）；前端 `loadConversationCharacters` 是 `4f142d9e6` 03-17（當時是不是也一次全送沒有逐版核對，低信心） | 流程前 | 查不到 | ④ 跨層接縫 | PLAN 測試矩陣（`a120c63fb` 後來補了 `TestBatchGetMaxIDs_EqualsTheFrontendGroupSize` 兩邊對釘） | `git log -S'maxBatchSize' -- character/handler.go` → `8ffd3a419`；RESEARCH §三 |
| E8 | `5d19f4743`／gated-card-corner-bleed | iPhone Safari 上，18+ 受閘卡片的模糊圖畫到圓角外，四角露出方塊 | PM 測試機（推定：PROGRESS 寫「PM 11-07 iPhone 截圖」「最後由 PM 真機驗收攔到」，沒寫是測試機還是正式站） | 推論（由真機結果推得，沒查引擎原始碼）：iPhone Safari 把 `filter: blur` 的圖獨立合成；host 有 `overflow: hidden` 但沒圓角，媒體盒有圓角但不是 stacking context | `4b1f27f18` 2026-11-07 09:39（`MediaGatePreview`） | 有：`d413c80c5`（pm-feedback-4 七階段記錄，11-07 09:39:55） | 0 | ② 只有真引擎看得到（Chromium 與 Playwright WebKit 都不紅） | 真瀏覽器走一趟（要真 iPhone） | gated-card PROGRESS §診斷、§流出分析（W4 與 `static-cards.mjs` 只量四邊中點與中心、沒量角；pm-feedback-4 的真機殘留清單沒列圓角）；`git show -s 4b1f27f18` |

註 1（E5 有更早的訊號）：`a048bbbf0`（10-12）寫進 BACKLOG 的「使用者刪掉的訊息從沒被真正清掉」已經記下 prod「軟刪 backlog 8,097 列」，但當時把它們當成使用者自己刪的，沒認出是排程刪的。所以 prod 資料 10-12 就看得到，10-30 才被正確判讀。
註 2（記錄與 commit 不符，照 commit）：`e4a297608` 的訊息是「Update commit message guidance」，但 diff 是 chat-workspace 四檔、+1052／−327，裡面就有 `loadMoreConversations`。

## 統計（流出＝E1–E6、E8，共 7 筆；E7 發現者查不到、不算進來）

- 漏網類別：① 2（E2、E3）｜② 1（E8）｜③ 1（E6）｜④ 3（E1、E4、E5）｜⑤ 0｜⑥ 0
- 誰先發現：查不到（外部回饋）4（E1–E4）｜prod 使用者或 prod 資料 2（E5 prod 資料、E6 PM 在正式站）｜PM 測試機 1（E8，推定）｜監控告警 0｜owner 自己用 0
- 引入時有沒有走審查：流程前 6（E1–E6）｜有 1（E8，`d413c80c5`）｜查不到 0 →**引入時走過審查仍流出：1 筆（E8）**
- 潛伏天數（7 筆）：0、86、86、87、87、171、197 → **中位 87、最大 197（E5）**；只看發現者確定的 3 筆（E5、E6、E8）：中位 171、最大 197
- 最早能攔的閘：PLAN 測試矩陣 6（E1–E6）｜真瀏覽器走一趟 1（E8）｜整合測試、廠商探針、部署後 prod 查詢、事前攔不到各 0（E5 另有「部署後 prod 查詢」可攔，見註 1）
- 同一個 7 筆裡，E1–E4 是同一包外部回饋（同一組表情程式），E5 與 E6 互相牽連（E5 製造的 NULL 房讓 E6 出現）

## 非流出清單（第一個發現者是審查／測試／研究，不進統計）

| 修正 commit／任務 | 缺陷 | 第一個發現者與日期 | 引入（有查才寫） | 備註 |
|---|---|---|---|---|
| `a88a5ed20`／purchase-funnel-events | `dd25ff2d6`（10-27 16:04）就地改了 `924ae6777`（14:08）建的 migration `20261027045934`（加 `plan_modal_shown`、FK 改 SET NULL）；測試站如果已經套用第一版，不會重跑，`plan_modal_shown` 會撞 CHECK | owner 看改動時指出，10-27（PROGRESS「（owner 10-27）…測試站可能已套用」） | `dd25ff2d6` 2026-10-27；±3 天內沒有同主題的七階段記錄（PROGRESS 寫第 6 階段未開始）→ 查不到 | 15 分鐘內修好；測試站有沒有真的跑過 `dd25ff2d6` 那版查不到 |
| `4bc24da3a`／bookmarks-reload-loop | 收藏清單讀取一直失敗時無上限重打 | age-gate-pm-rules-v2 審查 lane-ui「範圍外的既存發現」第 1 條，BACKLOG 由 `0ed68c29f` 寫入，10-21 | `70055bda6` 2026-03-17（blame：`effect`／`ensureLoaded`／`loading` 都在這支），流程前，到發現 218 天 | 已在 prod 7 個月，但第一個發現者是審查；PROGRESS 稱「本包修的上線 bug」 |
| `4bc24da3a` | 讀取在途時換帳號，`loading` 永久卡住（R3，既存）；直接換帳號而新帳號讀失敗時，畫面留著前一個帳號的收藏（R2） | 第 6 階段審查（R3）；age-gate-pm-rules-v2 走一趟 E1／E3（R2 源頭，10-21 標「未查證」） | 未查 | |
| `a120c63fb` | SQL 第三臂 `last_message_at IS NULL AND … id < $3`：從有訊息區翻進 NULL 區時漏掉 id 較大的房（prod 伺服器端 3 人、18 間） | 寫整合測試時發現，10-15（`fc1e585e8` age-gate 的測試 L311 把它寫成 `WHY:` 註解、測試繞開 NULL 房） | `0bf09b1ac` 2026-03-10（blame），流程前，219 天 | 被發現後寫成「決策」繼續留著；RESEARCH 稱「記錄的是缺陷，不是決策」 |
| `a120c63fb` | 訊息一變就無條件捲到底（往上讀時串流會把人拉回底部）；socket 重連與旅程補讀把已載入的舊頁清掉 | chat-history-loading 第 1 階段研究，10-30（RESEARCH §五-1、§五-2） | 未查 | |
| `68a9bd791` | 待機時 muse 在受傷臉上也播（D5 在待機路徑的漏洞） | 第 1 階段研究，10-29（RESEARCH B2 最後一點） | 未查 | 外部回饋沒提到 |
| `81de97170`／db-statement-timeout | 這包是保險，不是修事故。來源是 BACKLOG #77，出處是 chat-history-loading REVIEW 流出分析 R1：第 6 階段 security lane 攔下的搜尋成本放大（惡意構造 11–21 s／次、25 條連線與 River 共用），沒流到 prod。SPEC：「沒有池滿事故的證據」「prod 190 天成功語句最壞 36 秒」 | chat-history-loading 第 6 階段審查，11-01（BACKLOG 由 `8673db7a1` 寫入） | — | **不是被事故觸發的**；36 秒那筆推測是 10-22 告別補索引（`a048bbbf0`）之前的舊紀錄 |
| `81de97170` | 聊天圖片扣款分支只要是錯就標「跳過」：語句被上限切斷時，會把有錢的使用者記成餘額不足（R5） | db-statement-timeout 第 6 階段 silent lane，11-05 | 未查 | 只有在新的 60 s 上限下才會觸發；REVIEW 稱「同類第三次」 |
| `2a501b4ce`／gallery-generic-plan | 熱門列表在同一連線第 6 次起換成 generic plan，延遲隨作品數平方成長（合成資料 1,100 件時 10→190 ms） | explore-revamp 用合成資料量測時發現；owner 10-30 定不在 explore-revamp 修，BACKLOG 由 `11c056e46` 寫入，11-01 | 未查（熱門查詢 `60b3711de` 07-20，折疊 `30eb1a1c4` 09-29，哪一支引入翻轉沒核對） | prod 沒發生（10-30 量：1,027 件、最慢 6 ms）；在 11-05 開工是因為 60 s 上限會把翻轉變成週期性報錯 |
| `home-loading-speed`（進行中） | 從首頁點出去再按上一頁：重打 `/home`、先閃「正在加载…」、捲動與橫欄歸 0、圖再淡入 | explore-revamp 包 3 審查 R11，11-03（BACKLOG 由 `27e941b76` 寫入，owner 11-03 定另案）。**PM 11-09 在正式站回報**「每次點進去都會重新載入」 | 首頁 `providers: [HomeStore]` 隨頁銷毀：`27e941b76` 2026-11-03（審查記錄 `90e95661a`）；探索分頁同形：`70055bda6` 03-17；已快取的圖仍淡入：`0dd5b2cc2` 05-26（這三支是我用 `git log -S` 查的，記錄沒寫 commit） | 審查先發現、明知後照樣上線，6 天後 PM 回報；照第一個發現者的規則算非流出。記錄的結論（RESEARCH C0／C1）：資料 0.15–0.26 s 就回來，「慢」主要是畫面被清空、位置丟失，不是「資料多」；首次開站的 JS 串行源自 Angular CLI 上游 `d396324b4e`（09-08，有 SW 時 `preloadInitial` 預設改 false），不是 repo 內的 commit |

## 判定為新功能、不列缺陷

- `a120c63fb` 是**混合包**：缺陷修正有 E6、E7、SQL 漏房，加上研究時找到的兩個既存 bug；主體是新功能——聊天室往上載更早訊息（後端早就支援游標，前端從沒帶）、`GET /conversations?q=` 伺服器搜尋（原本只濾已載入的房）、每天一條日期分隔線、↓ 鈕改版、`/characters/batch` 切 50 一組（＝E7 的修法）。搜尋只搜已載入的房算 ⑤「需求沒涵蓋」，是誰提的查不到。
- `a88a5ed20` 裡 FK 改 `ON DELETE SET NULL` 與第六事件 `plan_modal_shown` 是分析端 v2 的需求變更，不是缺陷。

## 查不到清單

- E1–E4 的發現者：findings.md、REVIEW、PROGRESS、`0bd1ede95` commit 訊息都只寫「外部回饋」，沒寫提供者，也沒寫收到日期（≤10-29）。
- E7 的發現者與潛伏天數；E7 引入時前端是不是一次全送（沒逐版核對）。
- E6 的 PM 回報日期（只知道 ≤10-30）。
- E8 的發現環境（測試機或正式站）——推定測試機。
- funnel：測試站有沒有跑過 `dd25ff2d6` 版的 migration。
- gallery 翻轉的引入 commit。

## 分類規則沒涵蓋的情形（事實，供判讀）

1. 「已知仍上線」：home-loading-speed（審查 R11 先發現、owner 定另案、PM 6 天後在正式站回報）與 SQL `id < $3`（10-15 測試發現、寫成 WHY 留著、prod 3 人受影響）照「第一個發現者」都算非流出，但使用者確實碰到了。若把它們算流出，流出變 9 筆：home 屬 ⑤（返回快取與位置還原當時沒列為需求）、SQL 漏房屬 ①（翻頁從有訊息區跨進 NULL 區的組合，測試刻意繞開）。
2. 「PM 在正式站」沒有對應的類別：E6 歸「prod 使用者或 prod 資料」，E8 歸「PM 測試機」，兩者都是 PM。
3. 「外部回饋」沒有對應的類別：E1–E4 只能填查不到，所以「誰先發現」的分布受這 4 筆主導。
4. 已在 prod 很久、但第一個發現者是審查的缺陷（收藏重打 218 天、SQL 漏房 219 天）比流出那 7 筆潛伏更久，卻不進流出統計。

## 查過、沒發現問題

- `81de97170`：確認不是由事故觸發（SPEC §方向決議、RESEARCH 挑戰結論 1、BACKLOG #77 出處）。
- `2a501b4ce`：確認 prod 沒有發生翻轉（PROGRESS 結果：「prod 未發生翻轉」，10-30 最慢 6 ms）。
- 引入 commit 的核對（`git blame`／`git log -S`）：E1 `62f234059`（`--diff-filter=A`）、E2 `8fb188710`、E3／E4 `4a2640959`（blame L70–78）、E5 `aed925e63`（diff 內容與 chat-history-loading 第 2 階段紀錄一致）、E6 `e4a297608`／`4f142d9e6`、E8 `4b1f27f18`（與 gated-card PROGRESS 一致）、收藏 `70055bda6`、SQL `0bf09b1ac`。
- 7 筆流出裡只有 E8 落在 2026-09-01 之後；它前後 3 天的 `docs(prps)` 清單已逐條看過（11-04～11-08），對應 `d413c80c5`。
- 記錄與 commit 的說法逐筆比過：E5 數字（23e03b246 訊息與 RESEARCH §1 一致）、bookmarks「每秒約 780 次」已由該任務 PROGRESS 自己更正成本機數字、gated-card 的引入點，都沒有矛盾。唯一不符的是註 2（`e4a297608` 的 commit 訊息）。
