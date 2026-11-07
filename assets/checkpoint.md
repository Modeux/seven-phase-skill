# Checkpoint 回報形狀

**白話回報**（先講結果）：
1. 做了什麼（一兩句）。
2. 發現什麼——最重要的 1–3 件，用 owner 聽得懂的話；數字附來源。
3. 為什麼這樣決定（拍板了什麼、誰拍的）。
4. 接下來幹嘛（下一階段的第一步），**不在這裡問下一階段的問題**。
5. owner 會直接看到的外部變化（警報文案、面板、價格）要單獨點出來。
6. **能不能 `/clear`**：每次都一句話明說。能＝本階段結論都在 PROGRESS、下一步只讀任務文件、沒有背景 agent／外審在跑；不能就說卡在哪（例：片 A 還在跑）。同一段對話裡還掛著別的任務（例：另一個任務等 commit）時，建議先在這段對話收掉它，或之後開另一段對話處理，不要帶進下一階段。owner 不要多個任務的 context 混在同一段對話（10-09）。

**任務頁**（每次 checkpoint、所有階段）：`python3 ~/.claude/skills/seven-phase/scripts/planview.py <slug>`，回報貼它印的那一行（路徑＋各檔計數）。owner 開 `tmp/progress/<slug>.html`：儀表板、左側目錄、章節可收合、表格固定表頭＋逐欄篩選、mermaid 已渲染。頁是 md 原文的渲染、不是另一份文件：改了任務檔就重跑，不符以 md 為準。第 3／4 階段它印「圖閘未過」就還沒到 checkpoint。

**導讀頁**（第 2／3／4 階段 checkpoint；`answer-me-with-html` skill 已裝才做，沒裝回報寫一句「沒裝導讀頁 skill」）：用該 skill 產一頁 3–8 面板——方向決議或 §0 定案（`callout`／`kv`）、SPEC／PLAN 的 mermaid 改成它的 `flow`／`sequence`（節點名與 md 一致）、要 owner 拍板的題用 `ask`（題目與選項逐字同 AskUserQuestion、推薦項第一）、牽涉的實碼用 `src= lines=` 引。頁落 `tmp/<slug>/checkpoint-<N>.html`（CLI 輸出在別處就複製過來），PROGRESS 該階段紀錄記路徑。它是導讀：白話回報六點與 `/compact` 指令照舊，與 md 不符以 md 為準。

**/compact 指令**（一段、可直接貼）：
```
/compact <slug> 第 N 階段已完成（未 commit，改動在主 checkout 工作樹）；真相源 .claude/PRPs/plans/<slug>/{SPEC,PLAN,REVIEW,PROGRESS}.md（<REVIEW／PROGRESS 裡有什麼>；過程產物在 tmp/<slug>/…；<仍存在的 worktree 與何時清>）。下一步 owner 說「繼續」→ 第 N+1 階段：<第一步、要派什麼、要問 owner 什麼>。主 session <模型>、全程禁 commit／push、<專案硬規則一兩條>；compact 後先重新載入 seven-phase skill
```

**或 `/clear` 接續**（`/clear` 不花錢、會重載 CLAUDE.md；前提是本階段結論都已寫進 PROGRESS）：
```
繼續 <slug> 第 N+1 階段：先載入 seven-phase skill，讀 .claude/PRPs/plans/<slug>/PROGRESS.md「下一步」
```
worktree session 用 `/clear`，不要退出——退出會連 worktree 分支一起清掉。
