# 流程改動驗收帳

改流程（本 skill、repo 的 hook／agent 檔／CLAUDE.md 結構、settings）就在「待驗」加一列；`scripts/board.py` 在 SessionStart 列出到期的。驗收結果移到「結案」，判「沒用」的改動要撤回或改掉。
- **到期**：一定要有日期（`YYYY-MM-DD`）；等事件的寫「事件，最晚 日期」，到最晚那天還沒發生也算到期。
- **指令**：只准指向本 skill `scripts/`、repo 已 commit 的檔，或寫清楚的手動步驟。**不准指向 `tmp/`**——10-17 檢討時，兩筆驗收因為腳本放在 `tmp/` 被清掉而判不了。
- **判準**：寫成量得到的數字或是非題，附基線。
- 檢討的做法在 `phases/retro.md`；每次檢討在「檢討紀錄」加一列（看板用最後一列的日期算「之後又完成幾個任務」）。

## 待驗
| 到期 | 改動 | 指令 | 判準 | 出處 |
|---|---|---|---|---|
| 2026-10-19 | graphify 掛點（第 1／4 階段配方、圖→任務書、`graphify-query-guard`、`explorer-graph-seed-guard`） | 手動：對 10-12 之後 mtime 的 `~/.claude/projects/-Users-huntwu-Desktop-deepweave-deepweave*/**/*.jsonl`（含 `subagents/`），只數 Bash tool_use 指令裡的 `graphify query\|affected\|path`（別裸 grep，skill 文字會混進去）；另 grep deny 訊息前綴 `graphify-query-guard:`、`explorer-graph-seed-guard:` | ① 第 1 階段 session 過半有帶 `--context` 的 query；② 第 4 階段有刪除／改簽名的包，PLAN §0 有 dependents 清單；③ explorer 派工「不適用——理由」占比不高（太高＝繞閘）；④ Graph Seeds 裡 grep-only／起點無關的比例。基線（10-12 前 30 天）：52 個 seven-phase session 只 10 個跑過 graphify、157 次 query 只 2 次帶 `--context`、`path` 0 次；42 次派 code-explorer 只 5 次帶起點。沒達標→改成 `scripts/` 檢查（例如 RESEARCH.md 沒有「來源：graph」欄就擋 checkpoint），不再加文字 | skill `ebfa078`、`51a5d35`、`14b326d`、`6e7d673`；repo `6df8ef02` |
| 事件：下一個有 UI 的完整包第 7 階段；最晚 2026-10-31 | 排程四改（dockerslot 指令層排隊、走一趟與全閘並行、走一趟套件、免驗條目先修） | `python3 scripts/wallclock.py --days 14 --detail ui-lane-reviewer converge-verifier`；第 7 階段 PROGRESS 有沒有按 Docker 分波；整合測試輸出 grep `port not found` | ① 重走中位數降到約 10–15 分（基線 30）；② 第 7 階段不再按 Docker 分波；③ dockerslot 3 槽下沒有 testcontainers 假紅。10-17 期中：ui-lane-reviewer 中位 25.3（14 天）→21.2 分（10-16 後 13 次），但 wallclock 分不出首走與重走——驗收前先讓它依派工標籤分開（標籤含「重走」「修後」「Final」） | skill `87585f1` |
| 事件：接下來 3 個完整路的包第 7 階段；最晚 2026-11-14 | 流出分析（第 7 階段 §3；10-16）＋驗證員起草 | 讀各包 REVIEW.md `## 流出分析` 的落點欄與起草欄 | ① 不是全填「接受」或沒填（全是＝撤回流出分析）；② 主 session 沒把驗證員草稿改成「接受」、驗證員草稿本身也不是全「接受」（不過＝起草升級成 codex 外審）。10-17 期中 2/3：journey-process-depth 機械閘 2／接受 8、derivative-ui-ux 機械閘 8／接受 1，兩包都沒有「驗證員→主改」 | history 10-16 兩列 |
| 事件：接下來 3 個有 UI 的包；最晚 2026-11-14 | 原型觸發擴成三條（第 2／3 階段；10-16） | 讀各包第 6 階段 UI lane 地圖與 owner 驗收紀錄 | 「方向／手感不對」類條目比之前的 UI 包少；沒減少就撤回觸發擴充 | history 10-16 原型列 |
| 事件：接下來 2 個大包第 7 階段；最晚 2026-11-30 | PLAN 審查（大包第 4 階段四路；10-17） | 流出分析表「本該攔的閘」欄；PLAN 審查地圖每路的「改 PLAN」條數與獨家數 | ① 流出分析裡「本該攔＝PLAN」的條數下降（基線：journey-process-depth 10 列裡 7 列、derivative-ui-ux 9 列裡 8 列）；② 外審三路在兩包內至少有一條獨家被採納，否則回來跟 owner 檢討路數 | history 10-17 PLAN 審查列 |
| 事件：接下來 3 個完整路的包第 7 階段；最晚 2026-11-30 | 規則對照（第 4 階段第 11 條、`scripts/rulesfor.py`；10-18） | 各包 `python3 scripts/rulesfor.py --check .claude/PRPs/plans/<slug>/PLAN.md`；讀 REVIEW.md `## 流出分析`，數「規則檔早有同形規則」的列 | ① 三包 PLAN 都有 `## 規則對照` 且 `--check` 過；② 流出分析裡「規則早就有、PLAN 沒套用」的列，三包合計 ≤ 2（基線：10-17 檢討 4 包 19 列約 6 列）；③ 每包表格 ≤ 約 20 列，標「不相關」的被 PLAN 審查或第 6 階段推翻的 ≤ 1 條。① 或 ② 不過→改成第 6 階段開工前由驗證員對照、撤掉 PLAN 這條；③ 太長→收窄命中範圍（例如只列規則檔、不列子目錄 CLAUDE.md） | history 10-18 規則對照列 |
| 事件：下一個多包大功能的 ROADMAP／PLAN；最晚 2026-11-30 | 拆包每一刀寫明換到什麼＋驗證帳欄（10-17） | 讀那份 ROADMAP／PLAN 的包表 | 每刀都有理由、驗證帳有填；若每刀都填「③」或填了也沒合過任何一包，回頭檢討判準是否太鬆 | history 10-17 拆包列 |
| 2026-11-07 | 驗收帳＋看板「驗收到期」（本檔） | 讀本檔「結案」的日期對照「到期」 | 期間到期的每一列，都在到期後 3 天內結案或延期（延期附理由）。不過→驗收帳沒用，改回別的做法 | 10-17 檢討提案 1 |
| 2026-11-07 | 說明檔大小閘（看板列出超過 40KB 或比上次檢討長 20% 以上的 CLAUDE.md／`.claude/rules/`） | `python3 scripts/board.py --docsize` | ① 指示檔總量不再每週成長超過 5%（10-06 730KB → 10-17 1,119KB，11 天 +53%）；② 超過 40KB 的檔數不增加（10-17 瘦身後的數字為基線）；③ chat-workspace CLAUDE.md 維持 ≤ 40KB | 10-17 檢討提案 3 |
| 第二次檢討時；最晚 2026-11-14 | 跨任務檢討（`phases/retro.md`） | 讀本檔「結案」與「檢討紀錄」 | 兩次檢討採用的提案，至少一半驗收判「有用」；否則把檢討頻率改成每 10 個任務一次，或停掉 | 10-17 owner 定 |
| 事件：接下來 2 次有套件的重走（第 5 階段修後或第 7 階段收尾）；最晚 2026-11-07 | 重走用 `scripts/run_kit.mjs` 一次跑完套件＋截圖並行讀（`assets/walk-kit.md` 第 0 步；10-18） | `python3 scripts/wallclock.py --days 7 --detail ui-lane-reviewer` 找那兩次重走；讀其報告與 `<輪次>/run/summary.json` | ① 重走牆鐘 ≤15 分、模型 request ≤50（基線：中位 24.4 分／102 次；重走舊量 30 分）；② 報告有「這輪新驗」≥3 個情境、修過的條目逐條親手驗；③ 之後的第 6／7 階段或 owner 沒發現「runner 標 PASS、截圖其實壞了、agent 也沒報」的條目。① 不過→查時間花在哪再調；② 或 ③ 不過→撤回第 0 步，回到 MCP 逐支跑 | history 10-18 走一趟 runner 列 |

## 結案
| 日期 | 改動 | 判定 | 證據 | 處置 |
|---|---|---|---|---|
| 2026-10-17 | py-edit-guard（擋 Python 改檔、導回 Edit；10-07） | 部分有效 | 10-08 後 deepweave 的 transcript：Python 寫檔每千次 Bash 1.1 → 0.5（減半）；被擋 133 份、約 278 次，超過 2 次的 7 份，放行理由正當（突變工具、外審材料包）。但 `sed -i` 每千次 Bash 6.5 → 11.3、`perl -pi` 0.5 → 1.0（約翻倍），判準「沒有暴增」不過；同期 auto mode 的系統提示本身就鼓勵用 sed 改檔，歸因不了。原判準「Python 改檔占比 <15%」的腳本 `tmp/py-usage-audit/` 已被清掉 | hook 保留；下次檢討量 `sed -i`／`perl -pi` 改檔的失敗率，再決定要不要擋 |
| 2026-10-17 | 工作流／prompt cache 體檢（10-07：1h TTL、agents 清理、Workflow 扇出） | 未達標 | 10-16 補量：context 中位整體 258k、主對話 314k，目標 <200k。其餘判準（閒置失效 $ −70%、冷 compact 近 0）的腳本 `tmp/workflow-opt/` 已被清掉 | 列為下次檢討題目：主 session 占 14 天花費 39%，先補「主 session context 中位」的量測腳本（放 `scripts/`） |
| 2026-10-17 | CLAUDE.md 瘦身＋拆規則檔（10-05、10-06） | 沒守住 | 指示檔總量 10-06 730KB → 10-17 1,119KB；chat-workspace 34.6 → 81.7KB；worker 母檔 14.3 → 21.9KB；按需載入 14 天 $668 | 10-17 檢討提案 3：看板大小閘＋chat-workspace 拆規則檔 |

## 檢討紀錄
| 日期 | 範圍 | 報告 | 採用 |
|---|---|---|---|
| 2026-10-17 | 10-03～10-17（14 天，約 20 個任務） | `retros/2026-10-17.md` | 1 驗收帳進看板、3 說明檔大小閘＋chat-workspace 瘦身、檢討寫成流程；不採 2（go-reviewer 小包停派） |
