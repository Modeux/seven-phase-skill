# -*- coding: utf-8 -*-
"""Markdown tables of REVIEW.md / PROGRESS.md, read by header names (stdlib only).

Usage (library):
  from _mdtable import tables, pick_col, row_ids
  for t in tables(text):            # t.headings = [(level, title), ...] active at the table
      col = pick_col(t.header, "來源")
      for lineno, cells in t.rows:
          ids = row_ids(cells[0])   # ["R3"], ["L2", "L4"], ["L1", "L2", "L3"] for "L1–L3", [] for prose
          ids = row_ids(cells[0], any_prefix=True)   # also N1, NL3, P2… (map tables under a 地圖 heading)
"""
import collections
import re

_HEAD = re.compile(r"^(#{1,6})\s+(.*)$")
_SEP_CELL = re.compile(r":?-+:?")
_LIST_SEPS = "、,，;；／/"


def _id_patterns(id_re):
    sep = rf"[{_LIST_SEPS}]|[–—~～]|-(?=[A-Z])"
    return (re.compile(rf"^\**({id_re})((?:\s*(?:{sep})\s*{id_re})*)\**(?=\s|$|[（(：:*])"),
            re.compile(rf"({sep})?\s*({id_re})"))


_RL_IDS = _id_patterns(r"(?:R|L)\d+(?:[-.]\d+)?")
# WHY: incremental rounds number their maps N1…／NL1…; with R/L only, a whole round vanished without an error.
_ANY_IDS = _id_patterns(r"[A-Z]{1,3}\d+(?:[-.]\d+)?")
_PREFIX_NUM = re.compile(r"([A-Z]+)(\d+)$")
_MAX_RANGE = 60
_UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")
_FENCE = re.compile(r"^\s*(```|~~~)")

Table = collections.namedtuple("Table", "headings header rows")


def cells(line):
    """Cells of a pipe-table row; `\\|` inside a cell (escaped pipe) does not split it."""
    s = line.strip()[1:]
    s = s[:-1] if s.endswith("|") and not s.endswith("\\|") else s
    return [c.strip().replace("\\|", "|") for c in _UNESCAPED_PIPE.split(s)]


def unfenced(text):
    """The text with lines inside ``` / ~~~ fences blanked (line numbers kept): `# 重跑閘` in a bash block is
    not a heading, and a pipe inside a code sample is not a table."""
    out, fence = [], None
    for line in text.splitlines():
        m = _FENCE.match(line)
        if fence is None and m:
            fence = m.group(1)
            out.append("")
        elif fence is not None:
            out.append("")
            if line.strip().startswith(fence):
                fence = None
        else:
            out.append(line)
    return "\n".join(out)


def _norm(cell):
    return cell.strip().strip("*`").strip()


def tables(text):
    """Every pipe table with its header row and the heading path above it; separator rows dropped.

    A table broken by a blank line continues when the next block's first row starts with an id and has
    the same width (otherwise that row would be read as a new header and its finding lost)."""
    out, headings, cur, last = [], [], None, None
    for lineno, line in enumerate(unfenced(text).splitlines(), 1):
        m = _HEAD.match(line)
        if m:
            level = len(m.group(1))
            headings = [h for h in headings if h[0] < level] + [(level, m.group(2).strip())]
            cur = last = None
            continue
        if not line.strip().startswith("|"):
            cur = None
            continue
        row = cells(line)
        if cur is None:
            if last is not None and len(row) == len(last.header) and row_ids(row[0], any_prefix=True):
                cur = last
                cur.rows.append((lineno, row))
                continue
            cur = last = Table(list(headings), row, [])
            out.append(cur)
        elif not all(_SEP_CELL.fullmatch(c) for c in row):
            cur.rows.append((lineno, row))
    return out


def pick_col(header, name, avoid=()):
    """Index of the header cell named `name`: exact match, else prefix, else substring without any `avoid`
    word. WHY: substring-first read 嚴重度（宣稱→裁決） as the 裁決 column and dropped every verdict."""
    names = [_norm(h) for h in header]
    for test in (lambda h: h == name, lambda h: h.startswith(name),
                 lambda h: name in h and not any(a in h for a in avoid)):
        hit = next((i for i, h in enumerate(names) if test(h)), None)
        if hit is not None:
            return hit
    return None


def row_ids(cell, any_prefix=False):
    """Finding ids a row's first cell names: `R3`, `R3 title…`, `L2、L4`, `L1–L3`; [] when it names none.
    any_prefix=True also takes other capital prefixes (N1, NL3, P2) — only for tables known to be a map
    or a verdict table, since elsewhere F5／D1／M03 name focus areas, decisions and mutations."""
    lst, part = _ANY_IDS if any_prefix else _RL_IDS
    m = lst.match(_norm(cell))
    if not m:
        return []
    out, prev = [], None
    for sep, rid in part.findall(m.group(0)):
        a, b = _PREFIX_NUM.match(prev or ""), _PREFIX_NUM.match(rid)
        if sep and sep not in _LIST_SEPS and a and b and a.group(1) == b.group(1):
            lo, hi = int(a.group(2)), int(b.group(2))
            # WHY: a reversed or huge range is a typo; keep both ends instead of inventing ids or dropping one.
            if 0 < hi - lo <= _MAX_RANGE:
                out.extend(f"{b.group(1)}{n}" for n in range(lo + 1, hi + 1))
            else:
                out.append(rid)
        else:
            out.append(rid)
        prev = rid
    return list(dict.fromkeys(out))


def id_prefix(rid):
    m = re.match(r"[A-Z]+", rid)
    return m.group(0) if m else ""


def map_tables(text, discarded=None):
    """[(table, 來源 column, [(lineno, cells)] id rows)] of a REVIEW doc's dedup map.

    A map table has a 來源 column (not the id column) and id rows, and sits outside 收斂裁決 and 未收錄
    sections. INVARIANT: owner-question and result tables carry R ids next to a 來源 column too, so when
    any candidate sits under a heading containing 地圖, only those count (laneyield, reviewcov); the
    others go to `discarded`. Under a 地圖 heading any capital prefix is an id (N1…), elsewhere only R/L."""
    out = []
    for t in tables(text):
        if any("收斂裁決" in h or "未收錄" in h for _, h in t.headings):
            continue
        col = pick_col(t.header, "來源")
        under = any("地圖" in h for _, h in t.headings)
        rows = [(n, c) for n, c in t.rows if row_ids(c[0], any_prefix=under)]
        if col not in (None, 0) and rows:
            out.append((t, col, rows, under))
    under_map = [m[:3] for m in out if m[3]]
    if under_map and discarded is not None:
        discarded.extend(m[:3] for m in out if not m[3])
    return under_map or [m[:3] for m in out]
