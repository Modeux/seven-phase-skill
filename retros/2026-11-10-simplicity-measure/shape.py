#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Code shape at three snapshots of dev (before the workflow, 10-09, HEAD): size, file-length buckets,
Go function lengths, comment share, WHY/INVARIANT markers, test volume, source-reading (pin) tests,
instruction-file mass, and the largest prod files with their growth.
  python3 shape.py > shape.out
"""
import collections
import re
import statistics
import subprocess
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from mass import MARK, R, kind  # noqa: E402

SRC_LIT = re.compile(r'"[^"\n]*\.(go|ts|sql|md|ya?ml|html|css|py)"')
GO_READ = re.compile(r"os\.ReadFile\(|os\.ReadDir\(|filepath\.Walk|os\.Open\(")
GO_AST = re.compile(r'"go/(parser|ast|token)"')
TS_READ = re.compile(r"readFileSync\(|readdirSync\(|\?raw['\"]")


def rev_before(date):
    return subprocess.run(["git", "-C", R, "rev-list", "-1", f"--before={date}T00:00:00", "dev"],
                          capture_output=True, text=True, check=True).stdout.strip()


def blobs(rev):
    """{path: text} for prod/test/sql/rules files at rev."""
    ls = subprocess.run(["git", "-C", R, "ls-tree", "-r", rev], capture_output=True, text=True, check=True).stdout
    want = []
    for line in ls.splitlines():
        meta, path = line.split("\t", 1)
        mode, typ, sha = meta.split()
        if typ != "blob" or mode == "120000":
            continue
        k = kind(path)
        if k in ("prod", "test", "sql", "rules"):
            want.append((sha, path, k))
    p = subprocess.Popen(["git", "-C", R, "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    out = {}
    data = "".join(sha + "\n" for sha, _, _ in want).encode()
    raw, _ = p.communicate(data)
    pos = 0
    for sha, path, k in want:
        nl = raw.index(b"\n", pos)
        size = int(raw[pos:nl].split()[2])
        body = raw[nl + 1:nl + 1 + size]
        pos = nl + 1 + size + 1
        out[path] = (k, body.decode("utf-8", "replace"))
    return out


def is_comment(s, py):
    if py:
        return s.startswith("#")
    return s.startswith("//") or s.startswith("/*") or re.match(r"\*(\s|/|$)", s) is not None


def go_funcs(text):
    lens = []
    start = None
    for i, line in enumerate(text.split("\n")):
        if start is None:
            if line.startswith("func "):
                if line.rstrip().endswith("}") and "{" in line:
                    lens.append(1)
                else:
                    start = i
        elif line.startswith("}"):
            lens.append(i - start + 1)
            start = None
    return lens


def measure(rev):
    files = blobs(rev)
    m = collections.Counter()
    funcs = []
    sizes = {}
    for path, (k, text) in files.items():
        lines = text.split("\n")
        n = len(lines)
        ext = path.rsplit(".", 1)[-1]
        if k == "rules":
            m["rules_files"] += 1
            m["rules_bytes"] += len(text.encode())
            continue
        if k == "sql":
            m["sql_files"] += 1
            m["sql_loc"] += n
            if "/migrations/" in path:
                m["migrations"] += 1
            continue
        m[f"{k}_files"] += 1
        m[f"{k}_loc"] += n
        m[f"{k}_loc_{ext}"] += n
        if ext in ("go", "ts", "py"):
            py = ext == "py"
            for line in lines:
                s = line.strip()
                if not s:
                    continue
                if is_comment(s, py):
                    m[f"{k}_comment"] += 1
                    if MARK.search(s):
                        m[f"{k}_marker"] += 1
                else:
                    m[f"{k}_code"] += 1
        if k == "prod":
            if ext in ("go", "ts", "py"):
                sizes[path] = n
                for b in (500, 1000, 2000):
                    if n > b:
                        m[f"prod_files_gt{b}"] += 1
                        m[f"prod_loc_in_gt{b}"] += n
            if ext == "go":
                funcs.extend(go_funcs(text))
        else:
            if ext == "go":
                m["tests_go"] += len(re.findall(r"^func (Test|Benchmark|Fuzz)", text, re.M))
                m["subtests_go"] += text.count("t.Run(")
                if (GO_READ.search(text) and SRC_LIT.search(text)) or GO_AST.search(text):
                    m["pin_go_files"] += 1
                    m["pin_go_loc"] += n
            elif ext == "ts":
                m["tests_ts"] += len(re.findall(r"\b(it|test)(\.each\([^)]*\))?\(", text))
                if TS_READ.search(text):
                    m["pin_ts_files"] += 1
                    m["pin_ts_loc"] += n
            elif ext == "py":
                m["tests_py"] += len(re.findall(r"^\s*(async )?def test_", text, re.M))
    funcs.sort()
    if funcs:
        m["go_funcs"] = len(funcs)
        m["go_func_p50"] = int(statistics.median(funcs))
        m["go_func_p90"] = funcs[int(len(funcs) * .9)]
        m["go_func_p99"] = funcs[int(len(funcs) * .99)]
        m["go_func_max"] = funcs[-1]
        for b in (80, 150, 300):
            m[f"go_funcs_gt{b}"] = sum(1 for x in funcs if x > b)
            m[f"go_loc_in_funcs_gt{b}"] = sum(x for x in funcs if x > b)
        m["go_func_loc"] = sum(funcs)
    return m, sizes


def main():
    revs = [("09-21", rev_before("2026-09-22")), ("10-09", rev_before("2026-10-10")), ("HEAD", "dev")]
    res = [(name, *measure(rev)) for name, rev in revs]
    keys = []
    for _, m, _ in res:
        for k in m:
            if k not in keys:
                keys.append(k)
    print(f"{'metric':26}" + "".join(f"{name:>12}" for name, _, _ in res) + f"{'HEAD/09-21':>12}")
    for k in sorted(keys):
        vals = [m.get(k, 0) for _, m, _ in res]
        ratio = f"{vals[-1] / vals[0]:.2f}" if vals[0] else "-"
        print(f"{k:26}" + "".join(f"{v:12}" for v in vals) + f"{ratio:>12}")
    for name, m, _ in res:
        pc = m["prod_comment"] / ((m["prod_comment"] + m["prod_code"]) or 1) * 100
        tc = m["test_comment"] / ((m["test_comment"] + m["test_code"]) or 1) * 100
        print(f"[{name}] prod comment share {pc:.1f}%  test comment share {tc:.1f}%  "
              f"test:prod LOC {m['test_loc'] / (m['prod_loc'] or 1):.2f}  "
              f"rules KB per 1k prod LOC {m['rules_bytes'] / 1024 / (m['prod_loc'] / 1000 or 1):.2f}")
    head = res[-1][2]
    print("\nlargest prod files at HEAD (LOC at 09-21 / 10-09 / HEAD):")
    for path, n in sorted(head.items(), key=lambda kv: -kv[1])[:30]:
        print(f"  {res[0][2].get(path, 0):6} {res[1][2].get(path, 0):6} {n:6}  {path}")
    grow = sorted(((n - res[0][2].get(path, 0), path, n) for path, n in head.items()), reverse=True)[:20]
    print("\nprod files that grew most since 09-21 (delta, HEAD LOC):")
    for d, path, n in grow:
        print(f"  +{d:5} {n:6}  {path}")


if __name__ == "__main__":
    main()
