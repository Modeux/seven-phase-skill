#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Task page: SPEC／PLAN／REVIEW／PROGRESS of one seven-phase task as one navigable HTML (stdlib only).

  planview.py <slug|task dir>           # write <checkout>/tmp/progress/<slug>.html, print one summary line
  planview.py <slug> --open             # …and open it in the default browser
  planview.py --check <slug|task dir>   # exit 1 when SPEC.md or PLAN.md has neither a ```mermaid block
                                        #   nor a 「無圖：<理由>」 line, or PLAN.md keeps more than MAX_LONG
                                        #   lines over LONG_LINE chars (phases 3／4 readability gate)

The task files (plus plan-appendix/*.md, collapsed) are embedded verbatim and rendered in the browser (marked for GFM tables, mermaid for
diagrams, both version-pinned on jsdelivr), so the page cannot drift from the markdown: rerun it after
every edit. What the page adds over the IDE preview: a dashboard of counts, a sticky outline, collapsible
`##` sections, tables with a sticky header, wrapped cells and a per-column filter, and rendered diagrams.
WHY: PLAN.md runs 50–130KB with 200+ table rows and single lines past 4,000 chars; the IDE preview scrolls
those sideways and the owner cannot see decisions, slices, tests and gates at once (owner 11-07).
"""
import argparse
import html
import json
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _mdtable  # noqa: E402

PLANS = ".claude/PRPs/plans"
DOCS = ("SPEC.md", "PLAN.md", "REVIEW.md", "PROGRESS.md")
APPENDIX = "plan-appendix"  # machine-facing PLAN parts (rules.md, matrix.md, tests.md…), shown collapsed
GATED = ("SPEC.md", "PLAN.md")
LONG_LINE = 300  # chars; a table row past this is a wall of text in any renderer
MAX_LONG = 5     # PLAN.md lines over LONG_LINE allowed by --check (baseline 45／98／83 before the split)
# doc → [(chip label, heading keyword)]: rows of every table under a `##`/`###` whose title contains the keyword.
COUNTS = {
    "SPEC": [("決議", "決議"), ("假設", "假設"), ("原型差異", "原型差異")],
    "PLAN": [("定案", "定案"), ("切片", "切片"), ("矩陣", "矩陣"), ("測試", "測試"), ("閘", "閘"),
             ("審查地圖", "審查地圖"), ("規則對照", "規則對照")],
    "REVIEW": [("地圖", "地圖"), ("裁決", "裁決")],
    "PROGRESS": [],
}
MARKED = "https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js"
MERMAID = "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js"
_MERMAID_FENCE = re.compile(r"^\s*(?:```|~~~)\s*mermaid\b", re.M)
# 「無圖：<理由>」 anywhere in the doc (bare, bold or as a list item); the reason must be non-empty.
_NO_DIAGRAM = re.compile(r"^\s*(?:[-*]\s*)?\**無圖\**\s*[：:]\s*\S", re.M)
_H2 = re.compile(r"^##\s+(.*?)\s*#*\s*$", re.M)
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def split_frontmatter(text):
    """({key: value}, body) — PROGRESS.md carries `status`／`next`; marked would read the closing `---` as a
    setext h2 and the outline would show the whole header as a section title."""
    m = _FRONTMATTER.match(text)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        k, sep, v = line.partition(":")
        if sep:
            meta[k.strip()] = v.strip()
    return meta, text[m.end():]


def task_dir(arg, cwd="."):
    """`<slug>` under this checkout's .claude/PRPs/plans, or a path to the task directory itself."""
    p = pathlib.Path(arg)
    if p.is_dir() and any((p / d).is_file() for d in DOCS):
        return p.resolve()
    top = toplevel(cwd)
    cand = top / PLANS / arg
    if cand.is_dir():
        return cand.resolve()
    sys.exit(f"planview: no task directory for {arg!r} (tried {cand})")


def toplevel(path="."):
    out = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True)
    return pathlib.Path(out.stdout.strip() or ".").resolve()


def read_docs(tdir):
    docs = {d[:-3]: (tdir / d).read_text(encoding="utf-8") for d in DOCS if (tdir / d).is_file()}
    for p in sorted((tdir / APPENDIX).glob("*.md")):
        docs[f"appendix/{p.stem}"] = p.read_text(encoding="utf-8")
    return docs


def mermaid_count(text):
    return len(_MERMAID_FENCE.findall(text))


def long_lines(text, limit=LONG_LINE):
    """Lines outside code fences longer than `limit` characters."""
    return sum(1 for line in _mdtable.unfenced(text).splitlines() if len(line) > limit)


def no_diagram_declared(text):
    return bool(_NO_DIAGRAM.search(_mdtable.unfenced(text)))


def h2_titles(text):
    return [m.group(1) for m in _H2.finditer(_mdtable.unfenced(text))]


def rows_under(text, keyword):
    return sum(len(t.rows) for t in _mdtable.tables(text) if any(keyword in h for _, h in t.headings))


def owner_items(text):
    """Rows of the PLAN 審查地圖 whose 處置 is empty or hands the call to the owner."""
    n = 0
    for t in _mdtable.tables(text):
        if not any("審查地圖" in h for _, h in t.headings):
            continue
        col = _mdtable.pick_col(t.header, "處置")
        if col is None:
            continue
        for _, c in t.rows:
            cell = c[col].strip() if col < len(c) else ""
            if not cell or cell.startswith("交 owner"):
                n += 1
    return n


def summary(name, text):
    """[(label, count, section index or None)] for the dashboard; section index is 1-based among `##`."""
    titles = h2_titles(text)
    out = []
    kind = "PLAN" if name.startswith("appendix/") else name
    for label, kw in COUNTS.get(kind, []):
        n = rows_under(text, kw)
        if not n:
            continue
        idx = next((i for i, t in enumerate(titles, 1) if kw in t), None)
        out.append((label, n, idx))
    if name == "PLAN":
        hand = owner_items(text)
        if hand:
            idx = next((i for i, t in enumerate(titles, 1) if "審查地圖" in t), None)
            out.append(("交 owner", hand, idx))
    d = mermaid_count(text)
    if name in ("SPEC", "PLAN"):
        out.append(("圖", d, None) if d or not no_diagram_declared(text) else ("無圖（已宣告）", 0, None))
    elif d:
        out.append(("圖", d, None))
    ll = long_lines(text)
    if ll and kind in ("SPEC", "PLAN"):
        out.append(("長行", ll, None))
    return out


def check(tdir):
    """Gate lines for SPEC／PLAN: no mermaid block and no 無圖 declaration; PLAN.md with more than MAX_LONG
    lines over LONG_LINE chars (the machine-facing tables belong in plan-appendix/). [] when fine."""
    bad = []
    for d in GATED:
        p = tdir / d
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8")
        if not mermaid_count(text) and not no_diagram_declared(text):
            bad.append(f"{d}: 沒有 ```mermaid 圖，也沒有「無圖：<理由>」那一行（狀態機／時序／跨層資料流／改表關係要畫；真的沒有就宣告理由）")
        if d == "PLAN.md" and long_lines(text) > MAX_LONG:
            bad.append(f"{d}: 超過 {LONG_LINE} 字的行有 {long_lines(text)} 行（上限 {MAX_LONG}）——規則對照、矩陣的符號／行號、測試整表搬 {APPENDIX}/，儲存格 ≤60 字、長理由寫表格下方小節")
    return bad


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1d2330;--mute:#667085;--line:#e4e7ec;--acc:#3e63dd;--nav:260px}
@media (prefers-color-scheme:dark){:root{--bg:#14161a;--card:#1d2026;--fg:#e6e8ec;--mute:#98a2b3;--line:#2c3038;--acc:#7c9cf5}}
*{box-sizing:border-box}
body{margin:0;font:14px/1.6 -apple-system,"PingFang TC","Noto Sans TC",sans-serif;background:var(--bg);color:var(--fg)}
.top{position:sticky;top:0;z-index:5;background:var(--bg);padding:12px 20px;border-bottom:1px solid var(--line)}
.top h1{font-size:17px;margin:0 0 6px;display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}
.top h1 small{color:var(--mute);font-weight:normal;font-size:12px}
.dash{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center}
.dash b{font-size:12px;color:var(--mute);margin-right:2px}
.chip{border:1px solid var(--line);background:var(--card);border-radius:999px;padding:0 9px;font-size:12px;color:var(--fg);text-decoration:none;white-space:nowrap}
.chip.warn{border-color:#f5a524;color:#b4690e}.chip.ok{border-color:#30a46c;color:#218358}
.chip:hover{border-color:var(--acc)}
.wrap{display:grid;grid-template-columns:var(--nav) minmax(0,1fr)}
nav{position:sticky;top:64px;align-self:start;max-height:calc(100vh - 64px);overflow:auto;padding:12px 10px 24px 16px;border-right:1px solid var(--line);font-size:12px}
nav h2{font-size:12px;margin:10px 0 4px;color:var(--mute);text-transform:uppercase;letter-spacing:.04em}
nav a{display:block;color:var(--fg);text-decoration:none;padding:2px 6px;border-radius:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
nav a:hover{background:var(--card)}
nav .tools{display:flex;gap:6px;margin:0 0 6px}
nav button{font:inherit;font-size:11px;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:6px;padding:2px 8px;cursor:pointer}
main{padding:12px 24px 80px;min-width:0}
.doc{margin-bottom:28px}
.doc>h1{font-size:20px;margin:18px 0 8px;padding-bottom:6px;border-bottom:2px solid var(--line)}
details.sec{background:var(--card);border:1px solid var(--line);border-radius:8px;margin:10px 0;padding:0 14px}
details.sec>summary{cursor:pointer;font-size:15px;font-weight:600;padding:9px 0;list-style:none;display:flex;gap:10px;align-items:baseline}
details.sec>summary::before{content:"▸";color:var(--mute);font-size:12px}
details.sec[open]>summary::before{content:"▾"}
details.sec>summary .n{font-weight:normal;font-size:12px;color:var(--mute)}
details.sec>.body{padding:0 0 12px}
h3{font-size:14px;margin:16px 0 6px}h4{font-size:13px;margin:12px 0 4px}
p{margin:6px 0}ul,ol{padding-left:22px;margin:4px 0}li{margin:2px 0}
code{font:12px ui-monospace,Menlo,monospace;background:rgba(127,127,127,.12);padding:0 4px;border-radius:4px;word-break:break-all}
pre{background:rgba(127,127,127,.1);padding:10px;border-radius:6px;overflow:auto;font:12px/1.5 ui-monospace,Menlo,monospace}
pre code{background:none;padding:0}
pre.mermaid{background:var(--card);text-align:center;overflow:auto}
pre.mermaid svg{max-width:100%;height:auto}
blockquote{margin:6px 0;padding:2px 12px;border-left:3px solid var(--line);color:var(--mute)}
a{color:var(--acc)}
.tw{max-height:72vh;overflow:auto;border:1px solid var(--line);border-radius:6px;margin:8px 0}
.tw.full{max-height:none}
table{border-collapse:separate;border-spacing:0;font-size:12.5px;min-width:100%}
thead th{position:sticky;top:0;background:var(--card);z-index:1;text-align:left;border-bottom:1px solid var(--line);padding:6px 8px;vertical-align:bottom}
thead tr.flt th{top:30px;padding:2px 6px 6px;border-bottom:2px solid var(--line)}
thead input{width:100%;min-width:4em;font:inherit;font-size:11px;padding:1px 5px;border:1px solid var(--line);border-radius:4px;background:var(--bg);color:var(--fg)}
td{padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top;min-width:7em;max-width:44em;white-space:normal;overflow-wrap:anywhere}
td:first-child{white-space:nowrap;min-width:0}
tbody tr:hover td{background:rgba(127,127,127,.07)}
.tcap{display:flex;gap:10px;align-items:center;font-size:11px;color:var(--mute);margin:-2px 0 4px}
.tcap button{font:inherit;font-size:11px;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:6px;padding:0 7px;cursor:pointer}
.raw{white-space:pre-wrap}
mark{background:#ffe58f;color:inherit}
"""

JS = r"""
(function(){
  const esc=s=>s.replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const dark=matchMedia('(prefers-color-scheme: dark)').matches;
  const main=document.getElementById('main'), nav=document.getElementById('toc');
  if(!window.marked){ // offline: show the markdown as text so the page is still readable
    for(const [name,md] of Object.entries(DOCS)){
      const s=document.createElement('section');s.className='doc';
      s.innerHTML='<h1>'+esc(name)+'</h1><pre class="raw">'+esc(md)+'</pre>';main.appendChild(s);
    }
    nav.innerHTML='<p>marked 沒載入（離線？）：顯示原文</p>';return;
  }
  marked.use({gfm:true,renderer:{code(code,lang){
    if((lang||'').trim()==='mermaid') return '<pre class="mermaid">'+esc(code)+'</pre>';
    return '<pre><code class="lang-'+esc((lang||'').trim())+'">'+esc(code)+'</code></pre>';
  }}});
  const tocParts=[];
  for(const [name,md] of Object.entries(DOCS)){
    const sec=document.createElement('section');sec.className='doc';sec.id='doc-'+name;
    const h=document.createElement('h1');h.textContent=name+'.md';sec.appendChild(h);
    const tmp=document.createElement('div');tmp.innerHTML=marked.parse(md);
    // group every node after an h2 into a collapsible section; nodes before the first h2 stay as a preamble
    let cur=null,n=0;const links=[];
    for(const node of Array.from(tmp.childNodes)){
      if(node.nodeType===1&&node.tagName==='H2'){
        n++;const d=document.createElement('details');d.className='sec';d.open=!name.startsWith('appendix/');d.id=name+'-'+n;
        const sm=document.createElement('summary');sm.innerHTML='<span class="h">'+node.innerHTML+'</span><span class="n"></span>';
        const body=document.createElement('div');body.className='body';d.append(sm,body);sec.appendChild(d);cur=body;
        links.push('<a href="#'+d.id+'" title="'+esc(node.textContent)+'">'+node.innerHTML+'</a>');continue;
      }
      (cur||sec).appendChild(node);
    }
    main.appendChild(sec);
    tocParts.push('<h2><a href="#doc-'+name+'">'+esc(name)+'</a></h2>'+links.join(''));
  }
  nav.insertAdjacentHTML('beforeend',tocParts.join(''));
  // section summaries: table rows ＋ diagrams ＋ characters, so a collapsed outline still tells the size
  for(const d of main.querySelectorAll('details.sec')){
    const rows=d.querySelectorAll('tbody tr').length,figs=d.querySelectorAll('pre.mermaid').length;
    const chars=d.querySelector('.body').textContent.length;
    d.querySelector('.n').textContent=[rows?rows+' 列':'',figs?figs+' 圖':'',Math.round(chars/100)/10+'k 字'].filter(Boolean).join(' · ');
  }
  // tables: scroll box with sticky header, per-column filter, row counter, expand toggle
  for(const t of main.querySelectorAll('table')){
    const w=document.createElement('div');w.className='tw';t.parentNode.insertBefore(w,t);w.appendChild(t);
    const head=t.tHead,body=t.tBodies[0];if(!head||!body)continue;
    const cols=head.rows[0].cells.length,all=Array.from(body.rows);
    const cap=document.createElement('div');cap.className='tcap';
    const cnt=document.createElement('span');const btn=document.createElement('button');btn.textContent='展開全高';
    btn.onclick=()=>{w.classList.toggle('full');btn.textContent=w.classList.contains('full')?'收回捲動框':'展開全高';};
    cap.append(cnt,btn);w.parentNode.insertBefore(cap,w);
    const fr=head.insertRow();fr.className='flt';const inputs=[];
    for(let i=0;i<cols;i++){const th=document.createElement('th');const inp=document.createElement('input');inp.placeholder='篩選';th.appendChild(inp);fr.appendChild(th);inputs.push(inp);}
    const apply=()=>{const q=inputs.map(i=>i.value.trim().toLowerCase());let shown=0;
      for(const r of all){let ok=true;for(let i=0;i<cols&&ok;i++){if(q[i]&&!(r.cells[i]?.textContent||'').toLowerCase().includes(q[i]))ok=false;}
        r.style.display=ok?'':'none';if(ok)shown++;}
      cnt.textContent=shown===all.length?all.length+' 列':shown+' / '+all.length+' 列';};
    inputs.forEach(i=>i.addEventListener('input',apply));apply();
  }
  document.getElementById('all-open').onclick=()=>main.querySelectorAll('details.sec').forEach(d=>d.open=true);
  document.getElementById('all-close').onclick=()=>main.querySelectorAll('details.sec').forEach(d=>d.open=false);
  // a #hash from the dashboard or outline must open the section it points into
  const reveal=()=>{const el=location.hash&&document.getElementById(location.hash.slice(1));if(el&&el.tagName==='DETAILS')el.open=true;};
  addEventListener('hashchange',reveal);reveal();
  if(window.mermaid){
    mermaid.initialize({startOnLoad:false,theme:dark?'dark':'default',securityLevel:'loose'});
    mermaid.run({querySelector:'pre.mermaid'}).catch(e=>console.error('mermaid',e));
  }
})();
"""


def render(slug, docs, when):
    e = html.escape
    chips, bodies = [], {}
    for name, text in docs.items():
        meta, bodies[name] = split_frontmatter(text)
        parts = []
        if meta.get("status"):
            cls = " ok" if meta["status"] in ("deploy", "done") else (" warn" if meta["status"] == "blocked" else "")
            parts.append(f'<a class="chip{cls}" href="#doc-{name}" title="{e(meta.get("next", ""))}">status {e(meta["status"])}</a>')
        if meta.get("next"):
            parts.append(f'<a class="chip" href="#doc-{name}">next：{e(meta["next"][:40])}{"…" if len(meta["next"]) > 40 else ""}</a>')
        for label, n, idx in summary(name, text):
            cls = ""
            if label == "交 owner":
                cls = " warn"
            elif label == "圖":
                cls = " ok" if n else " warn"
            elif label == "長行":
                cls = " warn" if (name == "PLAN" and n > MAX_LONG) else ""
            href = f"#{name}-{idx}" if idx else f"#doc-{name}"
            parts.append(f'<a class="chip{cls}" href="{href}">{e(label)} {n}</a>')
        if parts:
            chips.append(f'<b>{e(name)}</b>' + "".join(parts))
    # INVARIANT: the markdown goes in as a JS string literal; `</` is split so a `</script>` inside a PLAN
    # cannot end the block. Everything else is left to JSON (quotes, newlines, U+2028 are legal in ES2019).
    payload = json.dumps(bodies, ensure_ascii=False).replace("</", "<\\/")
    return (f'<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            f'<title>{e(slug)}</title><style>{CSS}</style>'
            f'<script src="{MARKED}"></script><script src="{MERMAID}"></script></head><body>'
            f'<div class="top"><h1>{e(slug)} <small>產生於 {e(when)}；改了任務檔就重跑 <code>planview.py {e(slug)}</code>，內容是當時的 md 原文</small></h1>'
            f'<div class="dash">{"".join(chips)}</div></div>'
            f'<div class="wrap"><nav id="toc"><div class="tools"><button id="all-open">全部展開</button><button id="all-close">全部收合</button></div></nav>'
            f'<main id="main"></main></div>'
            f'<script>const DOCS={payload};</script><script>{JS}</script></body></html>')


def summary_line(out, docs):
    bits = []
    for name, text in docs.items():
        s = summary(name, text)
        if s:
            bits.append(f"{name} " + "、".join(f"{label} {n}" if label != "無圖（已宣告）" else label for label, n, _ in s))
    return f"planview: {out} · " + " · ".join(bits)


def main(argv):
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("task", help="slug under .claude/PRPs/plans, or the task directory")
    ap.add_argument("--check", action="store_true", help="only run the SPEC／PLAN diagram gate")
    ap.add_argument("--open", action="store_true", help="open the page in the default browser (macOS `open`)")
    ap.add_argument("--out", help="write the page here instead of <checkout>/tmp/progress/<slug>.html")
    a = ap.parse_args(argv)
    tdir = task_dir(a.task)
    slug = tdir.name
    if a.check:
        bad = check(tdir)
        for line in bad:
            print(f"planview --check {slug}: {line}")
        if not bad:
            print(f"planview --check {slug}: ok")
        return 1 if bad else 0
    docs = read_docs(tdir)
    if not docs:
        sys.exit(f"planview: {tdir} has none of {', '.join(DOCS)}")
    out = pathlib.Path(a.out) if a.out else toplevel(tdir) / "tmp" / "progress" / f"{slug}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    import datetime
    when = datetime.datetime.now().strftime("%m-%d %H:%M")
    out.write_text(render(slug, docs, when), encoding="utf-8")
    print(summary_line(out, docs))
    bad = check(tdir)
    for line in bad:
        print(f"planview: 好讀閘未過 — {line}")
    if a.open and sys.platform == "darwin":
        subprocess.run(["open", str(out)], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
