# 腳本索引（一支一行）

用法一律看 `python3 ~/.claude/skills/seven-phase/scripts/<name>.py --help`；這裡只回答「有沒有現成的、它做什麼」。專案專用的跑法與 exit code 在 `references/project-deepweave.md`。

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
- `board.py`：跨任務進度看板（另含「流程」區：`ACCEPTANCE.md` 到期的驗收、到期滿 5 列時提示順便檢討、過大的說明檔；`--docsize` 印每份說明檔的大小與增減）——掃主 checkout 與所有掛分支的 worktree 的 PROGRESS.md，讀 frontmatter、git 狀態當場算，寫 `<主 checkout>/tmp/progress/board.html` 並印摘要；HTML 下半部是 `.claude/PRPs/BACKLOG.md` 的 Backlog 兩欄（重要／有感）。`-h`／`--help` 只印用法、不寫檔；認不得的旗標 exit 2，不會被當成完整跑。每次跑都把未完成任務的「下一步」快照進 `tmp/progress/ledger.json`，目錄刪了仍以「目錄已刪・待部署」顯示，owner 說部署完了 → 確認是哪幾個後 `--deployed <slug…|all>` 清掉；`~/.claude/settings.json` 的 SessionStart hook（startup／clear／compact）以 `--hook session-start` 跑它，把摘要注入 context。
- `planview.py`：一個任務的 SPEC／PLAN／REVIEW／PROGRESS md 原文嵌進一頁 HTML（`tmp/progress/<slug>.html`）：儀表板計數（定案、切片、測試、閘、審查地圖、交 owner、圖）、固定目錄、`##` 章節收合、表格固定表頭＋逐欄篩選、mermaid 渲染（marked／mermaid 釘版 CDN，離線顯示原文）；每次 checkpoint 跑、改了任務檔重跑。`plan-appendix/*.md` 一併嵌入、預設收合。`--check <slug>`：SPEC／PLAN 既無 ```mermaid 也無 `無圖：<理由>`，或 PLAN.md 超過 300 字的行 >5，就 exit 1（第 3／4 階段好讀閘）。
- `taskcost.py`／`laneyield.py`：每個任務、每個階段目錄的花費；每一路審查的獨家發現與花費（調路數、檢討流程前先跑；精簡路讀 PROGRESS.md 的 `## 審查` 表）。`laneyield.py` 依表頭欄名讀地圖與裁決表，讀不懂的資料（認不出的裁決詞、來源不是 lane、編號重複、找不到地圖）一律印成 `DATA error`、不當零；`--no-cost --strict` 是第 7 階段裁決表的格式閘；一份 REVIEW 放多包、各包審查目錄不同時加 `--review-dir '<輪標題字>=tmp/<slug>/review2'`（可重複）。`agentcost.py`／`taskcost.py`／`laneyield.py`／`claudemdcost.py` 共用 `_usage.py`：含 Workflow agent，同一訊息多筆 usage 取各欄最大值，依每筆時間戳過濾，cache write 分 5m／1h 計價；`agentcost.py`／`taskcost.py` 加 `--all-worktrees` 連 worktree 的 session 一起算（`laneyield.py` 本來就掃全部 worktree，`claudemdcost.py` 沒有這個旗標）。金額是 API 等價、不是帳單，沒有價格的模型列為 unpriced、不算 $0。
- `tx.py`：transcript 查詢（`count`／`list`）：依天數、主 session 或 agent 種類、工具名、input 正規式、result／hook 訊息正規式（`--result-grep`，含 PostToolUse attachment）篩 tool_use，`--by session|agent|tool|day|file` 計數；檢討與驗收要掃 transcript 時用它，不再現寫腳本。
- `turns.py`：每回合工具呼叫數、閒置空檔、冷快取重寫、重複讀取，主 session 與各種 subagent 各一列；`--agents <id1>,<id2>` 比兩次派工（派工規則的 A/B）。
- `ctxio.py`：什麼進了 context——tool_result 字元依工具、輸出最大的 Bash 指令、Agent 交回與派工訊息的大小；看主 session 或某一種 subagent（`--agent`）。檢討時用它找該改成一行判定、讀窄一點或改成寫檔的地方。
- `accept.py`：`ACCEPTANCE.md` 驗收 runner：列到期／事件／未到的列，把指令欄裡跑得動的 code span 真跑（`--run`，輸出落 `tmp/accept/<日期>/`）、對著判準印；`--slug <slug>` 代入 `<slug>`／`<任務>`／`<docs(prps) hash>` 佔位符；不可跑與「手動／問 owner」的列成「需人判」清單；有副作用的 span（`rm`、`--apply`、`--deployed`、push／commit）一律不跑。`show <slug> [<檔名>]` 從最新仍含該檔的 `docs(prps)` commit 取回已刪任務目錄的檔。判定仍由主 session 做，不改 ACCEPTANCE.md。
