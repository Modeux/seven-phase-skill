# -*- coding: utf-8 -*-
"""Copy sections of a markdown file verbatim into a brief, each tagged with its source.

Usage:
  python3 extract.py SPEC.md --list                          # headings with line numbers
  python3 extract.py PLAN.md "0.1 owner 拍板" "B character" >> tmp/<slug>/impl/B-brief.md
  python3 extract.py SPEC.md L120-L180                       # a line range instead of a heading
  python3 extract.py PLAN.md "切片" --first                  # take the first of several matches

A heading selector matches any heading whose text contains it; the section runs to the next heading
of the same or a higher level. Zero matches, or several without --first, is an error — a brief built
from the wrong section is worse than a failed command.
"""
import argparse
import pathlib
import re
import sys

_H = re.compile(r"^(#{1,6})\s+(.*)$")


def headings(lines):
    out, fence = [], False
    for i, line in enumerate(lines):
        if line.startswith("```"):
            fence = not fence
        m = None if fence else _H.match(line)
        if m:
            out.append((i, len(m.group(1)), m.group(2).strip()))
    return out


def section(lines, heads, idx):
    start, level, _ = heads[idx]
    end = next((i for i, lv, _ in heads[idx + 1:] if lv <= level), len(lines))
    return start, end


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("selectors", nargs="*")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--first", action="store_true")
    a = ap.parse_args()
    path = pathlib.Path(a.file)
    lines = path.read_text(encoding="utf-8").splitlines()
    heads = headings(lines)
    if a.list or not a.selectors:
        for i, lv, text in heads:
            print(f"L{i + 1:<5} {'#' * lv} {text}")
        return
    shown = str(path)
    for sel in a.selectors:
        rng = re.fullmatch(r"L?(\d+)-L?(\d+)", sel)
        if rng:
            start, end = int(rng.group(1)) - 1, int(rng.group(2))
            label = f"L{start + 1}–L{end}"
        else:
            hits = [k for k, (_, _, text) in enumerate(heads) if sel in text]
            if not hits or (len(hits) > 1 and not a.first):
                found = "; ".join(f"L{heads[k][0] + 1} {heads[k][2]}" for k in hits) or "none"
                sys.exit(f"extract.py: selector {sel!r} matched {len(hits)} headings in {shown}: {found}")
            start, end = section(lines, heads, hits[0])
            label = heads[hits[0]][2]
        print(f"\n## {label}（逐字摘自 `{shown}` L{start + 1}–L{end}）\n")
        print("\n".join(lines[start:end]).rstrip())


if __name__ == "__main__":
    main()
