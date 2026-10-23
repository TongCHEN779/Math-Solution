# Math Solutions

Worked solutions to exercises from mathematics textbooks. For each book there is the book itself (PDF), and for each solved chapter a solutions PDF plus an HTML page in which every exercise is restated and followed by its solution.

Site: <https://tongchen779.github.io/Math-Solution/> (GitHub Pages, served from `docs/`).

## Books

| Book | Solved |
|------|--------|
| Atiyah–Macdonald, *Introduction to Commutative Algebra* | Chapters 1–11 (all) |
| Hartshorne, *Algebraic Geometry* | Chapter I (Varieties, 90 exercises) and Chapter II (Schemes, 134 exercises) |

## Layout

```
books/<slug>/book.json       title, authors, chapter list (a chapter with "tex" is published)
books/<slug>/book.pdf        the textbook
books/<slug>/chapter-N.tex   one LaTeX file per chapter: \exercise{label}{statement} followed by the solution
tools/macros.tex             math macros shared by the PDF *and* MathJax on the website
tools/preamble.tex           shared LaTeX preamble
build.py                     builds docs/ (HTML, SVG figures, PDFs)
docs/                        generated site -- commit it, GitHub Pages serves it
```

## Writing a chapter

Copy `books/atiyah-macdonald/chapter-1.tex`, and for every exercise write

```latex
\exercise{3.7}{Restated problem ...}
\pf Solution ...
```

Use `\section*{...}` to group exercises. Supported text-mode LaTeX: `\textbf`, `\emph`, `itemize`/`enumerate` (with `label=`), `$..$`, `\[..\]`, `align*`, and `tikzpicture` inside `center` (rendered to SVG for the web). Add the chapter to `book.json` (`"tex": "chapter-3.tex"`).

## Build

Requires Python 3, `pdflatex`, `latex` and `dvisvgm` (TeX Live / MacTeX).

```bash
python3 build.py            # everything
python3 build.py --no-pdf   # HTML only (faster)
cd docs && python3 -m http.server   # preview at http://localhost:8000
```

## Publishing

Repository Settings -> Pages -> Deploy from branch `main`, folder `/docs`.

## Adding a book

Create `books/<slug>/` with `book.json`, `book.pdf` and chapter files; rerun `build.py`.
