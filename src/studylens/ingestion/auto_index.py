from __future__ import annotations

import mimetypes
import re
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
from studylens.ingestion.edstem import EdStemIndexer, EdStemIndexResult, build_edstem_indexer
from studylens.ingestion.exams import ExamIndexResult, ExamsIndexer, build_exams_indexer
from studylens.ingestion.llm_extractor import LLMCourseExtractor
from studylens.ingestion.panopto import PanoptoVideoIndexer, PanoptoVideoIndexResult
from studylens.ingestion.scientia import parse_course_page
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
    course_extractor: LLMCourseExtractor
    panopto_indexer: PanoptoVideoIndexer | None = None
    exams_indexer: ExamsIndexer | None = None
    edstem_indexer: EdStemIndexer | None = None

    async def index_course(
        self,
        *,
        course_id: str,
        course_title: str,
    ) -> AutoIndexReport:
        summary = await self._resolve_course(
            course_id=course_id,
            course_title=course_title,
        )
        assert summary.url, "resolved course summary must have a URL"
        html = await self.fetcher.get_text(summary.url)
        course = parse_course_page(html, summary, summary.url)
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

        if self.exams_indexer is not None:
            exam_items = await self._index_exams(course_id=course.id)
            report.items.extend(exam_items)
            report.discovered_resources += sum(1 for item in exam_items if item.source_url)

        if self.edstem_indexer is not None:
            edstem_items = await self._index_edstem(
                course_id=course.id,
                course_title=course.title,
            )
            report.items.extend(edstem_items)
            report.discovered_resources += sum(1 for item in edstem_items if item.chunks)

        report.indexed_resources = sum(1 for item in report.items if item.status == "indexed")
        report.indexed_chunks = sum(item.chunks for item in report.items)
        return report

    async def _resolve_course(
        self,
        *,
        course_id: str,
        course_title: str,
    ) -> CourseSummary:
        base_url = str(self.settings.scientia_base_url)
        timeline_html = await self.fetcher.get_text(base_url)
        courses = await self.course_extractor.extract_courses(timeline_html, base_url)

        match = _match_course(courses, course_id=course_id, course_title=course_title)
        if match is not None:
            return match

        available = ", ".join(c.id for c in courses[:8]) or "(none)"
        raise IngestionError(
            f"Could not find {course_id} ({course_title}) on Scientia /modules. "
            f"Available IDs: {available}. Refresh BROWSER_STORAGE_STATE if SSO expired."
        )

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

    async def _index_exams(self, *, course_id: str) -> list[AutoIndexItem]:
        assert self.exams_indexer is not None
        results = await self.exams_indexer.index_course_exams(course_id=course_id)
        return [exam_result_to_item(result) for result in results]

    async def _index_edstem(
        self,
        *,
        course_id: str,
        course_title: str,
    ) -> list[AutoIndexItem]:
        assert self.edstem_indexer is not None
        results = await self.edstem_indexer.index_course_scope_notes(
            course_id=course_id,
            course_title=course_title,
        )
        return [edstem_result_to_item(result) for result in results]


def build_auto_indexer(
    settings: Settings,
    rag: RAGService,
    session: BrowserSession,
    *,
    include_panopto: bool = True,
    include_exams: bool = True,
    include_edstem: bool = True,
) -> CourseAutoIndexer:
    """Default wiring: BrowserFetcher + LLM extractor + Panopto/exams/EdStem.

    Raises ConfigurationError when ANTHROPIC_API_KEY is unset —
    the timeline lookup requires Claude. Callers that pass course_url
    explicitly skip the timeline entirely and don't hit this dependency.
    """

    extractor = LLMCourseExtractor.from_settings(settings)
    panopto = (
        PanoptoVideoIndexer(settings=settings, rag=rag, session=session)
        if include_panopto
        else None
    )
    exams = build_exams_indexer(settings, rag) if include_exams else None
    edstem = build_edstem_indexer(settings, rag, session) if include_edstem else None
    return CourseAutoIndexer(
        settings=settings,
        rag=rag,
        fetcher=BrowserFetcher(session),
        course_extractor=extractor,
        panopto_indexer=panopto,
        exams_indexer=exams,
        edstem_indexer=edstem,
    )


_CODE_PREFIX_RE = re.compile(r"^\s*[A-Z]{2,5}\s*\d{3,5}(?:\.\d+)?\s*[:\-—]\s*", re.IGNORECASE)


def _digit_tail(code: str) -> str:
    """Strip a leading alpha department prefix so codes can be compared.

    Imperial uses two parallel conventions: EdStem labels courses `COMP50001`
    while Scientia drops the prefix and lists them as `50001`. Comparing the
    digit-and-dot tail lets us match across both.
    """
    return re.sub(r"^[A-Za-z]+", "", code).strip()


def _strip_code_prefix(title: str) -> str:
    """Remove a leading `COMP 50001: ` prefix from an EdStem-style title."""
    return _CODE_PREFIX_RE.sub("", title).strip()


def _match_course(
    courses: list[CourseSummary],
    *,
    course_id: str,
    course_title: str,
) -> CourseSummary | None:
    """Find the Scientia course matching an EdStem-style code + title.

    Search order:
    1. Exact case-insensitive ID match (handles COMPM0101 ↔ COMPM0101).
    2. Digit-tail match (handles COMP50001 ↔ 50001).
    3. Title substring match against the code-stripped EdStem title.
    """
    wanted_id = course_id.upper()
    wanted_tail = _digit_tail(wanted_id)

    for course in courses:
        if course.url and course.id.upper() == wanted_id:
            return course

    if wanted_tail:
        for course in courses:
            if course.url and _digit_tail(course.id.upper()) == wanted_tail:
                return course

    needle = _strip_code_prefix(course_title).casefold()
    if needle:
        for course in courses:
            if course.url and needle in course.title.casefold():
                return course

    return None


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


def exam_result_to_item(result: ExamIndexResult) -> AutoIndexItem:
    return AutoIndexItem(
        title=result.title,
        kind="past_exam",
        status=result.status,
        stage="exams",
        source_url=result.source_url,
        local_path=result.local_path,
        chunks=result.chunks,
        error=result.error,
    )


def edstem_result_to_item(result: EdStemIndexResult) -> AutoIndexItem:
    return AutoIndexItem(
        title=result.title,
        kind="edstem_note",
        status=result.status,
        stage="edstem",
        chunks=result.chunks,
        error=result.error,
    )
