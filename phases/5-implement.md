# 第 5 階段：TDD 實作

嚴格 red → green → refactor。為什麼：紅測試是把 owner 決策與規格釘成可執行斷言的唯一方式；「一寫就綠」的測試什麼都沒證明。

## 紅由誰寫（2026-09-23 放寬）
- **一般切片／修復包**：一個 agent 一包寫紅＋綠——`tdd-guide`（high），見 `references/lanes.md`「實作與修復」。主給**測試設計清單**（每支測什麼、期望紅的原因），不給測試碼。agent 必交 `<片號>-red-run.out`（每支紅的失敗訊息逐字）；**紅跑存檔後不准再改那些測試**（用測試檔 sha 或 mtime 對 red-run.out 核）。
- **主審紅，不寫紅**：讀 red-run.out 判「紅的原因對不對」——編譯紅、fixture 紅、import 錯、跑錯 include 都不算紅；再抽 2–3 支做一次突變（把修法 revert 或用 `go test -overlay`／scratch 副本），該支必須紅，擋「測試跟著實作寫」。
- **仍由主寫紅**：碰 owner 拍板語義、錢、跨層不變式（credits、白名單、配額、告別 anchor 這類）。理由：agent 能寫對測試，但不知道哪些語義是 owner 拍板過的。主寫的紅測試在派工訊息裡點名「不准改」，收件時比對那段的 sha。
- **守衛測試**（對已正確的行為補釘）寫完就是綠的：改交**突變證明** `<片號>-mutation-run.out`（施加最小突變→該支紅→還原）。
- **文件包無紅**：主寫 grep pin 腳本（派工前新句全 0、舊句全 1），agent 落句後同一支腳本翻面；另跑「只改註解行」的 diff 檢查。

## 併發派工（2026-09-23）
- PLAN 的 wave 內、檔案集合互斥的片**同時派**多個實作 agent（一則訊息裡同時起）。
- **每片一份 `<片號>-brief.md`**：PLAN 裡歸這片的段落（含矩陣那片的符號／行號欄）、測試設計清單、相關 invariant，用 `scripts/extract.py` 逐字摘錄並附出處；SPEC／PLAN 全文列「需要時再讀」（SKILL.md「任務包瘦身」）。
- **隔離**：每片一棵私有 worktree。工作樹有未 commit 的改動時，`isolation: "worktree"` 起在預設分支、看不到這些改動——改用 `scripts/mkworktree.py`（detach 到本 HEAD＋套 `mkpatch.py` 產的 patch＋驗 sha）自己建樹，派工訊息寫明樹的絕對路徑。
- **交回**：agent 在私有樹改完，主用 `scripts/merge_back.py` 對快照算出改過的檔，確認主樹那些檔仍等於快照、片間零重疊後複製回主樹（不靠 agent 產 patch；`git apply` 從子目錄跑會靜默略過目錄外 hunk）。同波兩片打到同檔＝矩陣寫錯，回 Plan 修，不硬合。
- **共用資源——上限加在指令上，不加在 agent 上**：檔案互斥的片一波派完，不為 Docker 分波。任務包寫明：每條吃 Docker 的指令（`-tags integration`、testcontainers、sqlc 容器）一律包 `scripts/dockerslot.py --`（預設同時 3 條、其餘排隊，Bash 設 `timeout: 600000`；exit 75＝等太久，照訊息重試或回報）；sqlc 用 `--pool sqlc --slots 1`；主 session 的全量整合用 `--all`。前端片不吃 Docker，不排在後端片後面——派工同時由主 session 先在前端樹跑 `pnpm install`。每波結束清殘留網路；Python 共用 venv 只准唯讀掛載進一次性容器。為什麼：agent 九成時間在讀碼與推理、`go test` 約佔 4%，按 agent 分波等於讓不碰 Docker 的時間也排隊（age-gate-unify 第 7 階段：驗證員 3＋2 兩波多等約 17 分、前端修復片等後端那波約 39 分）。
- **先探針再整批**：第一波只派一片驗「寫紅＋綠＋交 red-run.out」處方，通過才併發其餘。

## 主 session 的驗收（不放寬）
- 偏離裁決：agent 自報的每條偏離逐條接受／否決，否決的寫成第二輪紅丟回同一 agent。
- **主親跑閘**：每片跑「受影響包」的 build／vet／單元＋該片具名整合；**不採信 subagent 自報**（已知失敗模式：只跑窄範圍 `-run` 就報綠，漏掉命名不同的連帶紅）。輸出重導向到檔案再 grep（接 `tail` 會藏掉前面的包並把 exit code 歸零）。
- diff 生產碼對快照，確認只動該片範圍；改完一律 `git status --porcelain`（不帶 pathspec）對帳。
- 全閘（整合全包、lint、audit、前端整包＋production build、契約 lint）只在本階段末跑一次，判準是「對既存紅基線零新增」。
- 涉 UI 的包，階段末的真瀏覽器走一趟派 `ui-lane-reviewer`（步驟、樹與 dev server、證據目錄寫在任務包）；主 session 讀結論、開它點名的一兩張截圖。為什麼：瀏覽器輸出很大，留在主 session 會讓之後每一回合都重付（owner 10-05 定）。
  - **與全閘同時跑**：最後一片合回主樹後，立刻用 `mkpatch.py`＋`mkworktree.py` 建走一趟專用的快照樹並派出，主 session 同時在主樹跑全閘。全閘期間主樹又改了檔：`checktree.py --baseline <走一趟快照的 baseline.sha256> <主 checkout>` 列出變動，有走一趟碰得到的執行期檔（前端非 spec 檔、handler／service／SQL）就只重驗涉及的步驟（用套件）；只動測試、文件、lint 樣式的不必重驗。為什麼：走一趟不等全閘，每趟省約 15–20 分。
  - **交件附走一趟套件**（`assets/walk-kit.md`，任務包寫明套件路徑與該檔路徑；步驟寫成 runner 相容）。修完重走、第 7 階段收尾那趟從套件起手，**主 session 派工前先用 `scripts/run_kit.mjs` 一次跑完套件**（試點中，做法見 `walk-kit.md`「重走怎麼用」第 0 步）：重走實測中位 30 分、比第一次 25 分還久，因為每次都從零摸環境與頁面（約四成呼叫在讀檔）。
  - 走一趟找到「中」以上的行為 bug：修片照 SKILL.md「修片進場門檻」，任務包用 `assets/fixplan.md`。

## 加速
- 同一層、各自預估 <20 分鐘的小片，或要讀的檔案大量重疊的片，合成一片、一個 agent、一次記錄。
- 不拿做完的 agent 接下一片：它的 context 已長到幾十萬 token，subagent 快取 5 分鐘就過期，回頭用要整段重寫；新開一顆反而便宜。重用只限同一片的立即後續（第二輪紅、被否決的偏離）。
- 小改動（1–3 檔、內容已定）主 session 直接做，不派。
- PROGRESS 每片五行（做了什麼／紅→綠證據／閘結果／殘留／下一步）；lane 與 agent 報告原文留在 `tmp/<slug>/`，不進任務文件。

→ Checkpoint。
