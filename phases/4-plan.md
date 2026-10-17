# 第 4 階段：Plan

目標：依 SPEC 寫 `PLAN.md`。**PLAN 未經 owner 核准前禁止寫任何實作碼。** 有 planner 類 agent 可委其起草（planner 沒有 Write：要它把全文分段回傳或改用有 Write 的 agent 落檔），主 session 審視整合後定稿。

PLAN 必須有：
1. **§0 定案**：owner 拍板表、主 session 定案（D1…，標「審查可推翻，但要附理由」）、SPEC 文字更正。
2. **規模與審查路數**：預估正式碼行數、是否碰錢／權限／跨層不變式 → 小包或大包（SKILL.md「依規模分級」），列出第 6 階段要開的 lane。切成多個可各自部署的包時，每一刀寫換到什麼、包表加「驗證帳」欄（SKILL.md「規模閥門」）。
3. **垂直切片**：每步端到端可運行；切片總表含「wave」欄、併發後臨界路徑估時。**依要讀的檔案切**：兩片要讀的檔案大量重疊（同一套件、同一組呼叫者）就合成一片、一顆 agent；碰不同區域的才拆開。每開一顆 agent 都要重付初始 context 與子目錄 CLAUDE.md，同一任務裡 tdd agent 讀的程式碼約三成是別顆也讀過的。
4. **單一 writer 檔案矩陣**：同一波裡 `routes.go`／`wire.go`／`openapi.yaml`／`api.types.ts`／同一 Go 套件／同一 spec 檔／`db/queries`＋sqlc 生成碼只屬於一片。資料層（migration＋sqlc＋設定全鏈）通常自成第一波單線。矩陣每片另列**要碰的符號、行號範圍、主要呼叫者**（第 1 階段查到的位置；寫矩陣時對每個要改簽名／搬動／刪除的符號重跑一次 `graphify affected "<Func()>" --relation calls`＋grep 取聯集——第 1 階段到第 4 階段之間 dev 可能已前進），這欄逐字進任務包——tdd-guide 每回合重讀的 context 約四成是自己 Read／cat／grep 找程式碼。`openapi.yaml`（32k 行單檔、零外部 `$ref`）必列**path 字串與 schema 名＋行號**、要套用的既有 `*_openapi_test.go` pin（`pathBlock`／`schemaBlock`／`jsonTags` 反射比對），並明寫「只 `grep -n` 定位後 `Read` offset／limit，不整檔讀」。
5. **每片的測試清單**：每支測什麼、期望紅的原因；**紅的歸屬**（主寫或 agent 寫，見第 5 階段）；先 grep 全 repo 既有 pin，避免漏掉命名不同的連帶紅。
6. **閘**：受影響包的 build／vet／單元＋具名整合（每片）；全閘（整合全包、lint、audit、前端整包＋production build、契約 lint）只在第 5 階段末與第 7 階段末各跑一次。寫出每個閘的既存紅基線與判準（「不新增」而非「全綠」）。
7. **風險與回滾**（含混版部署時序）、**部署後驗收**、**不做清單**（列在這裡的東西第 6 階段不算缺陷）。
8. **本任務用到的 skills／MCP／agents 清單**與各自用途、使用時機。
9. 範圍面：沒有讀者的表不單獨先建，與第一個讀者同批做。
10. **刪除／重構／新模組的圖查核**（根 CLAUDE.md §graphify）：PLAN 要刪除、合併或改簽名的每個符號先 `graphify query "<symbol>" --context call --budget 1500`；在 `graphify-out/GRAPH_REPORT.md` god-nodes 表內、或 dependents >3 的，列進 §0 定案附 dependents 清單，owner 核准 PLAN 才算確認，沒列的不得在第 5 階段順手移除。新增模組／helper 前先 `graphify query "<行為關鍵詞>"` 找既有等價物（規則的 WHY 在節點 `rationale` 屬性），找到就重用、找不到在 PLAN 寫一句「查過：無等價物」。

→ Checkpoint（owner 核准 PLAN 才能進第 5 階段）。
