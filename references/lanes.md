# 審查與驗證的路數

## 為什麼要三軸
- **關注面 lane**（`lane-reviewer`＋自寫 prompt：規格忠實度、影響範圍、時間與順序…）打的是你指得到的方向。
- **語言／領域專門 agent**（go-reviewer、python-reviewer、typescript-reviewer、security-reviewer、silent-failure-hunter、pr-test-analyzer、comment-analyzer）帶內建清單，會去查你沒想到的 stdlib 邊界——不能用 lane-reviewer／general-purpose＋好 prompt 取代；給它們 invariants 當前置閱讀，不覆蓋它們的方法論。
- **異源外審**（codex、grok、agy）的價值在於**不知道**你的設計意圖；codex 常抓到多個 Claude reviewer 一起漏的東西。
- 同源模型會一起瞎，但瞎的軸不同——所以內審路數不因「有外審」而省，反之亦然。

## 配路教訓
- 先問這輪的不確定性在**推理**還是**事實**。推理型（跨層可達性、時序、失敗路徑）→ 異源外審值錢；事實型（版面、對比度、cascade 誰勝出、斷言守不守得住）→ 預算放在能**執行**的 lane（突變、真瀏覽器），prompt 寫明「數字必須你自己跑出來，我給的是待否證的假設」。材料完整內聯、推理鏈短時，外審常與內審完全重疊。
- 替補或加 lane 時先問「現有各路的鏡頭清單缺哪一個」，不是「誰比較強」。常被漏的鏡頭：**錯誤／失敗路徑**、**時間與順序**、**使用者的位置（焦點／捲動）**。
- 分工審查會系統性漏掉接縫（每條 lane 都在自己的邊界內看到「這一半是對的」）→ 一定留一路**沒有分派邊界**的視角（通常是 codex），升級嚴重度的依據是它多追的那一步事實，不是語氣。
- agent 名字描述專長不是能力：寫完 lane prompt 回頭問「這份任務書要求的動作，那個 agent type 做得到嗎」（a11y-architect 沒有 Bash、跑不了瀏覽器）。

## effort
- comment-analyzer、go-reviewer、silent-failure-hunter、tdd-guide＝high；其餘 reviewer 仍 xhigh。由來：10-06 重放 A/B——三路 reviewer 已確認條目零漏、花費 −44%；tdd-guide 兩片（跨層、碰錢＋權限）突變殺傷相同、花費 −37%（`history.md`）。
- tdd-guide 降檔後唯一看到的退步：照抄任務書的錯前提寫成 INVARIANT。第 6 階段註解 lane 是這類錯的防線，不因小包而省。
- 擴大判準：接下來兩個包跑完第 6 階段，用 `laneyield.py` 比這三路的獨家發現與確認數有沒有比 10-06 前少。沒少 → typescript-／database-／python-reviewer 也改 high；少了 → 三路退回 xhigh。推理型（lane-reviewer、converge-verifier）、碰錢權限（security-reviewer）、突變（pr-test-analyzer）、真瀏覽器（ui-lane-reviewer）不在擴大範圍。
- 再做 effort A/B：另建 `ab-<名>-high.md`（本體逐字相同，只改 name／effort），新檔要到下一回合才載入；不改原檔，並行 session 會在不知情的情況下中招。重放材料＝凍結快照＋原任務書；任務書刪了可以從任一 lane transcript 的 Read 結果逐字還原。xhigh 自己重跑一次當雜訊基準：嚴重度本來就會浮動，比的是有沒有抓到。

## 小包（正式碼 ≤1500 行，且不碰 credits／權限／跨層不變式）：6–8 路
| lane | agent | 何時 |
|---|---|---|
| 每個碰到的層一個語言 reviewer | go-／python-／typescript-reviewer | 必開 |
| 註解正確性 | comment-analyzer（沒有 Write／Bash：主落檔）；小包程式註解＋文件合一路，範圍＝帶 `WHY:`／`INVARIANT:` 前綴的行＋本包改到的 CLAUDE.md／`.claude/rules/`／openapi／SPEC | 必開 |
| 測試品質＋突變 | pr-test-analyzer（突變樹唯一 writer） | 必開 |
| 靜默失敗 | silent-failure-hunter | 必開 |
| 規格忠實度＋case walk | lane-reviewer | 必開 |
| 異源外審 | codex（gpt-6-sol xhigh） | 必開 |
| 安全／隱私 | security-reviewer | 碰使用者輸入、log／錯誤外洩、權限時 |
| UI/UX | `ui-lane-reviewer`＋瀏覽器截圖（繼承全部工具、自帶 Playwright server（`--codegen=none`）取代 plugin 那套；lane-reviewer 已限縮工具、開不了瀏覽器） | 涉 UI 時；驗不了的標「需人工目視」 |

## 大包或高風險包：完整路數
小包清單全開，再加：security-reviewer（必開）、影響範圍 lane（所有 caller／呼叫點實讀第三方碼；caller 清單起手用任務書「圖給的起點」節——主 session 派工前跑 `graphify affected "<Func()>" --relation calls` 貼進去，lane 再 grep 取聯集；本包新符號圖裡沒有、只能 grep）、時間與順序 lane（預算鏈、取消語義、視窗邊界、混版時序）、grok、agy；註解 lane 可拆成程式碼註解與文件兩路。任一路跑不了就換一個不同鏡頭替補。

## 收斂驗證
- 只驗「有爭議／HIGH 以上／需 owner 拍板」的條目：小包 1 路、大包依條目量 2 路起（同層的條目放同一路，例：偵測面、追蹤、單一服務、時間預算）。
- agent：`converge-verifier`（xhigh，有 Write 寫報告）。
- 驗證員 ≥2 顆時用 `Workflow` 扇出（owner 已同意的用法；先載 `workflow-authoring`；第 6 階段內審同法，見 `phases/6-review.md` §4）：`agent(prompt, {agentType: 'converge-verifier', schema})`，frontmatter 的 effort 會保留（實測 xhigh）。**Workflow 內的 subagent 被 harness 擋寫報告檔**（「Subagents should return findings as text」，探針檔照寫）——schema 放 `report_markdown` 全文欄位，主 session 從結果落檔，任務書不要求寫報告檔。
- 其餘條目由修復者邊修邊確認。

## 實作與修復
- 切片實作（每片一棵私有樹）：
  - `tdd-guide`（high）：所有實作片與修復片。碰錢、權限、owner 拍板語義的紅照舊由主 session 先寫。
  - tdd 只用 `tdd-guide`：`ab-tdd-guide-v2` 三次 A/B 後 10-13 刪除（owner 定）。前兩次 v2 便宜約 3 成、但漏了「同一行為在多個呼叫點」的守衛；加了「每個呼叫點各一支」後第三次兩臂偵測打平（預登記 19／21、多呼叫點 6／6），v2 反而貴 12%。那句與 testcontainers 契約已搬進 `tdd-guide`，同時拿掉 80% 覆蓋率（綁不存在的 npm 指令）、八項邊界清單與「mock external dependencies」；v2 原文留在 `character-dual-portrait` worktree 的 `tmp/character-dual-portrait/xmut-w4/ab-tdd-guide-v2.recovered.md`。
  - `ab-security-reviewer-v2` 只在 owner 指定 A/B 的包跑兩臂。security v2 便宜 3 成、在沒有安全缺陷的包上分不出偵測力，待碰錢／權限的包再比。合併前一律由主 session 跑交叉突變（同一語義突變、各臂自己的程式碼、`go test -overlay`），不信兩臂自報。
- 文件片：`doc-slice`（high，每句先回程式碼確認）；codemap／README 生成才用 doc-updater。
- 探索／定位：code-explorer（high）；判斷性探索用 general-purpose／Explore（effort 跟主 session）。
- 組材料包、批次文字改寫、graphify 抽取：quick-task（high）。
- 真瀏覽器走一趟（第 5／7 階段末）：`ui-lane-reviewer`（xhigh）。
- 盲評（模型／提示詞探針）：`blind-judge`（xhigh、不載 CLAUDE.md、只有 Read／Write／Glob）；位置由主 session 隨機化後才交給它。

## 派工前檢查
- agent 的工具集（有沒有 Write／Bash；lane-reviewer、quick-task 沒有瀏覽器與 MCP）。
- 共用任務書已寫明每棵可寫樹的唯一 writer。
- 並行 agent 的交付檔名帶 lane／片號。
- 共用一份沒實測過的處方且 >5 顆 → 先一顆探針。

## 成本參考
調路數前先跑 `scripts/laneyield.py <slug>…`（每一路的獨家發現、確認數與花費）與 `scripts/taskcost.py --slug <slug>`（各階段目錄花費）。transcript 多數訊息只記串流開頭的 `output_tokens`（少算 5–10 倍、thinking 又遮蔽），腳本的 output 金額不可信；比較時看回合數與 cache 讀寫。

### 2026-09-28，正式碼約 1000 行的一包
13 路審查＋5 路收斂驗證（每路 24–30 萬 token）＋4 片修復（約 100 萬）。13 條 MEDIUM 有 7 條是註解／文件——這是依規模分級的由來。
