# 第 6 階段：多方審查

目標：從不同鏡頭找缺陷，每條附嚴重度、file:line、具體證據，全部寫進 `REVIEW.md` 的去重地圖。

## 1. 定路數
先跑 `scripts/numstat.py` 看實際規模，對照 SKILL.md「依規模分級」與 `references/lanes.md` 定 lane 清單。拿不準就往完整走。

## 2. 準備審查對象（凍結快照）
1. `scripts/mkpatch.py` 產 `change*.patch`、`baseline.sha256`、`files.txt`（排除無關的任務目錄與 `tmp/`）。
2. `scripts/mkworktree.py` 建 **唯讀快照**（所有 lane 的 file:line 以它為準），需要突變的話再建一棵**突變樹**，指定唯一 writer（通常 pr-test-analyzer）。
3. 為什麼不讓 lane 直接看主樹：有 Bash 的 lane 遇到「想驗證」的衝動就會寫檔；快照的免疫力來自「沒有人寫它」。可寫的樹在**所有** lane 的共用任務書明寫「唯一 writer＝X」，不只寫在 X 的 prompt。
4. 快照裡放一份 `tmp/review-input/`（SPEC、PLAN、patch、files.txt），給外審用。

## 3. 共用任務書
從 `assets/review-brief.md` 填：這包在做什麼（白話）、真相源、審查對象與新檔清單、環境規則（唯讀、禁在 host 跑會重建共用環境的指令、測試配方、禁連 prod 與量測 API）、證據規則（註解與文件不是證據、任務書裡的事實是待否證的假設）、**已知既存紅**（不要報）、owner 指定的重點面 F1…、報告格式與「已查證無問題」一節。
任務書本身加必讀材料照 SKILL.md「任務包瘦身」控制在約 90KB 內（審查對象的 diff 與程式碼不算）：SPEC／PLAN 只逐字摘與本包相關的節，全文路徑列「需要時再讀」；規格忠實度 lane 例外，可讀 SPEC 全文。
任務書裡每條 invariant 都標出處（SPEC 條號或參照實作的 file:symbol）並先回查——它同時是第 7 階段的裁決依據，寫錯會把正確實作判成違規。「某功能只在使用者觸發時才跑」「某欄位有讀者」這類要 grep 到實際呼叫點才寫。

## 4. 派工
- **內部 lane 一律用 `Workflow` 扇出**（owner 10-07；先載 `workflow-authoring`）：每路一份派工單 `tmp/<slug>/review/tasks/<名>.md`（鏡頭、必讀／需要時再讀、可跑的指令），script 只放 `{key, agentType}` 清單＋一段共用交件句，`agent(prompt, {agentType, label: 'lane-<名>', schema})`（不傳 model／effort，frontmatter 會保留；關注面 lane 用 `lane-reviewer`，不用 general-purpose）。Workflow 內的 subagent 寫不了報告檔 → schema 帶 `report_markdown`（全文）＋`summary`＋`counts`；**script 只回 summary／counts**（全文不灌回主 session），收件時 `scripts/wf_reports.py` 從 run 的 `journal.jsonl` 抽 `report_markdown` 落檔成 `lane-<名>.md`。外審三路是 CLI，不進 Workflow，同一則訊息用 `run_external.py` 背景跑。
- 外審三路照 `references/external-review.md`：先 `git -C` 確認在 repo 根、`wc -c` 材料、agy 材料要自含且 ≤170KB；全部背景跑，大包前 8 分鐘 0 輸出屬正常。
- lane 死掉或外審跑不了：記清楚是「沒跑」還是「空跑」，**換一個不同鏡頭替補**，不同質重跑（常被漏的鏡頭見 `references/lanes.md`）；跑不了的原因（例如缺金鑰）屬 owner 的設定，主 session 不改 owner 的 CLI 設定、也不去讀它的憑證。
- 規格忠實度 lane 要主動列「本節沒處理的 3 個情境」——fan-out 抓錯誤行為，case walk-through 才抓缺少的行為。
- 註解 lane 分「該刪（複述程式碼）／該補（非直覺處沒寫）／該修（與行為不符或過時）」。
- 測試 lane 用突變驗證 PLAN 記的閘指令（`-run` regex、`--include`）真的跑得到本包測試——否則 PROGRESS 的「全綠」可能是空集合全綠。
- 涉 UI 的 UI/UX lane（`ui-lane-reviewer`）以 SPEC 的 UI/UX 條目驗收：design system 一致性、無障礙（語意標籤、鍵盤、焦點）、loading／empty／error 狀態、響應式；用瀏覽器實際渲染、截圖為證，每次量測附 DOM 指紋證明量的是本包。驗不了的標「需人工目視」交 owner。
- 突變只在突變樹做；主 session 在審查期間起的 dev server／`go run` 記下啟動時間——「檔案已還原」不等於「跑著的程序已還原」。

## 5. 收件與去重
- 每份報告先 `grep -i truncated`；審完 `scripts/checktree.py --baseline <review 目錄>/baseline.sha256 <主 checkout>` 驗一次：baseline 檔不符＝有 lane 寫到主樹；「清單外」warning 逐條看是不是並行 session／owner 的正常改動（主 checkout 是共用的，所以預設只警告）。
- lane 回報與任務書的 invariant 衝突時，先驗 invariant 再驗 finding；invariant 的勘誤寫進 REVIEW.md（只在對話裡說，compact 後第 7 階段會沿用錯版本）。
- **合成交給 `doc-slice`**（owner 10-05 定）：任務包給各 lane 報告路徑、本節格式與 intake gate。它寫 REVIEW.md 的 lane 配置表、執行結果、重點面結論表（每個 F 一列＋主要證據 lane）、**去重地圖**（R1…＝MEDIUM 以上、L1…＝LOW；每條：宣稱嚴重度、一句話、位置、來源——來源寫條目編號 `<lane>#<n>`，n＝該 lane 報告裡第 n 個 `### [SEVERITY]` 標題，最後附幾路同報），intake gate 擋下的條目列進「未收錄」一節、各附一句理由。
- **主 session 核對而不逐份重讀**：`scripts/reviewcov.py tmp/<slug>/review <REVIEW.md>` 要 exit 0：預期的每一路（`tasks/*.md`＋`ext-*.log`＋`*.status.json`）都交件且狀態 ok（失敗、缺件、空檔、早於快照的舊報告都會擋），每條發現都在地圖表格的「來源」欄或未收錄節（只出現在敘述或其他欄位裡不算，地圖表頭要有「來源」欄）；沒照標題格式的報告（常是外審）主 session 親讀後加 `--ack-manual <lane>`，跑不了而換鏡頭替補的加 `--waive <lane>=<理由與替補>`；再讀全部 CRITICAL／HIGH 原文、隨機抽 3 條 MEDIUM 原文對地圖。合錯（兩條不同的被併成一條）就拆開並在地圖註明。
- intake gate：只有「嚴重技術債」或「潛在 bug 風險」（含跨層漂移、誤導性註解）才進地圖；審美、風格偏好、PLAN「不做清單」裡的東西不進（除非證明本包讓它變糟）。
- 收件後 lane 報告原文留在 `tmp/`，REVIEW.md 只留地圖與結論。

## 6. 審完不要順手做的事
不修、不問 owner 第 7 階段的拍板題（那些在第 7 階段用 AskUserQuestion 問）。快照與突變樹留著給第 7 階段用。

→ Checkpoint（回報附：路數、零／幾條 CRITICAL、最強訊號、哪幾條要 owner 拍板）。
