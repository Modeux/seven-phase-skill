# B1 — 10-05～10-21 修正 commit 的流出取證

範圍：brief 列的 13 個 commit，拆成 17 列。只讀取證，沒有提任何建議。暫存檔放在 `tmp/retro-1110-fp/B1-src/`（stat-*.txt、prog-*.md、各 docs commit 的 *.diff）。

口徑：
- **潛伏天數**＝引入 commit 日期到修正 commit 日期。發現日期能查到的寫在「證據」欄。
- **誰先發現**照 brief 的定義：被審查、研究讀碼或測試發現的一律標「非流出」，即使缺陷早已在主線上存在多月，不進統計。這類列另外標「既存」（修正前已經在 dev 主線上；部署狀態沒查 prod，只有寫出 prod 數字的才算確定上線）。
- 非流出列的「漏網類別」和「最早能攔的閘」加了括號，只供參考，不計入統計。

## 逐列表

| # | 修正 commit／任務 | 症狀（白話） | 誰先發現 | 根因一句 | 引入 commit 與日期 | 引入時審查 | 潛伏天數 | 漏網類別 | 最早能攔的閘 | 證據 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `aedef0755`（director-image-url，沒有獨立的 docs(prps)） | 聊天影片的兩個導演改走 qwen 備援時，只要帶圖就 100% 被拒（400 Missing Content-Length），動作導演退回念台詞模板，首幀導演退回固定模板 | **prod 使用者回報**（10-05 owner 轉述：「有声留影要么只说台词，没有动作」），再用 owner 授權的 prod Langfuse 查詢定位：動作導演 7/7、首幀導演 59/59 | CDN 對 PNG 做 gzip 而且不帶 Content-Length（根因在 repo 外）。repo 這一側是：DashScope Direct 從 09-02 起會把附圖送出去，聊天兩個導演都沒有對長度設防 | repo 側是 `dbc0d6e8e` 2026-09-02（DashScope Direct 開始送 `req.Images`）。首幀導演的 slot 在 07-02 種入 qwen3.7-plus，當時的定位是「text fallback」（`20260702044911_seed_chat_video_frame_director_slot.sql`），附圖本身在 `10a1b7f42` 07-02 和 `88d422953` 07-16 就有了。CDN 何時開始 gzip：查不到 | **有**：`2b59ed00e` 09-02「model-slot 續命鏈**六**階段記錄」（當時還是六階段流程） | 33（09-02→10-05；CDN 生效日查不到，所以實際上線後潛伏多久無法確定） | ②只有真引擎看得到。真廠商探針其實跑過（`director_vision_smoke_test.go`，2026-08-31 量到 6 個 qwen 都有 image_tokens），但圖用的是 `img.ligongfu248788.asia` 這個非 prod 主機，缺的是 prod CDN 那份回應形狀（也可以歸③） | 廠商真實呼叫探針（拿 prod CDN 的圖） | `B1-src/b848b17d6.diff` L39、L124；`dbc0d6e8e` 的 dashscope/director.go 註解「Vision: req.Images ride as image_url…」；`git show dbc0d6e8e:…/director_vision_smoke_test.go` L51。附帶事實：同一個根因 10-02 已在帮我想（`af450a7a7`／`95fcdd8d7` L521）查到，也寫進了 aiclient/CLAUDE.md，但聊天導演到 10-05 使用者回報後才修，已知未掃共 3 天 |
| 2 | `9cba48eaa`（tk，沒有記錄） | 方案頁、付費牆的劃線價 10-20 改成「只標真實折扣」，10-21 改回 ×2 劃線價 | owner（commit 的 WHY 寫「owner 10-21 reversed the 10-20 pick」；是不是自己用過才改，查不到） | 不是實作錯，是前一天的 owner 拍板第二天翻轉 | `419124953` 2026-10-20（tk「redesign billing offers…」，WHY 寫「owner 2026-10-20 拍板，不畫 ×2 劃線價」）。`419124953^` 的 billing-page 裡沒有任何 ×2 程式碼 | 查不到（10-17～10-23 沒有計費主題的 docs(prps)；pm-feedback-fixes `9ffe19c09` 只有一條定價指南快取，不相干） | 1 | ⑥其他：拍板翻轉，不是缺陷 | 事前攔不到 | `B1-src/9cba48eaa.diff`（billing-helpers.ts WHY）、`B1-src/419124953.diff` |
| 3 | `f9eb9ba5c`（user-identity-p2-portrait 的副產物） | 任何帳號（不論成年狀態）都能用公開的 POST /api/v1/generations，帶自己的提示詞和任意參考圖網址付費生圖或生影片 | 非流出：p2-portrait 第 6 階段的 security lane 讀碼發現（「範圍外既存一條…要另案」）；既存 | 端點把 input_params 原樣交給 worker。後來產圖的功能都改由伺服器組輸入，這個入口卻沒關 | 流程前：`1fa20917a` 2026-03-11（`generation_type` 從一開始就是 `oneof=text image video voice`，`input_params` 必填） | 流程前 | 208 | （⑤ 當時沒有成年閘的需求） | （事前攔不到） | `B1-src/f84073e49.diff` L790、L997；`git show 1fa20917a:…/generation/model.go` L59–61 |
| 4 | `ed80db42c`（chat-image-decider-gender，精簡路） | 留影判斷器不知道角色性別，分類範例全寫「她」，男角色的 scene 常被寫成「她」，有時還多出長髮（探針：qwen 22/58、Seed 3/60） | 非流出：user-identity-card 研究期間 subagent 讀碼報告 `tmp/plan-explore/C-directors.md` 的副發現（≤10-04）；既存 | 判斷器的輸入沒有性別欄位，範例句又寫死「她」 | 流程前：`2bc5e7bc6` 2026-05-12（加入【分類範例】與 actor enum） | 流程前 | 148 | （②要真 LLM 才看得出） | （廠商真實呼叫探針） | `B1-src/6d2323cb7.diff` L928、L1244；`B1-src/prog-chat-image-decider-gender.md`「前提」 |
| 5 | `1b5230d0d`（沒有獨立記錄） | 判斷器把「和戀人牽手」判成第三人（qwen），或把別人家的合照判成使用者入鏡（Seed） | 非流出：同 #4（C-directors 副發現）；既存 | other_named 的定義把「戀人」列成第三人 | 流程前：`2bc5e7bc6` 2026-05-12（`git log -S'戀人'` 最早就是它） | 流程前 | 148 | （②） | （廠商真實呼叫探針） | `B1-src/6d2323cb7.diff` L1244；`git show 1b5230d0d^:…/chat_image_decider.go` L112 |
| 6 | `d5d2a0644`（derivative-w3-director-timeout，七階段） | 高階 w3 影片的分鏡導演每次只給 25 秒，常常逾時，影片退成單一鏡頭（念台詞的臉）甚至失敗 | 非流出：derivative-video-prompt-p1 第 6 階段 timing lane 的 R7（CP1 探針兩側都正好卡在 25.0 秒，約 10-03）；收斂時 prod Langfuse 14 天聚合確認聊天 11/62、二創 6/18 逾時；**既存且已上 prod**（有 prod 數字） | w3 沿用 legacy 單段導演的 25 秒 `cfg.Timeout`，w3 把 MaxTokens 調到 2048 時沒有一起調 | `917da84b3` 2026-09-22（w3 五段骨架＋LLM 分鏡導演，`callCtx` 用 `d.cfg.Timeout`） | **有**：`49e9b8d4e` 09-22 wan3-spicy-prompt-direction 七階段記錄（另有 `6f7e3fe85` 09-21） | 15（發現於約 10-03） | （②真廠商延遲分布） | （廠商真實呼叫探針） | `B1-src/0c8e76fb1.diff` L582、L595、L651；`B1-src/prog-derivative-w3-director-timeout.md` 第 1 階段 |
| 7 | `93e456a95`（chat-video-silent-tail，精簡路） | 靜默片導演成功時，結尾仍要求「說話時嘴看得清楚」，和「不說話」的 mandate 矛盾 | 非流出：derivative-video-prompt-p1 REVIEW L7；既存。owner 盲看 6 對：舊結尾 1 支微動、新結尾 0 支，兩邊都沒有明顯說話，屬一致性修正 | 台詞與靜默三種模式共用同一個 `chatVideoMotionDisciplineTail` | 流程前：「clearly readable while speaking」那句由 `60174c50d` 2026-07-21 引入（靜默模式當時是否就共用這句：**沒有核**，低信心） | 流程前 | 78（低信心） | （⑥ prompt 字句不一致，看不到實際效果） | （PLAN 測試矩陣） | `B1-src/prog-chat-video-silent-tail.md`「出處」「結果」 |
| 8 | `b96d5448b`（breaker-slow-failures，七階段） | 斷路器每 30 秒換一代計數，單次超過 30 秒的失敗永遠不算，廠商出事時不會跳閘 | 非流出：derivative-image-seedream-pro 第 6 階段 timing lane 的 R10（10-04），收斂時 v3 對 gobreaker 原始碼實跑核實；既存 | gobreaker 的 closed 狀態每 `breakerInterval`＝30s 換代，`afterRequest` 會丟掉跨代的結果 | 流程前：`7e292c9b6` 2026-05-19（`breakerInterval = 30 * time.Second`） | 流程前 | 145 | （①狀態組合：失敗時間大於 interval 這格沒測） | （PLAN 測試矩陣） | `B1-src/6eb612c5.diff` L44、L447、L517 |
| 9 | `e7ef536c5`（wizard-spec-merge-repair，精簡路） | 前端整包單元測試在 bundle 階段就編不過；8 支「一般头像也裸露」開關測試巢狀在別的 it 裡，整段沒在跑 | 非流出：前端整包測試（編譯紅），但後面幾個任務都改用 `--exclude` 繞過，各寫一條 BACKLOG | 合併 `0ea7da41` 解衝突時，把 together 側的測試區塊接進 age-gate 側某支 it 的中間，還吃掉收尾 | `0ea7da41a` 2026-10-15 08:55（Merge dev into worktree-derivative） | 查不到（合併 commit，沒找到描述這次解衝突的同主題記錄） | 2 | （④合併衝突解錯） | （既有的前端整包測試已經紅了，被 --exclude 繞過；brief 的閘清單裡沒有對應項） | `B1-src/prog-wizard-spec-merge-repair.md`「定位」「流出分析」 |
| 10 | `8e202d877`(a)（wizard-allages-template-reuse，精簡路） | 全年齡角色沿用帶 18+ 欄位的外觀或人格模板時，建角一定 400 | 非流出：wizard-spec-merge-repair 審查的 codex#1＋spec#1（10-17）；既存 | 伺服器有三道全年齡閘，前端只鏡像了中間那道；人格沿用完全沒有閘；剝除集還少了 `intimacy_preferences_v2` | 人格側是流程前：`338af1888` 2026-05-07（reuse picked template ids，記錄稱 Bug B）。外觀側：`f710b5839` 2026-10-14（新增 `reusableTemplateId`，只鏡像 D5） | 人格側流程前；外觀側**有**：`ef86623aa` 10-14 character-dual-portrait 七階段記錄 | 163（人格）／3（外觀） | （④跨層：前端鏡像伺服器閘） | （PLAN 測試矩陣） | `B1-src/prog-wizard-allages-template-reuse.md`「定位」「流出分析」 |
| 11 | `8e202d877`(b) | 第一次送出失敗後年齡狀態變了（例如變成未成年），重試仍沿用先前建的 18+ 模板，結果 400 | 非流出：同上審查的 codex#2（10-17），本包紅測試 ⑥ 實跑確認 | 重試 id 只在 `setIsNsfw` 時清，有效分級變了不會經過 setter | 查不到（`_submitCreated*Id` 的引入沒追） | 查不到 | 查不到 | （①在途另一個動作） | （PLAN 測試矩陣） | `B1-src/prog-wizard-spec-merge-repair.md` R2；`prog-wizard-allages-template-reuse.md`「紅→綠」⑥ |
| 12 | `4721a18fd`(a) | 外觀模板管理 5 支 API 對格式錯的形象 id 回 400，不是 404（會洩漏 id 格式） | 非流出：character-dual-portrait REVIEW §9 L9（10-14）；既存（BACKLOG 原文寫「上線前就如此」） | handler 回 `invalid visual id format`，和 Public Payloads 規則不一致 | 流程前：`60adcbd8d` 2026-03-24 | 流程前 | 209 | （⑥契約不一致） | （PLAN 測試矩陣） | `B1-src/4721-backlog.diff` |
| 13 | `4721a18fd`(b) | 畫廊關著的時候按工坊發布，被拒的按壓仍會吃限流額度 | 非流出：chat-image-publish REVIEW 名單外（10-19）；既存 | `Publish` 路由只掛 `mutateLimiter`，畫廊開關沒排在限流前 | 查不到（畫廊 kill switch 是 `0bcd25be7` 07-13 加的，路由順序沒有逐 commit 核） | 查不到 | 查不到 | （⑥） | 查不到 | `B1-src/4721-backlog.diff`；`git show 4721a18fd -- …/derivative/handler.go` |
| 14 | `4721a18fd`(c) | 導演附圖探測讀不到檔案時間時，log 寫成「太新」。**行為沒變**（仍然 fail-closed），不是行為缺陷 | 非流出：derivative-video-director-quality REVIEW-2 §8 L7（10-11） | `Age<0` 被歸到 `errSisterTooFresh` | `aedef0755` 2026-10-05 | 有（`b848b17d6` 10-05 記錄寫 director-image-url「8 路審查＋收斂完成」；這個包本身沒有 docs(prps)） | 14 | （⑥ log 文字） | — | `B1-src/4721-backlog.diff` |
| 15 | `523ed00c1`(a) | 工坊查看器的「状态」列對被管理員下架的作品寫「公开」，同畫面的角標卻寫「已被移除」 | 非流出：derivative-public-reuse 收斂名單外 V-front#3（10-18） | `visibilityWord` 只看 visibility，不看 moderation_status | `d6d6a4489` 2026-10-17（二創工坊介面改版第①包，`visibilityWord` 首次出現） | **有**：`06fee9be4` 10-17 derivative-ui-ux 七階段記錄 | 2 | （①removed_by_admin 這個狀態沒列進測試） | （PLAN 測試矩陣） | `B1-src/523e-backlog.diff` |
| 16 | `523ed00c1`(b) | 工坊對「被管理員下架」同時用「已被移除」和「已被下架」兩個詞 | 非流出：同上 V-front#4（10-18）；用詞由 owner 10-18 選定 | 文案沒統一 | 查不到（沒追） | 查不到 | 查不到 | （⑥文案） | — | `B1-src/523e-backlog.diff` |
| 17 | `7269aeb69`（沒有記錄） | 聊天影片三個導演附圖時，首幀和頭像網址（公開網址、常是 18+）會進 Langfuse | 非流出：derivative-video-director-quality PLAN 包② §0 O7（≈10-09～10-11）列進 BACKLOG；owner 10-19 才定要遮 | 二創導演在 10-11 加了「只遮附圖網址」旗標，聊天導演沒跟上 | 查不到精確點（首幀導演附圖從 `10a1b7f42` 2026-07-02 開始；trace 何時開始記 input 沒追） | 流程前（附圖起點） | ≥109（07-02→10-19） | （⑤ owner 10-19 前沒有這條政策） | （事前攔不到） | `B1-src/7269.diff`；`git show 7269aeb69^:.claude/PRPs/BACKLOG.md` L337 |

## 統計（只算流出列，n＝2）

- 流出筆數：**2**。其中 #2 是 owner 拍板翻轉、不是實作缺陷；嚴格算「行為缺陷流出」只有 **1**（#1）。
- 漏網類別：①0、②1（#1）、③0（#1 也可以歸到③）、④0、⑤0、⑥1（#2）。
- 誰先發現：prod 使用者或 prod 資料 1（#1）、owner 1（#2）、監控告警 0、PM 測試機 0。
- 引入時有沒有走審查：有 1（#1，`dbc0d6e8e`／`2b59ed00e`，六階段）、流程前 0、查不到 1（#2）。
- **引入時走過審查仍流出：1 筆**（#1）。
- 潛伏天數：中位 17（兩筆取平均：1 與 33）、最大 33（#1）。只算 #1 的話是 33。#1 的 33 天是 repo 側的下限，CDN 開始 gzip 的日期查不到。

參考（非流出但修正前已在主線上，不計入上面的統計）：#3–#8、#10、#12、#13、#17 共 10 列，引入到修正分別是 208、148、148、15、78、145、163、209、查不到、≥109 天。其中只有 #6 有 prod 數字證明已上線受影響。這 10 列都是由後續**其他任務**的審查、研究或規劃發現的。

## 非流出清單

#3 `f9eb9ba5c`、#4 `ed80db42c`、#5 `1b5230d0d`、#6 `d5d2a0644`、#7 `93e456a95`、#8 `b96d5448b`、#9 `e7ef536c5`、#10–11 `8e202d877`、#12–14 `4721a18fd`、#15–16 `523ed00c1`、#17 `7269aeb69`。共 15 列，對應 11 個 commit。

## 查不到清單

- #1：CDN 從什麼時候開始對 PNG gzip（在 repo 外），所以「實際生效日」與真正的潛伏天數查不到；prod slot 的 qwen 那棒是從哪天起排在導演鏈上，屬 DB 資料、沒查 prod。
- #2：10-20 那次改版有沒有走審查；owner 翻轉的起因。
- #7：靜默模式是否從 07-21 起就共用「while speaking」結尾（只核到那句字串是 `60174c50d` 引入的）。
- #9：合併 `0ea7da41` 當下有沒有跑前端整包閘（記錄自己也寫「未查」）。
- #11：重試 id 機制的引入 commit。
- #13：Publish 路由「限流在煞車前」的引入 commit。
- #16：兩個詞並存的引入 commit。
- #17：Langfuse 開始記導演 input 的起點。

## 記錄與 commit 不符（照 commit 為準）

- `7269aeb69`：commit 訊息只講三個導演設 `OmitImageURLsFromTrace`，實際上還刪了 6 個前端檔，共 1273 行（`shared/chat-image-bubble`、`chat-image-drawer`、`chat-image-notification-pill` 與它們的 spec）。這些檔的 import 是在同一秒（10-19 17:43:23）的 `6c4dda882`「刪聊天圖死碼」拿掉的；在 `7269aeb69` 這棵樹上，`chat-workspace.ts:186` 和 `chat-workspace.store.ts:106` 還在 import 已刪的檔。
- `4721a18fd` 標題寫「導演附圖讀不到時間不再寫成太新」，看起來像行為修正，實際上 commit 內文寫的是行為不變，只改了 sentinel 和 log。
- `d5d2a0644`、`93e456a95`、`b96d5448b` 用的是 `feat(...)` 前綴，但修的是既存缺陷。
- `.claude/PRPs/BACKLOG.md`（`d26d09c02` L35）說「備援 qwen 的 7 次錯誤已由 `aedef075` 改送縮圖處理」，但 `aedef0755` 的實作是「原圖有長度就送原圖，原圖滿 20 秒後才探 `_w640.webp`」，不是一律送縮圖。
- wizard-spec-merge-repair：BACKLOG 原本寫的根因（helper 作用域）是錯的，記錄自己已經更正為合併解錯，和 commit 內容一致。

## checked, no issue（核過、沒有矛盾的）

- 各任務 PROGRESS 的出處欄和 BACKLOG 歷史對得上：`git log -S` 追到 `d26d09c02`、`6d2323cb7`、`6eb612c5`、`0c8e76fb1`、`b848b17d6`、`95fcdd8d7`。
- #1 的發現時點：10-04 的 `0c8e76fb1` 沒有任何 Content-Length 或 qwen 400 的字樣，所以首次出現就是 10-05 使用者回報之後的 prod 查詢（`b848b17d6` L39）。
- #6 的 R7 早於 10-05 使用者回報（`0c8e76fb1` 10-04 10:37 已收錄），所以「誰先發現」記為審查，不是使用者。
- #8 的 30 秒：`git show 7e292c9b6:…/breaker.go` L14 就是 30s。
- #3：`1fa20917a` 的 model.go 一開始就接受 image／video 與 input_params。
