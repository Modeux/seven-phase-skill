# 第 1 階段：Research

目標：搞清楚現況，並拿研究發現挑戰任務前提。產出 `RESEARCH.md`（現況摘要＋挑戰結論＋註解與行為不符清單）。

1. **盤點**專案配置的 subagents／skills／MCP，讀各 agent 的職責定義與工具集，供全流程對號起用。
2. **fan-out 平行探索**（職責相符的專案探索 agent 優先；code-explorer 是 high、判斷性探索用 general-purpose／Explore，後兩者 effort 跟主 session）分工：
   - graphify 查詢（定義處、引用點、資料流、依賴）——從 repo 根跑 `graphify query "<行為關鍵詞>" --context call --budget 1500`（不帶 `--context` 會被 `imports_from` 邊淹沒），讀第一行 `Start: [...]` 當候選檔清單；caller 用 `graphify affected "<Func()>" --relation calls`。圖不含新符號、行號會漂，結論回原始碼確認；
   - **graph 與 grep 兩邊都跑、候選取聯集**：RESEARCH.md 的現況摘要列出「行為發生點」清單並標每點來源（graph／grep／兩者）。只有 grep 的探索會漏掉換了用詞的路徑（provider 分支、fallback 組字器、後處理 pass）；
   - 關聯測試與環境設定盤點；可沿用的既有模式；
   - 需要時一路上網佐證：**學術論文／官方文件／規範／權威文章優先**，引用附連結＋一句「它如何支撐設計決策」；找不到可靠來源就明說，不得杜撰。
   - 需要 prod 數據時：先寫好確切查詢，**用 AskUserQuestion 問 owner 能不能跑**（它要回答什麼、答案會怎麼改變結論）；核准後主 session 自己跑，不交 subagent。沒核准就把查詢寫進 RESEARCH，標「待 owner 決定」。
3. 探索**一律以程式碼本體與測試為準**，註解與行為不符處記入現況摘要（後面要一併修）。探索 agent 只回結論、關鍵證據與檔案位置，不回大量原始碼。
4. 主 session 彙整後**用程式碼搜尋抽查交叉驗證**、補足 graph 沒涵蓋處。
5. 套 challenge-thinking：以研究發現挑戰任務前提與 owner 預設做法、找更簡或更貼合現有架構的替代方案、標記發現之間的矛盾與風險。**挑戰結論寫進 RESEARCH.md**，每條留一欄「處置：待討論」。
6. 先在 RESEARCH 標出**預估規模**（正式碼行數量級、是否碰錢／權限／跨層不變式），第 4 階段據此定審查路數。

→ Checkpoint（SKILL.md）。
