from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlparse

import httpx
from pydantic import BaseModel, Field

from studylens.config import Settings
from studylens.domain import CourseSummary, Resource
from studylens.errors import IngestionError, UnsupportedDocumentError
from studylens.ingestion.documents import build_chunks, extract_text
from studylens.ingestion.scientia import parse_course_page, parse_timeline
from studylens.retrieval.qa import RAGService

SUPPORTED_DOWNLOAD_SUFFIXES = {
    ".txt",
    ".md",
    ".rst",
    ".tex",
    ".csv",
    ".tsv",
    ".json",
    ".html",
    ".htm",
    ".pdf",
}


class AutoIndexItem(BaseModel):
    title: str
    kind: str
    status: str
    source_url: str | None = None
    local_path: str | None = None
    chunks: int = 0
    error: str | None = None


class AutoIndexReport(BaseModel):
    course_id: str
    course_title: str
    source_url: str | None = None
    discovered_resources: int = 0
    indexed_resources: int = 0
    indexed_chunks: int = 0
    items: list[AutoIndexItem] = Field(default_factory=list)


class Fetcher(Protocol):
    def get_text(self, url: str) -> str:
        ...

    def download(self, url: str) -> tuple[bytes, str | None]:
        ...


@dataclass(slots=True)
class HttpFetcher:
    timeout: float = 30.0

    def get_text(self, url: str) -> str:
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            return response.text

    def download(self, url: str) -> tuple[bytes, str | None]:
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            return response.content, response.headers.get("content-type")


@dataclass(slots=True)
class CourseAutoIndexer:
    settings: Settings
    rag: RAGService
    fetcher: Fetcher | None = None

    def __post_init__(self) -> None:
        if self.fetcher is None:
            self.fetcher = HttpFetcher()

    def index_course(
        self,
        *,
        course_id: str,
        course_title: str | None = None,
        course_url: str | None = None,
    ) -> AutoIndexReport:
        summary = self._resolve_course(
            course_id=course_id,
            course_title=course_title,
            course_url=course_url,
        )
        assert self.fetcher is not None
        html = self.fetcher.get_text(summary.url or course_url or "")
        course = parse_course_page(html, summary, summary.url or course_url or "")
        resources = [*course.materials, *course.exercises, *course.tutorials]
        report = AutoIndexReport(
            course_id=course.id,
            course_title=course.title,
            source_url=course.source_url,
            discovered_resources=len(resources),
        )

        for resource in resources:
            report.items.append(self._index_resource(resource))

        report.indexed_resources = sum(1 for item in report.items if item.status == "indexed")
        report.indexed_chunks = sum(item.chunks for item in report.items)
        return report

    def _resolve_course(
        self,
        *,
        course_id: str,
        course_title: str | None,
        course_url: str | None,
    ) -> CourseSummary:
        if course_url:
            return CourseSummary(id=course_id, title=course_title or course_id, url=course_url)

        assert self.fetcher is not None
        timeline_html = self.fetcher.get_text(str(self.settings.scientia_base_url))
        courses = parse_timeline(timeline_html, str(self.settings.scientia_base_url))
        normalized_id = course_id.upper()
        for course in courses:
            if course.id.upper() == normalized_id:
                return course
        if course_title:
            needle = course_title.casefold()
            for course in courses:
                if needle in course.title.casefold():
                    return course
        raise IngestionError(f"Could not find {course_id} on Scientia timeline")

    def _index_resource(self, resource: Resource) -> AutoIndexItem:
        if not resource.source_url:
            return AutoIndexItem(
                title=resource.title,
                kind=resource.kind,
                status="skipped",
                error="Resource has no source URL",
            )

        try:
            downloaded = self._download_resource(resource)
            text = extract_text(downloaded.local_path or Path())
            chunks = build_chunks(downloaded, text)
            indexed = self.rag.index_chunks(chunks)
            return AutoIndexItem(
                title=resource.title,
                kind=resource.kind,
                status="indexed",
                source_url=resource.source_url,
                local_path=str(downloaded.local_path) if downloaded.local_path else None,
                chunks=indexed,
            )
        except UnsupportedDocumentError as exc:
            return AutoIndexItem(
                title=resource.title,
                kind=resource.kind,
                status="skipped",
                source_url=resource.source_url,
                error=str(exc),
            )
        except Exception as exc:  # pragma: no cover - exact network/parser failures vary by source.
            return AutoIndexItem(
                title=resource.title,
                kind=resource.kind,
                status="failed",
                source_url=resource.source_url,
                error=str(exc),
            )

    def _download_resource(self, resource: Resource) -> Resource:
        assert self.fetcher is not None
        content, content_type = self.fetcher.download(resource.source_url or "")
        suffix = infer_suffix(resource.source_url or "", content_type)
        if suffix not in SUPPORTED_DOWNLOAD_SUFFIXES:
            raise UnsupportedDocumentError(
                f"Unsupported downloaded document type: {suffix or 'unknown'}"
            )

        output_dir = self.settings.raw_dir / safe_path_part(resource.course_id) / resource.kind
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{safe_path_part(resource.title)}{suffix}"
        output_path = unique_path(output_dir / filename)
        output_path.write_bytes(content)
        return resource.model_copy(
            update={
                "local_path": output_path,
                "mime_type": content_type,
                "metadata": {**resource.metadata, "auto_indexed": True},
            }
        )


def infer_suffix(url: str, content_type: str | None) -> str:
    path_suffix = Path(unquote(urlparse(url).path)).suffix.lower()
    if path_suffix:
        return path_suffix
    if content_type:
        mime_type = content_type.split(";", 1)[0].strip().lower()
        if mime_type == "text/plain":
            return ".txt"
        if mime_type == "text/html":
            return ".html"
        guessed = mimetypes.guess_extension(mime_type)
        if guessed:
            return guessed.lower()
    return ".txt"


def safe_path_part(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return normalized[:120] or "resource"


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for counter in range(2, 10_000):
        candidate = path.with_name(f"{stem}-{counter}{suffix}")
        if not candidate.exists():
            return candidate
    raise IngestionError(f"Could not find available filename for {path}")
