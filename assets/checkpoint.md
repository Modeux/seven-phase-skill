# Checkpoint 回報形狀

**白話回報**（先講結果）：
1. 做了什麼（一兩句）。
2. 發現什麼——最重要的 1–3 件，用 owner 聽得懂的話；數字附來源。
3. 為什麼這樣決定（拍板了什麼、誰拍的）。
4. 接下來幹嘛（下一階段的第一步），**不在這裡問下一階段的問題**。
5. owner 會直接看到的外部變化（警報文案、面板、價格）要單獨點出來。

**/compact 指令**（一段、可直接貼）：
```
/compact <slug> 第 N 階段已完成（未 commit，改動在主 checkout 工作樹）；真相源 .claude/PRPs/plans/<slug>/{SPEC,PLAN,REVIEW,PROGRESS}.md（<REVIEW／PROGRESS 裡有什麼>；過程產物在 tmp/<slug>/…；<仍存在的 worktree 與何時清>）。下一步 owner 說「繼續」→ 第 N+1 階段：<第一步、要派什麼、要問 owner 什麼>。主 session <模型>、全程禁 commit／push、<專案硬規則一兩條>；compact 後先重新載入 seven-phase skill
```

**或 `/clear` 接續**（`/clear` 不花錢、會重載 CLAUDE.md；前提是本階段結論都已寫進 PROGRESS）：
```
繼續 <slug> 第 N+1 階段：先載入 seven-phase skill，讀 .claude/PRPs/plans/<slug>/PROGRESS.md「下一步」
```
worktree session 用 `/clear`，不要退出——退出會連 worktree 分支一起清掉。
