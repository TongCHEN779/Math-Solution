#!/usr/bin/env python3
"""Build the Math-Solution site.

    python3 build.py            # build every book into docs/
    python3 build.py --no-pdf   # skip LaTeX->PDF (HTML only; reuses cached SVG figures)

Source layout (see README.md):
    books/<slug>/book.json      metadata + chapter list
    books/<slug>/book.pdf       the textbook
    books/<slug>/chapter-N.tex  restated problems + solutions (LaTeX, shared macros in tools/)
Output (GitHub Pages serves docs/):
    docs/index.html, docs/books/<slug>/index.html, docs/books/<slug>/chapter-N/{index.html,solutions.pdf,fig-*.svg}
"""
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
BOOKS = ROOT / "books"
TOOLS = ROOT / "tools"
CACHE = ROOT / "build"
MATHJAX = "https://cdn.jsdelivr.net/npm/mathjax@3.2.2/es5/tex-chtml.js"


# --------------------------------------------------------------------------
# LaTeX macros -> MathJax macros (tools/macros.tex is the single source)
# --------------------------------------------------------------------------
def mathjax_macros():
    macros = {}
    for line in (TOOLS / "macros.tex").read_text().splitlines():
        m = re.match(r"\\newcommand\{\\(\w+)\}\{(.*)\}\s*$", line)
        if m:
            macros[m.group(1)] = m.group(2)
    return macros


# --------------------------------------------------------------------------
# Minimal LaTeX (the subset used in the solution files) -> HTML
# --------------------------------------------------------------------------
def take_group(s, i):
    """s[i] == '{'; return (content, index after matching '}')."""
    assert s[i] == "{", s[i:i + 30]
    depth, j = 0, i
    while j < len(s):
        if s[j] == "\\":
            j += 2
            continue
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1:j], j + 1
        j += 1
    raise ValueError("unbalanced braces near: " + s[i:i + 60])


def roman(n):
    vals = [(10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
    out = ""
    for v, r in vals:
        while n >= v:
            out += r
            n -= v
    return out


class Converter:
    def __init__(self, figure_dir, fig_prefix="fig"):
        self.store = []
        self.figure_dir = figure_dir
        self.fig_prefix = fig_prefix
        self.fig_count = 0
        self.figures = []  # (index, tikz source)

    # tokens ---------------------------------------------------------------
    def stash(self, h):
        self.store.append(h)
        return "\x00%d\x00" % (len(self.store) - 1)

    def resolve(self, s):
        while "\x00" in s:
            s = re.sub(r"\x00(\d+)\x00", lambda m: self.store[int(m.group(1))], s)
        return s

    # blocks -----------------------------------------------------------------
    def blocks(self, text):
        """Full block-level conversion; returns HTML (tokens unresolved)."""
        text = re.sub(r"\\hfill\s*\$\\square\$", "\x02", text)

        def fig(m):
            self.fig_count += 1
            n = self.fig_count
            self.figures.append((n, m.group(1)))
            return self.stash(
                '<figure class="fig"><img src="%s-%d.svg" alt="Figure %d"></figure>'
                % (self.fig_prefix, n, n))
        text = re.sub(r"\\begin\{center\}\s*(\\begin\{tikzpicture\}.*?\\end\{tikzpicture\})\s*\\end\{center\}",
                      fig, text, flags=re.S)

        def disp(m):
            return self.stash('<div class="display">%s</div>' % html.escape(m.group(0), quote=False))
        text = re.sub(r"\\begin\{(align\*?)\}.*?\\end\{\1\}", disp, text, flags=re.S)

        def disp2(m):
            return self.stash('<div class="display">\\[%s\\]</div>' % html.escape(m.group(1), quote=False))
        text = re.sub(r"(?<!\\)\\\[(.*?)\\\]", disp2, text, flags=re.S)

        def lst(m):
            kind, opt, body = m.group(1), m.group(2) or "", m.group(3)
            label = None
            lm = re.search(r"label=([^,\]]*)", opt)
            if lm:
                label = lm.group(1)
            items = body.split("\\item")[1:]
            out = ['<ul class="lst">' if kind == "itemize" else '<ol class="lst">']
            for k, it in enumerate(items, 1):
                if kind == "itemize":
                    lab = "•"
                elif label:
                    lab = (label.replace("\\roman*", roman(k)).replace("\\alph*", chr(96 + k))
                           .replace("\\arabic*", str(k)))
                else:
                    lab = "%d." % k
                out.append('<li><span class="lab">%s</span><div class="item">%s</div></li>'
                           % (html.escape(lab), self.blocks(it.strip())))
            out.append("</ul>" if kind == "itemize" else "</ol>")
            return self.stash("".join(out))
        text = re.sub(r"\\begin\{(itemize|enumerate)\}(\[[^\]]*\])?(.*?)\\end\{\1\}", lst, text, flags=re.S)

        text = re.sub(r"(?<!\\)\$(.+?)(?<!\\)\$",
                      lambda m: self.stash("\\(%s\\)" % html.escape(m.group(1), quote=False)),
                      text, flags=re.S)

        out = []
        for para in re.split(r"\n\s*\n", text):
            # split a paragraph around block-level tokens
            pieces = re.split(r"(\x00\d+\x00)", para)
            buf = []

            def flush():
                t = "".join(buf).strip()
                buf.clear()
                if t:
                    h = self.inline(t)
                    if h.strip():
                        out.append("<p>%s</p>" % h)
            for p in pieces:
                m = re.fullmatch(r"\x00(\d+)\x00", p)
                if m and self.store[int(m.group(1))].startswith(("<div class=\"display", "<ul", "<ol", "<figure")):
                    flush()
                    out.append(p)
                else:
                    buf.append(p)
            flush()
        return "\n".join(out)

    # inline -----------------------------------------------------------------
    IGNORE = {"noindent", "bigskip", "medskip", "smallskip", "par", "small", "large", "normalsize", "pf",
              "hfill", "quad", "qquad", "relax"}
    WRAP = {"textbf": "strong", "emph": "em", "textit": "em", "texttt": "code"}

    def typo(self, s):
        s = s.replace("---", "—").replace("--", "–").replace("``", "“").replace("''", "”").replace("~", "\u00a0")
        return html.escape(s, quote=False)

    def inline(self, s):
        out, buf, i, n = [], [], 0, len(s)

        def flush():
            if buf:
                out.append(self.typo("".join(buf)))
                buf.clear()
        while i < n:
            c = s[i]
            if c == "\x00":
                flush()
                j = s.index("\x00", i + 1)
                out.append(s[i:j + 1])
                i = j + 1
            elif c == "\x02":
                flush()
                out.append('<span class="qed">∎</span>')
                i += 1
            elif c == "\\":
                m = re.match(r"\\([A-Za-z]+)\*?", s[i:])
                if m:
                    flush()
                    name = m.group(1)
                    i += m.end()
                    if name in self.WRAP:
                        while s[i] == " ":
                            i += 1
                        g, i = take_group(s, i)
                        out.append("<%s>%s</%s>" % (self.WRAP[name], self.inline(g), self.WRAP[name]))
                    elif name in self.IGNORE:
                        while i < n and s[i] in " \n":
                            i += 1
                    elif name in ("ldots", "dots"):
                        out.append("…")
                    else:
                        raise ValueError("unsupported text command \\%s near: %r" % (name, s[max(0, i - 40):i + 40]))
                else:
                    flush()
                    d = s[i + 1] if i + 1 < n else ""
                    i += 2
                    if d == " ":
                        out.append(" ")
                    elif d == ",":
                        out.append("\u2009")
                    elif d == "\\":
                        out.append("<br>")
                        if i < n and s[i] == "[":
                            i = s.index("]", i) + 1
                    elif d in "&%#_${}":
                        out.append(html.escape(d))
                    else:
                        raise ValueError("unsupported escape \\%s" % d)
            elif c == "{":
                flush()
                g, i = take_group(s, i)
                if g.lstrip().startswith("\\small"):
                    out.append("<small>%s</small>" % self.inline(g))
                else:
                    out.append(self.inline(g))
            elif c == "}":
                i += 1
            else:
                buf.append(c)
                i += 1
        flush()
        return "".join(out)


# --------------------------------------------------------------------------
# Chapter parsing
# --------------------------------------------------------------------------
def parse_chapter(tex):
    body = tex.split("\\maketitle", 1)[1].rsplit("\\end{document}", 1)[0]
    body = re.sub(r"(?m)^\s*%.*\n?", "", body)  # drop full-line LaTeX comments
    items, pos = [], 0
    pat = re.compile(r"\\section\*\{([^}]*)\}|\\exercise\{")
    cur = None  # current exercise dict collecting solution text
    last_end = 0
    intro_chunks = []

    def close(upto):
        nonlocal cur
        chunk = body[last_end:upto].strip()
        if cur is not None:
            cur["solution"] = chunk
            items.append(cur)
            cur = None
        elif chunk and items == [] and not intro_chunks:
            intro_chunks.append(chunk)
        elif chunk:
            items.append({"kind": "text", "text": chunk})
    for m in pat.finditer(body):
        if m.start() < last_end:
            continue
        close(m.start())
        if m.group(1) is not None:
            items.append({"kind": "section", "title": m.group(1)})
            last_end = m.end()
        else:
            label, j = take_group(body, m.end() - 1)
            stmt, j = take_group(body, j)
            cur = {"kind": "exercise", "label": label, "statement": stmt}
            last_end = j
    close(len(body))
    return (intro_chunks[0] if intro_chunks else ""), items


# --------------------------------------------------------------------------
# Figures: tikz -> SVG (cached)
# --------------------------------------------------------------------------
def build_figure(tikz, dest):
    key = hashlib.sha1((tikz + (TOOLS / "macros.tex").read_text()).encode()).hexdigest()[:16]
    cached = CACHE / "figs" / (key + ".svg")
    if not cached.exists():
        work = CACHE / "figwork" / key
        work.mkdir(parents=True, exist_ok=True)
        (work / "fig.tex").write_text(
            "\\def\\pgfsysdriver{pgfsys-dvisvgm.def}\n"
            "\\documentclass[border=6pt]{standalone}\n\\usepackage{tikz}\n"
            "\\input{%s}\n\\begin{document}\n%s\n\\end{document}\n" % ((TOOLS / "macros.tex").as_posix(), tikz))
        run(["latex", "-interaction=nonstopmode", "-halt-on-error", "fig.tex"], work)
        run(["dvisvgm", "--no-fonts", "-o", "fig.svg", "fig.dvi"], work)
        cached.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(work / "fig.svg", cached)
    shutil.copy(cached, dest)


def run(cmd, cwd):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("command failed: %s\n%s\n%s" % (" ".join(cmd), r.stdout[-2500:], r.stderr[-1500:]))


def build_pdf(tex_path, dest):
    out = CACHE / "pdf" / tex_path.stem
    out.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error",
             "-output-directory=" + str(out), tex_path.name], tex_path.parent)
    shutil.copy(out / (tex_path.stem + ".pdf"), dest)


# --------------------------------------------------------------------------
# HTML templates
# --------------------------------------------------------------------------
CSS = """\
:root{--bg:#fbfaf7;--fg:#1d1f23;--muted:#667085;--card:#ffffff;--line:#e4e1d9;--accent:#1d4ed8;--accent-soft:#e8eefc;
--prob:#f3f0e8;--prob-line:#d9d2bd;--code:#f1efe9;--shadow:0 1px 2px rgba(0,0,0,.05)}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#121316;--fg:#e8e8ea;--muted:#9aa0ab;--card:#1a1c21;--line:#2b2e35;
--accent:#8ab4ff;--accent-soft:#1e2a44;--prob:#1f2127;--prob-line:#363a45;--code:#23262d;--shadow:none}}
:root[data-theme=dark]{--bg:#121316;--fg:#e8e8ea;--muted:#9aa0ab;--card:#1a1c21;--line:#2b2e35;--accent:#8ab4ff;--accent-soft:#1e2a44;
--prob:#1f2127;--prob-line:#363a45;--code:#23262d;--shadow:none}
*{box-sizing:border-box}
html{scroll-behavior:smooth;scroll-padding-top:70px}
body{margin:0;background:var(--bg);color:var(--fg);font:17px/1.65 "Charter","Iowan Old Style","Palatino Linotype",Georgia,serif}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.top{position:sticky;top:0;z-index:10;background:color-mix(in srgb,var(--bg) 92%,transparent);backdrop-filter:blur(8px);border-bottom:1px solid var(--line)}
.top .in{max-width:1180px;margin:0 auto;padding:.7rem 16px;display:flex;gap:.6rem;align-items:center;font:14px/1.2 system-ui,-apple-system,"Segoe UI",sans-serif}
.top .crumbs{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--muted)}
.top .crumbs b{color:var(--fg);font-weight:600}
button,.btn{font:600 13px/1 system-ui,sans-serif;border:1px solid var(--line);background:var(--card);color:var(--fg);padding:.55rem .8rem;border-radius:8px;cursor:pointer;display:inline-flex;gap:.4rem;align-items:center;text-decoration:none!important;box-shadow:var(--shadow)}
button:hover,.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.btn.primary:hover{color:#fff;filter:brightness(1.08)}
.wrap{max-width:1180px;margin:0 auto;padding:0 16px}
.hero{padding:2.4rem 0 1.2rem}
.hero h1{font:700 clamp(1.7rem,4vw,2.5rem)/1.15 system-ui,-apple-system,"Segoe UI",sans-serif;margin:0 0 .4rem;letter-spacing:-.02em}
.hero p{margin:.2rem 0;color:var(--muted)}
.hero .actions{display:flex;flex-wrap:wrap;gap:.6rem;margin-top:1.1rem}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:1rem;margin:1.2rem 0 3rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:1.1rem 1.2rem;box-shadow:var(--shadow);display:block;color:inherit!important;text-decoration:none!important}
a.card:hover{border-color:var(--accent)}
.card h3{margin:0 0 .2rem;font:650 1.15rem/1.3 system-ui,sans-serif}
.card .meta{color:var(--muted);font:14px/1.4 system-ui,sans-serif}
.pill{display:inline-block;font:600 12px/1 system-ui,sans-serif;padding:.3rem .55rem;border-radius:99px;background:var(--accent-soft);color:var(--accent)}
.pill.soon{background:var(--code);color:var(--muted)}
table.chapters{width:100%;border-collapse:collapse;margin:1rem 0 3rem;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;font:15px/1.4 system-ui,sans-serif}
table.chapters td{padding:.8rem 1rem;border-top:1px solid var(--line);vertical-align:middle}
table.chapters tr:first-child td{border-top:0}
table.chapters td.n{width:3rem;color:var(--muted);font-variant-numeric:tabular-nums}
table.chapters td.act{text-align:right;white-space:nowrap}
table.chapters tr.soon td{color:var(--muted)}
.layout{display:grid;grid-template-columns:240px minmax(0,1fr);gap:2rem;align-items:start}
nav.toc{position:sticky;top:64px;max-height:calc(100vh - 80px);overflow:auto;font:14px/1.35 system-ui,sans-serif;padding:.5rem 0 2rem}
nav.toc h4{margin:1rem 0 .4rem;font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
nav.toc a{display:inline-block;padding:.22rem .55rem;margin:.1rem .1rem .1rem 0;border-radius:6px;color:var(--fg);border:1px solid var(--line);background:var(--card)}
nav.toc a:hover{border-color:var(--accent);text-decoration:none;color:var(--accent)}
main h2.sec{font:700 1.35rem/1.3 system-ui,sans-serif;margin:2.6rem 0 .6rem;padding-bottom:.4rem;border-bottom:2px solid var(--line)}
.intro{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:.4rem 1.2rem;margin:1rem 0 1rem;font-size:16px}
.exercise{background:var(--card);border:1px solid var(--line);border-radius:12px;margin:1.2rem 0;box-shadow:var(--shadow);overflow:hidden}
.exercise>h3{margin:0;padding:.8rem 1.2rem;font:650 1.05rem/1.3 system-ui,sans-serif;border-bottom:1px solid var(--line)}
.exercise>h3 a{color:var(--fg)}
.exercise>h3 a:hover{color:var(--accent);text-decoration:none}
.problem{background:var(--prob);border-bottom:1px solid var(--prob-line);padding:.2rem 1.2rem .3rem;font-size:16.5px}
.tag{display:inline-block;font:700 11px/1 system-ui,sans-serif;text-transform:uppercase;letter-spacing:.09em;color:var(--muted);margin:.8rem 0 .1rem}
details.solution>summary{cursor:pointer;list-style:none;padding:.7rem 1.2rem;font:700 11px/1 system-ui,sans-serif;text-transform:uppercase;letter-spacing:.09em;color:var(--accent);user-select:none}
details.solution>summary::-webkit-details-marker{display:none}
details.solution>summary::before{content:"▾ ";}
details.solution:not([open])>summary::before{content:"▸ ";}
.sol{padding:0 1.2rem .6rem}
p{margin:.7em 0}
.lst{list-style:none;padding:0;margin:.5em 0}
.lst li{display:flex;gap:.6em;margin:.35em 0}
.lst .lab{flex:0 0 2.1em;text-align:right;color:var(--muted)}
ul.lst .lab{flex-basis:1.2em;text-align:center}
.lst .item{flex:1;min-width:0}.lst .item>p:first-child{margin-top:0}.lst .item>p:last-child{margin-bottom:0}
.display{overflow-x:auto;overflow-y:hidden;padding:.1em 0}
.qed{float:right;margin-left:1rem;color:var(--muted)}
figure.fig{margin:1rem 0;padding:.8rem;background:#fff;border:1px solid var(--line);border-radius:10px;text-align:center;overflow-x:auto}
figure.fig img{max-width:100%;height:auto;min-width:420px}
code{background:var(--code);padding:.1em .35em;border-radius:4px;font-size:.92em}
footer{border-top:1px solid var(--line);color:var(--muted);font:14px/1.5 system-ui,sans-serif;padding:1.4rem 0 3rem;margin-top:3rem}
mjx-container{max-width:100%}
@media (max-width:900px){.layout{display:block}nav.toc{position:static;max-height:none;border-bottom:1px solid var(--line);margin-bottom:1rem}
body{font-size:16px}.top .crumbs{display:none}}
@media print{.top,nav.toc,footer{display:none}.layout{display:block}.exercise{box-shadow:none;break-inside:avoid-page}}
"""

JS_THEME = "<script>try{var t=localStorage.getItem('theme');if(t)document.documentElement.dataset.theme=t}catch(e){}</script>"
JS_UI = """<script>
function setAll(o){document.querySelectorAll('details.solution').forEach(function(d){d.open=o})}
function toggleTheme(){var r=document.documentElement,dark=r.dataset.theme?r.dataset.theme==='dark':matchMedia('(prefers-color-scheme:dark)').matches;
var t=dark?'light':'dark';r.dataset.theme=t;try{localStorage.setItem('theme',t)}catch(e){}}
if(location.hash){var e=document.querySelector(location.hash);if(e&&e.closest('details'))e.closest('details').open=true}
</script>"""


def page(title, body, rel, crumbs, mathjax=None, extra_top=""):
    """rel: path prefix from this page to docs root, e.g. '../../'."""
    mj = ""
    if mathjax is not None:
        cfg = {"tex": {"inlineMath": [["\\(", "\\)"]], "displayMath": [["\\[", "\\]"]],
                       "macros": mathjax, "processEnvironments": True},
               "options": {"skipHtmlTags": ["script", "noscript", "style", "textarea", "pre", "code"]}}
        mj = "<script>window.MathJax=%s;</script>\n<script defer src=\"%s\"></script>" % (json.dumps(cfg), MATHJAX)
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s</title>
%s
<link rel="stylesheet" href="%sassets/style.css">
%s
</head>
<body>
<div class="top"><div class="in"><div class="crumbs">%s</div>%s<button onclick="toggleTheme()" title="Toggle light/dark">◐</button></div></div>
%s
<div class="wrap"><footer>Solutions written with LaTeX; math rendered by MathJax. Source on <a href="https://github.com/TongCHEN779/Math-Solution">GitHub</a>.</footer></div>
%s
</body></html>
""" % (html.escape(title), JS_THEME, rel, mj, crumbs, extra_top, body, JS_UI)


def ex_id(label):
    return "ex-" + re.sub(r"[^0-9A-Za-z]+", "-", label)


def build_chapter(book, ch, macros, do_pdf):
    slug = book["slug"]
    src = BOOKS / slug / ch["tex"]
    outdir = DOCS / "books" / slug / ("chapter-%d" % ch["n"])
    outdir.mkdir(parents=True, exist_ok=True)
    tex = src.read_text()
    intro, items = parse_chapter(tex)
    cv = Converter(outdir)
    parts, toc = [], []
    if intro:
        parts.append('<div class="intro">%s</div>' % cv.blocks(intro))
    cur_toc = None
    for it in items:
        if it["kind"] == "section":
            parts.append('<h2 class="sec">%s</h2>' % cv.inline(it["title"]))
            cur_toc = {"title": it["title"], "links": []}
            toc.append(cur_toc)
        elif it["kind"] == "text":
            parts.append('<div class="intro">%s</div>' % cv.blocks(it["text"]))
        else:
            eid = ex_id(it["label"])
            sol = it["solution"]
            parts.append(
                '<section class="exercise" id="%s"><h3><a href="#%s">Exercise %s</a></h3>'
                '<div class="problem"><span class="tag">Problem</span>%s</div>'
                '<details class="solution" open><summary>Solution</summary><div class="sol">%s</div></details></section>'
                % (eid, eid, html.escape(it["label"]), cv.blocks(it["statement"]), cv.blocks(sol)))
            if cur_toc is None:
                cur_toc = {"title": "Exercises", "links": []}
                toc.append(cur_toc)
            cur_toc["links"].append((eid, it["label"]))
    # figures
    for n, tikz in cv.figures:
        build_figure(tikz, outdir / ("fig-%d.svg" % n))
    main = cv.resolve("\n".join(parts))
    nav = "".join('<h4>%s</h4>%s' % (html.escape(t["title"]), " ".join('<a href="#%s">%s</a>' % (i, html.escape(l)) for i, l in t["links"]))
                  for t in toc if t["links"])
    book_pdf = "../%s" % book["pdf"]
    if do_pdf:
        build_pdf(src, outdir / "solutions.pdf")
    n_ex = sum(len(t["links"]) for t in toc)
    body = """<div class="wrap"><div class="hero"><h1>Chapter %d. %s</h1>
<p>%s — %s</p><p>%d exercises, each restated before its solution.</p>
<div class="actions"><a class="btn primary" href="solutions.pdf">⬇ Solutions (PDF)</a><a class="btn" href="%s">📘 The book (PDF)</a>
<button onclick="setAll(true)">Show all solutions</button><button onclick="setAll(false)">Hide all solutions</button></div></div>
<div class="layout"><nav class="toc">%s</nav><main>%s</main></div></div>""" % (
        ch["n"], html.escape(ch["title"]), html.escape(book["title"]), html.escape(book["authors"]), n_ex, book_pdf, nav, main)
    crumbs = '<a href="../../../">Math Solutions</a> / <a href="../">%s</a> / <b>Chapter %d</b>' % (html.escape(book["short"]), ch["n"])
    (outdir / "index.html").write_text(page("%s · Chapter %d — %s" % (book["short"], ch["n"], ch["title"]), body, "../../../", crumbs, macros))
    return n_ex


def build_book(book, macros, do_pdf):
    slug = book["slug"]
    bdir = DOCS / "books" / slug
    bdir.mkdir(parents=True, exist_ok=True)
    shutil.copy(BOOKS / slug / "book.pdf", bdir / book["pdf"])
    rows, solved = [], 0
    for ch in book["chapters"]:
        if ch.get("tex"):
            build_chapter(book, ch, macros, do_pdf)
            solved += 1
            rows.append('<tr><td class="n">%d</td><td><a href="chapter-%d/">%s</a></td><td class="act">'
                        '<a class="btn" href="chapter-%d/">Read online</a> <a class="btn" href="chapter-%d/solutions.pdf">PDF</a></td></tr>'
                        % (ch["n"], ch["n"], html.escape(ch["title"]), ch["n"], ch["n"]))
        else:
            rows.append('<tr class="soon"><td class="n">%d</td><td>%s</td><td class="act"><span class="pill soon">coming soon</span></td></tr>'
                        % (ch["n"], html.escape(ch["title"])))
    book["_solved"] = solved
    body = """<div class="wrap"><div class="hero"><h1>%s</h1><p>%s · %s</p><p>%s</p>
<div class="actions"><a class="btn primary" href="%s">📘 Download the book (PDF)</a></div></div>
<table class="chapters">%s</table></div>""" % (
        html.escape(book["title"]), html.escape(book["authors"]), html.escape(str(book["year"])), html.escape(book["description"]),
        book["pdf"], "\n".join(rows))
    crumbs = '<a href="../../">Math Solutions</a> / <b>%s</b>' % html.escape(book["short"])
    (bdir / "index.html").write_text(page(book["title"] + " — solutions", body, "../../", crumbs))


def main():
    do_pdf = "--no-pdf" not in sys.argv
    macros = mathjax_macros()
    (DOCS / "assets").mkdir(parents=True, exist_ok=True)
    (DOCS / "assets" / "style.css").write_text(CSS)
    (DOCS / ".nojekyll").write_text("")
    books = []
    for bj in sorted(BOOKS.glob("*/book.json")):
        book = json.loads(bj.read_text())
        book["slug"] = bj.parent.name
        build_book(book, macros, do_pdf)
        books.append(book)
    cards = "".join(
        '<a class="card" href="books/%s/"><h3>%s</h3><div class="meta">%s</div><p class="meta">%s</p><span class="pill">%d of %d chapters</span></a>'
        % (b["slug"], html.escape(b["title"]), html.escape(b["authors"]), html.escape(b["description"]), b["_solved"], len(b["chapters"]))
        for b in books)
    body = """<div class="wrap"><div class="hero"><h1>Math Solutions</h1>
<p>Worked solutions to exercises from mathematics textbooks. Every exercise is restated, followed by its solution; each chapter is also available as a PDF.</p></div>
<div class="cards">%s</div></div>""" % cards
    (DOCS / "index.html").write_text(page("Math Solutions", body, "", "<b>Math Solutions</b>"))
    print("built %d book(s) into %s" % (len(books), DOCS))


if __name__ == "__main__":
    main()
