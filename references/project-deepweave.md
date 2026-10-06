# deepweave 專案配方（repo：`~/Desktop/deepweave/deepweave`）

專案的權威規則在根 `CLAUDE.md` 與各子目錄 `CLAUDE.md`；這裡只放七階段流程裡反覆要用、容易打錯的指令與基線。

## 測試與閘
- **Go**：一律 `env -u GOROOT go test -C <tree>/backend-golang ./...`（`GOROOT` 汙染讓 toolchain 靜默用錯版本）。整合測試加 `-tags integration`，會起 testcontainers、吃 Docker 位址池——同時跑的片數設上限，跑完清殘留網路。
- **ai-service（Python）**：**禁在 host 跑 `uv`／`just test-ai`／host pytest**（會把共用 venv 重建成 macOS 版，容器壞掉）。只用一次性容器：
  `docker run --rm --network none -v <tree>/ai-service:/app:ro -v <repo>/ai-service/.venv:/app/.venv:ro -w /app -e PYTHONDONTWRITEBYTECODE=1 -e LANGFUSE_TRACING_ENABLED=false -e APP_ENV=development -e MYPY_CACHE_DIR=/tmp/mypy ghcr.io/astral-sh/uv:python3.12-bookworm .venv/bin/python -m pytest -q -p no:cacheprovider`
  （ruff：`-m ruff check --no-cache app tests`；mypy：`-m mypy app`）。在 worktree 裡跑要先 `mkdir -p <tree>/ai-service/.venv` 當掛載點（`mkworktree.py --mkdir ai-service/.venv`）。zsh 下把指令存成變數再展開要用 `${=VAR}`。
- **Angular**：從 `angular-workspace/` 跑，單行子 shell `bash -c 'cd <tree>/angular-workspace && npx ng test admin --include <spec> --watch=false'`；worktree 要先 `pnpm install`。production build：`npx ng build <project>`。
- **契約**：`npx --yes @redocly/cli lint docs/api-contracts/openapi.yaml`。
- **lint／audit**：`just lint-gateway`（exit 1 是常態，判準「碰過的檔零新增」；`backend-golang/tmp/` 是 gitignored 探針碼，不算）；`bash scripts/audit-pgx-tx-patterns.sh`（既存 (b)×2、(e)×4）；`just fix-go-changed`（**零輸出**才過：這次改到的 Go 行都已是新語法；`--apply` 只套這些 hunk、併進本包；全庫 `just fix-go-check` 在存量清掉前 exit 1 是常態，不當閘）。
- **整合測試既存紅名單**：`backend-golang/CLAUDE.md` §Testing 的表（逐名比對，不用前綴）。
- 判「這條紅是不是我弄的」：`git archive HEAD | tar -x -C <tmp>` 匯出乾淨副本跑同一條；比 stash 安全（stash stack 與所有 worktree 共用）。

## 環境坑
- repo 有 24 份 `CLAUDE.md`：確認工作樹狀態用不帶 pathspec 的 `git status --porcelain`。
- go-fix hook 只 `gofmt` 剛改的檔，並把該檔「改到的行」上的 go fix 改寫塞進 context（不寫檔）；看到 `[go-fix hook]` 就在同一片裡改掉，別留到收尾。
- worktree 守衛會擋 shell 變數與命令替換：改寫成 `python3 <script>.py`。
- `rm -rf` 會被權限系統擋：清樹用 `git worktree remove --force`，暫存檔換新名字而不是刪。
- 改完 `db/queries` 立刻起 sqlc 容器會讀到截斷檔：腳本內 `sync`＋`sleep 3`。

## prod
- **查 prod 前先用 AskUserQuestion 問 owner**（SKILL.md 的 prod 規則）。
- DB：JetBrains 的 prod 連線設定 readOnly=false，**只下 SELECT**。
- Langfuse：`.env` 的金鑰就是 prod 專案；Metrics API 額度 100、與 prod 警報輪詢器共用，能少打就少打，subagent 禁打。查詢配方見記憶 `reference_prod_langfuse_and_db_query_recipe`。
- 換模型／改 prompt 前：tmp 探針走真實 service＋subagent 隨機 A/B 盲評（judge prompt 寫明位置每對隨機）。
