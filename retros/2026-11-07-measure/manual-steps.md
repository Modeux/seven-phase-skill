# 七階段工作流：主 session 手工步驟盤點（code-explorer 2026-11-07；主 session 代為落檔）

縮寫：SKILL＝SKILL.md；P1…P7＝phases/N-*.md；lean＝phases/lean.md；bug＝phases/bug.md；ckpt＝assets/checkpoint.md；rb＝assets/review-brief.md；prb＝assets/plan-review-brief.md；cb＝assets/converge-brief.md；fp＝assets/fixplan.md；pd＝references/project-deepweave.md；ext＝references/external-review.md。

80 列：55 純手工、23 腳本＋手工殘餘、2 全腳本（planview.py、reviewcov lane 閘）。

| # | 步驟 | 出處 | 頻率 | 現況 | 手工內容 | 機械／判斷 |
|---|---|---|---|---|---|---|
| 1 | 開工問模型＋分流 | SKILL§開工1；bug | 每任務 | 手工 | 問 owner | 判斷 |
| 2 | 續接：讀 PROGRESS／看板 | SKILL§開工2–3；ckpt | 每任務 | board.py＋手工 | 跑指令＋讀 | 機械 |
| 3 | 派工前確認 agent 工具集 | P1#1；SKILL§模型與agent | 每派工 | 手工 | 讀 frontmatter | 機械 |
| 4 | 寫 PROGRESS 階段表列、下一步、五行紀錄 | SKILL§Checkpoint1；P5；P7§5；lean§5 | 每 checkpoint＋每片 | 手工（recv 只附收件骨架） | 編輯 md | 混合 |
| 5 | 改 frontmatter status／next／phase | SKILL；lean；bug×3；board docstring | 每 checkpoint | 手工 | 編輯 md | 混合（next 判斷） |
| 6 | 跑 board.py；驗收到期轉述 | SKILL§Checkpoint1 | 每 checkpoint | board.py＋手工 | 跑指令 | 機械 |
| 7 | planview.py 產任務頁 | SKILL；ckpt | 每 checkpoint | planview.py | 跑指令 | 機械 |
| 8 | 導讀頁（第 2／3／4 階段） | ckpt§導讀頁 | 每 checkpoint | 手工 | 組文字 | 混合 |
| 9 | 白話回報六點 | SKILL§Checkpoint2 | 每 checkpoint | 手工 | 組文字 | 判斷 |
| 10 | 判斷能否 /clear、看快取冷熱 | ckpt6；SKILL§Checkpoint3、6 | 每 checkpoint | 手工 | 讀狀態 | 混合 |
| 11 | 組 /compact 段 | SKILL§Checkpoint3；ckpt | 每 checkpoint | 手工 | 組文字 | 混合（只有下一步判斷） |
| 12 | 晚交件併記錄＋更新 /compact | SKILL§Checkpoint5 | 每收件（晚到） | 手工 | 編輯 md | 混合 |
| 13 | 改流程時 ACCEPTANCE 加列 | SKILL§全程 | 每次改流程 | 手工 | 編輯 md | 判斷 |
| 14 | graphify 起點貼進派工 | P1#2；lean；bug；P4#4、#10；rb；prb；P7§4 | 每派工 | 手工 | 跑指令＋組文字 | 機械 |
| 15 | prod 查詢先問 owner | SKILL；P1；P7；bug；pd；cb | 每次 | 手工 | 問 owner | 判斷 |
| 16 | manifest → mkbrief | SKILL§任務包瘦身；P2；P5；P6§3；P7§4 | 每片 | mkbrief.py | 編輯 manifest | 混合 |
| 17 | 沒 Write 的 agent 交回全文由主落檔 | SKILL；P2；P4 | 每收件 | 手工 | 編輯檔 | 機械 |
| 18 | >5 顆先探針；第一波只派一片 | SKILL；P5 | 每階段 | 手工 | 派工 | 判斷 |
| 19 | 兩個 commit、fetch、status deploy、--deployed | SKILL；P7；lean§5；bug | 每任務 | 手工＋board | 跑指令＋編輯 md | 混合 |
| 20 | 搬耐久結論進 CLAUDE.md／rules | SKILL；P7§5；lean§4 | 每任務 | 手工 | 編輯 md | 判斷 |
| 21 | board --docsize 回報增減 | SKILL；P7§5 | 每任務 | board＋手工 | 跑指令 | 混合 |
| 22 | 寫 BACKLOG（四組、固定格式） | SKILL；P7§3、§5；lean；bug | 每任務 | 手工 | 編輯 md | 混合 |
| 23 | 「圖已過期」提醒 | SKILL；P7§5 | 每任務 | 手工 | 看改動檔 | 機械 |
| 24 | 記憶只改進行中那條 | P7§5 | 每任務 | 手工 | 編輯 md | 混合 |
| 25 | fan-out 探索、context7 | P1#2 | 每任務 | 手工 | 派工 | 判斷 |
| 26 | 彙整探索、來源欄 | P1#2、#4 | 每任務 | 手工 | 讀報告 | 混合 |
| 27 | 寫 RESEARCH | P1#3、#5、#6 | 每任務 | 手工 | 編輯 md | 判斷 |
| 28 | 三取向：mkbrief 組三份、派三顆 | P2§簡報前 | 條件 | mkbrief＋手工 | 派工 | 混合 |
| 29 | 比較表＋簡報＋AskUserQuestion | P2 | 每任務 | 手工 | 問 owner | 判斷 |
| 30 | 丟棄式原型 | P2#3；P3 | 條件 | 手工 | 派工 | 判斷 |
| 31 | SPEC 方向決議 | P2#5 | 每任務 | 手工 | 編輯 md | 判斷 |
| 32 | 訪談逐題；SPEC 各表 | P3 | 每任務 | 手工 | 問 owner＋編輯 md | 判斷 |
| 33 | 原型差異表 | P3 | 條件 | 手工 | 派工＋問 owner | 判斷 |
| 34 | mermaid／無圖；儲存格 ≤60 字；planview --check | P3；P4；ckpt | 第 3／4 階段 | planview --check＋手工 | 編輯 md | 混合 |
| 35 | 起草 PLAN | P4 | 每任務 | 手工 | 編輯 md | 判斷 |
| 36 | 規模分級 numstat＋判斷 | SKILL；P4#2；P6§1；lean§3 | 每審查輪 | numstat＋手工 | 跑指令 | 混合 |
| 37 | matrix.md 符號／行號／呼叫者 | P4#4 | 每片 | 手工 | 跑指令＋編輯 md | 混合 |
| 38 | 測試表、維度表；grep 既有 pin | P4#5 | 每片 | 手工 | 編輯 md | 判斷 |
| 39 | PLAN 寫閘與既存紅基線 | P4#6 | 每任務 | 手工 | 編輯 md | 混合（基線已版控） |
| 40 | god-node／dependents 查核 | P4#10 | 每任務 | 手工 | graphify | 混合 |
| 41 | 規則對照 rulesfor 骨架→填→--check | P4#11 | 每任務 | rulesfor＋手工 | 讀規則 | 混合 |
| 42 | 建審查快照、複製 review-input、指定 writer | P4 PLAN審查2；P6§2、§4；bug | 每審查輪 | mkpatch／mkworktree＋手工 | 跑指令 | 機械 |
| 43 | 填共用任務書 | P4；P6§3；rb；prb | 每審查輪 | 手工 | 組文字 | 混合 |
| 44 | 寫 lane 派工單＋Workflow script | P6§4；P4 | 每審查輪 | 手工 | 組文字 | 混合 |
| 45 | 組外審材料包（裁切、90KB、依片拆、摘錄＝矩陣列數） | ext；P6§4；P4 | 每審查輪 | 手工（extract.py；可交 quick-task） | 組文字＋跑指令 | 機械 |
| 46 | cwd 確認、run_external 三路、看 exit | ext；P6§4；P4 | 每審查輪 | run_external＋手工 | 跑指令 | 混合 |
| 47 | agy 手動：列路徑請 owner 跑、落檔＋手寫 status.json | ext§材料包 | 條件 | 手工 | 編輯檔 | 機械 |
| 48 | wf_reports 抽報告；grep truncated | P6§4、§5；ext | 每審查輪 | wf_reports＋手工 | 跑指令 | 機械 |
| 49 | checktree 驗主樹；清單外逐條看 | P6§5；P7§5 | 每審查輪 | checktree＋手工 | 跑指令 | 混合 |
| 50 | reviewcov lane 閘、--index | P4；P6§5 | 每審查輪 | reviewcov | 跑指令 | 機械 |
| 51 | 派 doc-slice 合成 REVIEW | P6§5；lean§3；bug | 每審查輪 | 手工 | 派工 | 判斷 |
| 52 | reviewcov exit 0；ack／waive；抽 3 條 MEDIUM 核對 | P6§5；lean§3；bug | 每審查輪 | reviewcov＋手工 | 讀報告 | 混合 |
| 53 | PLAN 審查地圖處置→reviewcov --plan-tests | P4 PLAN審查5 | 每審查輪 | 手工＋reviewcov | 編輯 md | 判斷 |
| 54 | 審查類 checkpoint 加報 | P4；P6 結尾 | 每審查輪 | 手工 | 組文字 | 混合（計數可算） |
| 55 | 每片私有樹；前端樹 pnpm install | P5；P7 | 每片 | mkpatch／mkworktree＋手工 | 跑指令 | 機械 |
| 56 | 同 wave 併發派工；dockerslot | P5；P7§4；fp | 每階段 | 手工 | 派工 | 混合 |
| 57 | 主寫紅、收件比 sha | P5；P7§4；fp | 條件 | 手工 | 寫測試＋跑指令 | 混合 |
| 58 | 審紅：red-run.out 原因；測試檔 sha／mtime 核對 | P5；P7§4 | 每收件 | 手工（recv 列成待判） | 讀輸出＋跑指令 | 混合 |
| 59 | 抽 2–3 支突變（manifest→mutate） | P5；P7§4 | 每收件 | mutate＋手工 | 編輯 manifest | 混合 |
| 60 | 偏離裁決 | P5 | 每收件 | 手工 | 讀報告 | 判斷 |
| 61 | recv 預演→--apply；填五欄 | P5；P7§4；pd | 每收件 | recv／merge_back＋手工 | 跑指令＋編輯 md | 混合 |
| 62 | 改語義的片：舊字樣全樹 grep | P5；P7§4 | 條件 | 手工 | 跑指令 | 混合 |
| 63 | 修片門檻：會紅的指令＋觸發表；收件重跑、PROGRESS 一行 | SKILL；P5；P7§4；lean；bug；fp | 每修片 | 手工 | 跑指令＋編輯 md | 混合 |
| 64 | 修片「要量的點」逐條跑、存輸出 | SKILL；fp§交付 | 每修片 | 手工 | 跑指令 | 機械 |
| 65 | 迴圈停損：證據包交三家外審 | SKILL；P7§4 | 觸發時 | run_external＋手工 | 組文字 | 混合 |
| 66 | 全閘（gate --all、fe、lint、audit、redocly、fix-go）；清孤兒容器 | P4#6；P5；P7§5；lean§4；pd | 第 5／7 階段末 | gate.py＋手工（lint／audit／契約） | 跑指令＋讀輸出 | 機械 |
| 67 | 走一趟：快照、run_kit、派 ui-lane；checktree 判重驗 | P5；P7§5 | 第 5／7 階段 | mkworktree／run_kit／checktree＋手工 | 跑指令＋派工 | 混合 |
| 68 | 挑驗證條目派 converge-verifier | P7§1；lean§4 | 每審查輪 | 手工 | 組文字 | 混合 |
| 69 | owner 拍板 ≤4 題 | P7§2；lean§4 | 每審查輪 | 手工 | 問 owner | 判斷 |
| 70 | 裁決表→laneyield --strict | P7§3；lean§3 | 每審查輪 | 手工＋laneyield | 編輯 md | 混合 |
| 71 | 流出分析 | P7§3；lean§4；cb | 每審查輪 | 手工 | 讀報告 | 判斷 |
| 72 | 修復切片＋fixplan＋mkbrief | P7§4；fp | 每修片 | mkbrief＋手工 | 組文字 | 混合 |
| 73 | 證據歸檔、git worktree remove 所有樹 | P7§5；P4；pd | 每審查輪 | 手工 | 跑指令 | 機械 |
| 74 | 精簡路建 PROGRESS | lean§1 | 每任務 | 手工 | 編輯 md | 判斷 |
| 75 | 止血判斷與確認 | bug | 每任務（bug） | 手工 | 問 owner | 判斷 |
| 76 | 止血開樹 | bug 止血1 | 止血 | 手工 | 跑指令 | 機械 |
| 77 | bug 診斷用 PROGRESS | bug | bug | 手工 | 編輯 md | 機械 |
| 78 | 「已修」判定 | bug§1 | 條件 | 手工 | 跑指令 | 機械 |
| 79 | 假設實驗、## 診斷、分流 | bug§3、§4 | bug | 手工 | 跑指令＋問 owner | 混合 |
| 80 | git show 取回已刪任務目錄 | retro.md | 每檢討 | 手工 | 跑指令 | 機械 |

## 2. 手工但機械（頻率高→低，agent 原判）
#17 hand-back 落檔；#14 圖起點貼派工；#3 工具集確認；#58 測試檔 sha 核對；#57 主寫紅 sha；#62 舊語義 grep；#4 片五行（閘結果來自 out 檔）；#37 matrix 符號行號；#56 wave 併發；#64 要量的點；#63 重跑會紅指令＋觸發表；#5 frontmatter；#11 /compact；#10 能否 /clear；#8 導讀頁；#12 晚交件；#45 外審材料包；#47 agy 手動落檔；#73 歸檔＋清樹；#43 共用任務書（機械欄）；#44 Workflow script；#54 審查 checkpoint 計數；#68 HIGH 篩；#23 圖過期；#76；#77；#78；#80；#39；#40；#22 BACKLOG 格式；#26；#79；#24。

## 3. 文件與腳本不一致
- agy 上限：P6§4 寫 ≤170KB；ext 與 run_external.py（`AGY_MAX_BYTES=90_000`）是 90KB。
- 範圍對帳：P5 仍列手工，recv.py 已做 scope／drift／merge_back 候選。
- PROGRESS 片紀錄三種形狀：P5 五行、SKILL 修片一行、recv 「### 收件」骨架＋五待判欄；P5 沒提 recv 骨架。
- 既存紅比對：P7§5／P4#6 讀起來像手比；pd 寫 gate.py 自動比。剩手比的只有 lint、audit、ruff、契約。
- 看板重算：SKILL 叫手跑；recv --apply 與 SessionStart hook 都會跑（重複非錯誤）。
- 驗證員任務包：P7§1 沒點名 mkbrief；reviewcov --index 已產逐條清單。
- PLAN 儲存格 ≤60 字：planview --check 只驗 >300 字行；60 字無腳本。
- cwd 確認（ext、P6§4）：run_external 有 --root；手工確認是否仍必要「？」。
- review-input 複製／mkpatch 排除：mkworktree／mkpatch 是否已做「？」。
- truncated 檢查：reviewcov 是否涵蓋「？」。
- PLAN 審查程式碼摘錄：規則寫明機械取法、核對標頭數＝矩陣列數，卻沒腳本、只說可交 quick-task。
