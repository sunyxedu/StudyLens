from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from pydantic import BaseModel, Field

from studylens.config import Settings
from studylens.domain import CourseSummary, Resource
from studylens.errors import IngestionError, UnsupportedDocumentError
from studylens.ingestion._paths import safe_path_part, unique_path
from studylens.ingestion.browser_session import AsyncFetcher, BrowserFetcher, BrowserSession
from studylens.ingestion.documents import build_chunks, extract_text
from studylens.ingestion.panopto import PanoptoVideoIndexer, PanoptoVideoIndexResult
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
    stage: str = "scientia"
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


@dataclass(slots=True)
class CourseAutoIndexer:
    """Index a Scientia course end-to-end, optionally including Panopto videos.

    Both Scientia and Panopto sit behind Imperial SSO, so production runs
    must inject a `BrowserSession`-backed fetcher and a Panopto indexer
    that shares the same session. Tests can inject any `AsyncFetcher` and
    a stub `PanoptoVideoIndexer`.
    """

    settings: Settings
    rag: RAGService
    fetcher: AsyncFetcher
    panopto_indexer: PanoptoVideoIndexer | None = None

    async def index_course(
        self,
        *,
        course_id: str,
        course_title: str | None = None,
        course_url: str | None = None,
    ) -> AutoIndexReport:
        summary = await self._resolve_course(
            course_id=course_id,
            course_title=course_title,
            course_url=course_url,
        )
        html = await self.fetcher.get_text(summary.url or course_url or "")
        course = parse_course_page(html, summary, summary.url or course_url or "")
        resources = [*course.materials, *course.exercises, *course.tutorials]
        report = AutoIndexReport(
            course_id=course.id,
            course_title=course.title,
            source_url=course.source_url,
            discovered_resources=len(resources),
        )

        for resource in resources:
            report.items.append(await self._index_resource(resource))

        if self.panopto_indexer is not None:
            panopto_items = await self._index_panopto(
                course_id=course.id,
                course_title=course.title,
            )
            report.items.extend(panopto_items)
            report.discovered_resources += sum(1 for item in panopto_items if item.source_url)

        report.indexed_resources = sum(1 for item in report.items if item.status == "indexed")
        report.indexed_chunks = sum(item.chunks for item in report.items)
        return report

    async def _resolve_course(
        self,
        *,
        course_id: str,
        course_title: str | None,
        course_url: str | None,
    ) -> CourseSummary:
        if course_url:
            return CourseSummary(id=course_id, title=course_title or course_id, url=course_url)

        timeline_html = await self.fetcher.get_text(str(self.settings.scientia_base_url))
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

    async def _index_resource(self, resource: Resource) -> AutoIndexItem:
        if not resource.source_url:
            return AutoIndexItem(
                title=resource.title,
                kind=resource.kind,
                status="skipped",
                stage="scientia",
                error="Resource has no source URL",
            )

        try:
            downloaded = await self._download_resource(resource)
            text = extract_text(downloaded.local_path or Path())
            chunks = build_chunks(downloaded, text)
            indexed = self.rag.index_chunks(chunks)
            return AutoIndexItem(
                title=resource.title,
                kind=resource.kind,
                status="indexed",
                stage="scientia",
                source_url=resource.source_url,
                local_path=str(downloaded.local_path) if downloaded.local_path else None,
                chunks=indexed,
            )
        except UnsupportedDocumentError as exc:
            return AutoIndexItem(
                title=resource.title,
                kind=resource.kind,
                status="skipped",
                stage="scientia",
                source_url=resource.source_url,
                error=str(exc),
            )
        except Exception as exc:  # pragma: no cover - exact network/parser failures vary by source.
            return AutoIndexItem(
                title=resource.title,
                kind=resource.kind,
                status="failed",
                stage="scientia",
                source_url=resource.source_url,
                error=str(exc),
            )

    async def _download_resource(self, resource: Resource) -> Resource:
        content, content_type = await self.fetcher.download(resource.source_url or "")
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

    async def _index_panopto(
        self,
        *,
        course_id: str,
        course_title: str,
    ) -> list[AutoIndexItem]:
        assert self.panopto_indexer is not None
        results = await self.panopto_indexer.index_course_videos(
            course_id=course_id,
            course_title=course_title,
        )
        return [panopto_result_to_item(result) for result in results]


def build_auto_indexer(
    settings: Settings,
    rag: RAGService,
    session: BrowserSession,
    *,
    include_panopto: bool = True,
) -> CourseAutoIndexer:
    """Default wiring: BrowserFetcher + Panopto indexer sharing one session."""

    panopto = (
        PanoptoVideoIndexer(settings=settings, rag=rag, session=session)
        if include_panopto
        else None
    )
    return CourseAutoIndexer(
        settings=settings,
        rag=rag,
        fetcher=BrowserFetcher(session),
        panopto_indexer=panopto,
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


def panopto_result_to_item(result: PanoptoVideoIndexResult) -> AutoIndexItem:
    return AutoIndexItem(
        title=result.title,
        kind="transcript",
        status=result.status,
        stage="panopto",
        source_url=result.source_url,
        local_path=result.local_path,
        chunks=result.chunks,
        error=result.error,
    )
