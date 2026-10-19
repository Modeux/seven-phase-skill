# seven-phase（七階段工作流）

**中文** | [English](README.en.md)

一個給 Claude Code 用的 skill。它把「做一個功能／修一個 bug」拆成七個步驟，**每一步做完都會停下來跟你報告，等你說「繼續」才往下走**。

> skill 是什麼？就是一包「說明書」。放進指定資料夾後，Claude 遇到相關的事會自己翻出來照著做。你不用背任何指令，用平常講話的方式叫它就行。

## 為什麼要用它

讓 AI 一口氣從頭做到尾，常見的下場是：做了半天才發現一開始方向就錯了，或是程式碼都存進去了你才第一次看到。這個 skill 讓方向盤一直在你手上：

- **每一步都停**：AI 先說它做了什麼、為什麼這樣決定、下一步要幹嘛。你說「繼續」它才動。
- **先想清楚，再寫程式**：查現況、跟你討論、問清楚細節、寫計畫，這四步你都點頭了，才開始寫程式。
- **先寫測試，再寫程式**：先寫一個「現在會失敗」的測試，再寫讓它通過的程式。
- **多人審查**：寫完後找好幾個獨立的審查員，各看一個面向（寫法、安全、測試、註解有沒有騙人……），最後把所有意見整理好、一條一條決定要不要改。
- **你沒說，它不存檔進版本紀錄（commit）、也不上傳（push）。**

---

## 怎麼安裝（照著做就好）

### 第 0 步：先確認電腦上有這些

| 東西 | 一定要嗎 | 怎麼確認 |
|---|---|---|
| Claude Code | 一定要 | 終端機打 `claude --version` 有出現版本號 |
| git | 一定要 | 打 `git --version` 有出現版本號 |
| Python 3 | 一定要（小工具都是 Python 寫的） | 打 `python3 --version` 有出現版本號 |
| Node.js | 不一定（只有「瀏覽器自動走一趟」會用到） | 打 `node --version` |
| Docker | 不一定（只有專案的整合測試要用） | 打 `docker --version` |
| codex／grok／agy | 不一定（外部 AI 審查員，沒裝就跳過、改找別人代班） | 打 `codex --version` 等 |

### 第 1 步：把 skill 下載到對的位置

打開終端機，貼上這一行：

```bash
git clone git@github.com:Modeux/seven-phase-skill.git ~/.claude/skills/seven-phase
```

意思是「把這包說明書下載到 `~/.claude/skills/seven-phase` 這個資料夾」。Claude Code 只會去 `~/.claude/skills/` 底下找 skill，**資料夾名字一定要是 `seven-phase`**。

> 這個 repo 是私人的。出現 `Permission denied` 代表這台電腦的 SSH 金鑰還沒加進有權限的 GitHub 帳號，先去 GitHub 的 Settings → SSH keys 加好再重試。

### 第 2 步：確認 Claude 看得到它

1. 把 Claude Code 關掉再重開。
2. 在輸入框打一個 `/`，清單裡出現 `seven-phase` 就成功了。

看不到的話，十之八九是資料夾放錯：打 `ls ~/.claude/skills/seven-phase/SKILL.md`，要能看到這個檔案。

### 第 3 步：把「審查員」也搬過來（換新電腦時一定要做）

這套流程會派一群有名字的小幫手去寫測試、做審查（例如 `tdd-guide`、`lane-reviewer`、`converge-verifier`、`security-reviewer`）。**它們的設定檔不在這個 repo 裡**，而是放在 `~/.claude/agents/`。

- 同一台電腦：不用管，已經在了。
- 換新電腦：把舊電腦的 `~/.claude/agents/` 整個資料夾複製到新電腦同一個位置。漏了這步，派工時會出現「找不到這個 agent」。

### 第 4 步（可選）：每次開 Claude 自動顯示進度看板

想要每次開 Claude Code 都先看到「哪些任務做到一半、哪些等部署」，就打開 `~/.claude/settings.json`，在 `"hooks"` 裡加這一段（已經有 `"hooks"` 就只加 `SessionStart` 那塊）：

```json
"hooks": {
  "SessionStart": [
    {
      "matcher": "startup|clear|compact",
      "hooks": [
        {
          "type": "command",
          "command": "python3 ~/.claude/skills/seven-phase/scripts/board.py --hook session-start",
          "timeout": 15
        }
      ]
    }
  ]
}
```

不加也沒關係，隨時跟 Claude 說「進度」它一樣會跑看板。

### 第 5 步（可選）：用在別的專案

`references/project-deepweave.md` 是 deepweave 專用的設定（怎麼跑測試、哪些紅燈本來就紅）。用在別的專案時：

1. 照它的樣子寫一份 `references/project-<你的專案名>.md`，寫清楚「測試怎麼跑、要過哪些檢查」。
2. 在 `SKILL.md` 搜尋 `project-deepweave.md`，旁邊補一句「在 <你的專案> 時讀 `project-<你的專案名>.md`」。

### 之後怎麼更新

```bash
cd ~/.claude/skills/seven-phase && git pull
```

---

## 怎麼使用

裝好之後，在 Claude Code 裡用平常講話的方式說：

| 你說 | 會發生什麼 |
|---|---|
| 「這個功能走七階段：……」 | 開始一個新任務。它會先問你要用哪個模型、走完整版還是精簡版 |
| 「繼續」／「下一階段」 | 你看完報告，放行到下一步 |
| 「繼續 <任務名>」 | 接著做之前做到一半的任務（換了對話視窗也接得回來） |
| 「進度」 | 看所有任務的看板 |
| 「檢討」 | 回頭看最近一批任務的品質、花費、速度，最多提 3 個改進給你挑 |

所有過程都記在專案裡的 `.claude/PRPs/plans/<任務名>/`。就算關掉視窗、或對話太長被壓縮，Claude 讀那裡的 `PROGRESS.md` 就知道做到哪了。

## 七個步驟

| 步驟 | 做什麼 | 產出 |
|---|---|---|
| 1 研究 | 看現在的程式長怎樣、找相關的檔案、挑戰「這個需求本身合不合理」 | `RESEARCH.md` |
| 2 討論 | 跟你確認大方向，在碰細節之前就抓出「方向錯了」 | `SPEC.md` 開頭的方向決議 |
| 3 訪談 | 一題一題問清楚細節和取捨 | `SPEC.md` 完整規格 |
| 4 計畫 | 寫實作計畫；大任務會先找其他 AI 審過計畫。你點頭前一行正式程式都不寫 | `PLAN.md` |
| 5 實作 | 失敗的測試 → 讓它通過 → 整理，一小塊一小塊做 | 程式碼 |
| 6 審查 | 好幾個審查員各看一面，把意見去掉重複、整理成一張表 | `REVIEW.md` |
| 7 收尾 | 一條一條決定審查意見、修好、跑完所有測試和檢查、做總結 | 決定表、總結 |

**小任務可以走精簡版**：三個條件都成立才行——怎麼修已經很清楚、不碰錢／權限／敏感內容、大約 300 行以內。精簡版省掉討論、訪談和中間的停點，**但審查一條都不省**。

## 資料夾裡有什麼

```
SKILL.md       入口：開工步驟、怎麼分流、停點規矩、全程規則、誰負責什麼
ACCEPTANCE.md  流程改動的驗收清單（改了什麼、何時驗、怎樣算有效）
phases/        每一步各一份做法（1-research.md … 7-converge.md），精簡版 lean.md，檢討 retro.md
retros/        每次檢討的報告
references/    用到才讀：審查員怎麼配、外部審查、每條規則的由來、專案專用設定
assets/        範本：審查任務書、修復任務包、停點報告格式、瀏覽器走一趟套件
scripts/       小工具（看板、隔離工作區、審查覆蓋檢查、花費統計……）
evals/         測試這個 skill 本身的案例
```

常用小工具（除了 `run_kit.mjs` 要 Node，其他都只用 Python 內建功能，不用另外裝套件）：

| 工具 | 做什麼 |
|---|---|
| `board.py` | 掃過所有任務，印出進度看板 |
| `mkpatch.py`／`mkworktree.py`／`checktree.py` | 把還沒存的改動拍一張快照，開一個隔離的工作區給審查員用，事後確認沒被動過 |
| `merge_back.py` | 把修復工作區的改動搬回主工作區 |
| `numstat.py` | 算這次改了多少行程式／測試／文件，決定要派多少審查員 |
| `extract.py` | 從文件原封不動摘段落進任務書（附出處） |
| `reviewcov.py` | 確認每個審查員都交件、意見表沒漏 |
| `run_external.py` | 跑外部 AI 審查（codex／grok／agy） |
| `run_kit.mjs` | 在瀏覽器裡一口氣跑完「走一趟」的所有步驟、存截圖 |
| `dockerslot.py` | 吃 Docker 的指令排隊（同時最多 N 條） |
| `agentcost.py`／`taskcost.py`／`laneyield.py`／`claudemdcost.py` | 算花了多少錢：每個小幫手、每個任務、每路審查買到什麼 |
| `wallclock.py` | 時間都花在哪 |

## 檢討與驗收

- 說「檢討」：看最近一批任務做得好不好、花多少、多快，並結清以前流程改動的驗收。不是每次都要跑——上次檢討後累積 5 個任務，看板會提醒你。
- `ACCEPTANCE.md`：每改一次流程就記一行「什麼時候、用什麼方法、怎樣算有效」，到期了看板會列出來。
- 看板也會列出過大的說明檔（超過 40KB，或比上次檢討長 20% 以上）。

## 想改規則之前

每條規則都是踩過坑換來的。覺得哪條多餘，先看 `references/history.md` 它在防什麼。改完在那張表補一行，在這個資料夾 commit；要備份到 GitHub 再 `git push`。
