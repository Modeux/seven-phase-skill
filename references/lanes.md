# 審查與驗證的路數

## 為什麼要三軸
- **關注面 lane**（general-purpose＋自寫 prompt：規格忠實度、影響範圍、時間與順序…）打的是你指得到的方向。
- **語言／領域專門 agent**（go-reviewer、python-reviewer、typescript-reviewer、security-reviewer、silent-failure-hunter、pr-test-analyzer、comment-analyzer）帶內建清單，會去查你沒想到的 stdlib 邊界——不能用 general-purpose＋好 prompt 取代；給它們 invariants 當前置閱讀，不覆蓋它們的方法論。
- **異源外審**（codex、grok、agy）的價值在於**不知道**你的設計意圖；codex 常抓到多個 Claude reviewer 一起漏的東西。
- 同源模型會一起瞎，但瞎的軸不同——所以內審路數不因「有外審」而省，反之亦然。

## 配路教訓
- 先問這輪的不確定性在**推理**還是**事實**。推理型（跨層可達性、時序、失敗路徑）→ 異源外審值錢；事實型（版面、對比度、cascade 誰勝出、斷言守不守得住）→ 預算放在能**執行**的 lane（突變、真瀏覽器），prompt 寫明「數字必須你自己跑出來，我給的是待否證的假設」。材料完整內聯、推理鏈短時，外審常與內審完全重疊。
- 替補或加 lane 時先問「現有各路的鏡頭清單缺哪一個」，不是「誰比較強」。常被漏的鏡頭：**錯誤／失敗路徑**、**時間與順序**、**使用者的位置（焦點／捲動）**。
- 分工審查會系統性漏掉接縫（每條 lane 都在自己的邊界內看到「這一半是對的」）→ 一定留一路**沒有分派邊界**的視角（通常是 codex），升級嚴重度的依據是它多追的那一步事實，不是語氣。
- agent 名字描述專長不是能力：寫完 lane prompt 回頭問「這份任務書要求的動作，那個 agent type 做得到嗎」（a11y-architect 沒有 Bash、跑不了瀏覽器）。

## 小包（正式碼 ≤1500 行，且不碰 credits／權限／跨層不變式）：6–8 路
| lane | agent | 何時 |
|---|---|---|
| 每個碰到的層一個語言 reviewer | go-／python-／typescript-reviewer | 必開 |
| 註解正確性 | comment-analyzer（沒有 Write／Bash：主落檔） | 必開 |
| 測試品質＋突變 | pr-test-analyzer（突變樹唯一 writer） | 必開 |
| 靜默失敗 | silent-failure-hunter | 必開 |
| 規格忠實度＋case walk | general-purpose | 必開 |
| 異源外審 | codex（gpt-6-sol xhigh） | 必開 |
| 安全／隱私 | security-reviewer | 碰使用者輸入、log／錯誤外洩、權限時 |
| UI/UX | general-purpose＋瀏覽器截圖 | 涉 UI 時；驗不了的標「需人工目視」 |

## 大包或高風險包：完整路數
小包清單全開，再加：security-reviewer（必開）、影響範圍 lane（所有 caller／呼叫點實讀第三方碼）、時間與順序 lane（預算鏈、取消語義、視窗邊界、混版時序）、grok、agy；註解 lane 可拆成程式碼註解與文件兩路。任一路跑不了就換一個不同鏡頭替補。

## 收斂驗證
- 只驗「有爭議／HIGH 以上／需 owner 拍板」的條目：小包 1 路、大包依條目量 2 路起（同層的條目放同一路，例：偵測面、追蹤、單一服務、時間預算）。
- agent：general-purpose（xhigh，需要 Write 寫報告）。
- 其餘條目由修復者邊修邊確認。

## 實作與修復
- 切片實作：tdd-guide（xhigh），每片一棵私有樹。
- 文件片：general-purpose（有判斷時）或 doc-updater（機械改寫，high）。
- 探索／定位：code-explorer（high）；判斷性探索用 general-purpose／Explore。
- 組材料包、批次文字改寫：quick-task（high）。

## 派工前檢查
- agent 的工具集（有沒有 Write／Bash）。
- 共用任務書已寫明每棵可寫樹的唯一 writer。
- 並行 agent 的交付檔名帶 lane／片號。
- 共用一份沒實測過的處方且 >5 顆 → 先一顆探針。

## 成本參考（2026-09-28，正式碼約 1000 行的一包）
13 路審查＋5 路收斂驗證（每路 24–30 萬 token）＋4 片修復（約 100 萬）。13 條 MEDIUM 有 7 條是註解／文件——這是依規模分級的由來。
