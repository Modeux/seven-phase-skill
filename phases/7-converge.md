# 第 7 階段：收斂

目標：每條發現都有裁決（confirmed／false positive／won't fix／unverified／owner 拍板），confirmed 的修掉，重跑全閘，寫總結。**不 commit。**

## 1. 收斂驗證（只驗值得驗的）
- 挑出**有爭議、HIGH 以上、或需要 owner 拍板**的條目，派 1–2 路 `converge-verifier`（大包依條目量分路，同層的放一路）。派工訊息只逐字附上它負責條目的地圖列與 lane 原文段落，其他 lane 全文列「需要時再讀」。純註解／文件措辭類不另派驗證——修的人邊修邊確認。驗證員一波派完，吃 Docker 的指令包 `scripts/dockerslot.py`（第 5 階段「共用資源」）；不要為了快把條目拆得更細：每顆約 20–30 分、和負責幾條幾乎無關（`references/lanes.md`「收斂驗證」）。
- **不必驗證的條目先修**：派驗證員的同一則訊息，把不在驗證名單、也不需拍板的條目組成修復片一起派出（照 §4 的做法）。條件：該片的檔案和任何待驗／待拍板條目的檔案不重疊，也不同根因（同根因、同一刀修得到的跟著待驗那條走）。這些片從第 6 階段的快照起樹；驗證與拍板完成後，剩下的片從合併後的主樹重新 `mkpatch.py`／`mkworktree.py` 起樹。為什麼：這些條目本來就由修的人確認，等驗證員與拍板只是排隊。
- 任務書用 `assets/converge-brief.md`：驗證員的工作是**試著推翻**每條宣稱（宣稱的情境可達嗎？HEAD 其實也一樣嗎？有別的機制已經擋住嗎？），推不翻才建議 confirmed；判定類別與報告格式照範本。false positive 要附推翻它的證據；推不翻也證實不了（缺環境、資料或重現不出來）的建議 `unverified`，並寫缺什麼證據、下一步、誰能補——缺證據不等於不成立。
- 需要 prod 數據才能下結論：驗證員寫出**確切查詢**與它會怎麼改變判定，由主 session 用 AskUserQuestion 問 owner 能不能跑（SKILL.md 的 prod 規則）。
- 驗證員會推翻審查 lane（例：lane 說某文件錯、驗證顯示文件對而 lane 錯）——這正是這一步的價值，裁決以驗證證據為準，並在修復任務包裡寫明「不要照 lane 原建議改」。

## 2. owner 拍板
需要拍板的條目用 AskUserQuestion 一次問完（最多 4 題）：每題兩三句白話脈絡、選項寫後果、推薦項排第一、關鍵對比附 preview。依據要來自驗證報告的實測數字，不要用審查 lane 的原始宣稱。

## 3. 裁決落檔
REVIEW.md 加「第 N 階段收斂裁決」表（標題含「收斂裁決」）：每條一列（編號、裁決、最終嚴重度、依據、處置／修在哪一片）＋名單外交叉發現（併入修復或記錄不修）。裁決欄以 `confirmed`／`confirmed-部分`／`false positive`／`won't fix`／`unverified`／`owner 拍板` 開頭（補充放括號），還沒定的寫 `pending`，修完回填——「修片確認」這類自創詞腳本認不得。宣稱嚴重度留在地圖，「最終嚴重度」欄只在這張表（`laneyield.py` 用它分 R／L），格內只放一個嚴重度字，原值或理由放括號（`LOW（原 HIGH）`）。寫完跑 `scripts/laneyield.py <slug> --no-cost --strict`：未辨認／pending／缺裁決的 R、來源認不出 lane、編號重複都會列出，exit 0 才算表齊。HIGH 以上的 `unverified` 不得在表裡停著：進 §2 owner 拍板，或寫進 `.claude/PRPs/BACKLOG.md`（附缺的證據與下一步）。UI 純主觀偏好不逕改，單列給 owner 拍板。SPEC／PLAN 依拍板修訂（標「第 7 階段」與日期）。
**範圍**：本包引入的、擋驗收的、擋安全上線的問題一律處置，包括審查才發現、超出原範圍的；與它同根因、同一刀修得到的既有缺口可併修（寫明哪些是既有的）。與本包無關的既有問題進 BACKLOG（附出處），不併進本包；其中碰重要面（錢、權限、未成年、資料遺失、全站可用性）或最終嚴重度 HIGH 以上的，不能只進 backlog，在 checkpoint 回報逐條點名給 owner。審查前就已 defer 的 MEDIUM／LOW 維持 backlog。

**流出分析**（根因問「為什麼發生」，這裡問「為什麼沒更早被攔下」）：裁決落定、切修復片之前，主 session 對下列條目各答一次「哪一道閘本該更早攔下它、為什麼沒攔」。
- **問哪些**：最終嚴重度 HIGH 以上的 `confirmed`（含名單外交叉發現、§5 走一趟才抓到的）；同一類問題以前出現過的（子目錄 CLAUDE.md、`.claude/rules/`、BACKLOG 已有同形狀條目，graphify＋grep 查）；本包修的 bug 本身是上線後才發現的，被修的那個也算一條。其餘不問——每條都問會變成儀式。
- **答到具體的閘與缺口**：閘＝PLAN 測試矩陣、tdd 的紅、全閘的某支 pin／audit、第 6 階段某一路、hook；缺口＝矩陣沒列這個情境、pin 只蓋三個呼叫點中的兩個、那一路的任務書沒列這個面、閘沒跑到這條路徑。缺口回原文或執行確認（「註解不可信」照樣適用）。「粗心」「模型漏看」不是答案——那不是閘。
- **落點四選一**（這一例的回歸測試修片本來就會寫，這裡要的是整類）：
  1. **機械閘**：蓋全部呼叫點的守門測試、audit 腳本、hook、lint。**同類再現只能選這條或 4**：上次的文字規則已經被跳過一次，再加文字沒用（history 10-13）。
  2. **形狀規則**：照全程規則寫進子目錄 CLAUDE.md／`.claude/rules/`，讓之後的 PLAN 與實作套得到。
  3. **流程**：PLAN 矩陣欄位、審查任務書、`references/lanes.md` 的檢查面——這是改本 skill，**不自己改**，列進 checkpoint 由 owner 決定。
  4. **接受**：沒有划算的更早閘，寫一句理由（例：只有真瀏覽器看得到，第 6 階段 UI lane 已是最早的閘）。
- **落檔**：REVIEW.md 在裁決表之後另起 `## 流出分析` 標題（不放在收斂裁決標題底下），表頭 `| 編號 | 本該攔的閘 | 為什麼沒攔 | 落點 |`；欄名不得含「來源」「裁決」，否則 `laneyield.py`／`reviewcov.py` 會把它當地圖或裁決表讀。精簡路寫在 PROGRESS.md 同名標題下。落點 1、2 併進 §4 的修復片（進 `feat` commit）；大到要另開任務或碰別的模組就進 BACKLOG，出處指到流出分析那一列。

## 4. 修復
- 依檔案範圍切片（每個檔只屬於一片），`assets/fixplan.md` 當共用任務包：背景、owner 拍板、環境規則、紅的歸屬、交付格式。§1 已先派的片不重切，這裡只切剩下的條目。每片另寫一份 `<片號>-brief.md`：從裁決表與驗證報告**逐字**摘出歸這片的條目（含建議新文字、測試設計、探針原文），不要叫每片去讀全部驗證報告。
- agent：程式碼片用 `tdd-guide`（`references/lanes.md`「實作與修復」）；文件片用 `doc-slice`。註解類條目在任務包寫明「先回程式碼確認宣稱成立再改，不成立就在報告標 false positive」。
- 碰拍板語義的紅由**主 session 先寫好、紅跑存檔**，再派實作；派工訊息點名主寫的測試不准改。
- 小修（1–3 檔、內容已確定，例如 SPEC／PLAN／openapi 描述、幾行註解）主 session 直接改；同一層、各自預估 <20 分鐘的修復片合成一片。
- 成因不明的難纏 bug 先套 `mattpocock-skills:diagnosing-bugs`（重現→最小化→假設→驗證）定位再修。
- 每片交件：主讀 red-run（紅的原因對）、比對主寫測試未動、自己抽一個突變、跑受影響包；通過後 `scripts/merge_back.py` 合回主樹。
- **修後複查只看裁決過的條目，不重開整包審查**（第 6 階段已經是完整審查）。主 session 驗不完、要派人複查時（修法改了跨層行為、或修片偏離裁決），任務書只給：該片負責的裁決條目原文、修片 diff、受影響呼叫點（`graphify affected`）。複查員逐條回「已修／未修」附 file:line——「改過」不等於「已修」，原缺陷要真的不在了；另報修片本身引入的新問題。修片 diff 以外的發現照 §3「範圍」分流，不觸發下一輪。
- **同一條修兩輪仍未收斂 → 停止原樣重派**：回頭重讀驗證證據、重定根因層或修法，或回報 owner 調整範圍。輪數或花費到頂代表該裁決了，不代表通過。
- 文件片（CLAUDE.md）可與程式碼片平行，但「Locked by／測試名清單」這類依賴程式碼片結果的句子留到最後由主 session 補。

## 5. 全閘與收尾
- 主 session 親跑 PLAN 的全閘，輸出存 `tmp/<slug>/converge/gate/`，逐項對既存紅基線（判準「零新增」；lint／ruff 這類要逐條比對，不只比數字）。
- 涉 UI：修復後的真瀏覽器走一趟派 `ui-lane-reviewer`，做法同第 5 階段：與全閘同時跑（最後一片合回後就用快照樹派出）、從走一趟套件起手，修過的條目逐條親手重驗（`assets/walk-kit.md`）。
- 確認改動檔集合＝原本的檔＋收斂新增的檔：`checktree.py` 對 baseline，「清單外」warning 就是多出來的檔，逐條確認是收斂新增還是並行 session 的改動，沒有無關檔。
- 突變紀錄等證據先歸檔到 `tmp/`，再 `git worktree remove --force` 清掉所有審查／修復樹。
- PROGRESS 寫第 7 階段紀錄與「下一步」（commit 範圍、主樹上不屬於本包的改動、部署後驗收）；backlog 依 SKILL.md「排序」分組寫進 `.claude/PRPs/BACKLOG.md`（格式見 SKILL.md「backlog 的家」），PROGRESS 只寫一行「backlog 已寫進 BACKLOG.md：N 條」。
- 記憶只更新「進行中」那一條的狀態，技術結論放子目錄 CLAUDE.md。
- 本包改了 **docs／templates／`.sql`／`openapi.yaml`／任何 `CLAUDE.md`／`.claude/rules/`** 嗎？有 → checkpoint 總結加一行「graphify 圖已過期（post-commit hook 只刷 AST 層），要不要 `/graphify . --update` 由你決定」；**不自跑 `--update`**（owner 10-05：傘狀全做完才一次更新）。

→ Checkpoint：白話總結（裁決數、拍板結果、閘結果、流出分析的落點——等 owner 決定的流程改動逐條列、owner 會看到的外部變化如警報文案），附 `/compact`，等 owner 說 commit。commit 形狀與 `git commit --only` 規則見 SKILL.md。
