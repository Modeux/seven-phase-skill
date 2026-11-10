# seven-phase skill 內部一致性稽核（2026-11-10，HEAD a58a2a8）

範圍：`SKILL.md`、`phases/*.md`（10）、`assets/*.md`（7）、`references/{lanes,external-review,project-deepweave}.md`、`agents/*.md`（24 份，不是 brief 說的 23 份）、`scripts/`、`README*.md`、`evals/evals.json`。全程唯讀；腳本只跑過 `--help`（輸出存在 `/private/tmp/claude-501/-Users-huntwu-Desktop-deepweave-deepweave/a775b548-cb3e-47ad-8ae3-5c7bc9a9822c/scratchpad/help/*.txt`）；`board.py` 沒有 `--help`，只讀了原始碼（見 H2）。

計數：**高 2、中 9、低 18**。

---

## 1. 發現表（依嚴重度）

| 編號 | 嚴重度 | 類型 | 位置 A（檔:行＋逐字） | 位置 B（檔:行＋逐字，或「不存在」＋指令） | 照著做會發生什麼 |
|---|---|---|---|---|---|
| H1 | 高 | 矛盾（d6d01f7 只改了一處） | `SKILL.md:73`「走查找到的「低」不開修片：寫進 BACKLOG，或在 checkpoint 交 owner 目視；「中」以上才修。」 | `SKILL.md:69`「**其餘**（低項、寫不出修法的有感、已知限制、純技術債）→ 不進 BACKLOG，只留在任務的 REVIEW.md／PROGRESS.md」；`scripts/board.py:41-43` 的 INVARIANT 註解寫只收兩組、別的標題底下的條目一律算 stray，`board.py:459-460` 會印「⚠ N 條寫在重要／有感以外的標題下」 | 第 5／7 階段的 agent 照第 73 行把走查找到的「低」寫進 BACKLOG。低項放不進「重要」也放不進「有感」，所以不是被看板報成 stray，就是被硬塞進「有感」。d6d01f7 才擋下的庫存增長又回來了。**需 owner 決定**：(a) 第 73 行改成「只留任務記錄，或在 checkpoint 交 owner 目視」（跟第 69 行一致）；(b) 保留第 73 行，把走查低項列為第 69 行的例外，並指定放在哪一組。 |
| H2 | 高 | 斷指標（旗標不存在，而且有副作用） | `SKILL.md:115`「## 腳本（`python3 ~/.claude/skills/seven-phase/scripts/<name>.py --help`）」：整節要 agent 用 `--help` 查每支腳本 | `scripts/board.py` 沒用 argparse（`grep -c argparse board.py` → 0）；`main()`（`board.py:671-691`）只認 `--hook`／`--deployed`／`--docsize`，其他參數一律當成完整跑：會寫 `tmp/progress/board.html`（`:691` `out.write_text(render_html(b))`），並把 ledger 快照寫進 `tmp/progress/ledger.json`（`:171`／`:191`）。所以我沒有跑它 | 照第 115 行跑 `board.py --help`，拿不到用法，反而在主 checkout 手動跑了一次看板並寫檔。這正是 `SKILL.md:43`「其他 checkpoint 不手動跑」（a58a2a8）想避開的事。修法二擇一：board.py 補一個 `-h/--help` 分支，或 SKILL 第 115 行把 board.py 列為例外（寫「讀檔頭 docstring」）。 |
| M1 | 中 | 缺件（新規則沒有落點，最新一筆 commit） | `SKILL.md:52`「owner 開工時說「每站都停」，整個任務照舊每階段停。」 | 整包只有這一處提到「每站」（`grep -n '每站' SKILL.md phases/*.md assets/*.md` → 只有 `SKILL.md:52`）。PROGRESS frontmatter 的欄位只有 `status`／`next`／`worktree`／`phase`（`SKILL.md:43`、`board.py:16-24` docstring），`/clear` 接續句 `assets/checkpoint.md:24`「繼續 <slug> 第 N+1 階段：先載入 seven-phase skill，讀 …PROGRESS.md「下一步」」也沒帶這個旗標 | owner 開工時說了「每站都停」，之後照協定在 checkpoint `/clear`，新 session 只讀 PROGRESS，看不到這句。到第 1、6 階段就照預設不停，在 owner 回覆前把 $15–80 的驗證與修復派出去（`SKILL.md:54` 自己寫的代價），違反 owner 明說的指示。`/compact` 的「全程規則」欄有機會把它帶過去，`/clear` 一定帶不過去。**需 owner 決定**記在哪裡：frontmatter 加一個欄位（board.py 要跟著認），或寫進 PROGRESS 開頭的分流行。 |
| M2 | 中 | 過期（a58a2a8 漏改） | `phases/bug.md:59`「階段表第 1 列記「bug 診斷」，照 Checkpoint 協定停，再接第 2 階段。」 | `SKILL.md:49-50`「**第 1、6 階段回報後接著做，有事才停** … **第 1 階段** → 直接準備第 2 階段的簡報…停在第 2 階段」 | bug 診斷完、走完整路：bug.md 叫 agent 停，但它指向的協定現在說第 1 階段（bug 診斷就記在這一列）不停。照 bug.md 會多停一站，照 SKILL 不停，兩種讀法都說得通。比較可能的本意：分流題剛在 §4 用 AskUserQuestion 問過，所以接著準備第 2 階段即可。這是推論，git log 裡找不到這條的決策（低信心）。 |
| M3 | 中 | 陳述不實 | `SKILL.md:104` xhigh 名單「`blind-judge`（盲評，不載 CLAUDE.md）」；`references/lanes.md:55`「`blind-judge`（xhigh、不載 CLAUDE.md、只有 Read／Write／Glob）」 | `agents/blind-judge.md:6`「effort: high」、`:3`「Blind A/B judge at fixed high effort」；從 555a0e3（10-19 搬進 repo）到現在沒改過（`git log -- agents/blind-judge.md` 只有這一筆） | 依 `SKILL.md:102`，effort 只由 frontmatter 決定，所以盲評實際跑 high。拿「xhigh 盲評」當模型／prompt 探針判準的人會高估判官的強度。**需 owner 決定**哪邊對：改文件寫 high，或把 agent 改成 xhigh。 |
| M4 | 中 | 陳述不實 | `SKILL.md:108`「定義都有回合規則（獨立的讀取同一則訊息並行發；每回合重付整段 context…）」 | `agents/quick-task.md`、`agents/blind-judge.md` 的本文都沒有回合規則（`grep -c -E '回合\|Turn rule\|turns are the cost\|same message\|one message'` 兩檔都是 0；ui-lane-reviewer 以 `:39`「parallel tool calls in one turn」計入）。`references/history.md:104` 自己也寫「22 份 agent 定義加回合規則」，總共 24 份 | 派 quick-task（組材料包、批次改寫、graphify 抽取）時，以為它會並行讀取，其實沒有這條指示，回合數照舊。修法：兩份定義補上同一段 Turn rule，或把第 108 行改成「除 quick-task、blind-judge 外」。 |
| M5 | 中 | 矛盾（數字；11-04 加了必開路，數量沒跟著改） | `SKILL.md:89`「**小包**…審查 6–8 路」；`references/lanes.md:21`「小包…：6–8 路」；`evals/evals.json:26`「內部＋外部審查共 6–8 路，而不是 12 路以上」 | `references/lanes.md:24-29` 的必開項：每層一個語言 reviewer、comment、pr-test、silent、規格忠實度、codex；`:31` a11y「涉 UI 時必開（…owner 11-04 定）」；`:32` UI/UX `ui-lane-reviewer`「涉 UI 時」。eval 2 的情境 `evals.json:22`「Go handler 2 檔、Angular 元件 3 檔」：go＋ts＋comment＋pr-test＋silent＋spec＋codex＝7，＋a11y＝8，＋ui-lane＝9（碰使用者輸入再加 security＝10）。數量是 09-28 e74a987 定的，a11y 必開是 852388a（11-04）加的 | 跨兩層又涉 UI 的小包，照 lanes.md 要開 9 路，照 SKILL／eval 上限 8 路。照 eval 判分，正確開 a11y／UI lane 的回應會被判錯；反過來壓到 8 路就違反「必開」。**需 owner 決定**：上限改成「6–10 路」，或寫明 a11y／UI lane 不算在 6–8 內。 |
| M6 | 中 | 斷指標（章節已搬走） | `phases/4-plan.md:19`「部署順序（`DROP COLUMN` 先上不再讀該欄的 gateway 再 migrate，`backend-golang/CLAUDE.md` §Migrations & sqlc）」 | `backend-golang/CLAUDE.md` 的標題（`grep -n '^#'`）只有：主題規則、DB Conventions、Character & Visual、Testing、Go 1.27…、Public Payloads…、Common Failures、DB Driver，**沒有 Migrations**。內容在 `.claude/rules/gateway/migrations-sqlc.md:11`「# Gateway：Migrations 與 sqlc」（`DROP COLUMN` 也在這裡）。repo 的 `db/CLAUDE.md:70` 同樣還指向舊章節 | 寫 PLAN 的人照指標找不到章節，只能從母檔的索引自己找（4-plan 已經把規則本身寫在行內，所以不致做錯）。改指向 `.claude/rules/gateway/migrations-sqlc.md`。 |
| M7 | 中 | 過期（5971c6f 漏改 README） | `README.md:171`「上次檢討後累積 5 個任務，看板會提醒你。」；`README.en.md:172`「after 5 tasks since the last retro, the board reminds you」 | `phases/retro.md:11`「或 `accept.py --due` 有 ≥5 列到期（看板會提示）…不照任務數或天數跑」；`board.py:476-480` 用的是 `len(acc["due"]) >= RETRO_EVERY`（到期驗收列數，不是任務數） | owner 照 README 等「5 個任務」的提示，看板實際在「到期驗收滿 5 列」才提示，兩者對不上。 |
| M8 | 中 | 過期（d6d01f7 漏改） | `SKILL.md:138`「HTML 下半部是 `.claude/PRPs/BACKLOG.md` 的 Backlog 四欄。」 | `board.py:43` `BACKLOG_GROUPS = (("重要", "#e5484d"), ("有感", "#f5a524"))`；`render_backlog`（`:650-664`）照這兩組產欄 | 現在只有兩欄，「四欄」會讓人以為「其餘」「已知限制」還在看板上，回頭往那兩組寫（同 H1 的方向）。 |
| M9 | 中（低信心） | 矛盾＋缺件（agy 例外沒進第 6 階段，又撞上新的不停規則） | `phases/6-review.md:22`「lane 死掉或外審跑不了：記清楚是「沒跑」還是「空跑」，**換一個不同鏡頭替補**」 | `references/external-review.md:17`「**CLI 跑不動就交 owner 手動跑，不換鏡頭替補**…在 checkpoint 列出材料包的絕對路徑與大小請 owner 手動跑」、`:27`「第 6 階段的 agy 例外，改交 owner 手動跑」；`SKILL.md:51` 第 6 階段照舊停的條件只有「有 CRITICAL…某一路跑不了而替補要 owner 決定、`reviewcov.py` 沒過」；`synth.py:120-122` 任何一路 failed（含外審）都 exit 1，要 `--waive` 才往下 | 走一遍：第 6 階段 agy CLI 失敗。照 6-review 該替補，照 external-review 不替補、改交 owner。新規則下第 6 階段不停，下一個 checkpoint 在驗證交件之後（`7-converge.md:15`）。要往下走就得先 `--waive agy` 讓 synth 過，地圖合成、驗證員都派出去了，owner 之後貼回的 agy 結果怎麼併、要不要重驗，整包都沒寫。只讀 6-review 的 agent 會直接替補。這是推論，沒有實跑。**需 owner 決定**：agy 失敗算不算「照舊停」的條件，或者寫明 waive 之後 agy 結果的併入流程。 |
| L1 | 低 | 陳述不實 | `SKILL.md:108`「lane-reviewer、quick-task 只有 Read／Write／Bash／Grep／Glob」 | `agents/quick-task.md:4`「tools: Read, Write, Edit, Bash, Grep, Glob」（從 10-19 起就是這樣） | quick-task 其實還有 Edit。不影響派工判斷。 |
| L2 | 低 | 陳述不完整 | `SKILL.md:104-105` 的 xhigh／high 兩份名單 | 名單外的 5 份：`architect.md`、`code-architect.md`、`code-reviewer.md`、`fastapi-reviewer.md`、`performance-optimizer.md`，`:6` 都是「effort: xhigh」 | 名單不是全集；`lanes.md:52` 另外寫了 code-architect 是 xhigh（正確）。 |
| L3 | 低 | 斷指標（旗標） | `SKILL.md:140`「`agentcost.py`／`taskcost.py`／`laneyield.py`／`claudemdcost.py` 共用 `_usage.py`：…`--all-worktrees` 連 worktree 的 session 一起算」 | `laneyield.py --help`、`claudemdcost.py --help` 都沒有 `--all-worktrees`（laneyield 預設就掃全部 worktree，claudemdcost 只有 `--project-dir/--days/--top`） | 有人對 laneyield／claudemdcost 帶 `--all-worktrees` 會被 argparse 拒絕，exit 2。 |
| L4 | 低 | 腳本說明與程式不符（exit code） | `synth.py:16`（help）「1 an internal lane is missing or failed and not waived」 | `synth.py:120-122`：`bad` 收的是**所有**不在 `_PASS` 的路（外審 failed 也算）→ `abort(1, …)` | 外審失敗同樣 exit 1，不是只有內審。`SKILL.md:123`「缺件 exit 1」本身沒寫錯。 |
| L5 | 低 | 矛盾（數字） | `SKILL.md:89` 小包「收斂驗證只驗…的條目 1–2 路」 | `references/lanes.md:38`「小包 1 路、大包依條目量 2 路起」；`phases/lean.md:24`「派 1 路」 | 小包驗證路數上限說法不一致。 |
| L6 | 低 | 矛盾（門檻） | `phases/7-converge.md:46`「有一份被推過 40KB 的，這包內就合併改寫或拆成規則檔」 | `SKILL.md:83`「超過 40KB（根 `CLAUDE.md` 12KB、`backend-golang`／`angular-workspace` 母檔 20KB）」；`board.py:54` `DOC_HOT_CAPS` | 一包把根 CLAUDE.md 推過 12KB：照 7-converge 不必處理，照 SKILL／看板要處理。 |
| L7 | 低 | 孤兒 | `scripts/ctxio.py`（5971c6f 新增） | SKILL／phases／assets／references 都沒提到（只出現在 `ACCEPTANCE.md:90`、`history.md:104`、retros） | 主 session 不知道有這支工具，檢討時可能重寫一支一樣的。 |
| L8 | 低 | 斷指標 | `references/lanes.md:18`「比較腳本在 repo `tmp/laneyield-cmp/compare.py`」 | `[ -e …/deepweave/tmp/laneyield-cmp/compare.py ]` → 不存在 | 要重做 effort 擴大比較時找不到腳本（指向 `tmp/` 本來就違反 `SKILL.md:82` 的精神）。 |
| L9 | 低 | 斷指標 | `references/lanes.md:47`「v2 原文留在 `character-dual-portrait` worktree 的 `tmp/…/ab-tdd-guide-v2.recovered.md`」、`:48`「`tmp/character-dual-portrait/post/agents-backup/`」 | `ls …/.claude/worktrees/character-dual-portrait` 與 `…/tmp/character-dual-portrait` → 都不存在；`git worktree list` 也沒有 dual | 備份已經不在，只剩歷史意義。 |
| L10 | 低 | 斷指標 | `agents/doc-updater.md:30`「`npx tsx scripts/codemaps/generate.ts`」 | repo 沒有 `scripts/codemaps/generate.ts`（`docs/CODEMAPS` 有） | doc-updater 照範本跑這條會失敗。 |
| L11 | 低 | 過期（數字） | `references/project-deepweave.md:85`「repo 有 24 份 `CLAUDE.md`」 | `git -C <repo> ls-files \| grep -c -E '(^\|/)CLAUDE\.md$'` → 27 | 只是數字舊了，結論不變（同名檔多、pathspec 會解析錯）。 |
| L12 | 低 | 兩語版不同步 | `README.md:157` 工具表有 `planview.py` | `README.en.md:156-168` 工具表沒有 planview | 英文讀者不知道有任務頁工具。 |
| L13 | 低 | 陳述不實 | `README.md:152`「除了 `run_kit.mjs` 要 Node，其他都只用 Python 內建功能」；`README.en.md:154` 同義 | `scripts/walk_lib.mjs`（Node＋Playwright）、`scripts/walk_env.sh`（bash） | 安裝說明少列兩支非 Python 工具。 |
| L14 | 低 | 清單不完整 | `SKILL.md:30`「範本：`assets/`（審查任務書、PLAN 審查任務書、收斂驗證任務書、修復任務包、checkpoint 回報、走一趟套件）」；`README.md:147` | `assets/synth-brief.md` 存在（9c8b069），清單沒列 | 由 synth.py 自動取用，不影響流程。 |
| L15 | 低 | eval 與 SKILL 不完全對齊 | `evals/evals.json:32`「沒有 CRITICAL、沒有質疑方向的發現、沒有跑不了的路就不停」 | `SKILL.md:51` 照舊停的條件是「某一路跑不了**而替補要 owner 決定**、`reviewcov.py` 沒過」 | eval 比 SKILL 嚴（任一路跑不了就要停），又漏了 reviewcov 沒過這一條；照 SKILL 換了鏡頭繼續做的回應可能被判錯。 |
| L16 | 低 | 不一致 | `assets/checkpoint.md:19` `/compact` 範本「真相源 .claude/PRPs/plans/<slug>/{SPEC,PLAN,REVIEW,PROGRESS}.md」 | `SKILL.md:58`「`{RESEARCH,SPEC,PLAN,REVIEW,PROGRESS}.md`＋`plan-appendix/`」 | compact 摘要可能漏掉 RESEARCH 與 appendix。 |
| L17 | 低 | 缺件 | `phases/bug.md:28`「commit 完 `status: deploy`，`next` 寫部署要點與確認查詢。」 | `SKILL.md:43`「改成 `status: deploy`…**這一次手動跑 `python3 …/board.py`**（任務目錄可能在下一次開 session 前就被刪，ledger 要先存到部署要點）」 | 止血任務只讀 bug.md 的話會漏跑這一次看板。SKILL 整份載入時有這條規則，所以列低。 |
| L18 | 低 | 門檻描述不完整 | `SKILL.md:83`「或比上次檢討長 20% 以上」 | `board.py:51` `DOC_GROWTH_MIN = 2_000`、`:317` `now - old > max(old * DOC_GROWTH, DOC_GROWTH_MIN)` | 小檔長 20% 但不到 2,000 bytes 不會被列出。 |

---

## 2. 這次變更沒處理的情境（逐一走過程式與文字）

1. **owner 說了「每站都停」之後 `/clear`**（M1）：`SKILL.md:52` 只在對話裡生效 → `assets/checkpoint.md:24` 的接續句只帶 slug 與階段 → 新 session 讀 PROGRESS frontmatter（`board.py:16-24` 定義的欄位裡沒有這一項）→ 照第 7 條預設，第 1、6 階段不停。結果：違反 owner 的明確指示。
2. **第 6 階段 agy CLI 失敗**（M9）：`run_external.py` 寫 failed status → `synth.py:120-122` exit 1 → agent 只能 `--waive`，不然就停。6-review 說替補，external-review 說交 owner 手動跑；新規則下第 6 階段不停，owner 貼回的 agy 結果什麼時候併、怎麼併，沒有寫。
3. **回報的 bug 診斷後走完整路**（M2）：`bug.md:59` 叫 agent 停，它指的協定 `SKILL.md:49-50` 卻說第 1 階段不停，兩種行為都說得通。
4. **第 5／7 階段走查找到的「低」**（H1）：`SKILL.md:73` 叫 agent 寫進 BACKLOG，同檔 `:69` 叫它不准寫；寫進去會被 `board.py:459-460` 報成 stray。
5. **小包涉 UI 又跨兩層**（M5）：lanes.md 的必開項加總到 9，SKILL／eval 上限 8。

---

## 3. 查過、沒問題（覆蓋證明）

### 問題 1 斷指標
- **腳本存在性**：`scripts/` 頂層 33 支非 `_` 開頭的 .py／.mjs／.sh，SKILL／phases／assets／references／README／evals／agents 提到的腳本名全部存在。只抓到通用範例名（`main.py`、`app.py`、`env.sh` 是套件裡的檔、`drive.mjs`／`lib.mjs`／`build.py` 是反例），沒有指向不存在的腳本；`walks.py`／`prep_gap.py`（f97c2e1 commit message）在 `retros/2026-11-10-measure/`，文件本身沒引用。
- **旗標／子命令／exit code**（`--help` 輸出或原始碼，逐條比對文件）：
  - `gate.py`：go／fe／fe-build／ai／lint／clean-docker、共用旗標、7 種 verdict，exit 0/1/2/3/4/75（`project-deepweave.md:8-25`、`SKILL.md:128`）。
  - `recv.py`：17 個旗標、第一行格式、preflight、覆蓋表、新增 skip 樣式，exit 0/1/2/75（`project-deepweave.md:53-61`、`5-implement.md:23`、`SKILL.md:130`）。
  - `mutate.py`：旗標、KILLED／SURVIVED／INVALID、exit 0/1/2；`MUTATE RECOVER CLEARED`／`RECOVER OK` 字串在 `mutate.py:344`／`:372`。
  - `dbq.py`：旗標、預設 50 列（`:43`）、exit 0/1/2/3（`:39`）、`deepweave-postgres`。
  - `dockerslot.py`：`--all/--pool/--slots/--status/--max-wait`，預設 300 秒、預設 3 槽，exit 75。
  - `synth.py`：旗標，exit 0/1/2/3（除 L4）。
  - `reviewcov.py`：`--index/--section/--waive/--ack-manual/--plan-tests/--expect/--list`（`4-plan.md:31-32`、`6-review.md:34`、`bug.md:24`、`lean.md:20`）。
  - `laneyield.py`：`--no-cost --strict`、`--review-dir 'TEXT=DIR'`、精簡路讀 PROGRESS `## 審查`。
  - `run_external.py`：codex／grok／agy 指令逐參數對（`gpt-6-sol`／`xhigh`、`grok-4.7`、`gemini-4-argon`／`high`、`--skip-git-repo-check`、`--verbatim`、`--output-format plain`），`AGY_MAX_BYTES = 90_000`、`AGY_PRINT_TIMEOUT = "20m"`，exit 0／CLI rc／3／2／124，`ext-<lane>.log` 與 `lane-<x>.status.json`（`external-review.md:8-10,27`、`6-review.md:21`、`lanes.md:29`）。
  - `mkpacket.py`：manifest 鍵、四階裁剪、exit 0/1/2、`--check`（`external-review.md:18,24`、`SKILL.md:127`）。
  - `mkbrief.py`：`--check`、`over-90kb`、exit 0/1/2。
  - `rulesfor.py`：`--check <任務目錄|rules.md|PLAN.md>`、套用／不相關開頭（`4-plan.md:24`、`SKILL.md:132`）。
  - `planview.py`：`--check <slug>`、`LONG_LINE=300`、`MAX_LONG=5`、缺檔跳過（第 3 階段沒有 PLAN 不會誤擋）。
  - `accept.py`：`--due/--run/--row/--slug/show`、輸出目錄 `tmp/accept/<today>/`、有副作用的 span 不跑。
  - `checktree.py`（`--strict`，不涵蓋 tmp／任務目錄／gitignored 檔）、`mkpatch.py`（7 個產出、`--exclude/--root`）、`mkworktree.py`、`merge_back.py`（`--allow-revert/--only-files`，exit 0/1/2）、`extract.py`、`numstat.py`。
  - `tx.py`（count／list、`--result-grep`、`--by`）、`turns.py`、`wf_reports.py`（MISSING、counts）、`agentcost.py`／`taskcost.py`（`--all-worktrees/--days/--slug`）、`claudemdcost.py --days`、`wallclock.py`（扣掉 away 時段）、`uilane_audit.py`（`retro.md:19-21`、`lanes.md:65`、`SKILL.md:135-141`）。
  - `run_kit.mjs`：`--kit/--round/--fingerprint/--steps/--plan/--cfg/--engine/--device/--action-timeout`，exit 0/1/2，`<round>/run/summary.json`、`storage-state.json`、`rerun:` 那一行（`walk-kit.md:9,11,63,66`）。
  - `walk_env.sh`：`<round> [stop|dropdb]`、佔用 port 時 exit 2、寫 `kit/walk.config.json`。
  - `walk_lib.mjs`：`launch`、walk 物件上的 15 個方法名、`DEFAULT_ACTION_TIMEOUT_MS = 10_000`、`breakpoints: [840, 1180]`。
  - `board.py`：`--hook session-start/--docsize/--deployed`、ledger、`RETRO_EVERY=5`（算的是到期列）、40KB／12KB／20KB／20% 門檻（除 H2、M8、L18）。
- **「第 N 條／§N／『章節』」指標**：約 45 個，全部對到存在、內容相符的位置。包括：SKILL「Checkpoint 協定」第 1／6／7 條、「分流」「依規模分級」「規模閥門」「任務包瘦身」「修片進場門檻」第 1／2 項、「backlog 的家」「說明檔大小」「全程規則」、§模型與 agent；`checkpoint.md` 第 6 條；`4-plan.md` 第 4／5／7／9／11 條與「PLAN 審查」第 5 條；`5-implement.md`「主親跑閘」「共用資源」「等走一趟時先備第 6 階段」「舊語義字樣全樹 grep」「主 session 的驗收」；`6-review.md` §1–§6；`7-converge.md` §1「不必驗證的條目先修」§2 §3「範圍」「流出分析」§4 §5；`lean.md` §3「升級」；`1-research.md` 第 3／5／6 步；`2-discuss.md` 第 3 步；`3-interview.md`「圖先文後」「沒有出處的常識判斷」；`walk-kit.md`「repro」「重走怎麼用」第 0 步、「誰能用哪部分」；`fixplan.md`「行為 bug 的回饋迴圈」、環境規則「真瀏覽器」；`converge-brief.md`「流出草稿」；`external-review.md`「PLAN 審查的材料包」；`lanes.md` §effort「收斂驗證」「實作與修復」；`retros/2026-11-10-first-principles.md` §2；`retros/2026-11-07-token-time.md` 提案 2；`ACCEPTANCE.md`「待驗」「檢討紀錄」；根 CLAUDE.md §graphify；`db/CLAUDE.md` §Migration（實際標題是「Migration / upgrade notes (goose)」，有 lossy Down 的內容）。
- **repo 路徑**（16 個）：`docs/graphify.md`、`.claude/hooks/py-edit-guard.py`、`known-red-integration.txt`、`known-red-{frontend,admin}.txt`、`scripts/audit-pgx-tx-patterns.sh`、`docs/api-contracts/openapi.yaml`、`.claude/rules/evidence-and-root-cause.md`、`.claude/skills/{diagram,meaningful-test-design}`、`BACKLOG.md`、`BACKLOG-archive.md` 等都存在。`known-red-golint.txt` 不存在，但 `project-deepweave.md:66` 已寫「名單目前不存在＝空」。hooks `explorer-graph-seed-guard`／`graphify-query-guard`／`go-fix.sh`／`py-edit-guard` 都在。just recipe `erd`／`fix-go-changed`／`fix-go-check`／`sqlc`／`lint-gateway` 都在。`~/.claude/settings.json` 有 `promptCacheTtl`、`CLAUDE_CODE_SUBAGENT_MODEL`，SessionStart matcher 是 `startup|clear|compact` 並跑 `board.py --hook session-start`。記憶檔 `reference_prod_langfuse_and_db_query_recipe.md` 存在。
- **外部 skill**：`mattpocock-skills:{prototype,grilling,diagnosing-bugs}`（plugin cache 1.3.1）、`pm-acceptance` 都存在。

### 問題 2 矛盾與舊說法（git log -12）
- **a58a2a8**（停站、checkpoint 不手動跑 board）：README.md／README.en.md 開頭與「為什麼」、evals 1／2、`phases/1-research.md:19`、`6-review.md:38-41`、`7-converge.md:15`、`assets/checkpoint.md:11`、`SKILL.md:8,41-54` 彼此一致。手動跑 board.py 只剩 commit 那一次（`SKILL.md:43`）、開工找進度（`:13`）、`--docsize`（`7-converge.md:46`、`retro.md:19`，這條不寫 HTML）。recv.py `--apply` 會自動跑 board（`recv.py:810`），不算手動，沒有矛盾。遺留：M1、M2、H2。
- **d6d01f7**（Backlog 只收兩組）：`converge-brief.md:24,31`、`synth-brief.md:27`、`7-converge.md:18-19,23,49`、`lean.md:28` 都已改成三分流或 archive。遺留：H1、M8。
- **036c725**：`SKILL.md:135-136` 與 wallclock／uilane_audit 的 help（away 時段）一致。
- **f97c2e1**：`SKILL.md:46`「例外兩個」已在 a58a2a8 更新；`5-implement.md:30` 一致。
- **bd7d000**：只動 walk_env.sh，文件沒有描述被改到的行為，不必跟著改。
- **5971c6f**：24 份 `tools:` 都沒有 LSP；`retro.md:11` 與 `board.py` 已改成看到期列數。遺留：M4、M7、L7。
- **9c8b069**、**8b1e4fc**、**d28945c**、**0fdcb45**、**cc8f234**、**7ecff0c**：synth／pr-test-analyzer 管線、ui-lane 背景探針（`walk-kit.md:41` 與 agent 本文 `:42` 一致）、gate lint／accept／tx／mkpacket、plan-appendix 搬移（全部提到「規則對照」的地方都指向 `plan-appendix/rules.md`）、planview 閘、gate／mutate／recv／dbq／mkbrief 配方：文件與工具一致。遺留只有 L12、L14。

### 問題 3 對 agent 定義的陳述（24 份 frontmatter 逐檔核過）
- **全部 `model: claude-opus-5-5`**：成立。
- **每份都有 `maxTurns`**（100–380）：成立。
- **不列 `LSP`**：成立。
- **xhigh 名單**：planner、database-reviewer、security-reviewer、pr-test-analyzer、a11y-architect、lane-reviewer、ui-lane-reviewer、converge-verifier 成立；blind-judge 不成立（M3）。
- **high 名單 10 顆**：tdd-guide、comment-analyzer、go-reviewer、silent-failure-hunter、typescript-reviewer、python-reviewer、doc-slice、doc-updater、code-explorer、quick-task，全部成立。
- **工具集**：
  - lane-reviewer 的 tools 是 `Read, Write, Bash, Grep, Glob`，成立。quick-task 多了 Edit（L1）。
  - comment-analyzer、planner、architect、code-explorer 都只有 `Read, Grep, Glob`：「沒有 Write」「沒有 Bash」（`1-research.md:9`、`4-plan.md:3`、`lanes.md:25`）成立。
  - code-architect 沒有 Write（`2-discuss.md:8`），成立。
  - a11y-architect 沒有 Bash（`lanes.md:13,31`），成立。
  - blind-judge 只有 `Read, Write, Glob` 而且 `omitClaudeMd: true`，成立。
  - converge-verifier 有 Write，成立。
  - ui-lane-reviewer：Playwright `@0.0.84`、`--codegen=none`，白名單正好 14 個 `mcp__playwright__browser_*`（`lanes.md:32`），成立。
- **`/Users/huntwu/.claude/agents`** 是 symlink，`readlink` → `/Users/huntwu/.claude/skills/seven-phase/agents`。兩邊是同一個目錄，檔案清單必然一致：24 份 .md 加一個 `.DS_Store`。

### 問題 4 孤兒與缺件
- 頂層腳本只有 `ctxio.py` 沒被任何流程文件提到（L7），其餘 32 支都至少被提到一次。
- 文件提到、但不存在的檔：L8、L9、L10、M6（章節）。
- evals 1／3／4／5 的期望與現行 SKILL、`bug.md` 一致；eval 2 有 M5、L15。

---

## 4. 沒查到或查不了

- **`maxTurns`「約 1.3–1.5 × 實測最大回合數」**（`SKILL.md:108`）：要用 `turns.py`／transcript 量，brief 只准跑 `--help`，沒驗。觀察：24 份裡 15 份恰好是 180，像統一預設值。這只是推測，沒證實。
- **`workflow-authoring` skill**（`6-review.md:20`、`lanes.md:40`「先載 `workflow-authoring`」）：`~/.claude/skills`、`~/.claude/plugins`（深度 9）都找不到，可能是 harness 內建，沒證實。
- **`answer-me-with-html` skill**：本機沒裝，但 `assets/checkpoint.md:15` 寫的是「已裝才做」，屬於條件式，不算斷指標。
- **外部 CLI 的旗標**（codex／grok／agy／graphify 的 `--context`、`--graph`、`--relation`）：不是 skill 的腳本，沒跑它們的 `--help`。只核對到 run_external.py 原始碼與文件一致。
- **`project-deepweave.md:66` audit 既存基線「(b)×2、(e)×4」**：要實跑 audit 腳本，沒跑。
- **Workflow 的 `agent(prompt, {agentType, label, schema})` 介面、`fork` 用主 session 模型、Workflow 內不能寫檔**：harness 行為，沒法從檔案驗證。
- **金額與時間數字**（$15–80、28 分、966 次等）：出處在 retros／history，沒重算。
- **`references/history.md`、`ACCEPTANCE.md`、`retros/`**：照 brief 沒整份讀，只 grep 了 blind-judge、ctxio、22 份等出處。

## 5. 對 brief 事實的更正
- agent 定義有 **24 份**，不是 23 份：`ls agents/*.md | wc -l` → 24，commit 555a0e3（10-19）的標題也寫「24 個 agent 定義搬進 repo」。
