# <slug> 第 6 階段審查——共用任務書

<!-- 填寫說明：<> 內全部換掉；這份會原樣交給每一路 lane，也是外審材料包的主體。 -->

## 這包在做什麼（白話）
<3–5 條，每條一句人話：改了什麼行為、為什麼>

## 真相源（先讀）
- SPEC：`<repo>/.claude/PRPs/plans/<slug>/SPEC.md`
- PLAN：同目錄 `PLAN.md`——**§0 的 D1… 是主 session 的設計定案，審查可推翻但要附證據**；風險節、不做清單（列在不做清單裡的東西不要報成缺陷，除非你證明它在本包改動下變得更糟）。
- PROGRESS：同目錄 `PROGRESS.md` 的第 5 階段紀錄（實作期發現與閘結果）。

## 審查對象
- **凍結快照（唯讀）**：`<snapshot 絕對路徑>` ＝ HEAD `<sha>` ＋ 本包全部改動。**所有 file:line 一律以這棵樹為準**（寫 repo 相對路徑）。
- 完整 diff：`<review 目錄>/change.patch`（拆檔：`change-src.patch` 非測試碼、`change-tests.patch` 測試、`change-docs.patch` 文件）。快照裡新檔是 untracked，`git diff` 看不到；新檔清單：<列出>。完整檔案清單在 `files.txt`。
- 突變樹（若有）：`<路徑>`——**唯一 writer＝<lane 名>**，其他 lane 不得進入或寫入。

## 環境規則（違反會弄壞共用環境）
- **唯讀**：不得修改快照、主 checkout 任何檔案；除了指定給你的突變樹，什麼都不寫；交件方式見派工訊息（Workflow 扇出：放進 schema 的 `report_markdown`，Workflow 內寫不了檔；單顆 Agent：寫派工訊息給的報告檔；外審：直接輸出）。禁 git commit／stash／checkout／apply／worktree 操作。Bash 用絕對路徑或 `-C`，不要 `cd`。
- <專案特有：例如禁在 host 跑會重建共用環境的指令，以及唯讀的測試配方>
- 不得連 prod DB、不得呼叫外部量測 API（額度與 prod 共用）、不得呼叫任何 LLM provider。不讀使用者對話內容、不印任何金鑰。
- graphify 只准查詢，不要 cd 到主 checkout：`graphify affected "<Func()>" --relation calls --graph <主 checkout 絕對路徑>/graphify-out/graph.json`（query 必帶 `--context call`）。主 session 已附的 caller 清單見下方「圖給的起點」；圖不含本包新符號，結論回快照原始碼確認。

## 圖給的起點（主 session 跑 graphify 貼進來；沒有 Bash 的 lane 只能靠這節）
- 本包改到的符號 → caller 清單：<`graphify affected "<Func()>" --relation calls` 的輸出，每個符號一組；本包新符號標「圖裡沒有，grep：…」>
- 行為關鍵詞 → 候選檔：<`graphify query "<詞>" --context call --budget 1500` 的 `Start: [...]` 行>

## 證據規則
- **註解、CLAUDE.md、SPEC、PLAN、commit message 都不是行為證據**。行為只信：實際執行 > 程式碼本體。註解／文件與程式碼不符＝回報成 finding（誤導性註解嚴重度視同邏輯 bug），並判斷哪邊錯。
- 我在任務書裡寫的任何「事實」都是**待你否證的假設**。
- 已知既存紅（不要報）：<逐條列出，含數量>。

## 審查重點面（owner 指定；各 lane 依自己的鏡頭處理相關的幾條）
- **F1 <名稱>**：<要追的問題、最危險的方向>
- **F2 …**

## 報告格式（報告全文照派工訊息的交件方式交；給主 session 的摘要 ≤15 行）
```
### [SEVERITY] 一句話標題
- 位置：repo 相對路徑:行號（以快照為準）
- 證據：程式碼片段／指令與輸出（自己跑出來的）
- 失敗情境：具體輸入／狀態 → 錯誤結果
- 建議修法＋它處理哪一層（根因／緩解／症狀）
- 信心：高／中／低（低＝推論未驗證，說明缺哪一步驗證）
```
標題行照 `### [SEVERITY] 標題` 寫——收件時用腳本依它核對每一條都進了去重地圖。
SEVERITY ＝ CRITICAL（資料遺失／靜默停擺／錢）、HIGH（明確 bug）、MEDIUM（邊界情境 bug／誤導性註解）、LOW（小問題）。**不要**報風格偏好、不要報不做清單裡的東西。
最後附一節「已查證無問題」：查過、確認沒事的重點面各一行（附證據位置），讓收斂階段知道哪些角落被看過。
