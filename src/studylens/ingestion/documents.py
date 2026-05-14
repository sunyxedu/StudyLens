from __future__ import annotations

import mimetypes
import re
from pathlib import Path

from bs4 import BeautifulSoup

from studylens.domain import DocumentChunk, Resource
from studylens.errors import UnsupportedDocumentError

TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".rst",
    ".tex",
    ".csv",
    ".tsv",
    ".json",
    ".py",
    ".java",
    ".c",
    ".cpp",
}
HTML_SUFFIXES = {".html", ".htm"}
PDF_SUFFIXES = {".pdf"}


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_text(path: Path) -> str:
    """Extract readable text from supported local files."""

    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return normalize_text(path.read_text(encoding="utf-8", errors="ignore"))
    if suffix in HTML_SUFFIXES:
        soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="ignore"), "html.parser")
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        return normalize_text(soup.get_text("\n"))
    if suffix in PDF_SUFFIXES:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise UnsupportedDocumentError("Install studylens[documents] to parse PDFs") from exc
        reader = PdfReader(str(path))
        return normalize_text("\n\n".join(page.extract_text() or "" for page in reader.pages))

    mime_type, _ = mimetypes.guess_type(path.name)
    raise UnsupportedDocumentError(
        f"Unsupported document type for {path.name} ({mime_type or 'unknown'})"
    )


def _split_paragraphs(text: str) -> list[str]:
    paragraphs = [
        part.strip()
        for part in re.split(r"\n\s*\n", normalize_text(text))
        if part.strip()
    ]
    if paragraphs:
        return paragraphs
    return [normalize_text(text)] if text.strip() else []


def chunk_text(text: str, *, max_chars: int = 1400, overlap: int = 180) -> list[str]:
    """Split text into overlapping chunks with stable paragraph boundaries."""

    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if overlap < 0:
        raise ValueError("overlap cannot be negative")
    if overlap >= max_chars:
        raise ValueError("overlap must be smaller than max_chars")

    paragraphs = _split_paragraphs(text)
    chunks: list[str] = []
    current = ""

    def push_current() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
            current = current[-overlap:].strip() if overlap else ""

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            push_current()
            start = 0
            while start < len(paragraph):
                end = min(start + max_chars, len(paragraph))
                chunk = paragraph[start:end].strip()
                if chunk:
                    chunks.append(chunk)
                if end == len(paragraph):
                    break
                start = max(0, end - overlap)
            current = chunks[-1][-overlap:].strip() if overlap and chunks else ""
            continue

        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
        else:
            push_current()
            current = f"{current}\n\n{paragraph}".strip() if current else paragraph

    if current.strip() and (not chunks or current.strip() != chunks[-1].strip()):
        chunks.append(current.strip())
    return chunks


def build_chunks(
    resource: Resource,
    text: str,
    *,
    max_chars: int = 1400,
    overlap: int = 180,
) -> list[DocumentChunk]:
    return [
        DocumentChunk(
            course_id=resource.course_id,
            resource_id=resource.id or "",
            kind=resource.kind,
            text=chunk,
            position=index,
            title=resource.title,
            source_url=resource.source_url,
            metadata=dict(resource.metadata),
        )
        for index, chunk in enumerate(chunk_text(text, max_chars=max_chars, overlap=overlap))
    ]
