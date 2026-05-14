from __future__ import annotations

from studylens.domain import SearchResult


LATEX_COMPACT_PREAMBLE = r"""\documentclass[9pt,a4paper]{article}
\usepackage[margin=0.42in]{geometry}
\usepackage{multicol}
\usepackage{amsmath,amssymb}
\usepackage{enumitem}
\usepackage{titlesec}
\usepackage[T1]{fontenc}
\usepackage{lmodern}
\setlength{\parindent}{0pt}
\setlength{\parskip}{1.5pt}
\setlength{\columnsep}{12pt}
\setlist{nosep,leftmargin=*}
\titlespacing*{\section}{0pt}{2pt}{1pt}
\titlespacing*{\subsection}{0pt}{1pt}{0pt}
\pagestyle{empty}
"""


def format_search_results(results: list[SearchResult], *, max_chars: int = 9000) -> str:
    blocks: list[str] = []
    remaining = max_chars
    for index, result in enumerate(results, start=1):
        chunk = result.chunk
        header = f"[{index}] {chunk.title or chunk.resource_id} ({chunk.kind}, score={result.score:.3f})"
        body = chunk.text.strip()
        block = f"{header}\n{body}"
        if len(block) > remaining:
            block = block[: max(0, remaining)]
        if block.strip():
            blocks.append(block)
            remaining -= len(block)
        if remaining <= 0:
            break
    return "\n\n".join(blocks)


def wrap_latex_document(title: str, body: str) -> str:
    return (
        f"{LATEX_COMPACT_PREAMBLE}\n"
        "\\begin{document}\n"
        "\\begin{multicols*}{2}\n"
        f"\\section*{{{title}}}\n"
        f"{body.strip()}\n"
        "\\end{multicols*}\n"
        "\\end{document}\n"
    )

