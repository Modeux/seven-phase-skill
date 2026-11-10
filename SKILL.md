---
name: seven-phase
description: owner 的七階段開發工作流（research → 討論 → interview → plan → TDD 實作 → 多方審查 → 收斂），含每階段做法、審查／驗證路數分級、checkpoint 協定、commit 規則與可重用腳本。Use this whenever the owner wants a feature or fix taken through the seven-phase workflow (七階段、七階段工作流、走完整流程), reports a behavior bug to fix or asks for an urgent prod fix (修 bug、緊急修正、止血), pastes the workflow text, says 「繼續」／「下一階段」 at a checkpoint, asks to review／converge／commit a task that has a task directory under `.claude/PRPs/plans/`, or when resuming such a task after a /compact or a new session — compaction drops details like model names and lane rules, so reload it rather than working from the summary. Also use it when the owner says 「檢討」 (cross-task retro over quality／cost／speed, `phases/retro.md`) or when a process change needs an acceptance row in `ACCEPTANCE.md`.
---

# 七階段工作流

owner 是產品負責人不是工程師。這套流程把方向控制權與 token 用量留在 owner 手上：在 owner 要做決定的地方停下來回報、等「繼續」（第 2、3、4、5、7 階段結束，以及第 7 階段收斂驗證交件後；第 1、6 階段回報後接著做，見「Checkpoint 協定」第 7 條）；需求簡單的走精簡路，只在交付時停（見「分流」）。流程規則以本 skill 為準；記憶只放 owner 偏好與進行中任務的狀態。規則看起來多餘時，先讀 `references/history.md` 看它在防什麼。

## 開工第一步
1. **問模型**：新任務開工前用 AskUserQuestion 問 owner 要不要 `/model` 切到 fable（或當時更強的模型）當主 session，**問句附成本與判準**：fable 輸入／輸出單價約 opus-5.5 的 2.5 倍，主 session 的輸出大半是思考，換模型會直接放大這筆；官方說 fable 適合目標模糊、找根因、架構決策、長時間自主工作，範圍明確的實作與收斂用 opus-5.5。推薦項依任務類型給。owner 選維持就用當前模型（預設 opus-5.5），**中途不切換**（換模型的下一回合整段重讀、沒有快取）。續接既有任務不必再問。
   - 同一則 AskUserQuestion 第二題問**分流**（精簡路／完整七階段，推薦項第一＋一句理由，判準見「分流」）。回報的行為 bug 這題改到診斷完才問；符合止血條件時連模型也不問、沿用當前模型（`phases/bug.md`）。
2. 找現況：`.claude/PRPs/plans/<slug>/PROGRESS.md` 存在就讀它開頭的分流、階段表與「下一步」，從那裡接。不知道是哪個任務、或 owner 問「進度」：看 SessionStart 注入的進度看板，沒有就跑 `scripts/board.py`。
3. 讀當前階段的檔案：`phases/<n>-*.md`；精簡路讀 `phases/lean.md`；回報的行為 bug 讀 `phases/bug.md`（只讀當前這一份，別一次全讀）。

| 階段 | 檔案 | 產出 |
|---|---|---|
| 1 Research | `phases/1-research.md` | RESEARCH.md（現況摘要＋挑戰結論） |
| 2 討論 | `phases/2-discuss.md` | SPEC.md 首段「方向決議」 |
| 3 Interview | `phases/3-interview.md` | SPEC.md 完整規格 |
| 4 Plan | `phases/4-plan.md` | PLAN.md（大包含 PLAN 審查地圖；owner 核准前禁寫實作碼） |
| 5 TDD 實作 | `phases/5-implement.md` | 程式碼＋PROGRESS 切片紀錄 |
| 6 多方審查 | `phases/6-review.md` | REVIEW.md 去重地圖 |
| 7 收斂 | `phases/7-converge.md` | 裁決表、修復、全閘、總結 |
| 精簡路 | `phases/lean.md` | 一份 PROGRESS.md（計畫、審查地圖、結果） |
| Bug 入口（診斷後接精簡路或完整路；含止血軌） | `phases/bug.md` | PROGRESS.md `## 診斷`／`## 止血` |
| 跨任務檢討（owner 說「檢討」） | `phases/retro.md` | `retros/<日期>.md`、`ACCEPTANCE.md` 結案與新列 |

參考檔**用得到時才讀**，開工時不要全讀（評測時多讀兩份參考檔，除了多花 token 沒有好處）：`references/lanes.md`（第 4 階段定路數、第 6／7 階段派工時）、`references/external-review.md`（第 6 階段派外審、大包第 4 階段 PLAN 審查時）、`references/history.md`（想推翻或放寬某條規則時）、`references/project-deepweave.md`（在 deepweave repo 要跑測試、閘或碰 prod 時）。
範本：`assets/`（審查任務書、PLAN 審查任務書、收斂驗證任務書、修復任務包、checkpoint 回報、走一趟套件）。腳本：`scripts/`（見下）。

## 分流：精簡路或完整七階段
開工判一次（回報的行為 bug 診斷完才判，見 `phases/bug.md`），寫進 PROGRESS.md 開頭。**審查不分流**：兩條路都照「依規模分級」派該派的 reviewer，含外審（owner 10-05、10-06：「唯獨 review 不能省」）。止血 commit 前跑 2 路，完整審查併進後續的修復任務（owner 11-05）。
- **精簡路**——三條都成立才走：
  1. **需求明確**：修法已定或只有一種合理做法，不需要 owner 做設計／文案取捨（backlog 寫好修法的條目、根因已定位的 bug、補測試、一行級 UI 修正）。
  2. **不碰重要面**：credits、權限、18+／未成年、跨層不變式、prod 資料、全站可用性（斷路器、retry、逾時預算）。
  3. **小**：正式碼約 300 行內。
- **完整七階段**：其餘一律。重要功能、要 owner 定方向的使用者有感功能，正是討論與訪談存在的理由——把方向錯誤提前到寫程式之前。
- 拿不準走完整。精簡路途中撞到上面任一條 → 依 `phases/lean.md`「升級」停下改走完整。

## Checkpoint 協定（完整路，硬規則；第 1、6 階段不停，見第 7 條；精簡路只在交付時停，見 `phases/lean.md`）
owner 吃過「一路衝到底、末端才發現前提錯」的虧，也要自己決定何時 compact——所以 owner 要做決定的地方都停。
1. 結論與下一步寫進 PROGRESS.md（階段表那一列＋「## 下一步」＋「## 第 N 階段紀錄」五行：做了什麼／關鍵裁決／紅→綠或證據／閘結果／殘留），**同時改檔頭 frontmatter**：`status`（`owner` 等 owner 拍板／說繼續／commit／push、`doing` 做到一半被打斷、`blocked` 等外部、`deploy` 已 commit 剩部署驗收、`done`）＋`next`（一句話：下一步是什麼、誰要動）；任務住在 worktree 加 `worktree: <名稱>`，多包任務或階段表推不出來時加 `phase:`（如 `第三包 5/7`）。改完跑 `scripts/planview.py <slug>` 產任務頁（第 2／3／4 階段另有導讀頁；兩者的形狀見 `assets/checkpoint.md`）。精簡路在建檔與交付時各改一次；owner 說 commit 並 commit 完，改成 `status: deploy`、`next` 寫部署要點，**這一次手動跑 `python3 ~/.claude/skills/seven-phase/scripts/board.py`**（任務目錄可能在下一次開 session 前就被刪，ledger 要先存到部署要點）；其他 checkpoint 不手動跑，看板由 SessionStart hook 在 startup／`/clear`／`/compact` 重算（11-10：主 session 31 天手動跑了 966 次）。
2. **完全停止**，白話回報：做了什麼、發現什麼、為什麼這樣決定、接下來幹嘛。
3. 附一段可直接貼的 `/compact <摘要>`（位置、真相源檔、下一步、全程規則），用 `assets/checkpoint.md` 的形狀；**並明說現在能不能 `/clear`**（判準同檔第 6 條；owner 不要多個任務的 context 混在一起）。
4. **等 owner 說「繼續」**才進下一階段。不得順勢往下做，也不得在回報裡先問下一階段的問題。例外兩個：第 5 階段等走一趟時先寫第 6 階段的任務書與派工單，只寫不派（`phases/5-implement.md`「等走一趟時先備第 6 階段」）；第 1、6 階段照第 7 條接著做。
5. 背景 subagent 在 checkpoint 之後才交件：併進記錄，下一則回覆一句話告知，並給更新版 `/compact`。
6. **compact 要趁暖、要早**：`/compact` 在 owner 離開**之前**貼（主對話快取 1h，`~/.claude/settings.json` 的 `promptCacheTtl`；用量超額時降為 5m；冷的 compact 會把整段歷史按未快取價重讀），目標 compact 時 context ≤ 200k（10-07 實測中位 358k＝太晚）。回來發現冷了（`/usage` 的 `Prompt cache (main)` 顯示 cold）而且下一階段不需要延續對話 → `/clear` 再讀 PROGRESS，不要 compact；要放棄一條路用 `/rewind`（回到已快取的前綴，比 compact 便宜）。
7. **第 1、6 階段回報後接著做，有事才停**（owner 11-10：只在 owner 要做決定的地方停）。照第 1 條寫 PROGRESS（`status: doing`）、照第 2 條發一則白話摘要並明說「接著做下一階段，要停就打字」，不附 `/compact`、不等回覆，同一段對話直接往下：
   - **第 1 階段** → 直接準備第 2 階段的簡報（含條件觸發的三取向對照），停在第 2 階段。照舊在第 1 階段結束就停的情況：挑戰結論推翻任務前提或 owner 指定的做法、有 prod 查詢等 owner 核准、預估規模大到要提 ROADMAP 拆任務。
   - **第 6 階段** → 合成與核對做完（`phases/6-review.md` §5）直接照 `phases/7-converge.md` §1 派收斂驗證與不必驗證的修復片；**checkpoint 移到驗證交件之後**（`phases/7-converge.md` §2）：一則回報放第 6 階段摘要、驗證結果與拍板題，沒有拍板題也停這一次，owner 回覆後才切剩下的修復片。照舊在第 6 階段結束就停的情況：有 CRITICAL、有發現質疑 SPEC 的方向或範圍、某一路跑不了而替補要 owner 決定、`reviewcov.py` 沒過。
   - owner 開工時說「每站都停」，整個任務照舊每階段停。

   為什麼：10-10～11-10 這兩站 66 次回覆，owner 帶新指示的只有 2 次，其餘是 `/clear` 後貼接續句；6→7 等待中位 28 分、三成超過 2 小時、31 天合計 89 小時，那段時間機器閒著（`retros/2026-11-10-first-principles.md` §2）。「有事才停」是 owner 追問品質影響後加的：拿掉的是放行，不是煞車。代價 owner 已知：審查後每包約 $15–80 的驗證與修復會在 owner 回覆前派出去。

## 全程規則
- **禁 git commit／push**，直到 owner 明說（owner 要在進歷史之前看過整包結果）。commit 形狀＝兩個：`feat(x): ...`（程式碼＋CLAUDE.md／`.claude/rules/`＋契約）與 `docs(prps): <slug> 七階段記錄`（任務目錄）。工作樹常有別人／別任務的已暫存改動：**一律 `git commit --only -F <msg> -- <明確路徑>`**（新檔先 `git add` 那幾個路徑），絕不用裸 `git commit` 或 `git add -A`。push 前 `git fetch` 並確認 ahead-only。
- **真相源**＝`.claude/PRPs/plans/<slug>/{RESEARCH,SPEC,PLAN,REVIEW,PROGRESS}.md`＋`plan-appendix/`（PLAN 的機器面：規則對照、矩陣符號／行號、測試整表；不放 repo root，會被當暫存清掉）。耐久結論另外搬進最近的子目錄 `CLAUDE.md`——任務文件會被刪；母檔開頭有「主題規則」索引的（worker、backend-golang、angular-workspace），寫進索引指向的 `.claude/rules/<area>/*.md`、不寫回母檔，沒有合適主題就新建規則檔（`paths:` 寫 repo 根相對的錨定 glob、不加 `**/`）並在母檔索引加一行。寫成 pattern：規則＋一句理由＋落點（路徑／符號／slug），不帶任務代號、日期、commit、測試名清單與實驗經過；同主題就合併改寫既有段落，不往後追加。為什麼：子目錄 CLAUDE.md 讀到就整份載入、之後每回合重讀，逐次追加的沉積曾把 worker 那份堆到 277KB（10-05 全面瘦身成約一半，10-06 再把三份母檔的主題章節拆成碰到才載入的規則檔，worker 母檔剩 14KB）。
- **主 session 讀得窄**：主 session 是整個流程裡開最久的 context，讀進的每一行到下次 compact／`/clear` 前每一回合都再付一次。讀程式碼帶行號範圍、搜尋先用 `grep -l`／`-c`／`-m` 量大小；大範圍探索派 code-explorer；agent 報告先讀 hand-back 摘要，要裁決的條目才開全文；真瀏覽器操作派 `ui-lane-reviewer`。親跑閘照舊（輸出寫檔再 grep）。
- **註解不可信**：任何階段不得用註解、CLAUDE.md、SPEC、記憶推斷程式行為；只信執行＞程式碼。註解與行為不符＝必修 issue，誤導性註解嚴重度視同邏輯 bug。新文字不得以任務代號（「包 A」「R8」「REVIEW §x」）當唯一說明；可寫 slug。
- **owner 溝通**：先講人話再附技術細節；技術縮寫首次出現附一句白話。拍板題一律 AskUserQuestion（推薦項排第一標 Recommended、選項寫「選了會發生什麼」、關鍵對比用文字圖 preview），一次一件事。
- **挑戰 owner**：有證據的立場要堅持並攤開證據、被說服才改；owner 新資訊推翻結論要坦白更新。發現更好的做法要反向挑戰並附理由。
- **規模閥門**：小而明確的走精簡路（見「分流」）；明顯太大可提議 ROADMAP.md 拆子任務（owner 同意）。
  - **拆包每一刀寫明換到什麼**（ROADMAP 拆子任務、PLAN 切成可各自部署的包都適用）：只認三種——①真的會分開部署或分開回滾；②兩包之間 owner 要親手試、可能因此改方向；③不拆的話單包會超過大包門檻（「依規模分級」），或把重要面和大量一般改動混在同一份 diff。三種都沒有就合成一包、內部照樣分片。為什麼：每包都付一次固定的驗證成本（審查路數、收斂驗證員、走一趟、全閘、部署驗收），拆 N 包就付 N 次；「一起去一趟」同一畫面拆了 7 包、共用一個旗標同次部署，「各自部署」沒用上（history 10-17）。
  - 子任務表（ROADMAP）或包表（PLAN）每包加「驗證帳」欄：這包的驗收項各自要到哪一層才證明得了（單元／pin＜指定整合＜全閘＜走一趟＜探針盲評＜部署後 prod 查詢），走一趟、探針、prod 查詢各幾次。比較切法時比各包加總，不只比單包。
- **backlog 的家是 `.claude/PRPs/BACKLOG.md`，入口只收會被拉走的**（不在任何任務目錄裡）：收斂或交付時沒修的發現、後續小包、另開任務，當下分三路——
  - **重要**（錢、權限、未成年、全站可用性、資料遺失，或最終嚴重度 HIGH 以上）→ `## 重要`；
  - **使用者有感、而且寫得出修法**（`方向：` 是一個做法，不是「再查」）→ `## 有感`；
  - **其餘**（低項、寫不出修法的有感、已知限制、純技術債）→ 不進 BACKLOG，只留在任務的 REVIEW.md／PROGRESS.md（隨 `docs(prps)` commit 進歷史，`accept.py show <slug> REVIEW.md` 取回）。
  格式：每條 `- [ ] **白話標題** — 一句影響。方向：…。出處：\`路徑\` 條目 @<hash>｜狀態：未開／等外部：…／任務：<slug>（進行中）`；出處寫 commit 過的路徑，**不准指向 `tmp/`**（會被清掉）。開成任務就改狀態，做完就刪那一行。看板（`board.py`）只讀這兩組；其他 `## ` 標題底下有條目、或出處指向 `tmp/`，看板會報數。11-10 以前的「其餘」「已知限制」在 `.claude/PRPs/BACKLOG-archive.md`（看板不讀；第 7 階段查「同類以前出現過」時一起 grep）。為什麼：owner push 時會刪任務目錄，所以要有個家（10-07）；但 10-07～11-09 這個家從 146 列漲到 829 列、真正關掉 ≤22%，重要 70 條有 62 條沒開，出處指向 `tmp/` 的路徑 65% 已消失——寫進去不會被做的條目只是庫存，還把重要的埋住（11-10 TPS 檢討）。
- **排序**：backlog、後續小包與 ROADMAP 依「重要（錢、權限、未成年、全站可用性、資料遺失）＞使用者有感」排（owner 10-06：「優先重要功能跟使用者有感功能，其餘收斂」）。
- **修→重走的迴圈要停損**（owner 10-19；第 5／7 階段與精簡路的「修片→重走」都適用）：
  - 走查找到的「低」不開修片：寫進 BACKLOG，或在 checkpoint 交 owner 目視；「中」以上才修。
  - 重走只量剛修的條目、沿用既有腳本（套件用 `run_kit.mjs`）；抽樣回歸只在 checkpoint 前跑一次。
  - **修片進場門檻**（「中」以上的行為 bug，第一次修就適用；註解、文件、守衛測試不適用）：派修片前，主 session 把兩樣東西寫進該片 `<片號>-brief.md`（回報的 bug 同樣要這兩樣，診斷時就做，`phases/bug.md` 依編號引用）：
    1. **會紅的指令**：已經跑過、紅在回報的**那個症狀**上（不是附近另一個失敗），附指令與紅的輸出。只有真引擎重現得出來就用真引擎：套件步驟（`run_kit.mjs --steps`）、真執行的 curl／整合測試；走一趟找到的，`ui-lane-reviewer` 交件時已附重現步驟（`assets/walk-kit.md`「repro」）。做不出來就不派修片：寫下試過什麼，向 owner 要能重現的環境或錄下的證據（HAR、log、附時間點的螢幕錄影）。
    2. **觸發表**：會影響同一塊的觸發情況（狀態×動作、在途×另一動作、0／>0、失敗、到底）逐列用同一種指令跑過，記下現在紅或綠。
    agent 照 `assets/fixplan.md`「行為 bug 的回饋迴圈」做；收件時主 session 重跑那條指令與整張觸發表，PROGRESS 該片紀錄寫一行：指令、觸發表幾列、修後幾列符合預期。
    只有真瀏覽器看得到的 bug，派工訊息附走一趟環境（唯讀，規則見 `assets/fixplan.md` 環境規則「真瀏覽器」）；修片報告的「主 session 要量的點」**逐條有腳本跑過、存了輸出才算收件**，沒腳本的點＝還沒收。
  - **同一塊（同一元件或行為）修了兩輪、重走仍冒「中」以上的新 bug＝迴圈**：停開修片；把這塊歷輪的走查發現與量測、相關程式碼、觸發表組成一份證據包（與 reviewer 手上的同一份事實），交 codex／grok／agy 並行討論「做法本身對不對、該換什麼設計、哪些要真引擎測」（`run_external.py`）；收件合成後用 AskUserQuestion 問 owner「採用收斂出的做法（附預估時間）／停損（記 BACKLOG）」，答之前不開下一片。
  - 為什麼：journey-scene-layer 第 5 階段修片 FX3～FX13、重走十輪、每輪約 1 小時，後幾輪的新 bug 都是上一輪修法帶出來的，低項也走完整循環（owner：「我被你的行為卡了好幾小時」「主要重工的浪費」）。進場門檻借自 `mattpocock-skills:diagnosing-bugs`：等修第二次才列觸發表會晚一輪，第一次修就跑過整張表，才擋得到修法在別的觸發情況帶出的新 bug；那份 skill 在 transcript 裡從沒被呼叫過（修片 agent 沒有 Skill 工具），所以要點直接寫進任務包。
- **改流程就登記驗收**：改本 skill、repo 的 hook／agent 檔／settings，或 CLAUDE.md 結構，同一次就在 `ACCEPTANCE.md`「待驗」加一列（到期日、指令、判準＋基線、出處）；驗收指令不准指向 `tmp/`。看板在 SessionStart 列出到期的列；看到「驗收到期」，在下一次回報時跟 owner 提一句。為什麼：10-17 檢討時，5 筆流程驗收有 2 筆腳本被清掉、2 筆過期沒跑，規則一直加卻不知道哪些有用。
- **說明檔大小**：看板列出超過 40KB（根 `CLAUDE.md` 12KB、`backend-golang`／`angular-workspace` 母檔 20KB）或比上次檢討長 20% 以上的 CLAUDE.md／`.claude/rules/` 檔。第 7 階段搬耐久結論後跑 `scripts/board.py --docsize`，checkpoint 回報淨增減；把一份推過上限的，同一包內合併改寫或依「全程規則」拆規則檔。為什麼：10-06 瘦身後 11 天，指示檔總量 730KB → 1,119KB，文字規則「合併改寫、不往後追加」沒守住。
- **graphify 只准查詢**，改完 docs／sql／openapi 只提醒圖過期，沒 owner 指示不跑 `--update`。
- **prod**：**任何 prod 查詢（DB、Langfuse 等量測 API）都先用 AskUserQuestion 問 owner**，附確切查詢、它要回答什麼、答案會怎麼改變決定；只看聚合數字也一樣要問（owner 09-28 定）。核准後由主 session 跑，subagent 一律禁打。DB 只下 SELECT；Langfuse 額度與 prod 警報輪詢器共用。不讀使用者對話內容、不印任何金鑰。

## 依規模分級（2026-09-28 owner 定；優先於 history 裡的舊規則）
在 Plan 階段用 `scripts/numstat.py` 或估算標出「正式程式碼行數（不含測試與文件）＋是否碰錢／權限／跨層不變式」，第 6 階段開工前用實際 diff 再確認一次：
- **小包**（正式碼 ≤1500 行，且不碰 credits／權限／跨層不變式）：審查 6–8 路、收斂驗證只驗「有爭議／HIGH 以上／需 owner 拍板」的條目 1–2 路。
- **大包或高風險包**：第 4 階段 checkpoint 前多一輪 PLAN 審查（內部 `lane-reviewer`＋codex＋grok＋agy，見 `phases/4-plan.md`「PLAN 審查」）；第 6 階段完整路數（含 grok／agy 外審）、收斂驗證依條目量分路。
- 拿不準就往完整走。路數組成見 `references/lanes.md`。
- **主 session vs subagent**：1–3 檔、內容已確定的小改（照報告改註解、改 SPEC／PLAN）主 session 直接做；彼此獨立的多片、需要大量閱讀而主 session 不必記住的、審查（要新視角）交 subagent。主 session context 已很長時偏向 subagent（每一步都重送整段對話）。

## 模型與 agent
- **取捨順序：品質 > 省錢 > 速度**（owner 10-16）。評估降 effort、換模型、拆片、砍路數，一律照這個順序判：
  - 省錢先動不碰品質的槓桿（依 laneyield 砍「零獨家已確認」的路、任務包瘦身、少回合、久等前先縮 context），換模型排最後。
  - 要判斷的工作（審查、收斂驗證、規劃、實作片）不降模型；不為了讓小模型接手而把片切得更細——本專案的 bug 多在接縫（跨層漂移、三處對齊），切細等於把接縫切掉，錯誤只是移到整合與收斂才被抓。
  - 降檔／換模型的 A/B 除了正確性，必驗**完整性**（以原設定產出為答案，比檔案清單／命中數／節點數，不得少）與**下游**（同一份產出餵同一顆 reviewer，已確認 findings 不得變少）；任一不過就維持原設定。sha 對上不代表沒漏。
  - Sonnet／Haiku 的延遲優勢不構成換模型的理由。
  - **不開 fast mode**（owner 11-06：「我會破產」）：單價 2 倍、走 usage credits；要快從少回合與工具下手。
- 主 session：簡報、訪談、規劃、委派協調、**親跑測試與閘**、一切最終裁決。subagent 只回結論與證據，**不代表 owner 做任何決定**。
- subagent 一律 opus-5.5，**effort 只能由 agent frontmatter 決定**（`~/.claude/agents/*.md` 的 `model:`／`effort:`；prompt 寫 effort 是安慰劑；Agent 工具的 `model` 參數勿傳）。
  - **唯一例外：graphify 語意抽取**派 quick-task 時傳 `model: "sonnet"`（Sonnet 5.5 high），或改走 agy `gemini-3.8-flash` 細切；不用 Haiku。配方與量測在 repo `docs/graphify.md`。
  - **xhigh（要判斷）**：planner、database-reviewer、security-reviewer、pr-test-analyzer、a11y-architect、`lane-reviewer`（關注面 lane）、`ui-lane-reviewer`（真瀏覽器的 UI lane 與走一趟）、`converge-verifier`（收斂驗證）、`blind-judge`（盲評，不載 CLAUDE.md）。
  - **high（範圍已定，或 A/B／laneyield 證實不掉品質）**：tdd-guide、comment-analyzer、go-reviewer、silent-failure-hunter、typescript-reviewer、python-reviewer（見 `references/lanes.md` §effort）、`doc-slice`（文件片）、doc-updater（codemap／README）、code-explorer、quick-task（graphify 抽取除外，見上）。
  - **內建 agent（general-purpose、Explore、Plan）沒有 frontmatter，effort 跟著主 session 走**：主 session 開 max 它就跑 max，開 medium 就跑 medium。有固定用途的工作一律派上面的專用 agent，general-purpose 只留給不屬於任何一類的雜務。
  - agent 檔改動不必重啟：改既有檔的 frontmatter，下一次派工就生效；新建或刪除的檔，下一個使用者回合才出現在可用清單（同回合派會 not found）；已在跑的那顆維持舊設定跑完。驗證看 `~/.claude/projects/<proj>/<sid>/subagents/*.jsonl` 的 `"model"` 與 `"effort"`。內建 agent 的模型靠 `~/.claude/settings.json` 的 `CLAUDE_CODE_SUBAGENT_MODEL`；`fork` 永遠跑主 session 的模型與 effort。花費用 `scripts/agentcost.py` 量。
- **派工前確認 agent 的工具集**：comment-analyzer／planner 等沒有 Write（交付要主 session 落檔或改用有 Write 的 agent）；沒 Bash 的跑不了測試；lane-reviewer、quick-task 只有 Read／Write／Bash／Grep／Glob（少背約 15k token 的工具定義），要瀏覽器的派 `ui-lane-reviewer`。有 `tools:` 白名單的 agent 只拿得到列出的工具；11-07 起定義都不列 `LSP`（從工作目錄看被 git 忽略的路徑——`tmp/` 樹、從主 checkout 查 `.claude/worktrees/*`——找引用與跳定義回假的空結果，11-04、11-05 探針），找符號與呼叫點一律 grep 加任務包的 caller 清單；定義都有回合規則（獨立的讀取同一則訊息並行發；每回合重付整段 context，回合數就是錢。11-07 探針：code-explorer 33→18 回合、$ −18%、找到的檔一樣；`scripts/turns.py` 量每回合呼叫數）；每顆都有 `maxTurns`（約 1.3–1.5 × 實測最大回合數，失控保險，到頂會硬停、hand-back 可能不完整）。
- **主 session 的模型與等待**：Fable 每次呼叫約 Opus 5.5 的 3 倍，閒置失效在 700–800k context 一次 $7–10；派出一批要等 10 分鐘以上的 subagent 之前先縮 context（checkpoint ＋ `/compact` 或 `/clear`）；切模型只在 `/clear` 之後（切了就是全量重寫）；effort 在 Fable／Opus 5.5 切換不打斷快取，派工／收件／寫記錄階段可降 `high`。
- 專案專用 agent 優先於通用 agent；語言專門 reviewer 不能用 general-purpose＋好 prompt 取代（內建清單打的是別的方向）。
- 委派：自足任務包（目標、輸入路徑、環境規則、交付格式）；**長交付一律寫檔**，hand-back 只要 ≤12 行摘要＋檔案路徑；並行 agent 的中間檔／交付檔名帶片號或 lane 名（共用 scratchpad 會互截）。
- **任務包瘦身**：派工訊息分「必讀」與「需要時再讀」兩欄。必讀只放這片用得到的段落，從原文**逐字摘錄**進 `<片號>-brief.md` 並附出處路徑（用 `scripts/mkbrief.py`，不手抄、不每次現寫摘錄腳本），不改寫成摘要（摘要會引入錯誤，也不是證據）；全文路徑一律留在「需要時再讀」。必讀合計 ≤ 約 90KB（`wc -c`，約 3 萬 token），超過要在派工訊息寫理由。不算在內的：審查對象本身（diff、程式碼）、agent 自己決定去讀的程式碼。為什麼：subagent 的花費七成以上是每一回合重讀累積的上下文，前面多讀一段，後面每一回合都再付一次；逐字摘錄加上保留全文路徑，品質不會因此下降。
- **>5 顆 agent 共用一份沒實測過的處方 → 先派一顆探針**。

## 腳本（`python3 ~/.claude/skills/seven-phase/scripts/<name>.py --help`）
- `numstat.py`：未 commit 改動（或對某個 base）依 prod／test／doc 分類的行數，給規模分級用。
- `mkpatch.py`：產 `change.patch`（含 untracked 新檔）、`change-src/-tests/-docs.patch`、`baseline.sha256`、`files.txt`，以及 `outside.sha256`（被 `--exclude` 排除的既有改動，給 checktree 分辨「本來就髒」）。
- `mkworktree.py`：`git worktree add --detach HEAD` ＋ 套 patch ＋ 對 baseline 驗 sha（快照／突變樹／修復樹都用它）。
- `checktree.py`：對 baseline 驗一棵或多棵樹的 sha（不符 exit 1）；清單外的變動另列 warning（`--strict` 才 fail）。`tmp/`、任務目錄與 gitignored 檔不在檢查內，所以它不是「整棵樹沒人寫過」的證明。
- `merge_back.py`：把工作樹相對快照改過的檔複製回主 checkout。候選＝修復樹與快照兩邊 `git status` 的聯集（改回 HEAD、刪掉快照新檔都抓得到）；兩棵 HEAD 不同、主樹那些檔不等於快照、片與片重疊都會停；刪除只回報不套用；把檔改回 HEAD 一律先停（分不出是刻意還原還是 agent 誤 reset），確認是裁決要的才加 `--allow-revert <片>:<路徑>`；`--only-files F` 要求候選恰等於那份清單，多或少都停（`recv.py` 用它）。
- `run_external.py`：跑 codex／grok／agy 外審（背景跑，見 `references/external-review.md`）。失敗 exit 非零、舊報告不動，每次寫 `lane-<x>.status.json`。
- `dockerslot.py`：`-- <cmd>` 拿到全機 N 個槽位之一才跑（預設 3，flock 檔，持有者死掉自動釋放）；`--all` 拿全部且讓後到的排在它後面（全量整合）、`--pool <名> --slots 1`（sqlc）、`--status` 看誰佔著；等超過 `--max-wait`（預設 300 秒）exit 75 並印出持有者。讓 agent 一波派完、只在吃 Docker 的那一刻排隊。
- `synth.py`：第 6 階段合成派工零手工——`wf_reports.py` 落檔、盤點每一路（ok／失敗／缺件／外審還在跑）、`reviewcov.py --index`、照 `assets/synth-brief.md` 產 `synth-task.md`、印派工句；缺件 exit 1、外審未交 exit 3（等背景通知再跑，不 sleep）。
- `wf_reports.py`：Workflow 扇出（第 6 階段內審、第 7 階段驗證）的報告全文從 run 的 `journal.jsonl` 抽出、依 label 落檔（subagent 在 Workflow 內寫不了檔）；每路寫 status 檔（含 schema 的 `counts`），`tasks/` 有派工單卻沒啟動的列 MISSING。
- `extract.py`：markdown 依標題或行號逐字切段、附出處。組任務包用下面的 `mkbrief.py`。
- `mkbrief.py`：一份純文字 manifest 組好每片的 `<片號>-brief.md`（手寫開頭＋逐字摘錄附出處＋該片會載入的規則＋需要時再讀），超過 90KB 沒寫理由就拒絕；`--check` 比對有沒有過期。
- `mkpacket.py`：一份 manifest 組好外審三份材料包 `packet-{codex,grok,agy}.md`（第 6 階段 diff 審、第 4 階段 PLAN 審）：程式碼摘錄照 PLAN 矩陣（或 `plan-appendix/matrix.md`）機械取、規則只取 PLAN 引到的節，核對摘錄標頭數＝矩陣列數、每份 `wc -c`；agy 超過 `run_external.py` 的 90KB 上限就照 `references/external-review.md` 的階梯自動裁、`agy-split` 依片拆，裁不下 exit 2（檔仍寫出，交 owner 手動跑）。`--check` 只核對不寫檔。
- `gate.py`：跑閘（Go、Angular、ai-service、Go lint），一行判定，紅、編譯失敗、零支、沒跑完不會被讀成綠；自動讀樹裡的既存紅名單。`lint` 模式跑 golangci 整包：OOM（137）就自動分批到跑完、沒跑完的 pattern 判 INCOMPLETE（不會被讀成綠），既存紅讀 `backend-golang/known-red-golint.txt`（`path :: linter :: message`，不含行號）；`--clean-docker` 先清孤兒 testcontainers。用法與 exit code 見 `references/project-deepweave.md`。
- `mutate.py`：一份 manifest 跑多個突變，判 KILLED／SURVIVED／INVALID（沒編譯過不算殺死）；Go 走 overlay 不碰樹，其他保證還原（kill -9 後 `--recover`）。
- `recv.py`：收一片交件的機械步驟（交付檔、範圍、閘的覆蓋、跑閘、閘跑的就是要合的、`merge_back`、PROGRESS 骨架、board），不帶 `--apply` 只預演。改 `.go` 自動多跑全 repo vet；主線動了、沒還原的 mutate、新增 skip 都拒收。判斷（紅的原因、偏離、抽突變）仍由主 session 做。用法見 `references/project-deepweave.md`。
- `dbq.py`：本機 Docker DB 查詢與 clone、`scripts/fixtures/` 固定角色資料；預設唯讀（見 `references/project-deepweave.md`）。
- `rulesfor.py`：依 PLAN 檔案矩陣路徑列出命中的 `.claude/rules/` 檔與子目錄 `CLAUDE.md`，產生 `## 規則對照` 骨架（放 `plan-appendix/rules.md`）；`--check <任務目錄>` 會擋缺列，也擋處置不是「套用：」或「不相關：」開頭的（第 4 階段第 11 條）；11-07 前把表寫在 PLAN.md 的舊任務照常檢查。
- `reviewcov.py`：第 6 階段收件（第 4 階段 PLAN 審查地圖另加 `--plan-tests`：「改 PLAN」要點名 PLAN 裡有的測試編號）。核對預期的每一路都交件（失敗、缺件、空檔、舊報告、標題形狀混雜、`counts` 對不上都擋），每條發現都在去重地圖的「來源」欄或未收錄節、地圖編號不重複、來源不指向不存在的條目（`--ack-manual`／`--waive`／`--section` 見 `phases/6-review.md` §5）；`--index` 產生給合成用的計數表與逐條清單（md＋json）。
- `claudemdcost.py`：子目錄 CLAUDE.md 與限定範圍規則（讀到才載入）的花費，依檔案彙總、worktree 副本併回原路徑。
- `wallclock.py`：每種 subagent 的牆鐘（中位／p90／最大）與時間花在哪類工具；檢討與排程改動的驗收用。牆鐘已扣掉斷線停擺與等續跑的閒置（`away` 欄，`_usage.away_spans`；`uilane_audit.py` 同）。
- `uilane_audit.py`：`ui-lane-reviewer` 每次的生成／等工具切分（平行呼叫不重算）、腳本重複率、自寫基礎腳本數、等失敗執行的佔比、峰值 context、探針背景／前景數與前景等待、輪詢迴圈等待。
- `run_kit.mjs`、`walk_lib.mjs`、`walk_env.sh`：走一趟的 runner、共用瀏覽器函式庫、起環境（`assets/walk-kit.md`）。
- `board.py`：跨任務進度看板（另含「流程」區：`ACCEPTANCE.md` 到期的驗收、到期滿 5 列時提示順便檢討、過大的說明檔；`--docsize` 印每份說明檔的大小與增減）——掃主 checkout 與所有掛分支的 worktree 的 PROGRESS.md，讀 frontmatter、git 狀態當場算，寫 `<主 checkout>/tmp/progress/board.html` 並印摘要；HTML 下半部是 `.claude/PRPs/BACKLOG.md` 的 Backlog 四欄。每次跑都把未完成任務的「下一步」快照進 `tmp/progress/ledger.json`，目錄刪了仍以「目錄已刪・待部署」顯示，owner 說部署完了 → 確認是哪幾個後 `--deployed <slug…|all>` 清掉；`~/.claude/settings.json` 的 SessionStart hook（startup／clear／compact）以 `--hook session-start` 跑它，把摘要注入 context。
- `planview.py`：一個任務的 SPEC／PLAN／REVIEW／PROGRESS md 原文嵌進一頁 HTML（`tmp/progress/<slug>.html`）：儀表板計數（定案、切片、測試、閘、審查地圖、交 owner、圖）、固定目錄、`##` 章節收合、表格固定表頭＋逐欄篩選、mermaid 渲染（marked／mermaid 釘版 CDN，離線顯示原文）；每次 checkpoint 跑、改了任務檔重跑。`plan-appendix/*.md` 一併嵌入、預設收合。`--check <slug>`：SPEC／PLAN 既無 ```mermaid 也無 `無圖：<理由>`，或 PLAN.md 超過 300 字的行 >5，就 exit 1（第 3／4 階段好讀閘）。
- `taskcost.py`／`laneyield.py`：每個任務、每個階段目錄的花費；每一路審查的獨家發現與花費（調路數、檢討流程前先跑；精簡路讀 PROGRESS.md 的 `## 審查` 表）。`laneyield.py` 依表頭欄名讀地圖與裁決表，讀不懂的資料（認不出的裁決詞、來源不是 lane、編號重複、找不到地圖）一律印成 `DATA error`、不當零；`--no-cost --strict` 是第 7 階段裁決表的格式閘；一份 REVIEW 放多包、各包審查目錄不同時加 `--review-dir '<輪標題字>=tmp/<slug>/review2'`（可重複）。`agentcost.py`／`taskcost.py`／`laneyield.py`／`claudemdcost.py` 共用 `_usage.py`：含 Workflow agent，同一訊息多筆 usage 取各欄最大值，依每筆時間戳過濾，cache write 分 5m／1h 計價；`--all-worktrees` 連 worktree 的 session 一起算。金額是 API 等價、不是帳單，沒有價格的模型列為 unpriced、不算 $0。
- `tx.py`：transcript 查詢（`count`／`list`）：依天數、主 session 或 agent 種類、工具名、input 正規式、result／hook 訊息正規式（`--result-grep`，含 PostToolUse attachment）篩 tool_use，`--by session|agent|tool|day|file` 計數；檢討與驗收要掃 transcript 時用它，不再現寫腳本。
- `accept.py`：`ACCEPTANCE.md` 驗收 runner：列到期／事件／未到的列，把指令欄裡跑得動的 code span 真跑（`--run`，輸出落 `tmp/accept/<日期>/`）、對著判準印；`--slug <slug>` 代入 `<slug>`／`<任務>`／`<docs(prps) hash>` 佔位符；不可跑與「手動／問 owner」的列成「需人判」清單；有副作用的 span（`rm`、`--apply`、`--deployed`、push／commit）一律不跑。`show <slug> [<檔名>]` 從最新仍含該檔的 `docs(prps)` commit 取回已刪任務目錄的檔。判定仍由主 session 做，不改 ACCEPTANCE.md。
Bash 一律絕對路徑或 `git -C`／`go test -C`，**不要 `cd`**（工作目錄會殘留，下一個 bridge 或 pathspec 會跑錯樹）。
