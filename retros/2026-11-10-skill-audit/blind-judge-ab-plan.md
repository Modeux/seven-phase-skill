# blind-judge effort A/B（high 對 xhigh）作法與判準

預登記：先寫判準再跑。跑完把結果寫在本檔最後的「結果」，再照「判定」改定義或文件。

## 為什麼要測
- `SKILL.md` 與 `references/lanes.md` 從 10-05 起寫 blind-judge 是 xhigh；`agents/blind-judge.md` 的 frontmatter 是 `effort: high`（10-19 搬進 repo 時就是）。哪一邊是本意查不到紀錄。
- 用量：10-11～11-10 派了 173 次、約 $127（`turns.py --days 30`）。升 xhigh 估每月多 $75–100（用 10-06 reviewer 降檔省 37–44% 反推，未實測）。
- owner 11-10 被問要以哪邊為準時回：「可以AB test看看？」
- A/B 定案前，文件照現況寫 high。

## 兩臂
- A：`blind-judge`（high，現況）。
- B：`ab-blind-judge-xhigh`（本體逐字相同，只改 `name`、`effort` 與 description 開頭的 A/B 警語）。不改原檔：並行 session 會在不知情的情況下跑到被改的設定。

## 材料（12 份）
1. **重放 6 份**（真實任務、沒有標準答案）：過去的材料檔多半已被清掉（173 次派工裡只剩 1 次的路徑還在），從 transcript 還原。
   - 找派工：從 repo 根跑 `python3 ~/.claude/skills/seven-phase/scripts/tx.py list --days 45 --all-worktrees --tool Agent --grep '^blind-judge' --jsonl`。
   - 還原材料：該次派工的 subagent transcript（`~/.claude/projects/<proj>/<sid>/subagents/*.jsonl`）裡第一批 Read 的 tool_result 就是材料包原文；派工訊息本身是任務書。
   - 挑 6 份：不同 session、不同評分標準、每份至少 5 對；只挑文字材料（圖片盲評不重放）。
2. **植入缺陷 6 份**（有標準答案）：從重放材料各取一對，其中一份原樣、另一份複製後植入一個該份評分標準明列的缺陷。明顯、中等、細微各 2 份；位置每對隨機，mapping 另存、任務書明令禁讀。

## 派工
- 每份材料 3 顆：A、A 重跑（雜訊基準）、B，共 36 顆。任務書三顆逐字相同，沿用原派工的評分標準，並寫明「位置每對隨機，整體觀察描述風格類型、不要用 A 組／B 組」。輸出 JSON 寫檔：每對的 winner、各項分數、一句理由。
- **先派 1 份材料的 3 顆當探針**：看 subagent transcript 的 `"effort"` 欄真的是 high／xhigh，輸出檔格式可解析，再派其餘。
- 主 session 的 context 先縮（`/clear` 後開新對話跑）：36 顆要等，別帶著大 context 每回合重付。

## 量什麼
1. 重放一致率：以「對」為單位比 winner。A↔A 重跑＝雜訊；A↔B＝處理效果。
2. 植入缺陷命中：判官選原樣那份為勝、且理由點到植入的缺陷，才算命中。每臂 6 份各幾份。
3. 成本：`python3 ~/.claude/skills/seven-phase/scripts/turns.py --agents <id…>` 每顆的 $ 與回合數。

## 判定
- **改成 xhigh**：B 的命中比 A 多 2 份以上（6 份裡）；或重放裡 A↔B 不一致的對，主 session 逐對讀雙方引文後 B 對的佔多數而且至少 3 對。
- **維持 high**：命中差 ≤1 份，且 A↔B 不一致率不高於 A↔A 重跑的雜訊。
- **其餘**：數字攤給 owner 拍板。
- 定案後：改 `agents/blind-judge.md`（若改 xhigh）、`SKILL.md` 與 `references/lanes.md` 的分級名單拿掉「A/B 待跑」，刪掉 `agents/ab-blind-judge-xhigh.md`，`references/history.md` 加一列，`ACCEPTANCE.md` 那一列移到結案。

## 已知限制
判官與多數被評的輸出都是 Claude 系；植入的缺陷是人造的；樣本小，只回答「看不看得出差別」，不是精確的效應量。媒體類（圖片、影片）的盲評結論不在這次範圍。

## 結果
（未跑）
