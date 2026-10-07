---
name: seven-phase
description: owner 的七階段開發工作流（research → 討論 → interview → plan → TDD 實作 → 多方審查 → 收斂），含每階段做法、審查／驗證路數分級、checkpoint 協定、commit 規則與可重用腳本。Use this whenever the owner wants a feature or fix taken through the seven-phase workflow (七階段、七階段工作流、走完整流程), pastes the workflow text, says 「繼續」／「下一階段」 at a checkpoint, asks to review／converge／commit a task that has a task directory under `.claude/PRPs/plans/`, or when resuming such a task after a /compact or a new session — compaction drops details like model names and lane rules, so reload it rather than working from the summary.
---

# 七階段工作流

owner 是產品負責人不是工程師。這套流程把方向控制權與 token 用量留在 owner 手上：每階段停下來回報、等「繼續」；需求簡單的走精簡路，只在交付時停（見「分流」）。流程規則以本 skill 為準；記憶只放 owner 偏好與進行中任務的狀態。規則看起來多餘時，先讀 `references/history.md` 看它在防什麼。

## 開工第一步
1. **問模型**：新任務開工前用 AskUserQuestion 問 owner 要不要 `/model` 切到 fable（或當時更強的模型）當主 session，**問句附成本與判準**：fable 輸入／輸出單價約 opus-5.5 的 2.5 倍，主 session 的輸出大半是思考，換模型會直接放大這筆；官方說 fable 適合目標模糊、找根因、架構決策、長時間自主工作，範圍明確的實作與收斂用 opus-5.5。推薦項依任務類型給。owner 選維持就用當前模型（預設 opus-5.5），**中途不切換**（換模型的下一回合整段重讀、沒有快取）。續接既有任務不必再問。
   - 同一則 AskUserQuestion 第二題問**分流**（精簡路／完整七階段，推薦項第一＋一句理由，判準見「分流」）。
2. 找現況：`.claude/PRPs/plans/<slug>/PROGRESS.md` 存在就讀它開頭的分流、階段表與「下一步」，從那裡接。
3. 讀當前階段的檔案：`phases/<n>-*.md`；精簡路讀 `phases/lean.md`（只讀當前這一份，別一次全讀）。

| 階段 | 檔案 | 產出 |
|---|---|---|
| 1 Research | `phases/1-research.md` | RESEARCH.md（現況摘要＋挑戰結論） |
| 2 討論 | `phases/2-discuss.md` | SPEC.md 首段「方向決議」 |
| 3 Interview | `phases/3-interview.md` | SPEC.md 完整規格 |
| 4 Plan | `phases/4-plan.md` | PLAN.md（owner 核准前禁寫實作碼） |
| 5 TDD 實作 | `phases/5-implement.md` | 程式碼＋PROGRESS 切片紀錄 |
| 6 多方審查 | `phases/6-review.md` | REVIEW.md 去重地圖 |
| 7 收斂 | `phases/7-converge.md` | 裁決表、修復、全閘、總結 |
| 精簡路 | `phases/lean.md` | 一份 PROGRESS.md（計畫、審查地圖、結果） |

參考檔**用得到時才讀**，開工時不要全讀（評測時多讀兩份參考檔，除了多花 token 沒有好處）：`references/lanes.md`（第 4 階段定路數、第 6／7 階段派工時）、`references/external-review.md`（第 6 階段派外審時）、`references/history.md`（想推翻或放寬某條規則時）、`references/project-deepweave.md`（在 deepweave repo 要跑測試、閘或碰 prod 時）。
範本：`assets/`（審查任務書、收斂驗證任務書、修復任務包、checkpoint 回報）。腳本：`scripts/`（見下）。

## 分流：精簡路或完整七階段
開工判一次，寫進 PROGRESS.md 開頭。**審查不分流**：兩條路都照「依規模分級」派該派的 reviewer，含外審（owner 10-05、10-06：「唯獨 review 不能省」）。
- **精簡路**——三條都成立才走：
  1. **需求明確**：修法已定或只有一種合理做法，不需要 owner 做設計／文案取捨（backlog 寫好修法的條目、根因已定位的 bug、補測試、一行級 UI 修正）。
  2. **不碰重要面**：credits、權限、18+／未成年、跨層不變式、prod 資料、全站可用性（斷路器、retry、逾時預算）。
  3. **小**：正式碼約 300 行內。
- **完整七階段**：其餘一律。重要功能、要 owner 定方向的使用者有感功能，正是討論與訪談存在的理由——把方向錯誤提前到寫程式之前。
- 拿不準走完整。精簡路途中撞到上面任一條 → 依 `phases/lean.md`「升級」停下改走完整。

## Checkpoint 協定（完整路每階段結束，硬規則；精簡路只在交付時停，見 `phases/lean.md`）
owner 吃過「一路衝到底、末端才發現前提錯」的虧，也要自己決定何時 compact——所以每階段都停。
1. 結論與下一步寫進 PROGRESS.md（階段表那一列＋「## 下一步」＋「## 第 N 階段紀錄」五行：做了什麼／關鍵裁決／紅→綠或證據／閘結果／殘留）。
2. **完全停止**，白話回報：做了什麼、發現什麼、為什麼這樣決定、接下來幹嘛。
3. 附一段可直接貼的 `/compact <摘要>`（位置、真相源檔、下一步、全程規則），用 `assets/checkpoint.md` 的形狀。
4. **等 owner 說「繼續」**才進下一階段。不得順勢往下做，也不得在回報裡先問下一階段的問題。
5. 背景 subagent 在 checkpoint 之後才交件：併進記錄，下一則回覆一句話告知，並給更新版 `/compact`。
6. **compact 要趁暖、要早**：`/compact` 在 owner 離開**之前**貼（快取 5 分鐘就冷，用量額度時段主對話只有 5m TTL；冷的 compact 會把整段歷史按未快取價重讀），目標 compact 時 context ≤ 200k（10-07 實測中位 358k＝太晚）。回來發現冷了（`/usage` 的 `Prompt cache (main)` 顯示 cold）而且下一階段不需要延續對話 → `/clear` 再讀 PROGRESS，不要 compact；要放棄一條路用 `/rewind`（回到已快取的前綴，比 compact 便宜）。

## 全程規則
- **禁 git commit／push**，直到 owner 明說（owner 要在進歷史之前看過整包結果）。commit 形狀＝兩個：`feat(x): ...`（程式碼＋CLAUDE.md／`.claude/rules/`＋契約）與 `docs(prps): <slug> 七階段記錄`（任務目錄）。工作樹常有別人／別任務的已暫存改動：**一律 `git commit --only -F <msg> -- <明確路徑>`**（新檔先 `git add` 那幾個路徑），絕不用裸 `git commit` 或 `git add -A`。push 前 `git fetch` 並確認 ahead-only。
- **真相源**＝`.claude/PRPs/plans/<slug>/{RESEARCH,SPEC,PLAN,REVIEW,PROGRESS}.md`（不放 repo root，會被當暫存清掉）。耐久結論另外搬進最近的子目錄 `CLAUDE.md`——任務文件會被刪；母檔開頭有「主題規則」索引的（worker、backend-golang、angular-workspace），寫進索引指向的 `.claude/rules/<area>/*.md`、不寫回母檔，沒有合適主題就新建規則檔（`paths:` 寫 repo 根相對的錨定 glob、不加 `**/`）並在母檔索引加一行。寫成 pattern：規則＋一句理由＋落點（路徑／符號／slug），不帶任務代號、日期、commit、測試名清單與實驗經過；同主題就合併改寫既有段落，不往後追加。為什麼：子目錄 CLAUDE.md 讀到就整份載入、之後每回合重讀，逐次追加的沉積曾把 worker 那份堆到 277KB（10-05 全面瘦身成約一半，10-06 再把三份母檔的主題章節拆成碰到才載入的規則檔，worker 母檔剩 14KB）。
- **主 session 讀得窄**：主 session 是整個流程裡開最久的 context，讀進的每一行到下次 compact／`/clear` 前每一回合都再付一次。讀程式碼帶行號範圍、搜尋先用 `grep -l`／`-c`／`-m` 量大小；大範圍探索派 code-explorer；agent 報告先讀 hand-back 摘要，要裁決的條目才開全文；真瀏覽器操作派 `ui-lane-reviewer`。親跑閘照舊（輸出寫檔再 grep）。
- **註解不可信**：任何階段不得用註解、CLAUDE.md、SPEC、記憶推斷程式行為；只信執行＞程式碼。註解與行為不符＝必修 issue，誤導性註解嚴重度視同邏輯 bug。新文字不得以任務代號（「包 A」「R8」「REVIEW §x」）當唯一說明；可寫 slug。
- **owner 溝通**：先講人話再附技術細節；技術縮寫首次出現附一句白話。拍板題一律 AskUserQuestion（推薦項排第一標 Recommended、選項寫「選了會發生什麼」、關鍵對比用文字圖 preview），一次一件事。
- **挑戰 owner**：有證據的立場要堅持並攤開證據、被說服才改；owner 新資訊推翻結論要坦白更新。發現更好的做法要反向挑戰並附理由。
- **規模閥門**：小而明確的走精簡路（見「分流」）；明顯太大可提議 ROADMAP.md 拆子任務（owner 同意）。
- **排序**：backlog、後續小包與 ROADMAP 依「重要（錢、權限、未成年、全站可用性、資料遺失）＞使用者有感＞其餘」排；其餘合成一包走精簡路（owner 10-06：「優先重要功能跟使用者有感功能，其餘收斂」）。
- **graphify 只准查詢**，改完 docs／sql／openapi 只提醒圖過期，沒 owner 指示不跑 `--update`。
- **prod**：**任何 prod 查詢（DB、Langfuse 等量測 API）都先用 AskUserQuestion 問 owner**，附確切查詢、它要回答什麼、答案會怎麼改變決定；只看聚合數字也一樣要問（owner 09-28 定）。核准後由主 session 跑，subagent 一律禁打。DB 只下 SELECT；Langfuse 額度與 prod 警報輪詢器共用。不讀使用者對話內容、不印任何金鑰。

## 依規模分級（2026-09-28 owner 定；優先於 history 裡的舊規則）
在 Plan 階段用 `scripts/numstat.py` 或估算標出「正式程式碼行數（不含測試與文件）＋是否碰錢／權限／跨層不變式」，第 6 階段開工前用實際 diff 再確認一次：
- **小包**（正式碼 ≤1500 行，且不碰 credits／權限／跨層不變式）：審查 6–8 路、收斂驗證只驗「有爭議／HIGH 以上／需 owner 拍板」的條目 1–2 路。
- **大包或高風險包**：完整路數（含 grok／agy 外審）、收斂驗證依條目量分路。
- 拿不準就往完整走。路數組成見 `references/lanes.md`。
- **主 session vs subagent**：1–3 檔、內容已確定的小改（照報告改註解、改 SPEC／PLAN）主 session 直接做；彼此獨立的多片、需要大量閱讀而主 session 不必記住的、審查（要新視角）交 subagent。主 session context 已很長時偏向 subagent（每一步都重送整段對話）。

## 模型與 agent
- 主 session：簡報、訪談、規劃、委派協調、**親跑測試與閘**、一切最終裁決。subagent 只回結論與證據，**不代表 owner 做任何決定**。
- subagent 一律 opus-5.5，**effort 只能由 agent frontmatter 決定**（`~/.claude/agents/*.md` 的 `model:`／`effort:`；prompt 寫 effort 是安慰劑；Agent 工具的 `model` 參數勿傳）。
  - **xhigh（要判斷）**：planner、其餘語言／領域 reviewer（typescript-／database-／python-／security-reviewer 等）、pr-test-analyzer、`lane-reviewer`（關注面 lane）、`ui-lane-reviewer`（真瀏覽器的 UI lane 與走一趟）、`converge-verifier`（收斂驗證）、`blind-judge`（盲評，不載 CLAUDE.md）。
  - **high（範圍已定，或 10-06 A/B 證實不掉品質）**：tdd-guide、comment-analyzer、go-reviewer、silent-failure-hunter（見 `references/lanes.md` §effort）、`doc-slice`（文件片）、doc-updater（codemap／README）、code-explorer、quick-task（含 graphify 抽取）。
  - **內建 agent（general-purpose、Explore、Plan）沒有 frontmatter，effort 跟著主 session 走**：主 session 開 max 它就跑 max，開 medium 就跑 medium。有固定用途的工作一律派上面的專用 agent，general-purpose 只留給不屬於任何一類的雜務。
  - agent 檔改動不必重啟：改既有檔的 frontmatter，下一次派工就生效；新建或刪除的檔，下一個使用者回合才出現在可用清單（同回合派會 not found）；已在跑的那顆維持舊設定跑完。驗證看 `~/.claude/projects/<proj>/<sid>/subagents/*.jsonl` 的 `"model"` 與 `"effort"`。內建 agent 的模型靠 `~/.claude/settings.json` 的 `CLAUDE_CODE_SUBAGENT_MODEL`；`fork` 永遠跑主 session 的模型與 effort。花費用 `scripts/agentcost.py` 量。
- **派工前確認 agent 的工具集**：comment-analyzer／planner 等沒有 Write（交付要主 session 落檔或改用有 Write 的 agent）；沒 Bash 的跑不了測試；lane-reviewer、quick-task 只有 Read／Write／Bash／Grep／Glob（少背約 15k token 的工具定義），要瀏覽器的派 `ui-lane-reviewer`。有 `tools:` 白名單的 agent 只拿得到列出的工具——10-07 起 reviewer／tdd-guide／code-explorer 都列了 `LSP`（符號導航先用它、grep 只找字串；10-07 前 subagent 的 LSP 呼叫是 0）；每顆都有 `maxTurns`（約 1.3–1.5 × 實測最大回合數，失控保險，到頂會硬停、hand-back 可能不完整）。
- **主 session 的模型與等待**：Fable 每次呼叫約 Opus 5.5 的 3 倍，閒置失效在 700–800k context 一次 $7–10；派出一批要等 10 分鐘以上的 subagent 之前先縮 context（checkpoint ＋ `/compact` 或 `/clear`）；切模型只在 `/clear` 之後（切了就是全量重寫）；effort 在 Fable／Opus 5.5 切換不打斷快取，派工／收件／寫記錄階段可降 `high`。
- 專案專用 agent 優先於通用 agent；語言專門 reviewer 不能用 general-purpose＋好 prompt 取代（內建清單打的是別的方向）。
- 委派：自足任務包（目標、輸入路徑、環境規則、交付格式）；**長交付一律寫檔**，hand-back 只要 ≤12 行摘要＋檔案路徑；並行 agent 的中間檔／交付檔名帶片號或 lane 名（共用 scratchpad 會互截）。
- **任務包瘦身**：派工訊息分「必讀」與「需要時再讀」兩欄。必讀只放這片用得到的段落，從原文**逐字摘錄**進 `<片號>-brief.md` 並附出處路徑（用 `scripts/extract.py`，不手抄、不每次現寫摘錄腳本），不改寫成摘要（摘要會引入錯誤，也不是證據）；全文路徑一律留在「需要時再讀」。必讀合計 ≤ 約 90KB（`wc -c`，約 3 萬 token），超過要在派工訊息寫理由。不算在內的：審查對象本身（diff、程式碼）、agent 自己決定去讀的程式碼。為什麼：subagent 的花費七成以上是每一回合重讀累積的上下文，前面多讀一段，後面每一回合都再付一次；逐字摘錄加上保留全文路徑，品質不會因此下降。
- **>5 顆 agent 共用一份沒實測過的處方 → 先派一顆探針**。

## 腳本（`python3 ~/.claude/skills/seven-phase/scripts/<name>.py --help`）
- `numstat.py`：未 commit 改動（或對某個 base）依 prod／test／doc 分類的行數，給規模分級用。
- `mkpatch.py`：產 `change.patch`（含 untracked 新檔）、`change-src/-tests/-docs.patch`、`baseline.sha256`、`files.txt`。
- `mkworktree.py`：`git worktree add --detach HEAD` ＋ 套 patch ＋ 對 baseline 驗 sha（快照／突變樹／修復樹都用它）。
- `checktree.py`：對 baseline 驗一棵或多棵樹（含主 checkout）的 sha。
- `merge_back.py`：把工作樹相對快照改過的檔複製回主 checkout（先確認主樹那些檔仍等於快照、片與片之間零重疊）。
- `run_external.py`：跑 codex／grok／agy 外審（背景跑，見 `references/external-review.md`）。
- `extract.py`：markdown 依標題或行號逐字切段、附出處（組任務包）。
- `reviewcov.py`：核對 REVIEW.md 去重地圖涵蓋每份 lane 報告的每一條（第 6 階段收件）。
- `claudemdcost.py`：子目錄 CLAUDE.md 與限定範圍規則（讀到才載入）的花費，依檔案彙總、worktree 副本併回原路徑。
- `taskcost.py`／`laneyield.py`：每個任務、每個階段目錄的花費；每一路審查的獨家發現與花費（調路數、檢討流程前先跑）。
Bash 一律絕對路徑或 `git -C`／`go test -C`，**不要 `cd`**（工作目錄會殘留，下一個 bridge 或 pathspec 會跑錯樹）。
