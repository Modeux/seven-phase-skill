# 走一趟套件（walk kit）

第一次真瀏覽器走一趟的 `ui-lane-reviewer` 交件時一併留下，放 `<證據目錄>/kit/`。兩個目的：重走不必重新摸環境與頁面；同一步驟前後在同一個位置量，前後才比得起來。

## 內容
- `README.md`：步驟表（編號、對應 SPEC 條目、腳本檔、通過條件、這輪結果）、用到的帳號與種資料、已知不穩定點。
- `env.sh`：照順序起環境（先確認 port 沒被別的工作樹佔用 → gateway → ng serve → 種資料），印出 server URL、服務的是哪棵樹、啟動時間。只做事、不判斷。
- `login.js`：一支 `async (page) => {...}`，登入並回傳帳號。
- `step-<n>.js`：每步一支 `async (page) => {...}`：操作、量 DOM（附本包獨有的 DOM 指紋）、截圖到 `<證據目錄>/<輪次>/step-<n>.png`、回傳量測 JSON。捲動位置、視窗寬、主題寫死在腳本裡。探索時用 `browser_run_code_unsafe` 跑（有 `filename` 參數就帶絕對路徑，沒有就貼檔案內容）；重走用 `scripts/run_kit.mjs`（下節）。
  - **runner 相容（10-18 起，試點中）**：每輪會變的參數集中在**單行** `const CFG = { round: '…', … };`（runner 在這行後面注入覆寫；寫成多行就只能跑預設值）；截圖路徑由 `CFG.round` 組；回傳物件帶 `ok: true|false`（沒帶的步驟 runner 只能標 DONE，等於沒判）；不靠 `require`／Node 全域。
- `checks.sql`：每步要對的 DB 查詢，只 SELECT。
- `repro-<條目>.js`（任何一趟回報「中」以上的行為 bug 時都要附）：runner 相容、會在回報的那個症狀上回 `ok: false` 的重現步驟，交件前用 `run_kit.mjs --steps` 跑過一次；另附觸發表草稿（會影響同一塊的狀態×動作、在途×另一動作、0／>0、失敗、到底），每列一支步驟或寫「套件測不到＋理由」。這是修片的會紅指令與觸發表（SKILL.md「修片進場門檻」），主 session 審過才派修片。

## 重走怎麼用（第 5 階段修完重走、第 7 階段收尾）
0. **（試點中，10-18 起每一次有套件的重走都照做，驗收見 `ACCEPTANCE.md`）主 session 派工前**跑 `env.sh <輪次>`，再一次跑完套件：`node ~/.claude/skills/seven-phase/scripts/run_kit.mjs --kit <套件> --round <輪次> --fingerprint '<本包 DOM 指紋選擇器>' [--steps …|--plan plan.json] [--cfg '{"step-x.js":{…}}']`（exit 0 全過／1 有步驟失敗／2 起環境或指紋失敗——2 就先修環境，不派）。任務書附 `<輪次>/run/summary.json` 路徑、stdout 摘要與修片 diff，寫明：**不再用 MCP 逐支重跑套件步驟**；runner 標 PASS 的版面／對比／窄寬步驟仍要看它的截圖（R2-1：腳本過、320 截圖才看到裁切）；截圖一回合並行讀多張。為什麼：14 天 46 次 ui-lane-reviewer，78% 牆鐘是模型回合、中位 102 次幾乎全串行的 request，模型在當 test runner；每支步驟一回合、手改 CFG 又一回合。
1. 讀 runner 結果，失敗步驟與修片碰到的步驟逐步比對上一輪；需要逐幀 sampler、注入、手動操作的才開 MCP 瀏覽器。
2. **套件是下限不是上限**：每條修過的條目照樣讀修片 diff、親手再驗一次，另走至少三個修片沒明顯處理的情境。套件沒涵蓋的新狀態補進套件。為什麼：10-06 lean 版 UI lane 省掉先讀碼，結果漏了 R7——審查型的讀碼是在生假設，套件只省得掉重複摸索。
3. 報告分兩節：「套件重跑」（步驟、與上一輪的差異）與「這輪新驗」。

## 誰能用哪部分
- 第 5 階段重走、第 7 階段收尾走一趟：全部。
- 第 6 階段 UI/UX lane：只給 `env.sh`、`login.js` 與種資料，不給 `step-*.js` 與上一輪結果——這一路要的是獨立視角。
