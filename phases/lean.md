# 精簡路

進來的條件見 SKILL.md「分流」。省的是：討論、訪談、RESEARCH／SPEC／PLAN／REVIEW 四份檔、中間的停點。照舊的是：紅先於綠、審查（路數照「依規模分級」，含外審）、收斂、主 session 親跑閘、commit 等 owner 明說、全程規則。

## 1. 定位與前提
- 讀窄找到要改的檔與符號（大範圍派 code-explorer）。要改的行為可能不只一處發生時，先跑 graphify 再 grep、候選取聯集（配方見 `phases/1-research.md` 第 2 步）。backlog 條目或 bug 報告寫的根因與修法是**待否證的假設**：回程式碼核對它還成立（行號會漂、別的包可能已修）。
- 寫 `.claude/PRPs/plans/<slug>/PROGRESS.md`（唯一的任務檔，續接靠它）：
  - 檔頭 frontmatter `status: doing`＋`next`（格式見 SKILL.md「Checkpoint 協定」第 1 條），交付時改成 `status: owner`。
  - 開頭一行「分流：精簡路」＋三條理由各一句。
  - `## 計畫` ≤15 行：改哪些檔／符號、紅測試（測什麼行為、為什麼現在會紅）、閘指令、審查 lane 清單（`references/lanes.md`）、不做清單。
- 完成判準：每條前提都有程式碼出處。任一條不成立、或發現要 owner 取捨 → 走「升級」。

## 2. 紅→綠
- 先寫紅、跑出紅（輸出存 `tmp/<slug>/`），再實作到綠；1–3 檔主 session 直接做，多片才派 tdd-guide（`phases/5-implement.md` 的私有樹與單一 writer 規則照用）。
- 碰到的層各跑一次 targeted 測試＋build（deepweave 的指令與收尾閘見 `references/project-deepweave.md`）。

## 3. 審查（不省）
照 `phases/6-review.md` §1–§4 做：`numstat.py` 確認規模、凍結快照、共用任務書、派 lane（小包表的必開項全開，含 codex；條件項依觸發）。收件照 §5，但去重地圖寫進 PROGRESS.md 的 `## 審查` 表（條目、來源 `<lane>#<n>`、嚴重度、裁決、處置），用 `reviewcov.py tmp/<slug>/review .claude/PRPs/plans/<slug>/PROGRESS.md` 核對涵蓋。

## 4. 收斂
- 要 owner 取捨的條目：批成一則 AskUserQuestion（推薦項第一），不另開 checkpoint。
- 有爭議或 HIGH 以上：派 1 路 `converge-verifier`；其餘主 session 邊修邊確認。修復照紅→綠。
- 全閘照 `phases/7-converge.md` §5（對既存紅基線「零新增」）；耐久結論照全程規則搬進子目錄 `CLAUDE.md`／`.claude/rules/`。

## 5. 交付（唯一的 checkpoint）
PROGRESS.md 補 `## 結果`（紅→綠、審查裁決、閘結果、殘留；backlog 依 SKILL.md「排序」分組寫進 `.claude/PRPs/BACKLOG.md`，這裡只記條數），照 `assets/checkpoint.md` 回報並停。commit 形狀與完整路相同（程式碼一個、`docs(prps)` 一個），等 owner 說。

## 升級
定位時前提不成立、途中碰到重要面、需要 owner 做設計／文案取捨、或正式碼明顯超過約 300 行 → 停下回報（checkpoint），owner 同意後改走完整：從第 3 階段接（方向有疑問則第 2 階段），已寫的紅與實作保留，PROGRESS.md 改成階段表。
