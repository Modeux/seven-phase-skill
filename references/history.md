# 規則的由來（推翻任何一條之前先讀這裡）

每條規則都是一次踩坑或 owner 的明確指示換來的。看起來多餘的規則，通常是在防一個你沒看到的失敗。

改這個 skill 的任何檔之後：在這張表加一行（日期、規則、由來＋owner 原話），然後在 skill 目錄 `git -C ~/.claude/skills/seven-phase commit -am '<一句話>'`——這個目錄不在任何專案 repo 裡，沒 commit 就無法還原。

| 日期 | 規則 | 由來 |
|---|---|---|
| 09-04→09-21 | 六階段改七階段：Research 與 Interview 之間插入「討論」 | 「一路衝到底、末端才發現前提錯」——把方向錯誤的發現點提前到碰細節之前 |
| 09-09 | 精簡路只能縮 Interview／SPEC＋PLAN 合寫 | hotfix 七路審查裡的字表錯字、URL 切詞、live harness Skip 全是完整路 lane 抓到的；精簡路四路只抓到一部分 |
| 09-17 | 每階段 checkpoint、停等「繼續」、附 /compact | owner 要控制 token 用量與方向；自己決定何時 compact |
| 09-21 | 「註解不可信」升為全程規則；comment-analyzer 必開、誤導註解視同邏輯 bug | 誤導性註解同時帶偏人與模型，比缺註解更貴 |
| 09-21 | 閘分兩層、同層小片合併、記錄瘦身 | batch2 實測：每片 tdd-guide 只佔 14–35 分鐘，六到七成時間花在主 session 寫紅、跑全閘、寫記錄 |
| 09-23 | subagent 一律 opus-5.5，xhigh／high 兩級，靠 frontmatter | 實測 prompt 寫 effort 無效；09-23 前 frontmatter 是 sonnet／haiku，「一律 opus xhigh」其實沒生效 |
| 09-23 | 紅的歸屬放寬：一般包 tdd-guide 寫紅＋綠，主審紅＋抽突變；拍板語義／錢／不變式仍主寫 | opus-5.5 的 tdd-guide 抓到主寫測試的錯；主寫紅是主 session context 與牆鐘的最大宗。反例：agent 不知道某個標記的語義重量，所以語義包仍主寫 |
| 09-23 | Implement 併發：私有樹、單一 writer 矩陣、先探針 | owner「加速實作」；風險全在共用檔與共用 Docker |
| 09-27 | 主 session 預設 opus-5.5，開工前問要不要切 fable | owner「開發統一用 opus 5.5 就好了」＋「之後都可以先問我是否要切換」 |
| 09-28 | **依規模分級**：小包審查 6–8 路、收斂驗證只驗爭議／HIGH／拍板項、小改主 session 直接做 | memory-refusal-stop-retry：正式碼約 1000 行，13 路審查＋5 路驗證＋4 片修復花數百萬 token；13 條 MEDIUM 有 7 條是註解／文件。真正改變結果的只有少數幾條（低樣本盲區、偵測面消失、驗證擋下一次錯誤修法）。owner：「我覺得可以按照你的建議」。**推翻 09-09 的無條件版**：大包與高風險包仍完整 |
| 09-28 | commit 一律 `git commit --only -- <paths>` | 主樹常有別任務已暫存的刪除／改動；裸 `git commit` 會把它們一起帶進去 |
| 09-28 | 工作流從記憶搬成 personal skill；記憶縮成指標 | 記憶檔長成 30KB 的疊加修正，每次讀都要自己推哪條蓋掉哪條；skill 可分階段載入、帶範本與腳本、compact 後可重新載入。評測（3 題 × 有／無 skill）：規則面兩者一樣好（18/18），差別在腳本——沒 skill 的那組每次手寫 patch／worktree／sha 工具 |
| 09-28 | 查 prod（DB、Langfuse）前先問 owner | 評測中有一組在研究階段自行排了 prod 字數分佈查詢；owner：「prod 查詢要先問」 |
| 09-28 | description 維持原樣 | 觸發評測 20 句（10 該觸發／10 近似但不該，含「繼續剛剛的 docker 除錯」「只 commit justfile」「查 prod 佇列」）× 3 次：60/60 全對，優化迴圈第 1 輪即結束，不改寫。評測集在 `seven-phase-workspace/trigger/eval_set.json`，改 description 前先用它重跑 |
| 09-28 | 參考檔用得到時才讀 | 評測中開新任務那組在第 1 階段多讀了 lanes／history，只多花 token |
| 10-02 | 固定用途改派專用 agent（`lane-reviewer`／`converge-verifier` xhigh、`doc-slice` high、graphify 抽取改 quick-task）；`tdd-impl`（high）試行；任務包瘦身（必讀逐字摘錄 ≤ 約 90KB、全文列「需要時再讀」）；新增 `scripts/agentcost.py` | 21 天 935 顆 subagent 實測（API 價換算）：subagent 約主 session 3.5 倍；73% cache 讀、25% cache 寫、2% 輸出——花在累積上下文，不在思考；general-purpose 36%、tdd-guide 30%、專門 reviewer 合計約 25%。舊文字「general-purpose 跑 xhigh」與行為不符：內建 agent 沒有 frontmatter，effort 跟主 session（260 回合跑 max、99 回合跑 medium）。去重地圖顯示每路 reviewer 都有獨家發現，故 reviewer 不降檔。owner：「1、2、3 都做，4 和 5 先不動」 |
| 10-05 | 主 session 讀得窄；第 6 階段收件改由 `doc-slice` 合成去重地圖（來源寫 `<lane>#<n>`）＋`reviewcov.py` 核對＋主 session 讀 CRITICAL／HIGH 原文並抽 3 條；真瀏覽器走一趟交 `ui-lane-reviewer`；lane-reviewer／quick-task 限縮工具、新增 `blind-judge`（不載 CLAUDE.md）；PLAN 矩陣加符號／行號欄、tdd agent 加讀取守則；模型問句附成本與判準；checkpoint 附 `/clear` 接續；新腳本 `taskcost.py`／`laneyield.py`／`reviewcov.py`／`extract.py` | 21 天 transcript 實測（API 價換算約 $12k）：主 session 41%、每回合 context 中位 246k；subagent 73% cache 讀、輸出只 1.4%，所以降 effort 不是槓桿，回合數 × context 才是；tdd-guide 每回合重讀的 context：子目錄 CLAUDE.md 24%、自己 Read／cat／grep 41%（10-05 更正：初版只算工具輸出、漏了 nested CLAUDE.md 載入）；瀏覽器 batch 83 次、1,200 萬字元進了主 session；探針：全工具 agent 首回合 44.6k vs 限縮 29.5k。converge-verifier 保留全部工具（偶爾要開瀏覽器驗 UI 條目，限縮每週只省約 $1–2）。主對話快取固定 1h 是官方選項但 owner 不設。owner：「全部照清單做」「交給 UI lane」「照規則 commit」 |
| 10-05 | 切片依要讀的檔案合併；不拿做完的 agent 接下一片 | 21 天實測：同一任務裡 tdd agent 的 Read 輸出 29% 落在別顆也讀過的檔案；每顆新 agent 固定重付初始 context 16%＋子目錄 CLAUDE.md 24%；但 tdd-guide 做完時 context 約 42 萬、subagent 快取 5 分鐘，重用接下一片每回合約貴一倍且失去並行。owner：「加進去」 |
| 10-05 | 耐久結論以 pattern 形式寫進子目錄 CLAUDE.md、合併改寫不追加 | 實測子目錄 CLAUDE.md 載入 21 天約 $1,200、tdd-guide 重讀 context 的 24%；24 份共 1.35MB，內容多是逐次任務追加的實驗經過、任務代號、日期、測試名清單。官方準則：每行問「刪掉它 Claude 會犯錯嗎」。owner：「全部的 claude.md 都有類似的問題 需要瘦身 可以直接來」 |
| 10-05 | 主 session 直接做的小修仍照第 6 階段派審查（含外審），只省任務目錄與 checkpoint | director-image-url（導演附圖 400 小修，正式碼約 150 行）：owner 准「修正很小可以直接修」後補一句「最後審查該送出的 reviewer 還是要送，外部審查很重要」 |
| 10-06 | 收尾閘加 `just fix-go-changed`（改到的 Go 行零舊式寫法）；go-fix hook 改成只回報改到的行、經 additionalContext 給模型 | 舊 hook 只寫 stderr＋exit 0：468 次觸發、送進模型的 content 全是空字串，Go 1.27 升級一個月累積 99 檔待現代化，幾乎都是新寫的測試檔。owner：「三段都做」（寫的當下回報、收尾閘、分支合完再全庫掃） |
| 10-06 | 耐久結論的落點：母檔有「主題規則」索引時寫進 `.claude/rules/<area>/*.md`、不寫回母檔；新主題新建規則檔（錨定 glob、不加 `**/`）＋母檔索引一行 | repo `f5fea15c` 依官方建議（<200 行、局部規則改 path-scoped rules）把 worker／backend-golang／angular-workspace 拆到 147→14、68→24、52→17KB；skill 仍寫「搬進最近的子目錄 CLAUDE.md」會讓之後的文件片寫回母檔、無聲重新長胖。glob 錨定形式經實測三種情境（主 checkout、`tmp/` 手動 worktree、隔離 worktree 子代理）都正確匹配，`**/` 會在 tmp worktree 重複載入。owner：「seven-phase skill 要改」 |
| 10-06 | comment-analyzer、go-reviewer、silent-failure-hunter 改 high（其餘 reviewer 維持 xhigh，兩包後依 `laneyield.py` 決定是否擴大到 ts／db／python）；comment-analyzer 保留，小包程式註解＋文件合一路、範圍收窄到前綴行＋改到的文件 | 官方 effort 文件：Opus 5.5 沒有指定檔位，要「用自己的評測掃一輪、不要沿用舊模型設定」；xhigh 的典型用途是 30 分鐘以上、百萬 token 級的任務，而且「Lower effort also means fewer and terser tool calls」。審查 agent 實測中位 13–25 分鐘。**10-05「output 只占 1.4%、降 effort 不是槓桿」是量測錯誤**：transcript 多數訊息只記串流開頭的 output_tokens（一顆 go-reviewer 54 則訊息記 704，可見輸出就約 1 萬），而且 effort 會改變工具呼叫數→回合數→cache 讀。second-ref 重放 A/B（同一凍結快照、原 prompt 只換路徑）：三路 xhigh $18.61 → high $10.36（只算 cache），回合 −29%、時間 −40%；已確認條目零漏（comments 4/4、go 2/2、silent 3/3，漏的 2 條 LOW 裁決都是 won't fix）；xhigh 自己重跑一次，嚴重度也一樣浮動。註解 lane 5 包共約 5 條獨家 MEDIUM 以上成立，與 security／tests／timing 同量級，所以不拿掉。owner：「先改實測的三個」「寫進 history＋lanes」「保留＋瘦身」 |
| 10-06 | tdd-guide 改 high；tdd-impl 併回 tdd-guide（試行結案、agent 刪除） | 直接比 tdd-guide 的 xhigh 與 high，不再用 tdd-impl 試行（它同時換了 effort 與角色 prompt，又只拿簡單片，分不出是哪個在省錢）。重放兩片、每片 xhigh 重跑＋high，突變與閘由主 session 親跑：thumbs 片 A（跨層）兩組突變 5/5、閘全綠，high $2.11 vs xhigh $3.05／$3.68；second-ref 片 D（碰錢＋權限）三組（含原版）突變殺傷相同、原版漏的 D10／D14／D25–D29 兩組也都漏（是任務書測試清單的缺口，不是 effort），high $3.98 vs $6.72／$5.98、時間 8 vs 15–17 分鐘。推理：片 D 兩組都主動提報原版沒抓到的 replay 例外過寬（審查時 codex 標 HIGH）；片 A 的 high 把任務書的錯前提寫成 INVARIANT（唯一退步，註解 lane 的防線範圍）。tdd-impl 17 次紀錄品質四項都沒問題，但省的主要是 effort，tdd-guide 降檔後只差角色 prompt。材料與數字在 repo `tmp/ab-tdd/RESULT-{A,D}.md`。owner：「改 high」「併回 tdd-guide」 |

## 更早就存在、仍有效的教訓
- **審查數量 vs 收斂**（04-28，owner）：「reviewer 多是可以的，有些 bug 會因為角度不同才會浮現；真正要關心的是如何收斂，less is more」→ 槓桿在 intake gate：只有嚴重技術債或潛在 bug 風險才進地圖。09-28 的分級只縮小包的路數，不取消多角度。
- **共用可寫快照**（09-02）：只在一個 lane 的 prompt 給了可寫樹，別的有 Bash 的 lane 自己進去跑突變，差點交出一條自己種下的 CRITICAL。
- **隔離 worktree 起在預設分支**（09-02）：`isolation: worktree` 不是「複製我現在的工作樹」；agent 看到的是另一個世界。
- **並行 agent 共用 scratchpad 會互截**（09-07）：三個 agent 都寫 `gen.py`，一段內容靜默消失。
- **長交付靠 hand-back 會掉**（09-18）：planner 三輪只收到摘要，空轉約 50 萬 token。
- **先一顆探針再派整批**（09-10）：51 顆 agent 照錯的處方跑完才發現，一顆探針 13 萬 token 就得到答案，成本差 50 倍。
- **外部 bridge 繼承殘留 cwd**：審查被派到錯的工作樹且不報錯。
- **專門 reviewer 不能用 general-purpose＋好 prompt 取代**（08-07）：我預期「邊際收益偏低」而補開的四路專門 agent，貢獻了全輪唯一 CRITICAL（stdlib 邊界 `s[:-1]` panic）。
- **invariant 封包本身會錯**（08-07）：20 條錯 2 條、第二輪 10 條錯 3 條，其中一條是整個設計的地基（「語音是使用者觸發」——其實自動合成）。
- **接縫要留沒有 lane 的視角**（08-07）：七條 lane 全漏、codex 獨抓的 3 條發現，都是「兩半各自對、接縫錯」。
- **死掉的 lane 補不同鏡頭**（08-11、08-25）：agy 額度用盡，改補 silent-failure-hunter／時間與順序，替補那路抓到全輪最值錢的 CRITICAL。
- **事實型不確定性下實跑勝過異源**（08-12）：外審三路零獨家，4 條無人重複的 HIGH 全來自實跑突變與真瀏覽器的兩路。
- **突變 lane 與真瀏覽器 lane 共用一棵樹**（08-26）：突變經熱重載推進瀏覽器、主 session 在突變視窗中 `go run` 把突變版編進去；新檔沒有 git baseline 可救 → 快照＋sha256 基準的由來。
