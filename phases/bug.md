# Bug 入口

用在回報來的行為 bug：owner 回報、PM 問題單、prod 異常、backlog 條目。實作或審查途中發現的 bug 照 SKILL.md「修片進場門檻」；註解、文件、測試修復和單獨的非 bug 調整照一般分流。

先判要不要止血。符合任一條就走止血軌，§1–§3 途中才發現也切過去，以下統稱損失：
- 查詢或 log 顯示還在增加：資料遺失、錯扣錯發、18+ 內容或權限外洩。
- 全站或主流程不可用。
- 不可逆的期限早於正常修復能完成的時間（例：軟刪的資料幾天後硬刪，修復來不及）。

其餘直接 §1：先建 `.claude/PRPs/plans/<slug>/PROGRESS.md`，frontmatter 寫 `status: doing`、`phase: bug 診斷`、`next`，開頭一行「分流：待診斷」。

## 止血軌
目標只有一個：讓損失停止。修復是另一個任務。沿用當前模型。

1. **開樹**：`git -C <主 checkout> fetch origin`，再 `git -C <主 checkout> worktree add <主 checkout>/.claude/worktrees/hotfix-<slug> -b hotfix/<slug> origin/master`；dev 常有還沒驗收的功能。PROGRESS.md 只建在這棵樹：`status: doing`、`phase: 止血`、`worktree: hotfix-<slug>`、`next`。
2. **定位**：在這棵樹找出所有造成損失的路徑，含已排程、已進佇列、其他 binary，以及讓損失變得救不回的點。配方同 §1「發生點」。
3. **確認**：一則 AskUserQuestion 請 owner 確認走這條，附手上的證據和要加的開關，同時核准兩條 prod 查詢：定損查詢，以及 prod 實跑的 SHA（主機上 `go version -m /proc/<pid>/exe` 的 `vcs.revision`）。SHA 不等於這棵樹的 HEAD 時講明差異，由 owner 選：樹改開在 prod 的 SHA，或接受差異一起上線。
4. **定損**：`## 止血` 寫正在損失什麼、多少、為什麼還在擴大、期限，每項附查詢或 log。
5. **只停用或還原**：關排程、關旗標、revert 程式或設定，或加一個只讓出事那段不跑的開關。第 2 步找到的路徑都要停；已進佇列的交 owner 決定怎麼處理。改變原本行為、救回資料都屬於修復任務；其他新邏輯要先問 owner。
6. **紅→綠**：測試釘住「該停的停了、該繼續跑的還在跑」。停用點標 `WHY[<slug>]`，恢復時搜得到。
7. **2 路審查，commit 前過閘**：
   - 快照：`python3 ~/.claude/skills/seven-phase/scripts/mkpatch.py --root <樹> --out tmp/<slug>/review`，再 `python3 ~/.claude/skills/seven-phase/scripts/mkworktree.py --root <樹> --path tmp/<slug>/review/wt-snap --patch tmp/<slug>/review/change.patch --baseline tmp/<slug>/review/baseline.sha256`。
   - 派兩顆 `lane-reviewer`，派工單 `tmp/<slug>/review/tasks/{reach,impact}.md`，報告 `tmp/<slug>/review/lane-{reach,impact}.md`。`reach`：還有哪條路徑會繼續造成損失。`impact`：停掉之後誰連帶壞、該繼續跑的有沒有被一起停。任務書用 `assets/review-brief.md`，SPEC／PLAN 節改填 `## 止血`。
   - 去重地圖寫進 PROGRESS.md 的 `## 審查` 表（形狀見 `phases/lean.md` §3），`reviewcov.py tmp/<slug>/review <PROGRESS.md>` exit 0 才能 commit。
   - 這裡只修「損失沒止住」和「帶出新損失」兩類，其餘留給修復任務。再改碼就回到本步重審。
8. **交付並停**：碰到的層跑 targeted＋build。主 checkout 的 `.claude/PRPs/BACKLOG.md`「重要」現在就寫一條：恢復被停用的東西、修復、補完整審查，附 `WHY[<slug>]`。照 Checkpoint 協定停，`status: owner`，`next` 寫等 owner 說 commit。回報 diff 大小、審查結果、部署要點、部署後用哪條查詢確認損失停了。
   - commit、合併 master、部署、合回 dev 都等 owner 說；合回 dev 也要做，因為測試機跑 dev。
   - commit 形狀照全程規則兩個，程式碼那個的標題含「止血」。commit 完 `status: deploy`，`next` 寫部署要點與確認查詢。
9. **修復是新任務**：owner 另開時照開工第一步問模型，用新 slug；分支基底看修復要不要單獨上線。根因已知就把 `## 止血` 當 §1–§3 的起點，只補缺的；症狀已被止血擋掉時，用定損紀錄當證據，不必再重現。審查任務書附上止血 commit 的 diff，照修復的規模跑完整路數，流出分析涵蓋這次事故。

## 1. 定位與重現
- **發生點**：從 repo 根跑 `graphify query "<行為關鍵詞>" --context call --budget 1500`（讀 `Start:`）、`graphify affected "<Func()>" --relation calls`，再 grep 取聯集；在 worktree 裡加 `--graph <主 checkout>/graphify-out/graph.json`。
- **會紅的指令**：照 SKILL.md「修片進場門檻」第 1 項，用斷言得到這個症狀的最低一層（數得到請求次數就用 spec），到不了才用真引擎。症狀在資料上可以用唯讀查詢，prod 先問 owner；LLM 輸出型用探針（`references/project-deepweave.md`）。
- **幅度與範圍**：多常發生、誰碰得到、從何時開始。回報裡的數字和根因都是待否證的假設。

完成判準：指令與紅的輸出存進 `tmp/<slug>/`。試過 3 種不同的重現方式（環境、帳號狀態、時序）仍不紅，就停在這裡：
- 條目 `@hash` 之後有相關 commit，而且同一條指令在該 commit 之前的版本紅、現在綠 → 回報 owner 已修，刪 BACKLOG 那行，`status: done`。
- 其餘 → 寫下試過什麼，向 owner 要能重現的環境或證據；要不要結案由 owner 定。

## 2. 觸發表
照「修片進場門檻」第 2 項，每列用同一種指令跑，再加一欄「修後應有的結果」。

完成判準：每列都有實跑的現況和修後預期，測不到的列寫理由。

## 3. 假設與根因
先驗最可能的假設（backlog 寫好的根因就是它），格式「如果原因是 X，改 Y 症狀就會消失」。會紅的指令轉綠、觸發表每列都符合修後預期，一個假設就夠。否則列 3–5 個排了序、可推翻的假設，每個實驗只改一個變數。實驗用探針、突變樹或 `go test -overlay`，主 checkout 的正式碼維持原樣。

- 涉及錢、權限、年齡、保留期：寫政策出處。只有程式碼和照現況寫的測試撐著的交 owner（理由見 `phases/1-research.md` 第 3 步）。
- 全被推翻：寫下排除了什麼，交 owner（要環境、證據或停損）。

完成判準：根因一句話＋程式碼出處＋實驗證據，標明根因／緩解／症狀，而且解釋得了觸發表每一列。解釋不了就是還沒找到。

## 4. 分流（這時才判）
PROGRESS.md `## 診斷` 寫齊五項：症狀與會紅的指令、幅度與範圍、觸發表、假設紀錄、根因。照 SKILL.md「分流」三條判，用 AskUserQuestion 問 owner（推薦項第一＋理由）；拿不準推薦完整路。只有一題取捨就在這則一起問，答完算修法已定。答完把開頭那行改成分流結果，frontmatter 拿掉 `phase`。

兩條路都一樣：首版修完，重跑會紅的指令與整張觸發表，寫下修後預期與實際，不符就處理完才進審查。

- **精簡路**：照 `phases/lean.md`，§1 只補分流理由與 `## 計畫`，§2 的紅用會紅的指令加上放得下的回歸測試。
- **完整路**：`## 診斷` 搬進 RESEARCH.md，補 `phases/1-research.md` 第 3、5、6 步（政策出處、挑戰結論、預估規模）；觸發表進 PLAN 的驗收項。階段表第 1 列記「bug 診斷」，照 Checkpoint 協定停，再接第 2 階段。

**一張單多條**（PM 問題單）：pm-acceptance 已判成非 bug 的條目不進來。整張單一個 slug、一份 PROGRESS，`## 診斷` 每條一小節。每條各跑 §1–§3，同根因合併；重現不了的條目等證據，其他條照走；其餘非 bug 條目不跑診斷，到 §4 一起判。要拆包照 SKILL.md「拆包每一刀寫明換到什麼」。
