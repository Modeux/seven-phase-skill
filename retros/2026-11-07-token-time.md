# token／時間檢討（2026-11-07，第三次）

範圍：10-24～11-07（14 天，265 個主 session、860 顆 agent，API 等價 $7,608）。問題：工作流的作業裡還有哪些 token 或時間的浪費、重工可以消掉。品質 > 省錢 > 速度。11-06、11-07 上午兩次檢討看的是「一次性腳本」與「手工步驟」，這次看 token 與時間本身。

## 錢花在哪（canonical：`agentcost.py`／`taskcost.py`）
| 工序 | $ | 佔比 | 形狀 |
|---|---:|---:|---|
| 主 session | 2,564 | 34% | 21k 次 API 往返、context 中位 233k；每回合 1.42 個工具呼叫、69% 只有一個；Bash 17k 次＝主 session 工具呼叫的 57% |
| tdd-guide（實作片） | 1,873 | 25% | 252 顆、中位 71 回合、context 中位 226k；77% 回合只有一個呼叫 |
| 審查各路 | ≈1,300 | 17% | lane-reviewer 583、pr-test 218、comment 134、security 90、db 72、silent 59、go 55、ts 55、a11y 39 |
| 走一趟（ui-lane-reviewer） | 629 | 8% | 58 顆、中位 35 分、p90 103 分 |
| 收斂驗證 | 390 | 5% | |
| 文件片 | 312 | 4% | |
| 探索與規劃 | ≈330 | 4% | code-explorer 190、planner 95、code-architect 35 |
| 盲評、探針、雜務 | ≈200 | 3% | |

主 session 的模型：Opus 84%（$2,165，每次 $0.109）、Fable 16%（$401，每次 $0.284）——模型不是槓桿。

## 什麼東西進了 context（`ctxio.py`）
- 主 session 14 天吃進 49.5M 字元的工具輸出（≈12M token）：**Bash 輸出 61%**（grep 3,350 次、sed 1,611、cat 786——用 shell 讀檔 5.7k 次，Read 另 3.4k 次）、Read 31%、agent 交件只 1%（中位 1,105 字元，守住 ≤12 行）。
- tdd-guide 吃進 63M 字元：Bash 63%（sed 2,488、grep 3,715、cat 719＝20M 字元；測試輸出約 6.5M）、Read 33%。
- 成本模型：每一回合付一次整段 context（cache read $0.2/M）＋當回合新增的寫入；**回合數 × context 就是錢**。主 session 一回合 ≈ $0.11，tdd-guide ≈ $0.09。

## 量到的浪費（每項都小）
| 項目 | 14 天 | 占比 | 處置 |
|---|---:|---:|---|
| 快取冷掉重寫（主 session：owner 離開 ≥1h 回來 29 次 $46、AskUserQuestion 開著 7–39 小時 3 次 $16、compact 等其他 $57；agent：5 分鐘快取，pr-test-analyzer 突變跑 >5 分 $51，其餘 ≈$100） | ≈$270 | 3.5% | 「compact 要趁暖」真正賭注只有 $62／兩週；agent 5 分鐘 TTL 是 harness 給的，`promptCacheTtl: 1h` 只管主 session——管線化驗收時要預期這筆，不是工具沒用 |
| 同一段 context 內重讀同一檔 | 主 4 次、tdd-guide 7 次 | ~0 | 無 |
| 工具與 hook 延遲 | Read 0.07 秒、hook 0.02–1 秒 | ~0 | 無。`wallclock.py` 的「Edit 1,510 分」是模型生成 old／new 字串的時間，不是工具時間，讀那張表時要知道 |
| 14 小時的 agent 停擺（3cd58b5f 兩顆） | 11-02 斷網 `ENOTFOUND`，owner 回來才續 | 外部 | 無 |
| 讀取一回合一個（可併的上限：code-explorer 41% 回合、comment-analyzer 67%、planner 63%、a11y 56%；tdd-guide 77% 回合只一個呼叫） | 估 $300–600 | 4–8% | **提案 1**。注意 ui-lane-reviewer 11-03 起定義裡已寫「獨立讀取並行」，仍 75% 單呼叫——文字可能沒用，先探針 |
| 重工：收斂目錄占任務花費（explore-revamp 23%、portrait 17%、voice 15%、journey-revamp 5%）；走一趟重走 14／58 顆 | ≈$575（四大包） | 13% | 已在驗收中：11-04 維度表（修復占比 ≤22%）、10-19 停損；不另提 |
| 審查路獨家確認（11-04 後 6 包）：test 37、a11y 11、silent 7、comment 7、ui 6、spec 5、security 5、ts 4；**impact 0、go 0、grok 0、agy 0（portrait 3 條 `agy` 來源 DATA error 未計）**、timing 1、codex 1、db 1 | — | — | 只報數字：impact 上一窗（10-19～11-04）有 7 條獨家，會浮動；go／grok owner 10-17／11-04 已選保留 |
| owner 回 checkpoint 中位 1.9 分 | — | — | 不是瓶頸（11-04 同） |

## 已在路上、這次不重做
11-06／11-07 的工具（`gate.py` 一行判定→砍 tdd-guide 的測試輸出；`recv.py`／`synth.py`／`mkpacket.py`→砍主 session 回合）、探針與測試 lane 管線化（測試 lane p90 84 分、走一趟 p90 103 分）、維度表、PLAN 好讀化——驗收列都在 `ACCEPTANCE.md`，到期 11-30～12-15。

## 提案
| # | 提案 | 層級 | 刪除 | 品質 | 驗收 |
|---|---|---|---|---|---|
| 1 | **讀取併回合**：先一顆探針 A/B（code-explorer＋tdd-guide 各一片，同一任務包，定義加／不加「獨立的 Read／Grep／sed 同一則訊息並行發」），比回合數、$、完整性（檔案清單／發現數不得少）、下游（同一份交件餵同一顆 reviewer）；過了才寫進 tdd-guide、code-explorer、comment-analyzer、planner、lane-reviewer、各 reviewer 的定義。量測腳本收進 `scripts/turns.py`（本次 `turns.py`）與 `scripts/ctxio.py` | 根因（回合數×context） | agent 定義 `tools:` 裡的 LSP（11-04 證實切片樹回假空結果；14 天只 54 次呼叫，定義卻每則訊息都背） | 讀的內容不變，只是同一回合送；A/B 完整性不過就不推 | 事件：探針後 3 個完整包；`turns.py --days 30`：code-explorer／comment-analyzer／planner 每回合呼叫 ≥1.9（基線 1.57／1.52／1.40）、tdd-guide ≥1.7（1.44）；第 6 階段流出分析沒有「explorer 漏檔」「片漏讀規則」；A/B 不過＝不推、結案記「文字沒用」 |
| 2 | **檢討改成驗收到期才跑**：21 天跑了 5 次檢討（10-17、10-19、11-04、11-06、11-07）＋本次，主 session 結構數字每次一樣（context 中位 258k→233k→235k→233k）；10-17 採用的 3 條驗收判 0 有用，10-19～11-07 採用的 11 列全部未到期。改成「`accept.py --due` 有 ≥5 列到期時跑一次、與結清驗收同一個 session」 | 根因（規則加得比驗證快） | `phases/retro.md`「滿 5 個任務提示」＋看板那行提示 | 不碰審查 | 11-08～12-15 檢討 ≤1 次；12-15 前 `ACCEPTANCE.md` 結案列 ≥10 筆且用 `accept.py` 輸出 |

沒提的：agent 5 分鐘快取（$100／兩週，1%）、主 session 用 shell 讀檔（換 Read 不省回合）、impact／go／grok 路（數字浮動或 owner 已定）。

## 拍板（owner 11-07）：兩條全做

## 探針（提案 1，同一任務包 A/B，B 多一段回合規則）
| agent | 任務 | A 回合／$／秒 | B 回合／$／秒 | 每回合呼叫 A→B | 完整性 |
|---|---|---|---|---|---|
| code-explorer | 追 18+ 受閘模糊圖從 API 到四個掛載面 | 33／$2.04／200 | 18／$1.68／167 | 1.94→2.89 | 兩邊都到 `gatePost`→`withholds`→解鎖集合→`ChatUnlockVersion` 重讀、同一條開放問題；A 多讀 3 個周邊檔（按鈕列、卡片、`image_gate.go`），B 多讀 SQL 原檔（`probe-explorer-{A,B}.md`） |
| tdd-guide | gated-card-corner-bleed 重做（樹在修前的 commit） | 13／$0.70／107 | 11／$0.65／103 | 1.69→2.00 | 紅→綠→兩個突變都紅、css.md 一條，兩邊相同 |

結論：回合 −45%／−15%、$ −18%／−7%、牆鐘 −17%／−4%，找到的東西一樣 → 寫進 22 份 agent 定義（`agents/*.md`「Turn rule」一行，ui-lane-reviewer 已有自己的那句）；`tools:` 全部拿掉 LSP。樣本各一對，驗收列用 `turns.py --days 30` 看三包。
探針樹：`tmp/probe-batch/{A,B}`（detached worktree，owner 11-07 未准移除，留著；要清用 `git worktree remove --force tmp/probe-batch/A`、`…/B`）。

## 落檔
`ACCEPTANCE.md` 待驗兩列＋檢討紀錄一列；`references/history.md` 11-07 一列；`phases/retro.md` 何時跑、`scripts/board.py` 提示改到期列數、SKILL.md 工具集那句；`scripts/turns.py`、`scripts/ctxio.py`。

重跑：`retros/2026-11-07-token-time-measure/{turns,ctxio,toollat,editlat}.py --days 14`（從 repo 根跑；輸出存同目錄 `*.out`）。
