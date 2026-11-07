# 走一趟套件（walk kit）

第一次真瀏覽器走一趟的 `ui-lane-reviewer` 交件時一併留下，放 `<證據目錄>/kit/`。兩個目的：重走不必重新摸環境與頁面；同一步驟前後在同一個位置量，前後才比得起來。

## 內容
- `README.md`：步驟表（編號、對應 SPEC 條目、腳本檔、通過條件、這輪結果）、用到的帳號與種資料、已知不穩定點。
- `env.sh`：設好變數、呼叫 `scripts/walk_env.sh`，後面只加這包的種資料（下節）。只做事、不判斷。
- `login.js`：`async (page, walk) => {...}`，登入並回傳帳號（下節）。`run_kit.mjs` 登入後把 session 存成 `<輪次>/run/storage-state.json`，agent 自寫的 node 探針用 `launch()` 自動沿用、不再各自登入。
- `step-<n>.js`：每步一支 `async (page, walk) => {...}`：操作、量 DOM（附本包獨有的 DOM 指紋）、`walk.shot()` 截圖（截要判的元素或 `clip` 範圍；整頁只在判版面時用——圖的 token 跟面積成正比、開過就一直留在 agent 的 context）、回傳量測 JSON。捲動位置、視窗寬、主題寫死在腳本裡。探索與重走都用 `scripts/run_kit.mjs`（探索加 `--steps <這支>`；`browser_run_code_unsafe` 沒有 `walk` 參數，帶 `walk` 的步驟不能丟給它）。
  - **runner 相容**：每輪會變的參數集中在**單行** `const CFG = { … };`（runner 在這行後面注入覆寫；寫成多行就只能跑預設值）；回傳物件帶 `ok: true|false`（沒帶的步驟 runner 只能標 DONE，等於沒判）；要 Node 能力（SQL、檔案）就用 `walk` 提供的，步驟裡不靠 `require`／Node 全域。
  - **逾時**：宣告 `walk` 的步驟和 `launch()` 開的頁面，找不到元素 10 秒就失敗（`--action-timeout` 可調）。真的要等久的（生成、媒體）明寫 `{ timeout }`；只有「不存在」本身就是要量的東西才用 `.catch`，也要明寫短 timeout——軟等待吞掉逾時會讓結果靜默翻轉。為什麼：11-04～11-06 共 13 次約 18% 的腳本執行失敗，等它們佔牆鐘約 8%，舊的 30 秒預設讓每次失敗要等滿。沒宣告 `walk` 的舊步驟維持 30 秒。
  - **焦點探針（每支 step 必帶）**：每個走到的狀態呼叫一次 `walk.focusProbe(state, enter)`，結果放進回傳的 `focusDrops`、非空就 `ok: false`；報告列出每顆的可讀名稱與狀態，`navigated: true`（按了換頁）的另外判。`enter` 是把頁面帶回這個狀態的 async 函式：每按一顆都會改變狀態，所以每顆前都重進。它對每顆可見、可按的按鈕先重進該狀態、按 Enter、等兩幀（讓 Angular 先渲染），`document.activeElement` 是 `<body>` 或 null 就記一筆。為什麼：「按下即自我移除、焦點掉到 body」在 explore-revamp 包 2 已是第七個面再現（REVIEW「包 2 流出分析」R9），逐面靠人想到要按太晚。
    - **斷點預設帶上**（`walk.cfg.breakpoints`，deepweave：`[840, 1180]`）：每顆按鈕再各測一次「焦點停在它身上（不按），視窗拉到每個切換點的另一側」，掉到 body 的記一筆帶 `crossed`。報告把 `crossed` 與 `navigated` 分開判：按鈕在另一側版面本來就不存在、又沒有合理的接手者，可以判接受，但要寫理由。為什麼：explore-revamp 包 3 首頁橫欄箭頭在視窗跨 840px 時整組拿掉、焦點掉到 body（平板轉向就會碰到）；探針只按 Enter、不改寬度，第 5 階段沒抓到、記成已知限制，第 6 階段才收（REVIEW「包 3 流出分析」R2）。
- `checks.sql`：每步要對的 DB 查詢，只 SELECT。
- `repro-<條目>.js`（任何一趟回報「中」以上的行為 bug 時都要附）：runner 相容、會在回報的那個症狀上回 `ok: false` 的重現步驟，交件前用 `run_kit.mjs --steps` 跑過一次；另附觸發表草稿（會影響同一塊的狀態×動作、在途×另一動作、0／>0、失敗、到底），每列一支步驟或寫「套件測不到＋理由」。這是修片的會紅指令與觸發表（SKILL.md「修片進場門檻」），主 session 審過才派修片。

## 函式庫（不要重寫）
起環境、登入、量測都已經有了：`scripts/walk_env.sh`、`scripts/walk_lib.mjs`、`scripts/run_kit.mjs`。第二支步驟也要用的東西加進套件，不要各留一份。為什麼：11-04～11-06 共 13 次，`login.js` 寫了 23 次、`lib.mjs` 11 次、`env.sh` 9 次，同一次內的腳本行有三分之一是重複的，還各自做了 `prelude.js`＋`build.py`、`drive.mjs` 繞開 runner 的限制。

```bash
#!/bin/bash
# env.sh <輪次> [stop|dropdb]：port 被別棵樹佔用會 exit 2；印出 server URL、哪棵樹、啟動時間，並寫 kit/walk.config.json
export TREE=<樹> WALK=<證據目錄> DB=deepweave_walk_<slug> GW_PORT=75xx FE_PORT=43xx   # 要後台再加 ADMIN_PORT
~/.claude/skills/seven-phase/scripts/walk_env.sh "$@" || exit $?
# 這包的種資料寫在這裡
```
```js
// login.js
async (page, walk) => {
  const CFG = { email: 'creator.demo@deepweave.local' };
  const auth = await walk.signIn(CFG.email);
  await walk.go('/explore', 'app-root');
  return { ok: true, email: CFG.email, id: auth.user.id, url: page.url() };
}
```
```js
// 自己的 node 探針：開頭這樣就有瀏覽器、登入狀態與量測
import { launch } from '/Users/huntwu/.claude/skills/seven-phase/scripts/walk_lib.mjs';
const { page, walk, close } = await launch({ kit: '<套件>', round: 'r1' });  // 另可帶 engine、device、viewport
```
探針與 `run_kit.mjs` 都用 Bash `run_in_background: true` 背景跑（結束會收到 `<task-notification>`），邊跑邊寫下一支或讀已回來的結果；不輪詢（`for … sleep`、`until grep`、`pgrep`）、不前景掛長 `timeout`；唯讀探針同時最多 3 支，會寫（API 寫入、SELECT 以外的 SQL、`env.sh`、種資料）的單獨跑。為什麼：11-06～11-07 兩次乾淨 run，探針每支 1–2 分、一回合只跑一支，加上輪詢迴圈，串行等待佔牆鐘約四成。

| `walk` 上的呼叫 | 做什麼 |
|---|---|
| `signIn(email)`、`api(path, { method, data })` | 走 API 登入、下次載入前寫好 session；用同一個 token 打 gateway |
| `go(path, selector?)` | 先等上一頁的請求結束再換頁（中途離開會把 gateway 連線池拖垮，第一個症狀是空白頁 20 秒） |
| `sql(query)` | 打 `walk.config.json` 的資料庫；步驟只下 SELECT |
| `shot(name, target?)` | 存到輪次目錄。`target`：選擇器、Locator、`{ clip }`、`{ fullPage: true }`，不帶是視窗 |
| `rect(target)`、`style(target, props)` | 位置大小（找不到回 null、不等待）；computed style |
| `overflow()` | 頁面有沒有橫向溢出、最外層是哪些元素（自己捲動的橫欄與關著的抽屜不算） |
| `hit(target)` | 點元素中心點得到它嗎，點不到是誰蓋住 |
| `focusProbe(state, enter, breakpoints?)` | 見上面的焦點探針 |
| `settle()`、`errors`、`cfg` | 等兩幀；console error 與 pageerror；設定值 |
| `at(widths, fn, { height })` | 依序切每個寬度、`settle` 後跑 `fn(width)`，回 `[{ width, result }]`，結束一定還原視窗。`fn` 丟錯時該列是 `{ width, error }`，後面照跑，**step 要自己檢查 `error`**。`at(fn)` 用每個斷點 ±1 加原寬度。空清單或非正整數直接丟錯 |
| `tabOrder({ from, max, shift })` | 連按 Tab，回 `{ start, stops: [{ i, el, name, visible, rect }], invisible, ariaHidden }` 加結束原因（下表）。`from` 不算停點，找不到、多於一個或拿不到焦點都丟錯；`invisible`、`ariaHidden` 是有問題的停點 index；`shift` 要帶 `from` |
| `net({ match })` | 從現在開始記錄，回控制物件：`list(match?)` 回 `[{ method, url, status, ms, failed, pending }]`；`count(match?)` 含未回應和失敗；`wait(match, { timeout })` 回呼叫後的下一筆回應，**要在動作之前呼叫**，`.response` 可取 body；`stop()` 移除監聽。`match` 可寫 `'/x'`、`'POST /x'`、regex 或 `(row) => bool`。500 算回應（`failed: false`）；請求失敗不提早丟錯，逾時訊息會帶原因 |
| `scroll(target, to)` | `to` 是 `'top'`、`'bottom'` 或數字時先捲再 `settle`，省略只量；`target` 省略或 `null` 量 document。回 `{ found, scrollable, overflowY, top, height, client, max, atTop, atBottom }`。找不到是 `found: false`、不等待；不可捲動是 `scrollable: false`、`atTop`／`atBottom` 為 `null`，不要當成 true |

`tabOrder` 的結束原因：`leftDocument`（走過文件結尾，正常）、`droppedToBody`（焦點掉到 body，疑似 bug，到此結束）、`cycled`＋`cycledTo`（回到第幾個停點）、`stuck`（Tab 沒移動焦點）、`truncated`（碰到 `max`）。

帳號與基本資料用固定角色，不要再手寫：`python3 ~/.claude/skills/seven-phase/scripts/dbq.py --fixture all --db deepweave_walk_<slug>`（`--list-fixtures` 列出成年創作者、成年、未成年、未回答年齡閘、零點數帳號與一般／18+ 角色；只寫進走查自己的 DB，`deepweave` 一律拒絕）。只有這包獨有的資料才寫 `seed.sql`。

其他引擎與裝置：`run_kit.mjs --engine webkit`、`--device 'iPhone 14'`（觸控、視窗跟著裝置）。有步驟失敗時，stdout 最後一行是可以直接接回去的 `rerun: --steps a,b`。

## 重走怎麼用（第 5 階段修完重走、第 7 階段收尾）
0. **硬條件：主 session 派工前**跑 `env.sh <輪次>`，再一次跑完套件（任務書沒有 `<輪次>/run/summary.json` 路徑就不派；agent 不為這一輪新寫 `runN.mjs`——要加的步驟加進套件，算進同一塊的兩輪停損。為什麼：journey-scene-layer 沒用上 runner，第 9、10 輪各自新寫一套腳本；第 11 輪沿用第十輪腳本，20 分鐘重量完）：`node ~/.claude/skills/seven-phase/scripts/run_kit.mjs --kit <套件> --round <輪次> --fingerprint '<本包 DOM 指紋選擇器>' [--steps …|--plan plan.json] [--cfg '{"step-x.js":{…}}']`（exit 0 全過／1 有步驟失敗／2 起環境或指紋失敗——2 就先修環境，不派）。任務書附 `<輪次>/run/summary.json` 路徑、stdout 摘要與修片 diff 的路徑＋`--stat`（不貼全文，agent 用 `grep -n`／`sed -n` 只讀要的 hunk），寫明：**不再用 MCP 逐支重跑套件步驟**；runner 標 PASS 的版面／對比／窄寬步驟仍要看它的截圖（R2-1：腳本過、320 截圖才看到裁切）；截圖一回合並行讀多張。為什麼：14 天 46 次 ui-lane-reviewer，78% 牆鐘是模型回合、中位 102 次幾乎全串行的 request，模型在當 test runner；每支步驟一回合、手改 CFG 又一回合。
1. 讀 runner 結果，失敗步驟與修片碰到的步驟逐步比對上一輪；需要逐幀 sampler、注入、手動操作的才開 MCP 瀏覽器。
2. **修過的條目逐條親驗**：讀修片 diff、親手再驗一次，連同觸發表與修片碰到的接縫。沒被修片碰到的情境不在中間輪抽樣——抽樣回歸只在 checkpoint 前跑一次（SKILL.md「修→重走的迴圈要停損」）。套件沒涵蓋的新狀態補進套件。為什麼：10-06 lean 版 UI lane 省掉先讀碼，結果漏了 R7——審查型的讀碼是在生假設，套件只省得掉重複摸索。
3. 報告分兩節：「套件重跑」（步驟、與上一輪的差異）與「這輪新驗」。

## 誰能用哪部分
- 第 5 階段重走、第 7 階段收尾走一趟：全部。
- 第 6 階段 UI/UX lane：只給 `env.sh`、`login.js` 與種資料，不給 `step-*.js` 與上一輪結果——這一路要的是獨立視角。函式庫照用：裡面只有工具，沒有步驟與結果。
