# Bug 入口

用在回報來的行為 bug：owner 回報、PM 問題單、prod 異常、backlog 條目。實作或審查途中發現的 bug 照 SKILL.md「修片進場門檻」；註解、文件、測試修復和單獨的非 bug 調整照一般分流。

**prod 現在正在損失資料或錢、18+ 內容或權限正在外洩，或有救不回的期限**（例：軟刪的資料幾天後硬刪）→ 走下面的止血軌，以下統稱損失；§1–§3 途中才發現也切過去。

其餘直接 §1：先建 `.claude/PRPs/plans/<slug>/PROGRESS.md`，frontmatter 寫 `status: doing`、`phase: bug 診斷`、`next`，開頭一行「分流：待診斷」。

## 止血軌
目標只有一個：讓損失停止。完整修復是下一包。沿用當前模型。

1. **定位**：找出造成損失的點，以及讓損失變得救不回的點（配方見 §1「發生點」）。
2. **確認**：一則 AskUserQuestion 請 owner 確認走這條，同時核准定損要跑的 prod 查詢（附確切查詢與手上的證據）。
3. **開樹與定損**：`git fetch` 後 `git worktree add .claude/worktrees/hotfix-<slug> -b hotfix/<slug> origin/master`；基底用 prod 跑的版本，因為 dev 常有還沒驗收的功能。PROGRESS.md 建在這棵樹，frontmatter 同上再加 `worktree: hotfix-<slug>`。`## 止血` 寫正在損失什麼、多少、為什麼還在擴大、期限，每項附查詢或 log。
4. **只停用或還原**：關排程、關旗標、revert 程式或設定。上面兩種點都要停。救回資料屬於修復包。要加新邏輯才止得住就停下問 owner。
5. **紅→綠**：測試釘住「該停的停了、該繼續跑的還在跑」。停用點標 `WHY[<slug>]`，恢復時搜得到。
6. **2 路短審查，commit 前跑完**：直接派兩顆 Agent，不走 Workflow 與 `reviewcov.py`。`lane-reviewer` 看影響範圍（停掉之後誰連帶壞），`silent-failure-hunter` 看損失有沒有完全停、有沒有別條路徑。材料用 `mkpatch.py`＋`mkworktree.py`，都帶 `--root <這棵樹>`。任務書用 `assets/review-brief.md`，真相源改填 `## 止血`。這裡只修「損失沒止住」和「帶出新損失」兩類，其餘留給修復包。發現與處置寫進 `## 止血`。
7. **交付並停**：碰到的層跑 targeted＋build。回報 owner diff 大小、審查結果、部署要點、部署後用哪條查詢確認損失停了。commit、合併、部署都等 owner 說；這個 commit 只含程式碼與規則文件，PROGRESS 留到修復包的 `docs(prps)` commit。
8. **接修復**：owner 說繼續才開。BACKLOG「重要」記一條「恢復被停用的東西」，在這時問模型，再從 §1 做，沿用同一個 slug、這棵樹與這份 PROGRESS。修復包的審查任務書附上止血 commit 的 diff，照修復包的規模跑完整路數，流出分析也涵蓋這次事故。

## 1. 定位與重現
- **發生點**：graphify＋grep 取聯集（配方見 `phases/lean.md` §1）。
- **會紅的指令**：照 SKILL.md「修片進場門檻」第 1 項。症狀在資料上（被改錯、被刪）可以用唯讀查詢，prod 先問 owner；症狀在畫面上就要紅在畫面這一層；LLM 輸出型用探針（`references/project-deepweave.md`）。
- **幅度與範圍**：多常發生、誰碰得到、從何時開始。回報裡的數字和根因都是待否證的假設。

完成判準：指令與紅的輸出存進 `tmp/<slug>/`。試過 3 種不同的重現方式（環境、帳號狀態、時序）仍不紅，就停在這裡：
- 條目 `@hash` 之後有相關 commit，程式碼也對得上 → 回報 owner 已修，刪 BACKLOG 那行，任務結束。
- 其餘 → 寫下試過什麼，向 owner 要能重現的環境或證據。

## 2. 觸發表
照「修片進場門檻」第 2 項，每列用同一種指令跑。

完成判準：每列都有實跑的紅或綠，測不到的列寫理由。

## 3. 假設與根因
列 3–5 個排了序、可推翻的假設，格式「如果原因是 X，改 Y 症狀就會消失」。每個實驗只改一個變數，用探針、突變樹或 `go test -overlay` 做，主 checkout 的正式碼維持原樣。根因已寫好的 backlog 條目先核對那一條，被推翻才列 3–5 個。

- 涉及錢、權限、年齡、保留期：寫政策出處。只有程式碼和照現況寫的測試撐著的交 owner（理由見 `phases/1-research.md` 第 3 步）。
- 3–5 個全被推翻：載入 `mattpocock-skills:diagnosing-bugs`。

完成判準：根因一句話＋程式碼出處＋實驗證據，標明根因／緩解／症狀，而且解釋得了觸發表每一列的紅綠。解釋不了就是還沒找到。

## 4. 分流（這時才判）
PROGRESS.md `## 診斷` 寫齊五項：症狀與會紅的指令、幅度與範圍、觸發表、假設紀錄、根因。照 SKILL.md「分流」三條判，用 AskUserQuestion 問 owner（推薦項第一＋理由）；拿不準推薦完整路。答完把開頭那行改成分流結果，frontmatter 拿掉 `phase`。

- **精簡路**：照 `phases/lean.md`，§1 只補分流理由與 `## 計畫`，§2 的紅用會紅的指令加上放得下的回歸測試。
- **完整路**：`## 診斷` 搬進 RESEARCH.md，補 `phases/1-research.md` 第 3、5、6 步（政策出處、挑戰結論、預估規模），階段表第 1 列記「bug 診斷」，照 Checkpoint 協定停，再接第 2 階段。

**一張單多條**（PM 問題單）：整張單一個 slug、一份 PROGRESS，`## 診斷` 每條一小節。每條各跑 §1–§3，同根因合併；重現不了的條目等證據，其他條照走；非 bug 的條目不跑診斷，到 §4 一起判。要拆包照 SKILL.md「拆包每一刀寫明換到什麼」。
