#!/usr/bin/env python3
"""Check a body-only part file (\\section*{..} and \\exercise{..}{..}\\pf ... items) for a book.

    python3 tools/check_part.py books/hartshorne/part-1.tex

Wraps it in the standard header, compiles it with pdflatex (to a scratch dir under build/) and runs the
HTML converter on it (no docs/ output), printing the number of exercises or the first error.
"""
import re, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import build  # noqa: E402

part = Path(sys.argv[1]).resolve()
body = part.read_text()
wrapper = part.parent / ("_check_" + part.stem + ".tex")
wrapper.write_text("\\documentclass[11pt]{article}\n\\input{../../tools/preamble.tex}\n\\title{check}\n\\author{}\n\\date{}\n"
                   "\\begin{document}\n\\maketitle\n" + body + "\n\\end{document}\n")
try:
    out = ROOT / "build" / "partcheck" / part.stem
    out.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "-output-directory=" + str(out), wrapper.name],
                       cwd=wrapper.parent, capture_output=True, text=True)
    if r.returncode != 0:
        errs = [l for l in r.stdout.splitlines() if l.startswith("!")][:3]
        i = r.stdout.find("\n!")
        print("LATEX ERROR:", errs); print(r.stdout[i:i + 900]); sys.exit(1)
    cv = build.Converter(out)
    intro, items = build.parse_chapter(wrapper.read_text())
    n = 0
    for it in items:
        if it["kind"] == "exercise":
            n += 1
            h = cv.resolve(cv.blocks(it["statement"]) + cv.blocks(it["solution"]))
            if "\\" in re.sub(r"\\\(.*?\\\)|\\\[.*?\\\]|\\begin\{align\*?\}.*?\\end\{align\*?\}", "", h, flags=re.S):
                print("WARNING: raw backslash left in exercise", it["label"])
    print("OK: %d exercises; LaTeX compiled (%s)" % (n, out))
except Exception as e:
    print("CONVERTER ERROR:", repr(e)[:600]); sys.exit(1)
finally:
    wrapper.unlink(missing_ok=True)
