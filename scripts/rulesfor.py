# -*- coding: utf-8 -*-
"""List the rule files a PLAN's file matrix hits, and gate that PLAN answers every one.

Usage:
  python3 rulesfor.py <path>... >> plan-appendix/rules.md   # print a `## 規則對照` skeleton
  python3 rulesfor.py --check <task dir | rules.md | PLAN.md>  # exit 1 if a hit rule file has no row or no 處置
  python3 rulesfor.py --root <repo> ...         # default: git toplevel of cwd

The table lives in `plan-appendix/rules.md` (machine-facing; PLAN.md stays readable). --check given the task
directory, or a PLAN.md without the section, looks there; a PLAN.md that still carries the section (tasks
written before 11-07) is checked as is.

Hits, for each matrix path (existing or not yet created):
  1. `.claude/rules/**/*.md` whose frontmatter `paths:` glob matches it (`**` crosses directories,
     `*` does not); rule files without `paths:` are always loaded and are skipped.
  2. every subdirectory `CLAUDE.md` between the repo root and the path's directory (the root one is
     always loaded).
WHY: no following of `.claude/rules/...` mentions — the parent CLAUDE.md files carry topic indexes
naming every rule file, so one level of links pulled in a third of the rule tree as noise.

處置 must start with `套用：` (which section → which slice／test) or `不相關：` (why). The skeleton
carries the matrix paths in a `<!-- rulesfor: ... -->` comment so --check recomputes the same set.
"""
import argparse
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _common  # noqa: E402
import _mdtable  # noqa: E402

HEADING = "規則對照"
_MARK = re.compile(r"<!--\s*rulesfor:\s*(.*?)\s*-->", re.S)
_H2 = re.compile(r"^##\s+(.*)$")
_OK = ("套用：", "不相關：")


def glob_re(pattern):
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def rule_globs(text):
    """`paths:` list from YAML frontmatter, or [] when the file has none."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return []
    globs, inside = [], False
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if re.match(r"^paths:\s*$", line):
            inside = True
        elif inside and re.match(r"^\s*-\s*", line):
            globs.append(re.sub(r"^\s*-\s*", "", line).strip().strip("'\""))
        elif inside and line.strip():
            inside = False
    return globs


def sections(text):
    return [m.group(1).strip() for m in map(_H2.match, text.splitlines()) if m]


def hits(root, paths):
    """{rule file: sorted matrix paths that hit it}."""
    found = {}
    for rule in sorted((root / ".claude/rules").rglob("*.md")):
        res = [glob_re(g) for g in rule_globs(rule.read_text(encoding="utf-8"))]
        for p in paths:
            if any(r.match(p) for r in res):
                found.setdefault(rule.relative_to(root).as_posix(), set()).add(p)
    for p in paths:
        parts = pathlib.PurePosixPath(p).parent.parts
        for n in range(1, len(parts) + 1):
            md = pathlib.PurePosixPath(*parts[:n], "CLAUDE.md").as_posix()
            if (root / md).is_file():
                found.setdefault(md, set()).add(p)
    return {k: sorted(v) for k, v in sorted(found.items())}


def skeleton(root, paths):
    out = ["## " + HEADING, "<!-- rulesfor: " + " ".join(paths) + " -->",
           "| 規則檔 | 節 | 命中 | 處置 |", "|---|---|---|---|"]
    for f, why in hits(root, paths).items():
        secs = "／".join(sections((root / f).read_text(encoding="utf-8"))) or "（無 ## 節）"
        out.append("| `{}` | {} | {} | |".format(f, secs.replace("|", "\\|"), "、".join("`%s`" % w for w in why)))
    return "\n".join(out)


APPENDIX_RULES = pathlib.Path("plan-appendix") / "rules.md"
_SECTION = r"^##\s+" + HEADING + r"\s*$(.*?)(?=^##\s|\Z)"


def resolve(target):
    """The file that holds `## 規則對照`: the task dir's plan-appendix/rules.md, the file given, or — for a
    PLAN.md without the section — rules.md beside it."""
    target = pathlib.Path(target)
    if target.is_dir():
        return target / APPENDIX_RULES if (target / APPENDIX_RULES).is_file() else target / "PLAN.md"
    sibling = target.parent / APPENDIX_RULES
    if target.is_file() and not re.search(_SECTION, target.read_text(encoding="utf-8"), re.M | re.S) and sibling.is_file():
        return sibling
    return target


def check(root, target):
    plan = resolve(target)
    if not plan.is_file():
        return ["{}: 找不到檔案（規則對照放 {}）".format(plan, APPENDIX_RULES)]
    text = plan.read_text(encoding="utf-8")
    m = re.search(_SECTION, text, re.M | re.S)
    if not m:
        return ["{}: 沒有 `## {}` 節（跑 rulesfor.py <矩陣路徑> >> {} 產骨架）".format(plan, HEADING, APPENDIX_RULES)]
    body = m.group(1)
    mark = _MARK.search(body)
    if not mark or not mark.group(1).split():
        return ["`## {}` 沒有 `<!-- rulesfor: <矩陣路徑> -->` 註記，無法重算命中".format(HEADING)]
    rows = {}
    for t in _mdtable.tables(body):
        fi, di = _mdtable.pick_col(t.header, "規則檔"), _mdtable.pick_col(t.header, "處置")
        if fi is None or di is None:
            continue
        for _, row in t.rows:
            if len(row) > max(fi, di):
                rows[row[fi].strip().strip("`")] = row[di].strip()
    errs = []
    for f in hits(root, mark.group(1).split()):
        if f not in rows:
            errs.append("缺列：`{}`".format(f))
        elif not rows[f].startswith(_OK):
            errs.append("處置不是「套用：」或「不相關：」開頭：`{}` → {!r}".format(f, rows[f]))
    return errs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--check", metavar="TASK_DIR|RULES_MD|PLAN_MD")
    ap.add_argument("--root")
    a = ap.parse_args()
    root = pathlib.Path(a.root) if a.root else _common.toplevel(".")
    # WHY: hits() reads a missing rules directory as "no rule applies", so a wrong --root passed the PLAN gate.
    if not (root / ".claude/rules").is_dir():
        print("rulesfor: {} 底下沒有 .claude/rules（--root 錯了？）".format(root), file=sys.stderr)
        return 2
    if a.check:
        errs = check(root, pathlib.Path(a.check))
        for e in errs:
            print("rulesfor: " + e, file=sys.stderr)
        print("rulesfor: {}".format("FAIL {} 項".format(len(errs)) if errs else "OK"))
        return 1 if errs else 0
    if not a.paths:
        ap.error("給矩陣路徑，或用 --check <任務目錄>")
    print(skeleton(root, [p.strip("/") for p in a.paths]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
