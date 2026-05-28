from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from studylens.ingestion.captions import format_timestamp, parse_caption_segments
from studylens.ingestion.documents import extract_text, normalize_text
from studylens.ingestion.manifest import read_manifest

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


class CourseContextProvider(Protocol):
    def format_course_context(
        self,
        *,
        course_id: str,
        kinds: set[str] | None = None,
        max_chars: int = 180_000,
    ) -> str:
        ...

    def scope_notes(self, *, course_id: str, max_chars: int = 12_000) -> list[str]:
        ...


@dataclass(frozen=True, slots=True)
class CourseFileContext:
    title: str
    kind: str
    source_url: str | None
    local_path: str
    text: str


@dataclass(slots=True)
class ManifestCourseContextProvider:
    raw_dir: Path

    def format_course_context(
        self,
        *,
        course_id: str,
        kinds: set[str] | None = None,
        max_chars: int = 180_000,
    ) -> str:
        files = self._course_files(course_id=course_id, kinds=kinds)
        if not files:
            return "No local course files were found for this course."

        header = (
            f"Local course file context for {course_id}: {len(files)} files. "
            "Every discovered local file is represented below; long files are clipped "
            "evenly to fit the model context.\n"
        )
        per_file_chars = max(1200, (max_chars - len(header)) // max(1, len(files)))
        blocks = [header]
        remaining = max_chars - len(header)
        for index, file in enumerate(files, start=1):
            block_header = (
                f"\n\n[{index}] {file.title} ({file.kind})\n"
                f"Path: {file.local_path}\n"
            )
            if file.source_url:
                block_header += f"Source: {file.source_url}\n"
            available = max(0, min(per_file_chars, remaining - len(block_header)))
            body = _clip_text(file.text, max_chars=available)
            block = f"{block_header}{body}"
            if block.strip():
                blocks.append(block)
                remaining -= len(block)
            if remaining <= 0:
                break
        return "".join(blocks).strip()

    def scope_notes(self, *, course_id: str, max_chars: int = 12_000) -> list[str]:
        notes: list[str] = []
        remaining = max_chars
        for file in self._course_files(course_id=course_id, kinds={"edstem_note"}):
            text = file.text.strip()
            if not text:
                continue
            clipped = _clip_text(text, max_chars=remaining)
            if clipped:
                notes.append(clipped)
                remaining -= len(clipped)
            if remaining <= 0:
                break
        return notes

    def _course_files(
        self,
        *,
        course_id: str,
        kinds: set[str] | None = None,
    ) -> list[CourseFileContext]:
        manifest = read_manifest(self.raw_dir, course_id)
        if manifest is None:
            return []
        course_dir = self.raw_dir / manifest.course_id
        files: list[CourseFileContext] = []
        for item in manifest.items:
            kind = str(item.kind)
            if kinds is not None and kind not in kinds:
                continue
            if not item.local_path:
                continue
            path = course_dir / item.local_path
            if not path.exists():
                continue
            try:
                text = _extract_manifest_text(path, kind=kind)
            except Exception as exc:
                text = f"[Could not read {path.name}: {exc}]"
            text = text.strip()
            if not text:
                continue
            files.append(
                CourseFileContext(
                    title=item.title,
                    kind=kind,
                    source_url=item.source_url or None,
                    local_path=item.local_path,
                    text=text,
                )
            )
        return files


def auto_scope_notes(
    context_provider: CourseContextProvider,
    *,
    course_id: str,
) -> list[str]:
    """Pull local EdStem scope-note files for the course as plain text bullets.

    Falls back to an empty list so generation works even when no EdStem
    notes were indexed (or no browser session was configured).
    """

    return context_provider.scope_notes(course_id=course_id)


def format_scope_notes(notes: list[str]) -> str:
    if not notes:
        return "- No scope notes supplied."
    return "\n".join(f"- {note}" for note in notes)


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


def _extract_manifest_text(path: Path, *, kind: str) -> str:
    suffix = path.suffix.lower()
    if kind == "transcript" and suffix in {".srt", ".vtt"}:
        segments = parse_caption_segments(path.read_text(encoding="utf-8", errors="ignore"))
        if segments:
            return normalize_text(
                "\n".join(
                    f"[{format_timestamp(segment.start_seconds)}-"
                    f"{format_timestamp(segment.end_seconds)}] {segment.text}"
                    for segment in segments
                )
            )
    return extract_text(path)


def _clip_text(text: str, *, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    text = normalize_text(text)
    if len(text) <= max_chars:
        return text
    if max_chars <= 24:
        return text[:max_chars]
    return f"{text[: max_chars - 24].rstrip()}\n[clipped for context]"
