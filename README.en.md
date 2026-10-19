# seven-phase

[中文](README.md) | **English**

A skill for Claude Code. It breaks "build a feature / fix a bug" into seven steps, and **after every step it stops, tells you what it did, and waits for you to say "continue"**.

> What's a skill? A bundle of instructions. Drop it into the right folder and Claude picks it up on its own whenever it's relevant. You don't memorize any commands — you just ask in plain words.

> Note: the skill's own instruction files are written in Chinese, and the phrases it listens for are Chinese (see "How to use"). Claude reads them fine either way.

## Why use it

Let an AI run from start to finish in one go, and you often end up finding out the direction was wrong from the start — or seeing the code for the first time after it's already committed. This skill keeps you in the driver's seat:

- **It stops after every step**: the AI says what it did, why, and what's next. It only moves when you say "continue".
- **Think first, code later**: look at the current code, discuss with you, nail down the details, write a plan. Only after you approve all four does it write code.
- **Test first, code second**: it writes a test that fails right now, then the code that makes it pass.
- **Several reviewers**: when the code is done, several independent reviewers each check one angle (style, security, tests, misleading comments…). All their notes are merged and decided one by one.
- **It never commits or pushes unless you say so.**

---

## How to install (just follow along)

### Step 0: Check you have these

| Thing | Required? | How to check |
|---|---|---|
| Claude Code | Yes | Run `claude --version` in a terminal and see a version number |
| git | Yes | `git --version` |
| Python 3 | Yes (all helper scripts are Python) | `python3 --version` |
| Node.js | No (only for the automated browser walk-through) | `node --version` |
| Docker | No (only for your project's integration tests) | `docker --version` |
| codex / grok / agy | No (outside AI reviewers; if missing, they're skipped and another reviewer fills in) | `codex --version` etc. |

### Step 1: Download the skill into the right place

Open a terminal and paste this line:

```bash
git clone git@github.com:Modeux/seven-phase-skill.git ~/.claude/skills/seven-phase
```

It means "download this instruction bundle into the folder `~/.claude/skills/seven-phase`". Claude Code only looks for skills under `~/.claude/skills/`, and **the folder must be named `seven-phase`**.

> This repo is private. If you see `Permission denied`, this computer's SSH key hasn't been added to a GitHub account that has access. Add it under GitHub Settings → SSH keys, then try again.

### Step 2: Make sure Claude can see it

1. Quit Claude Code and open it again.
2. Type `/` in the input box. If `seven-phase` shows up in the list, you're done.

If it doesn't, the folder is almost certainly in the wrong place. Run `ls ~/.claude/skills/seven-phase/SKILL.md` — you should see that file.

### Step 3: Bring the reviewers along (required on a new computer)

The workflow hands work to a team of named helpers that write tests and do reviews (e.g. `tdd-guide`, `lane-reviewer`, `converge-verifier`, `security-reviewer`). **Their setup files are not in this repo** — they live in `~/.claude/agents/`.

- Same computer: nothing to do, they're already there.
- New computer: copy the whole `~/.claude/agents/` folder from the old computer to the same place on the new one. Skip this and you'll see "agent not found" when work gets handed out.

### Step 4 (optional): Show the progress board every time Claude starts

If you want to see "which tasks are half done, which are waiting to deploy" every time you open Claude Code, open `~/.claude/settings.json` and add this inside `"hooks"` (if `"hooks"` already exists, add only the `SessionStart` part):

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

Skipping this is fine — you can always say "進度" (progress) and Claude will run the board.

### Step 5 (optional): Use it in another project

`references/project-deepweave.md` holds settings for the deepweave project only (how to run its tests, which failures were already failing). For another project:

1. Write your own `references/project-<your-project>.md` in the same style, saying "how to run the tests and which checks must pass".
2. In `SKILL.md`, search for `project-deepweave.md` and add a line next to it: "in <your project>, read `project-<your-project>.md`".

### Updating later

```bash
cd ~/.claude/skills/seven-phase && git pull
```

---

## How to use

Once installed, just talk to Claude Code normally:

| You say | What happens |
|---|---|
| 「這個功能走七階段：……」 ("take this feature through seven-phase: …") | Starts a new task. It first asks which model to use and whether to go full or lean |
| 「繼續」／「下一階段」 ("continue" / "next phase") | You've read the report; let it move to the next step |
| 「繼續 <task name>」 ("continue <task name>") | Pick up a task you left half done (works even from a new chat window) |
| 「進度」 ("progress") | Show the board of all tasks |
| 「檢討」 ("retro") | Look back over recent tasks — quality, cost, speed — and suggest up to 3 improvements for you to pick from |

Everything is recorded in your project under `.claude/PRPs/plans/<task name>/`. Even if you close the window, or the chat gets long and compressed, Claude reads `PROGRESS.md` there and knows where it left off.

## The seven steps

| Step | What it does | Output |
|---|---|---|
| 1 Research | Look at the current code, find the related files, question whether the request itself makes sense | `RESEARCH.md` |
| 2 Discuss | Agree with you on the overall direction, catching "wrong direction" before any details | Direction decision at the top of `SPEC.md` |
| 3 Interview | Ask one question at a time about details and trade-offs | Full spec in `SPEC.md` |
| 4 Plan | Write the build plan; big tasks get the plan reviewed by other AIs first. No real code until you approve | `PLAN.md` |
| 5 Build | Failing test → make it pass → tidy up, one small slice at a time | Code |
| 6 Review | Several reviewers each check one angle; notes are de-duplicated into one table | `REVIEW.md` |
| 7 Wrap up | Decide on every review note, fix, run every test and check, summarize | Decision table, summary |

**Small tasks can take the lean path**, but only if all three are true: the fix is already clear, it doesn't touch money / permissions / sensitive content, and it's roughly 300 lines or less. The lean path skips the discussion, the interview and the stops in between — **but never skips review**.

## What's in the folder

```
SKILL.md       Entry point: how to start, full vs. lean, stop rules, rules for the whole run, who does what
ACCEPTANCE.md  Checklist for process changes (what changed, when to check it, what counts as working)
phases/        One how-to per step (1-research.md … 7-converge.md), lean path in lean.md, retro in retro.md
retros/        Report from each retro
references/    Read only when needed: how reviewers are picked, outside reviews, where each rule came from, project settings
assets/        Templates: review briefs, fix packets, the stop-report format, browser walk-through kit
scripts/       Helper tools (board, isolated workspaces, review coverage check, cost stats…)
evals/         Test cases for the skill itself
```

Handy helpers (all use only built-in Python — nothing extra to install — except `run_kit.mjs`, which needs Node):

| Tool | What it does |
|---|---|
| `board.py` | Scans every task and prints the progress board |
| `mkpatch.py` / `mkworktree.py` / `checktree.py` | Snapshot uncommitted changes, open an isolated workspace for reviewers, and confirm afterwards nothing was touched |
| `merge_back.py` | Move changes from a fix workspace back to the main one |
| `numstat.py` | Count changed lines of code / tests / docs to decide how many reviewers to send |
| `extract.py` | Copy sections from docs word-for-word into a brief (with source) |
| `reviewcov.py` | Check every reviewer turned in their report and nothing was dropped from the table |
| `run_external.py` | Run the outside AI reviewers (codex / grok / agy) |
| `run_kit.mjs` | Run every step of a browser walk-through in one go and save screenshots |
| `dockerslot.py` | Queue Docker-heavy commands (at most N at once) |
| `agentcost.py` / `taskcost.py` / `laneyield.py` / `claudemdcost.py` | What things cost: per helper, per task, and what each review lane bought |
| `wallclock.py` | Where the time went |

## Retros and acceptance

- Say 「檢討」 (retro): reviews how recent tasks went — quality, cost, speed — and settles old process-change checks. You don't need it every time; after 5 tasks since the last retro, the board reminds you.
- `ACCEPTANCE.md`: every process change gets one line — "when, how, and what counts as working". When it's due, the board lists it.
- The board also flags oversized instruction files (over 40KB, or 20%+ longer than at the last retro).

## Before changing a rule

Every rule was paid for by a past mistake. If one looks pointless, first read `references/history.md` to see what it guards against. After changing it, add a row to that table and commit in this folder; run `git push` to back it up to GitHub.
