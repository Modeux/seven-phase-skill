# deepweave 專案配方（repo：`~/Desktop/deepweave/deepweave`）

專案的權威規則在根 `CLAUDE.md` 與各子目錄 `CLAUDE.md`；這裡只放七階段流程裡反覆要用、容易打錯的指令與基線。

## 測試與閘
**跑測試一律走 `gate.py`，不要手打 `go test`／`ng test`／`docker run`**（它處理 GOROOT、`-tags integration`、Docker 槽位、逾時、ai 的一次性容器與掛載點，輸出判定一行就讀完）：
```
python3 ~/.claude/skills/seven-phase/scripts/gate.py go --tree T --pkg ./internal/x [--pkg ...] [--run NAME ...] [--integ] [--race] [--build|--vet [--tags T]] [--overlay F] [--timeout D]
gate.py go --tree T --all                                      # 全量整合：拿全部 Docker 槽位，背景跑
gate.py fe --tree T --project frontend|admin [--include PATH]  # 片收件不帶 --include，跑整包（frontend 約 15,000 支、90–140 秒）
gate.py fe-build --tree T --project frontend|admin
gate.py ai --tree T --repo <主 checkout> [--kind pytest|ruff|mypy] [PATH ...]
gate.py clean-docker [--apply]                                 # 列／清殘留 testcontainers（平常不必手動跑，見下面「測試容器自動清」）
gate.py lint --tree T [--pkg ./internal/x/...] [--clean-docker] # Go golangci 整包：OOM 自動分批到跑完，沒跑完＝INCOMPLETE
共用：--out FILE、--expect red、--known-red FILE、--no-known-red、--label
```
- 第一行 `GATE <mode> <verdict> pass=N fail=N skip=N <秒>s out=<路徑>`，機器讀 `<out>.summary.json`。
- verdict：`GREEN`；`RED`（斷言失敗）；`RED-KNOWN`（全部落在既存紅名單，算過）；`BUILD-FAIL`；`NO-TESTS`；`INCOMPLETE`（panic、逾時、被殺、有測試沒跑完、名單外的 unhandled）；`SLOT-TIMEOUT`。後四種都不是綠。
- exit：0 符合預期；1 不符；2 工具錯誤；3 NO-TESTS；4 INCOMPLETE；75 Docker 槽位等太久。
- **紅跑存檔**：`gate.py ... --expect red --out <片號>-red-run.out`。編譯失敗不算紅。
- **既存紅名單**隨程式碼版控，`fe` 自動讀 `angular-workspace/known-red-<project>.txt`，`go --integ／--all` 自動讀 `backend-golang/known-red-integration.txt`。不要自己比對名單；紅修好就刪那行。樹太舊、裡面還沒有名單時，`fe` 全專案會判 INCOMPLETE：同步 dev，或帶 `--known-red <主 checkout>/angular-workspace/known-red-<project>.txt`。
- **名單語法**：一行是一支測試的完整全名。列父測試不吸收子測試，要吸收才寫 `TestX/*`（TestX 本身與底下所有子測試，只有 go 認）。`unhandled <N> <spec> :: <訊息>` 要恰 N 個、訊息與該行完全相等。父測試本體有自己的 `*.go:N:` 輸出也算一筆失敗。`--known-red` 與 `--no-known-red` 同時給是工具錯誤。
- **`--run`** 可重複；同一個 `--run` 裡有 `|` 是工具錯誤，改寫成 `--run A --run B`。只命中被略過的測試、或點名的那支被略過，都是 NO-TESTS；整包閘的部分 skip 仍可綠，只看第一行 `skip=N`。
- **`--tags`**：`--vet --tags smoke`、`--build --tags smoke` 編譯其他 tag 後面的檔案；其他組合是工具錯誤（`--integ` 有自己的 tag）。
- 樹上有沒還原的 mutate 時 gate 直接工具錯誤，訊息印 `mutate.py --tree T --recover`。
- **測試容器自動清**：`gate.py go --integ／--all` 跑前、跑後各清一次殘留的 testcontainers（輸出一行 `docker: cleaned N leftover testcontainers`；被中斷、紅、等不到槽位也照清），自己這次起的在 `go test` 結束的當下就算殘留，不等 Ryuk。`gate.py ai`／`lint` 被逾時或訊號停掉時，回收自己起的那顆容器（`docker run --rm` 的 client 被殺，容器會繼續跑）。`dockerslot.py --` 手包的指令同樣跑前跑後清。
  - 殘留的判法（`scripts/_dockerclean.py`）：已 dead／exited；或容器標籤上的 session id 對不回任何活著的行程（起它的 `go test` 已結束）；判不了擁有者的才看年齡，超過 50 分鐘算。**對得回活著的 `go test` 就不動，不管多老**——別的 session 正在跑的整合測試不會被清掉。沒標 `org.testcontainers=true` 的容器（dev stack、compose）從不列入。
  - 擁有者判法只認驗過的 testcontainers-go 版本（`VERIFIED_GO`，目前 0.41.0）與 macOS。repo 升版後 `gate.py clean-docker` 會印 `note: testcontainers-go <版本> is not in VERIFIED_GO`、退回只看年齡；驗法：整合測試正在跑的時候另開一條跑 `gate.py clean-docker`，那次的容器算在 `kept=`（不在 `found=`）才把新版號加進去。
- worktree 要先 `pnpm install`（缺 node_modules 時工具會印該跑的指令）。

**突變一律走 `mutate.py`，不要手改檔再還原**：
```
python3 ~/.claude/skills/seven-phase/scripts/mutate.py --tree T --manifest M.txt --out <片號>-mutation-run.out [--only NAME] [--recover]
```
manifest 是純文字（`test:` 可放檔頭當預設），同一突變可有多個 `file:`：
```
test: go --pkg ./internal/x
=== M-a 描述
file: backend-golang/internal/x/a.go
<<<<<<< old
原文（在檔中必須恰好出現 1 次）
=======
新文
>>>>>>> new
```
- 結果：`RED`＝KILLED；`GREEN`／`RED-KNOWN`＝SURVIVED；其餘（沒編譯過、old 不唯一、基線不綠）＝INVALID。exit：0 全 KILLED；1 有 SURVIVED；2 有 INVALID 或工具錯誤。
- Go 檔走 `-overlay`，不碰樹，可在快照上跑、可並行。其他寫進樹→跑→還原，主 checkout 拒跑（`--allow-main` 才放行）。
- 樹鎖不等待：寫進樹的獨占，overlay 共享；衝突就是工具錯誤（`another mutate.py is running`），兩個 overlay 可並行。
- `test:` 不收 `--help`／`-h`、`go --build|--vet`、`ai --kind ruff|mypy`（都不跑測試，lint 紅會被讀成殺死）。
- pytest 紅的訊息含 SyntaxError、IndentationError、ImportError、ModuleNotFoundError 判 INVALID（突變沒載入，不算殺死）。
- 計畫後檔案被改過＝INVALID，不寫、不還原，保留別人的編輯。
- 進程被 kill -9 後，下次會拒跑並提示 `--recover`（還原原檔、驗 sha）。`--recover` 拒絕別棵樹的 journal。journal 不見或讀不了：照訊息先 `git -C T status` 查，再 `--recover --force`；這只解開死結、不還原任何檔案，輸出第一行是 `MUTATE RECOVER CLEARED`，不是 `RECOVER OK`。

**收件一律走 `recv.py`**（完整規則 `recv.py --help`）：
```
python3 ~/.claude/skills/seven-phase/scripts/recv.py --slice S1 --tree T --snapshot SNAP --root MAIN --gate "go --pkg ./internal/x" [--gate ...] --scope PATH [--require F] [--allow-narrow "理由"] [--allow-root-moved "理由"] [--allow-revert S1:path] [--out-dir D] [--progress PROGRESS.md] [--apply]
```
- 第一行 `RECV <片> <DRY-RUN-OK|APPLIED|REJECTED:<步驟>|TOOL-ERROR|MERGED-BUT-FAILED:<步驟>> files=N gates=N out=<路徑>`。不帶 `--apply` 只預演；`--apply` 要 `--progress`，檔要已存在。
- **一開始就拒收**（`REJECTED:preflight`）：片的樹有沒還原的 mutate、有 skip-worktree／assume-unchanged 旗標（`git status` 看不到那檔）、`--root` 與 `--snapshot` 的 HEAD 不同。HEAD 不同要重開快照重跑，或帶 `--allow-root-moved "理由"`（理由進 PROGRESS）。只比 HEAD：同波其他片合回造成的工作樹差異是預期內的，組合靠階段末全閘。
- **自動閘**：改到 `.go` 就先跑 `go --vet --pkg ./...`、`go --vet --tags integration --pkg ./...`；該檔另有 `//go:build` 標籤時再跑那個標籤（`a && b` 另跑 `--tags a,b`）。不能關、算進 `gates=N`、不算覆蓋。
- **`live_llm` 標籤本來就編不過**（`internal/worker` 的 `temperature_live_smoke_test.go`、`inline_tag_live_test.go`）：只有改到這兩支的片會被擋，順手修好再收，不開例外。
- **覆蓋**（沒涵蓋就 `REJECTED:coverage`）：`go.mod`／`go.sum` 要 `go --pkg ./...` 或 `--all`；`db/migrations`、`db/queries` 要至少一條 `go --integ` 或 `--all`；`package.json`、`pnpm-lock.yaml`、`angular.json`、`tsconfig*.json` 要 frontend 與 admin 兩個 `fe`；`ai-service/**` 要 `ai`；`backend-golang` 其他非 `.go` 檔要該目錄的包。`*.md`、`docs/**`、`.claude/**`、`tmp/**` 不要求；其他只列在「無閘規則的改動」。build、vet、lint、窄閘不算覆蓋。
- **新增略過**：測試檔比快照新增 `t.Skip` 類、`.skip(`、`.skipIf(`、`.only(`、`xit(`、`xdescribe(`、`pytest.mark.skip`、`pytest.skip(`、`xfail` 的行 → `REJECTED:coverage`。`--allow-narrow` 才放行，放行的項目逐一印出（`.go`、前端原始碼、`ai-service` 的 `.py` 沒涵蓋時它也放不了）。
- **提示不是拒絕，但一定在 stdout**：改到的測試檔、`這包沒有測試跑到：X`、無閘規則的改動。`--scope` 涵蓋某個深度或副檔名全部路徑（`**/*.go`、`*/*/*/*`）是工具錯誤。

- **ai-service（Python）**：**禁在 host 跑 `uv`／`just test-ai`／host pytest**（會把共用 venv 重建成 macOS 版，容器跑不起來；`gate.py ai` 會擋下並提示）。重建：一次性容器內 `uv sync --frozen`。
- **Docker 槽位**：`gate.py` 之外吃 Docker 的指令才自己包 `python3 ~/.claude/skills/seven-phase/scripts/dockerslot.py --`（如 `just sqlc` 用 `--pool sqlc --slots 1`）。預設池的指令跑前跑後會自動清殘留測試容器（上面「測試容器自動清」）。
- **契約**：`npx --yes @redocly/cli lint docs/api-contracts/openapi.yaml`。
- **lint／audit**：Go lint 一律 `gate.py lint --tree <T> --clean-docker`（不再手打 `just lint-gateway`：Docker VM 7.7GB 下整包常被 OOM 砍，過去每個任務都手寫縮範圍腳本、三個任務以「整包沒跑過」結案；工具 OOM 就分批到跑完，整包 GREEN／RED-KNOWN 才算跑過；`backend-golang/tmp/` 的探針碼自動歸 `ignored=`、不算紅；既存紅名單 `backend-golang/known-red-golint.txt`，11-07 實跑整包 88 秒、正式碼零 issue，所以名單目前不存在＝空）；`bash scripts/audit-pgx-tx-patterns.sh`（既存 (b)×2、(e)×4）；`just fix-go-changed`（**零輸出**才過：這次改到的 Go 行都已是新語法；`--apply` 只套這些 hunk、併進本包；全庫 `just fix-go-check` 在存量清掉前 exit 1 是常態，不當閘）。
- **Go 探針**：`backend-golang/tmp/` 下的 `.go` 探針會被 `go vet ./...`／`go build ./...` 掃到：探針檔存成 `.go.src`，用 `go test -overlay <json>` 掛進目標套件跑。
- 判「這條紅是不是我弄的」：`git archive HEAD | tar -x -C <tmp>` 匯出乾淨副本跑同一條；比 stash 安全（stash stack 與所有 worktree 共用）。

## 收尾清理（`scripts/cleanup.py`，任務交付前的硬閘）
```
python3 ~/.claude/skills/seven-phase/scripts/cleanup.py --slug S --root <樹>           # 只列：這包還開著、留著什麼
cleanup.py --slug S --root <樹> --apply                        # 清：程序 → 走查 DB → tmp/<slug>/ 下的樹 → gateway 執行檔與過期 pid 檔 → 殘留測試容器
cleanup.py --slug S --root <樹> --only procs --apply           # 只收某幾類：procs／dbs／trees／files／containers
cleanup.py --all --root <樹>                                   # 只列：<樹>/tmp/ 底下每個任務；不能 --apply（別的 session 可能正在做）
```
- 第一行 `CLEANUP <slug> OK|LEFT found=N removed=N left=N`，之後每項一行、行尾方括號是處置。exit：0 沒有殘留；1 還有（只列而有東西，或清不掉）；2 工具錯誤。
- 只認這包自己記下的：`tmp/<slug>/**/pids/*.pid` 裡還活著的程序和它的子程序（程序比 pid 檔新＝pid 被重用，不動）；`run/env.log` 記的 port 上、而且是從那份 log 記的樹在跑的 listener；`env.log`／`kit/walk.config.json` 記的 `deepweave_walk_*` 資料庫（還有連線就留著並報幾條）。共用的 `deepweave`、dev stack、別的任務的東西都碰不到。
- 樹移除前先存：`tmp/<slug>/cleanup-trees.txt` 每行 `<樹> head=<sha> tree=<sha>`；`git diff <head> <tree>` 看那棵樹改過什麼，`git archive <tree> | tar -x -C <目錄>` 整棵拿回來（git gc 約兩週後回收）。存不了就不刪、列成 `left`。
- 不歸它管：`dbq.py --clone` 建的資料庫（誰建誰 `dbq.py --drop`）、Docker 網路與 volume（要 owner 決定，別的專案可能在用）、樹上的探針檔（`checktree.py` 的「清單外」）。

## 本機 DB（`scripts/dbq.py`，不要再手打 `docker exec … psql`）
預設 `deepweave-postgres`／`deepweave`、**唯讀**（Postgres session 設定擋寫入，exit 3），每個結果上限 50 列，超過會寫明總數與完整檔路徑。工具只連本機 Docker，沒有 host 參數。
```
python3 ~/.claude/skills/seven-phase/scripts/dbq.py "SELECT ..."
dbq.py --db NAME --file checks.sql [--raw] [--limit N] [--out FILE]     # --limit 0＝不截斷
dbq.py --schema TABLE                  # 欄位、型別、約束、FK、trigger；欄位不確定先查，不要猜
dbq.py --tables PATTERN
dbq.py --clone deepweave_x [--tree PATH]   # 建庫＋migrate＋dev seed，約 4 秒；--tree 預設 cwd 的 git toplevel
dbq.py --fixture all --db deepweave_x      # 或 creator,minor；對 deepweave 一律拒絕
dbq.py --write --db deepweave_x --file seed.sql
dbq.py --drop deepweave_x | --list-dbs | --list-fixtures   # --drop 只刪 dbq 建的；walk_env 建的用它自己的 dropdb
```
exit：0 成功；1 SQL 錯、欄位不存在、fixture 核對不符；2 參數錯、Docker 或 DB 不在；3 被安全規則拒絕。fixture 帳號密碼 `deepweave-demo-123`，點數一律走 `point_transactions`。限制：角色資料只驗過詳情 API，首頁不會出現；fixture 用 SQL 寫 `adult_status`，走查中途要改得走 `PUT /me/community/settings/adult-status`。

## 環境坑
- repo 各層有二十多份同名的 `CLAUDE.md`：確認工作樹狀態用不帶 pathspec 的 `git status --porcelain`。
- go-fix hook 只 `gofmt` 剛改的檔，並把該檔「改到的行」上的 go fix 改寫塞進 context（不寫檔）；看到 `[go-fix hook]` 就在同一片裡改掉，別留到收尾。
- worktree 守衛會擋 shell 變數與命令替換：CLI 橋接／材料包組裝改寫成 `python3 <script>.py`；改既有檔一律 Edit／Write（`.claude/hooks/py-edit-guard.py` 會擋 Python 改檔，Edit 做不到時加 `# allow-py-edit: <理由>`）。該 hook 指令尾的 `; exit 0` 是刻意的：找不到檔時 `python3` exit 2，PreToolUse exit 2＝擋下所有 Bash。`.claude/settings.json` 舊有的 `$TOOL_INPUT` 密鑰 hook 是 owner 10-07 刻意刪的（變數不存在、從未生效；照原 pattern 修好 30 天會擋約 1,035 個正常指令，真正整檔印出密鑰檔只 2 次），別修回來。
- `rm -rf` 會被權限系統擋：清樹用 `git worktree remove --force`，暫存檔換新名字而不是刪。
- 改完 `db/queries` 立刻起 sqlc 容器會讀到截斷檔：腳本內 `sync`＋`sleep 3`。

## prod
- **查 prod 前先用 AskUserQuestion 問 owner**（SKILL.md 的 prod 規則）。
- DB：JetBrains 的 prod 連線設定 readOnly=false，**只下 SELECT**。
- Langfuse：`.env` 的金鑰就是 prod 專案；Metrics API 額度 100、與 prod 警報輪詢器共用，能少打就少打，subagent 禁打。查詢配方見記憶 `reference_prod_langfuse_and_db_query_recipe`。
- 換模型／改 prompt 前：tmp 探針走真實 service＋subagent 隨機 A/B 盲評（judge prompt 寫明位置每對隨機）。
