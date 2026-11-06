# -*- coding: utf-8 -*-
"""Assemble every slice's `<ID>-brief.md` from one plain-text manifest: hand-written head, verbatim
excerpts with sources, auto-loaded rules, on-demand paths. All or nothing.

Usage:
  python3 mkbrief.py MANIFEST                  # write all briefs
  python3 mkbrief.py MANIFEST --only S1 S2     # just these IDs (--only may repeat)
  python3 mkbrief.py MANIFEST --check          # write nothing; exit 1 when a brief differs from a rebuild
  python3 mkbrief.py MANIFEST --list           # IDs and what each one declares

Manifest: paths are relative to root. A comment is a whole line starting with `#`; a comment after a
value is an error. Only "double quotes" group words and nothing needs escaping.
  root: /abs/tree
  out: tmp/<slug>/impl
  alias: P=.claude/PRPs/plans/<slug>/PLAN.md
  shared: tmp/<slug>/impl/shared.md
  head: tmp/<slug>/impl/common-head.md
  ondemand: docs/x.md 為什麼可能要讀

  == S1
  head: tmp/<slug>/impl/S1-head.md
  extract: P "S1 character"
  extract: P L20 L25-L30 "切片 A"
  extract: P 切片 --first
  extract: tmp/<slug>/facts.md --all
  note: 衝突時以 SPEC 為準
  files: backend-golang/a.go web/b.html
  ondemand: P 全部 D-M
  over-90kb: 規格忠實度要讀 SPEC 全文

Before the first `==`: root, out, alias, shared (a slice may override it), head and ondemand (these two
go in every brief). Every alias is listed under 需要時再讀 in every brief. A slice writes
<out>/<ID>-brief.md. head, extract, files and ondemand repeat. extract takes heading text (quoted when
it has spaces or shares the line) or line ranges, in order; --first takes the first of several heading
matches, --all the whole file. note is one literal line between excerpts. files may not exist yet.
ondemand is a path or alias, then an optional note. over-90kb is required when head + excerpts exceed 90KB.

Prints one conclusion line, then `S1  42.1KB  <path>` per ID (size = head + excerpts, the budgeted part).

Exit codes:
  0  written, or --check found every brief identical
  1  content problem: selector with 0 or several matches, bad line range, missing file, undefined
     alias, required reading over 90KB without a reason, --check mismatch. Nothing is written.
  2  tool or argument problem: bad flags, unreadable manifest, manifest syntax, root not found,
     unknown --only ID, output not writable. Nothing is written.
"""
import argparse
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import extract  # noqa: E402
import rulesfor  # noqa: E402

# INVARIANT: SKILL.md「任務包瘦身」budgets required reading in `wc -c` bytes, so KB here is 1000 bytes.
LIMIT = 90000
MAX_DETAIL = 25

_DIRECTIVE = re.compile(r"^([a-z0-9-]+):(?:\s+(.*))?$")
_SLICE = re.compile(r"^==\s*(.*)$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_ALIAS = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*)\s*=\s*(\S.*)$")
# WHY: not shlex — headings carry apostrophes and backslashes, and an LLM should not have to escape them.
_TOKEN = re.compile(r'"([^"]*)"|(\S+)')

PREAMBLE_KEYS = {"root", "out", "alias", "shared", "head", "ondemand"}
SLICE_KEYS = {"shared", "head", "ondemand", "extract", "note", "files", "over-90kb"}


class ManifestError(Exception):
    """Syntax the manifest parser cannot read (exit 2)."""


class Slice:
    def __init__(self, sid, line):
        self.id, self.line = sid, line
        self.heads, self.ondemand, self.excerpts, self.files = [], [], [], []
        self.shared = self.over = None


class Manifest:
    def __init__(self):
        self.root = self.out = self.shared = None
        self.aliases, self.heads, self.ondemand, self.slices = {}, [], [], []


def tokens(value):
    """[(text, was_quoted)]; raises on an unterminated quote."""
    out = []
    for m in _TOKEN.finditer(value):
        if m.group(1) is not None:
            out.append((m.group(1), True))
        elif '"' in m.group(2):
            raise ManifestError('引號沒有成對：{}'.format(m.group(2)))
        else:
            out.append((m.group(2), False))
    return out


def parse_extract(value):
    toks = tokens(value)
    if not toks:
        raise ManifestError("extract 要寫來源與選擇器")
    source, first, whole, selectors = toks[0][0], False, False, []
    for text, quoted in toks[1:]:
        if quoted:
            if not text:
                raise ManifestError('空的 "" 會命中所有標題')
            selectors.append(("heading", text))
        elif text == "--first":
            first = True
        elif text == "--all":
            whole = True
        elif text.startswith("--"):
            raise ManifestError("不認得 {}（只有 --first、--all）".format(text))
        elif extract.parse_range(text):
            selectors.append(("range", text))
        else:
            selectors.append(("word", text))
    if whole and selectors:
        raise ManifestError("--all 不能再接選擇器")
    if not whole and not selectors:
        raise ManifestError("extract 要有選擇器（標題、L120-L180）或 --all")
    # INVARIANT: bare words with others on the line are rejected, not read as separate selectors —
    # `extract: P S1 character` could otherwise resolve both words and excerpt the wrong sections.
    if len(selectors) > 1 and any(kind == "word" for kind, _ in selectors):
        raise ManifestError('標題選擇器有空白或多個時要加雙引號，例如 "S1 character"')
    return {"source": source, "selectors": selectors, "first": first, "whole": whole}


def parse_ondemand(value):
    m = _TOKEN.match(value)
    if not m or (m.group(2) and '"' in m.group(2)):
        raise ManifestError("ondemand 要寫路徑或 alias")
    return m.group(1) if m.group(1) is not None else m.group(2), value[m.end():].strip()


def parse(text):
    """Manifest from text; raises ManifestError carrying every syntax problem, one per line."""
    man, errs, cur, seen = Manifest(), [], None, set()

    def apply(n, key, value):
        scope = "片內" if cur else "第一個 == 之前"
        if key not in (SLICE_KEYS if cur else PREAMBLE_KEYS):
            raise ManifestError("{} 不能寫在{}".format(key, scope))
        if not value and key not in ("extract", "ondemand"):
            raise ManifestError("{} 沒有值".format(key))
        if key in ("root", "out", "alias", "shared", "head") and " #" in value:
            raise ManifestError("註解要獨立一行，值後面不能接 #：{}".format(value))
        if key in ("root", "out"):
            if getattr(man, key) is not None:
                raise ManifestError("{} 重複".format(key))
            setattr(man, key, value)
        elif key == "alias":
            m = _ALIAS.match(value)
            if not m:
                raise ManifestError("alias 要寫成 NAME=path")
            if m.group(1) in man.aliases:
                raise ManifestError("alias {} 重複".format(m.group(1)))
            man.aliases[m.group(1)] = (m.group(2).strip(), n)
        elif key == "shared":
            if cur:
                cur.shared = (value, n)
            elif man.shared is not None:
                raise ManifestError("shared 重複")
            else:
                man.shared = (value, n)
        elif key == "head":
            (cur or man).heads.append((value, n))
        elif key == "ondemand":
            (cur or man).ondemand.append(parse_ondemand(value or "") + (n,))
        elif key == "extract":
            item = parse_extract(value or "")
            item.update(kind="extract", line=n, raw=value)
            cur.excerpts.append(item)
        elif key == "note":
            cur.excerpts.append({"kind": "note", "line": n, "raw": value})
        elif key == "files":
            cur.files.extend((text, n) for text, _ in tokens(value))
        elif key == "over-90kb":
            cur.over = (value, n)

    for n, raw in enumerate(text.split("\n"), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        slice_match = _SLICE.match(line)
        directive = _DIRECTIVE.match(line)
        try:
            if slice_match:
                sid = slice_match.group(1).strip()
                if not _ID.match(sid):
                    raise ManifestError("片號只能有英數與 . _ -：{}".format(sid))
                if sid in seen:
                    raise ManifestError("片號 {} 重複".format(sid))
                seen.add(sid)
                cur = Slice(sid, n)
                man.slices.append(cur)
            elif directive:
                apply(n, directive.group(1), (directive.group(2) or "").strip())
            else:
                raise ManifestError("看不懂這行（要是 `key: value` 或 `== ID`）：{}".format(line))
        except ManifestError as e:
            errs.append("manifest L{}: {}".format(n, e))
    if man.root is None:
        errs.append("manifest: 缺 root:")
    elif not pathlib.PurePath(man.root).is_absolute():
        errs.append("manifest: root 要是絕對路徑：{}".format(man.root))
    elif not pathlib.Path(man.root).is_dir():
        errs.append("manifest: root 不存在：{}".format(man.root))
    if man.out is None:
        errs.append("manifest: 缺 out:")
    if not man.slices:
        errs.append("manifest: 沒有任何 `== ID`")
    if errs:
        raise ManifestError("\n".join(errs))
    return man


class Builder:
    def __init__(self, man):
        self.man = man
        self.root = pathlib.Path(man.root)
        self.errors, self.warnings = [], []
        self._cache = {}

    def abs(self, path):
        p = pathlib.Path(path)
        return p if p.is_absolute() else self.root / p

    def shown(self, path):
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    def locate(self, token, line, what, sid=""):
        """Path for an alias or a real path; records an error and returns None when it is neither."""
        where = "manifest L{}{}".format(line, " [{}]".format(sid) if sid else "")
        if token in self.man.aliases:
            return self.abs(self.man.aliases[token][0])
        path = self.abs(token)
        if path.exists():
            return path
        if "/" in token or "." in token:
            self.errors.append("{}: {} 不存在：{}".format(where, what, token))
        else:
            self.errors.append("{}: alias {} 未定義，也沒有這個檔（已定義：{}）".format(
                where, token, "、".join(self.man.aliases) or "無"))
        return None

    def read_lines(self, path, where):
        """(lines, heads), or None after recording why the file cannot be excerpted."""
        if path not in self._cache:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError) as e:
                self.errors.append("{}: 讀不了 {}：{}".format(where, path, e))
                self._cache[path] = None
            else:
                self._cache[path] = (lines, extract.headings(lines))
        return self._cache[path]

    def check_preamble(self):
        for name, (path, line) in self.man.aliases.items():
            if not self.abs(path).exists():
                self.errors.append("manifest L{}: alias {} 指到的檔不存在：{}".format(line, name, path))
        if self.man.shared:
            self.locate(self.man.shared[0], self.man.shared[1], "shared")

    def head_blocks(self, sl):
        out = []
        for token, line in self.man.heads + sl.heads:
            where = "manifest L{} [{}]".format(line, sl.id)
            path = self.locate(token, line, "head", sl.id)
            if path is None:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                self.errors.append("{}: 讀不了 {}：{}".format(where, path, e))
                continue
            if not text.strip():
                self.errors.append("{}: head 檔是空的：{}".format(where, token))
                continue
            out.append((line, "head: " + token, text.rstrip("\n")))
        return out

    def excerpt_spans(self, sl, item):
        """Resolved [(source path, start, end, label, shown, is line range)] for one extract line."""
        where = "manifest L{} [{}]".format(item["line"], sl.id)
        path = self.locate(item["source"], item["line"], "來源", sl.id)
        if path is None:
            return []
        if not path.is_file():
            self.errors.append("{}: 來源不是檔案：{}".format(where, item["source"]))
            return []
        loaded = self.read_lines(path, where)
        if loaded is None:
            return []
        lines, heads = loaded
        shown = self.shown(path)
        spans = []
        if item["whole"]:
            if not lines:
                self.errors.append("{}: 來源是空的：{}".format(where, shown))
                return []
            return [(path, 0, len(lines), path.name, shown, False)]
        for kind, sel in item["selectors"]:
            if kind == "range":
                start, end = extract.parse_range(sel)
                # INVARIANT: strict here on purpose. extract.py's slice would print an empty body (past
                # EOF) or a tag naming lines that do not exist (end past EOF) and still exit 0.
                if not 0 <= start < end <= len(lines):
                    self.errors.append("{}: {} 超出範圍或顛倒，{} 共 {} 行".format(where, sel, shown, len(lines)))
                    continue
                label = "L{}–L{}".format(start + 1, end)
            else:
                try:
                    start, end, label = extract.resolve_heading(lines, heads, sel, item["first"], shown)
                except extract.SelectorError as e:
                    self.errors.append("{}: {}".format(where, e))
                    continue
            if not "\n".join(lines[start:end]).strip():
                self.errors.append("{}: {} 切出來是空的（{}）".format(where, sel, shown))
                continue
            spans.append((path, start, end, label, shown, kind == "range"))
        return spans

    def warn_overlaps(self, sl, placed):
        for i, (line_a, path_a, s_a, e_a, shown) in enumerate(placed):
            for line_b, path_b, s_b, e_b, _ in placed[i + 1:]:
                if path_a == path_b and s_a < e_b and s_b < e_a:
                    self.warnings.append("manifest L{} [{}]: 與 manifest L{} 重疊，{} L{}–L{} ∩ L{}–L{}，會重複付 token".format(
                        line_b, sl.id, line_a, shown, s_b + 1, e_b, s_a + 1, e_a))

    def excerpt_blocks(self, sl):
        blocks, placed, sources = [], [], []
        for item in sl.excerpts:
            if item["kind"] == "note":
                blocks.append((item["line"], "note: " + item["raw"], "\n{}\n".format(item["raw"]), False, None))
                continue
            for path, start, end, label, shown, ranged in self.excerpt_spans(sl, item):
                lines = self._cache[path][0]
                text = extract.render(lines, start, end, label, shown, level=3)
                # INVARIANT: only heading spans may report "text unchanged". A line range whose new text
                # happens to occur elsewhere in the old brief is exactly the drift --check must not soften.
                verbatim = None if ranged or item["whole"] else "\n".join(lines[start:end]).rstrip()
                blocks.append((item["line"], "extract: " + item["raw"], text, ranged, verbatim))
                placed.append((item["line"], path, start, end, shown))
                if path not in sources:
                    sources.append(path)
        self.warn_overlaps(sl, placed)
        return blocks, sources

    def rule_paths(self, sl):
        paths = []
        for token, line in sl.files:
            where = "manifest L{} [{}]".format(line, sl.id)
            p = pathlib.PurePosixPath(token)
            # INVARIANT: a path the glob matcher cannot see silently yields no rules, which reads as
            # "no rules apply" — so anything that is not a plain repo-relative path fails here.
            if p.is_absolute():
                try:
                    p = p.relative_to(self.root.as_posix())
                except ValueError:
                    self.errors.append("{}: files 路徑不在 root 底下：{}".format(where, token))
                    continue
            if ".." in p.parts or re.search(r"[*?{},#]", token):
                self.errors.append("{}: files 要是單純的相對路徑（空白分隔、不用 glob）：{}".format(where, token))
                continue
            if not (self.root / p).parent.exists():
                self.warnings.append("manifest L{} [{}]: files 的目錄不存在（新目錄可忽略，打錯路徑會漏規則）：{}".format(line, sl.id, token))
            if p.as_posix() not in paths:
                paths.append(p.as_posix())
        return paths

    def rule_block(self, sl):
        if not sl.files:
            return None
        line = sl.files[0][1]
        if not (self.root / ".claude/rules").is_dir():
            self.errors.append("manifest L{} [{}]: root 底下沒有 .claude/rules，無法列規則：{}".format(line, sl.id, self.root))
            return None
        hits = rulesfor.hits(self.root, self.rule_paths(sl))
        if not hits:
            self.warnings.append("manifest L{} [{}]: files 沒有命中任何規則檔".format(line, sl.id))
            return (line, "files", "## 會自動載入的規則\n這些路徑沒有命中任何規則檔。")
        listing = "\n".join("- `{}`".format(rule) for rule in hits)
        return (line, "files", "## 會自動載入的規則\n讀到該目錄的檔會自動載入，不要再 Read 一次：\n" + listing)

    def ondemand_block(self, sl, sources):
        entries = {}
        for path, _line in self.man.aliases.values():
            entries[self.abs(path)] = ""
        for path in sources:
            entries.setdefault(path, "")
        line = None
        for token, note, n in self.man.ondemand + sl.ondemand:
            path = self.locate(token, n, "ondemand", sl.id)
            line = n
            if path is not None and (note or path not in entries):
                entries[path] = note
        if not entries:
            return None
        rows = ["- `{}`{}".format(p, " " + note if note else "") for p, note in entries.items()]
        return (line or sl.line, "需要時再讀", "## 需要時再讀\n" + "\n".join(rows))

    def build(self, sl):
        """(text, required bytes, [(manifest line, what, block text, is line range, heading body)])."""
        heads = self.head_blocks(sl)
        excerpts, sources = self.excerpt_blocks(sl)
        rules = self.rule_block(sl)
        ondemand = self.ondemand_block(sl, sources)
        shared = sl.shared or self.man.shared
        shared_block = None
        if shared:
            path = self.locate(shared[0], shared[1], "shared", sl.id)
            if path is not None:
                shared_block = "## 共用任務書\n`{}`：先讀這份，路徑、規則與交付格式都在裡面。".format(path)
        if not heads and not excerpts and not self.errors_for(sl):
            self.errors.append("manifest L{} [{}]: 這片沒有 head 也沒有 extract，必讀是空的".format(sl.line, sl.id))

        head_text = "\n\n".join(text for _, _, text in heads)
        excerpt_text = ""
        if excerpts:
            excerpt_text = "## 必讀摘錄\n出處路徑相對於 `{}`。\n".format(self.root) + "".join(b[2] for b in excerpts)
            excerpt_text = excerpt_text.rstrip("\n")
        required = len(head_text.encode("utf-8")) + len(excerpt_text.encode("utf-8"))

        header = None
        if required > LIMIT and not sl.over:
            self.errors.append("manifest L{} [{}]: 必讀 {:.1f}KB 超過 90KB；縮減，或加 `over-90kb: <理由>`".format(
                sl.line, sl.id, required / 1000))
        elif required > LIMIT:
            header = "> 必讀 {:.1f}KB，超過 90KB：{}".format(required / 1000, sl.over[0])
        elif sl.over:
            self.warnings.append("manifest L{} [{}]: 必讀只有 {:.1f}KB，over-90kb 理由沒寫進 brief，可以刪".format(
                sl.over[1], sl.id, required / 1000))

        parts = [header, head_text, shared_block, excerpt_text, rules[2] if rules else None,
                 ondemand[2] if ondemand else None]
        text = "\n\n".join(p for p in parts if p) + "\n"
        blocks = [(line, what, body, False, None) for line, what, body in heads]
        blocks += [(line, what, body.strip("\n"), ranged, verbatim) for line, what, body, ranged, verbatim in excerpts]
        if rules:
            blocks.append((rules[0], rules[1], rules[2], False, None))
        if ondemand:
            blocks.append((ondemand[0], ondemand[1], ondemand[2], False, None))
        return text, required, blocks

    def errors_for(self, sl):
        return any("[{}]".format(sl.id) in e for e in self.errors)


def capped(items):
    if len(items) <= MAX_DETAIL:
        return items
    return items[:MAX_DETAIL] + ["…另 {} 條（修完前面再跑一次）".format(len(items) - MAX_DETAIL)]


def target(man, root, sl):
    out = pathlib.Path(man.out)
    return (out if out.is_absolute() else root / out) / "{}-brief.md".format(sl.id)


def write_all(pending):
    """Temp files first, then rename: a failure while writing leaves every old brief untouched."""
    temps = []
    try:
        for path, _ in pending:
            if path.is_dir():
                raise OSError("目標是目錄：{}".format(path))
        for path, text in pending:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(".{}.tmp-{}".format(path.name, os.getpid()))
            temps.append(tmp)
            tmp.write_bytes(text.encode("utf-8"))
        for (path, _), tmp in zip(pending, temps):
            os.replace(str(tmp), str(path))
    finally:
        for tmp in temps:
            if tmp.exists():
                tmp.unlink()


def stale_details(old, blocks):
    out, drift = [], False
    for line, what, body, ranged, verbatim in blocks:
        if body in old:
            continue
        moved = verbatim is not None and verbatim in old
        out.append("  manifest L{} {} {}".format(line, what, "內文沒變，只是出處行號移了" if moved else "的內容變了"))
        drift = drift or ranged
    out = list(dict.fromkeys(out))
    if drift:
        out.append("  含行號選擇器：來源可能插刪行，重新產生前先核對行號還指到原段落")
    return out or ["  manifest 內容都還在，但 brief 多了或少了別的文字（手改過，或 manifest 刪過東西）"]


def run_check(rows):
    lines, stale = [], 0
    for sl, path, text, required, blocks in rows:
        size = "{}  {:.1f}KB  {}".format(sl.id, required / 1000, path)
        if not path.is_file():
            stale += 1
            lines.append(size + "  缺檔")
        elif path.read_bytes() != text.encode("utf-8"):
            stale += 1
            lines.append(size + "  過期")
            lines.extend(capped(stale_details(path.read_text(encoding="utf-8"), blocks)))
        else:
            lines.append(size + "  一致")
    if stale:
        print("mkbrief --check: FAIL {} 份過期或缺檔（共 {} 份），沒寫任何檔".format(stale, len(rows)))
    else:
        print("mkbrief --check: OK {} 份一致".format(len(rows)))
    print("\n".join(lines))
    return 1 if stale else 0


def print_list(selected):
    print("mkbrief: {} 片".format(len(selected)))
    for sl in selected:
        extracts = sum(1 for e in sl.excerpts if e["kind"] == "extract")
        print("{}  head={} extract={} files={} ondemand={}".format(
            sl.id, len(sl.heads), extracts, len(sl.files), len(sl.ondemand)))


def run(args):
    try:
        text = pathlib.Path(args.manifest).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        print("mkbrief: 讀不了 manifest：{}".format(e), file=sys.stderr)
        return 2
    try:
        man = parse(text)
    except ManifestError as e:
        print("mkbrief: FAIL manifest 語法，沒寫任何檔", file=sys.stderr)
        print(e, file=sys.stderr)
        return 2

    known = {sl.id for sl in man.slices}
    wanted = list(dict.fromkeys(args.only or []))
    unknown = [sid for sid in wanted if sid not in known]
    if unknown:
        print("mkbrief: --only 的片號不在 manifest：{}".format("、".join(unknown)), file=sys.stderr)
        return 2
    selected = [sl for sl in man.slices if not wanted or sl.id in wanted]
    if args.list:
        print_list(selected)
        return 0

    builder = Builder(man)
    builder.check_preamble()
    rows = []
    for sl in selected:
        built = builder.build(sl)
        rows.append((sl, target(man, builder.root, sl)) + built)
    errors = list(dict.fromkeys(builder.errors))
    warnings = list(dict.fromkeys(builder.warnings))
    if errors:
        print("mkbrief: FAIL {} 條，沒寫任何檔".format(len(errors)))
        print("\n".join(capped(errors)), file=sys.stderr)
        return 1

    if args.check:
        code = run_check(rows)
    else:
        write_all([(path, text) for _, path, text, _, _ in rows])
        print("mkbrief: OK {} 份{}".format(len(rows), "，{} 條警告".format(len(warnings)) if warnings else ""))
        for sl, path, _, required, _ in rows:
            print("{}  {:.1f}KB  {}{}".format(sl.id, required / 1000, path, "  over-90kb" if required > LIMIT else ""))
        code = 0
    if warnings:
        print("\n".join("警告 " + w for w in capped(warnings)))
    return code


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest")
    ap.add_argument("--only", nargs="+", action="extend", metavar="ID")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--list", action="store_true")
    args = ap.parse_args()
    try:
        return run(args)
    except (OSError, UnicodeDecodeError) as e:
        print("mkbrief: 工具錯誤，沒有完成：{}".format(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
