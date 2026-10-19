# 第 4 階段：Plan

目標：依 SPEC 寫 `PLAN.md`。**PLAN 未經 owner 核准前禁止寫任何實作碼。** 有 planner 類 agent 可委其起草（planner 沒有 Write：要它把全文分段回傳或改用有 Write 的 agent 落檔），主 session 審視整合後定稿。

PLAN 必須有：
1. **§0 定案**：owner 拍板表、主 session 定案（D1…，標「審查可推翻，但要附理由」）、SPEC 文字更正。
2. **規模與審查路數**：預估正式碼行數、是否碰錢／權限／跨層不變式 → 小包或大包（SKILL.md「依規模分級」），列出第 6 階段要開的 lane。切成多個可各自部署的包時，每一刀寫換到什麼、包表加「驗證帳」欄（SKILL.md「規模閥門」）。
3. **垂直切片**：每步端到端可運行；切片總表含「wave」欄、併發後臨界路徑估時。**依要讀的檔案切**：兩片要讀的檔案大量重疊（同一套件、同一組呼叫者）就合成一片、一顆 agent；碰不同區域的才拆開。每開一顆 agent 都要重付初始 context 與子目錄 CLAUDE.md，同一任務裡 tdd agent 讀的程式碼約三成是別顆也讀過的。
4. **單一 writer 檔案矩陣**：同一波裡 `routes.go`／`wire.go`／`openapi.yaml`／`api.types.ts`／同一 Go 套件／同一 spec 檔／`db/queries`＋sqlc 生成碼只屬於一片。資料層（migration＋sqlc＋設定全鏈）通常自成第一波單線。矩陣每片另列**要碰的符號、行號範圍、主要呼叫者**（第 1 階段查到的位置；寫矩陣時對每個要改簽名／搬動／刪除的符號重跑一次 `graphify affected "<Func()>" --relation calls`＋grep 取聯集——第 1 階段到第 4 階段之間 dev 可能已前進），這欄逐字進任務包——tdd-guide 每回合重讀的 context 約四成是自己 Read／cat／grep 找程式碼。`openapi.yaml`（32k 行單檔、零外部 `$ref`）必列**path 字串與 schema 名＋行號**、要套用的既有 `*_openapi_test.go` pin（`pathBlock`／`schemaBlock`／`jsonTags` 反射比對），並明寫「只 `grep -n` 定位後 `Read` offset／limit，不整檔讀」。
5. **每片的測試清單**：每支測什麼、期望紅的原因；**紅的歸屬**（主寫或 agent 寫，見第 5 階段）；先 grep 全 repo 既有 pin，避免漏掉命名不同的連帶紅。測試寫成表格、第一欄是編號（T1、H1-14…）：PLAN 審查的處置要點名它（下面「PLAN 審查」第 5 條）。
   - SPEC 有原型差異表的：每個「照做」列對到負責的片與驗收方式（走一趟步驟或測試編號），P 編號一個不漏。
6. **閘**：受影響包的 build／vet／單元＋具名整合（每片）；全閘（整合全包、lint、audit、前端整包＋production build、契約 lint）只在第 5 階段末與第 7 階段末各跑一次。寫出每個閘的既存紅基線與判準（「不新增」而非「全綠」）。
7. **風險與回滾**（含混版部署時序）、**部署後驗收**、**不做清單**（列在這裡的東西第 6 階段不算缺陷）。
8. **本任務用到的 skills／MCP／agents 清單**與各自用途、使用時機。
9. 範圍面：沒有讀者的表不單獨先建，與第一個讀者同批做。
10. **刪除／重構／新模組的圖查核**（根 CLAUDE.md §graphify）：PLAN 要刪除、合併或改簽名的每個符號先 `graphify query "<symbol>" --context call --budget 1500`；在 `graphify-out/GRAPH_REPORT.md` god-nodes 表內、或 dependents >3 的，列進 §0 定案附 dependents 清單，owner 核准 PLAN 才算確認，沒列的不得在第 5 階段順手移除。新增模組／helper 前先 `graphify query "<行為關鍵詞>"` 找既有等價物（規則的 WHY 在節點 `rationale` 屬性），找到就重用、找不到在 PLAN 寫一句「查過：無等價物」。
11. **規則對照**（每個完整路的包都要，不分大小）：把第 4 條檔案矩陣的路徑（含新檔、spec、`.html`）交給 `scripts/rulesfor.py <路徑…> >> PLAN.md` 產生 `## 規則對照` 表——`paths:` 命中的 `.claude/rules/` 檔＋沿途的子目錄 `CLAUDE.md`，每份一列、列出它的 `##` 節。逐份讀過再填處置：`套用：§<節> → <片／測試編號>`，或 `不相關：<一句理由>`；套用的節要真的落成矩陣的一列或一支測試。checkpoint 前 `scripts/rulesfor.py --check .claude/PRPs/plans/<slug>/PLAN.md` 要 exit 0。為什麼：10-17 檢討的 19 條漏網問題裡約 6 條，規則檔早就寫了同樣的規則，只是 PLAN 沒套用。限定範圍的規則要讀到命中的檔才會自動載入，而寫 PLAN 時讀的是正式碼，`.html`／spec 範圍的規則（例如 `a11y.md`、`dom-testing.md`）根本沒載入。結果同一件事到審查才被重新想一次，然後修一次、再走一趟。

## PLAN 審查（大包／高風險包；小包與精簡路不跑）
PLAN 定稿後、checkpoint 之前跑完，owner 一次看 PLAN 與審查結果，不多停一次。為什麼：流出分析裡第 6 階段才抓到的 HIGH，多數「本該攔的閘」是 PLAN——狀態矩陣缺列、先例沒回碼核對、歸類與規則檔矛盾——這三種在 checkpoint 上 owner 看不出來（owner 10-17 定：大包加 PLAN 外審、三方都要）。
1. **路數**：`lane-reviewer` 一顆（內部；沒寫 PLAN、讀得到程式碼與 `.claude/rules/`）＋codex＋grok＋agy，四路同時在背景跑。外審跑不了照 `references/external-review.md` 記「沒跑」、換鏡頭替補。
2. **快照**：`scripts/mkpatch.py --out tmp/<slug>/plan-review`（零改動也照跑，產空 patch 與 baseline）→ `scripts/mkworktree.py --path tmp/<slug>/plan-review/wt-snap --patch tmp/<slug>/plan-review/change.patch --baseline tmp/<slug>/plan-review/baseline.sha256`，SPEC／PLAN 複製進快照的 `tmp/review-input/`。為什麼不給主樹：grok 有執行能力，快照的免疫力來自沒有人寫它。checkpoint 後 `git worktree remove --force` 移除，第 6 階段另建。
3. **任務書**：填 `assets/plan-review-brief.md` 當共用任務書，「圖給的起點」貼第 4 條寫矩陣時跑的 `affected` 輸出。內部 lane 派工單 `tmp/<slug>/plan-review/tasks/inner.md`、報告 `lane-inner.md`（寫不了檔就 hand-back 全文、主 session 落檔）。外審材料包照 `references/external-review.md`「PLAN 審查的材料包」。
4. **收件**：`scripts/reviewcov.py tmp/<slug>/plan-review` 過 lane 閘。
5. **處置**（主 session）：PLAN.md 末尾加 `## PLAN 審查地圖`，表頭 `| 編號 | 宣稱嚴重度 | 一句話 | 位置 | 來源 | 處置 |`（來源寫 `<lane>#<n>`；R…＝MEDIUM 以上、L…＝LOW）。處置三選一：`改 PLAN（<節或定案>；測試 <編號>）`——預設，編號是第 5 條測試表裡的列，不改行為的（文件、不做清單、片歸屬）寫 `改 PLAN（<節>；無測試：<理由>）`；`不採：<file:line 或規則原句>`——要附程式碼或規則證據，「考慮過」不算；`交 owner`——要推翻 §0 owner 拍板的。HIGH 以上的「改 PLAN」，那支測試的列要寫齊審查提出的反例：初始狀態、觸發的失敗或交錯事件、預期結果、測試層。改完跑 `scripts/reviewcov.py tmp/<slug>/plan-review .claude/PRPs/plans/<slug>/PLAN.md --section "PLAN 審查地圖" --plan-tests` 要 exit 0（點名的編號不在 PLAN 或 `plan-appendix/` 的測試表裡就擋）。為什麼要點名測試：chat-image-publish 的 PLAN 審查已點出「結果不明」那一格、處置也寫了「改 PLAN」，測試卻只測了回讀前就看得到的那格，第 7 階段再修一輪（10-19 檢討）。不另派收斂驗證員：「不採」帶著寫 PLAN 的人的偏誤，所以下一條交給 owner 看。
6. **checkpoint 回報多三項**（第 11 條的 `--check` 不論大小包都要先過）：四路交件狀況（沒跑的寫原因）、採納幾條、HIGH 以上的「不採」逐條白話列出；「交 owner」的條目用 AskUserQuestion 問。

→ Checkpoint（owner 核准 PLAN 才能進第 5 階段）。
