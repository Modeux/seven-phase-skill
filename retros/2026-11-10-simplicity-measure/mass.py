#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Where the lines go, per period: added/deleted by artifact kind (git numstat on dev, no merges; sqlc output,
lockfiles, graphify-out and tmp/ excluded) and the comment share of added prod/test code lines (git log -p -U0).
  python3 mass.py > mass.out
"""
import collections
import re
import subprocess

R = "/Users/huntwu/Desktop/deepweave/deepweave"
PERIODS = [
    ("P0 before the workflow 06-01~09-21", "2026-06-01T00:00:00", "2026-09-22T00:00:00"),
    ("P1 workflow 09-22~10-09", "2026-09-22T00:00:00", "2026-10-10T00:00:00"),
    ("P2 workflow 10-10~11-10", "2026-10-10T00:00:00", "2026-11-11T00:00:00"),
]
SKIP = re.compile(r"(^backend-golang/internal/db/sqlc/|^graphify-out/|pnpm-lock\.yaml$|go\.sum$|package-lock\.json$"
                  r"|(^|/)dist/|(^|/)node_modules/|\.snap$|^tmp/)")
TEST = re.compile(r"(_test\.go$|(^|/)tests?/|(^|/)test_[^/]*\.py$|_test\.py$|\.spec\.[jt]sx?$|\.test\.[jt]sx?$"
                  r"|(^|/)e2e[^/]*/|(^|/)testdata/|(^|/)fixtures/|^angular-workspace/specs/)")
CODE_EXT = re.compile(r"\.(go|ts|html|css|scss|py)$")
MARK = re.compile(r"(WHY|INVARIANT)(\[[^\]]+\])?:")
KINDS = ("prod", "sql", "test", "contract", "rules", "records", "docs", "seed", "other")


def kind(path):
    if SKIP.search(path):
        return None
    if path.startswith(".claude/PRPs/"):
        return "records"
    base = path.rsplit("/", 1)[-1]
    if base in ("CLAUDE.md", "AGENTS.md") or path.startswith((".claude/rules/", ".claude/skills/", ".claude/agents/")):
        return "rules"
    if TEST.search(path):
        return "test"
    if re.search(r"openapi[^/]*\.ya?ml$", path):
        return "contract"
    if path.endswith(".md") or path.startswith("docs/") or "/docs/" in path:
        return "docs"
    if path.endswith(".sql"):
        return "seed" if "/seeds/" in path else "sql"
    if CODE_EXT.search(path):
        return "prod"
    return "other"


def norm(path):
    m = re.search(r"\{([^{}]*) => ([^{}]*)\}", path)
    if m:
        return (path[:m.start()] + m.group(2) + path[m.end():]).replace("//", "/")
    if " => " in path:
        return path.split(" => ", 1)[1]
    return path


def numstat(since, until):
    out = subprocess.run(["git", "-C", R, "log", "dev", "--no-merges", f"--since={since}", f"--until={until}",
                          "--numstat", "-M", "--format=@@%H\t%s"], capture_output=True, text=True, check=True).stdout
    tot = {k: [0, 0, 0] for k in KINDS}
    types = collections.Counter()
    commits = 0
    per_commit = []
    cur = None
    for line in out.splitlines():
        if line.startswith("@@"):
            commits += 1
            subj = line.split("\t", 1)[1] if "\t" in line else ""
            m = re.match(r"(\w+)[(:!]", subj)
            types[m.group(1).lower() if m else "(other)"] += 1
            cur = [0, 0]
            per_commit.append(cur)
            continue
        parts = line.split("\t", 2)
        if len(parts) != 3 or parts[0] == "-":
            continue
        k = kind(norm(parts[2]))
        if k is None:
            continue
        a, d = int(parts[0]), int(parts[1])
        tot[k][0] += 1
        tot[k][1] += a
        tot[k][2] += d
        if k == "prod":
            cur[0] += a
            cur[1] += d
    shrink = sum(1 for a, d in per_commit if d > a)
    touch = sum(1 for a, d in per_commit if a or d)
    return tot, types, commits, shrink, touch


def density(since, until):
    p = subprocess.Popen(["git", "-C", R, "log", "dev", "--no-merges", f"--since={since}", f"--until={until}",
                          "-p", "-U0", "-M", "--format=@@%H", "--", "*.go", "*.ts", "*.py"],
                         stdout=subprocess.PIPE, text=True, errors="replace")
    stats = {k: collections.Counter() for k in ("prod", "test")}
    k = None
    lang = "c"
    for line in p.stdout:
        if line.startswith("+++ "):
            path = line[6:].rstrip("\n") if line.startswith("+++ b/") else None
            kk = kind(path) if path else None
            k = kk if kk in ("prod", "test") else None
            lang = "py" if path and path.endswith(".py") else "c"
            continue
        if line.startswith("--- "):
            if line.startswith("--- a/") or line.startswith("--- /dev/null"):
                continue
        if k is None or not line or line[0] not in "+-":
            continue
        sign = "add" if line[0] == "+" else "del"
        s = line[1:].strip()
        if not s:
            stats[k][sign + "_blank"] += 1
            continue
        if lang == "py":
            is_c = s.startswith("#")
        else:
            is_c = s.startswith("//") or s.startswith("/*") or re.match(r"\*(\s|/|$)", s) is not None
        stats[k][sign + ("_comment" if is_c else "_code")] += 1
        if is_c:
            stats[k][sign + "_comment_chars"] += len(s)
            if MARK.search(s):
                stats[k][sign + "_marker"] += 1
        else:
            stats[k][sign + "_code_chars"] += len(s)
    p.wait()
    return stats


def main():
    for name, since, until in PERIODS:
        tot, types, commits, shrink, touch = numstat(since, until)
        prod_add = tot["prod"][1] + tot["sql"][1] or 1
        print(f"\n=== {name}: {commits} commits; commits touching prod code {touch}, of which prod shrank in {shrink}")
        print("  commit types: " + ", ".join(f"{k} {v}" for k, v in types.most_common(8)))
        print(f"  {'kind':9} {'files':>6} {'added':>8} {'deleted':>8} {'net':>8} {'del/add':>8} {'added per 100 prod+sql':>23}")
        for k in KINDS:
            f, a, d = tot[k]
            print(f"  {k:9} {f:6} {a:8} {d:8} {a - d:8} {(d / a if a else 0):8.2f} {a / prod_add * 100:23.0f}")
        st = density(since, until)
        for k in ("prod", "test"):
            c = st[k]
            code, com = c["add_code"], c["add_comment"]
            print(f"  [{k} .go/.ts/.py] added code {code}, comment {com} ({com / (code + com or 1) * 100:.1f}% of non-blank), "
                  f"WHY/INVARIANT lines {c['add_marker']}; comment chars/line {c['add_comment_chars'] / (com or 1):.0f} "
                  f"vs code {c['add_code_chars'] / (code or 1):.0f}; "
                  f"comment share by chars {c['add_comment_chars'] / ((c['add_comment_chars'] + c['add_code_chars']) or 1) * 100:.1f}%; "
                  f"deleted code {c['del_code']}, comment {c['del_comment']}")


if __name__ == "__main__":
    main()
