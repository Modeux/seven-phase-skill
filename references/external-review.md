# 外部異源審查（codex／grok／agy）

外審的價值來自它不知道你的設計意圖，所以材料包只給事實（任務書、SPEC／PLAN、diff），不給你的結論。用在兩處：第 6 階段審 diff（下面「材料包」），大包另在第 4 階段審 PLAN（文末「PLAN 審查的材料包」）。三路都用 `scripts/run_external.py` 在背景跑；跑之前確認目前工作目錄在 repo 根（殘留的 cwd 會把審查派到錯的樹，而且不報錯）。

## 配方
| lane | 指令（`run_external.py` 內建） | 要點 |
|---|---|---|
| codex | `codex exec -m gpt-6-sol -c sandbox_mode="read-only" -c model_reasoning_effort=xhigh -C <快照> --skip-git-repo-check -o <out.md> -` ＋ stdin 材料 | 從 repo 根跑、stdin 用 `-`；read-only sandbox 跑不了測試，prompt 直接說不必跑。detached 啟動會 wedge，前景（或背景的前景 subprocess）才正常。判活看 `~/.codex/sessions` rollout 檔 mtime；沒報告就 `codex exec resume <session-id>` 補出——**永遠用 session id，不要 `--last`**（會接到別人的 session）。gpt-6-sol 不在 `models_cache.json` 清單但 API 接受，別改回。 |
| grok | `grok --prompt-file <packet> --verbatim -m grok-4.7 --output-format plain`，cwd＝快照 | `--write` 旗標已被 CLI 移除（帶了 exit 2）；read-only 意圖寫在 prompt 裡。它有執行能力，材料包的環境陷阱警語對它同樣必要。大包前 8 分鐘 0 bytes 是正常，30–40 分鐘交件常見。 |
| agy | `agy --model gemini-4-argon --effort high --print "<packet 全文>"` | effort 預設 high、明寫不省略。10-16 實測 argon 不帶 `--effort`、或用後綴名 `gemini-4-argon-high` 再帶 `--effort` 都收（舊版 3.8-flash 不帶會被拒、後綴＋flag 曾被拒），配方統一用基底名＋flag。headless 對任何工具自動拒絕 → 材料包開頭寫「你沒有工具，只分析 packet 文字，忽略允許跑測試的句子」，且 packet 要自含 diff 與必要檔案。stdin／參數約 190KB 截斷，實務上限 170KB。設定檔指定的 provider 缺金鑰時 1 秒內 exit 1——屬 owner 的 CLI 設定，不改、不讀憑證，記「沒跑」並換鏡頭替補。 |

## 材料包
- 共用任務書（`assets/review-brief.md`）＋一段「你的角色：沒有分派邊界的跨層視角」：列出要追的接縫（A 層 route → B 層 client → C 層 worker → 狀態 → 面板），強調「兩個各自正確的元件湊起來會不會錯」「追到能改的最深一層」。
- codex：路徑改寫成快照內的 `tmp/review-input/`，diff 用檔案不內聯。
- grok：非測試碼 diff 內聯，測試／文件 diff 給檔案路徑。
- agy：PLAN 只給定案、風險、不做清單三節＋非測試碼 diff＋文件 diff，全部內聯，控制在 170KB 內。
- 送出前 `wc -c` 每份材料；收件先 `grep -i truncated`。

## PLAN 審查的材料包（第 4 階段，大包）
審查對象是 PLAN 加上 HEAD 的程式碼，沒有 diff。偏誤風險在「主 session 挑哪段程式碼給外審看」——挑的人就是寫 PLAN 的人，所以摘錄一律照 PLAN 的檔案矩陣機械取，不另挑。
- 共用任務書：`assets/plan-review-brief.md`（填好的）。
- codex：任務書＋快照內 `tmp/review-input/` 的 SPEC／PLAN 路徑，自己讀 repo，不附摘錄。
- grok、agy：同一份自含材料包——任務書＋PLAN 全文＋SPEC 全文＋矩陣每一列「符號／行號範圍」的程式碼摘錄（從快照取，每段標頭 `path:Lx-Ly`，前後各多帶 10 行）＋PLAN 提到的每個 `.claude/rules/*.md`／`CLAUDE.md` 的原文（PLAN 只引某一節就取那一節，用 `scripts/extract.py`）。組包可交 quick-task；收件前核對摘錄標頭數＝矩陣列數。agy 超過 170KB 時依序裁：規則檔改成只留 PLAN 引到的那一節 → SPEC 只留方向決議與驗收節 → 摘錄去掉前後 10 行；PLAN 全文與摘錄本身不裁。grok 用同一份（不另外裁短）。

## 收件
`run_external.py` 只在 CLI exit 0 且報告非空時才把 `<out>.partial` 改名成報告；否則以非零結束（CLI 的 rc，或 3＝rc 0 但沒有報告；agy 材料過大＝2），舊報告不動、也不算本輪。每次都寫 `lane-<x>.status.json`，`reviewcov.py` 讀它判失敗。背景跑完看 exit code，非零就記「沒跑」並換鏡頭替補。
外審報告同樣是待驗證的宣稱：跟內審一起去重進 REVIEW.md，信心「中／低」的條目在第 7 階段優先驗證。
