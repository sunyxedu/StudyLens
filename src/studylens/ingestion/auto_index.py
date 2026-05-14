"""Per-course ingestion orchestrator.

The pipeline is split into two phases on purpose:

  Phase 1 — crawl_course()
    Agent-driven discovery (Scientia, Panopto) and deterministic discovery
    (past exams, EdStem) per course. Everything that's found is downloaded
    to `data/raw/{course_id}/{kind}/...` and a manifest `_crawl.json` is
    written. No vectors, no LLM tokens spent on indexing yet.

  Phase 2 — index_local()
    Pure CPU: reads the manifest, extracts text, chunks, embeds, and
    upserts into Qdrant. Can be re-run without re-crawling (useful when
    you change chunk size or swap embedding models).

  sync_course() = crawl_course() then index_local()

The disk layout uses ResourceKind values literally — material/, exercise/,
tutorial/, transcript/, past_exam/, edstem_note/ — so the kind in the
Qdrant payload equals the folder name equals the type annotation.
"""

from __future__ import annotations

import mimetypes
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx
from pydantic import BaseModel, Field

from studylens.config import Settings
from studylens.domain import CourseSummary, DocumentChunk, Resource
from studylens.domain.models import ResourceKind
from studylens.errors import IngestionError, UnsupportedDocumentError
from studylens.ingestion._paths import safe_path_part, unique_path
from studylens.ingestion.browser_session import AsyncFetcher, BrowserFetcher, BrowserSession
from studylens.ingestion.captions import build_caption_chunks, parse_caption_segments
from studylens.ingestion.documents import build_chunks, extract_text
from studylens.ingestion.edstem import EdStemCrawler
from studylens.ingestion.exams import ExamsClient
from studylens.ingestion.llm_extractor import LLMCourseExtractor
from studylens.ingestion.manifest import (
    CourseManifest,
    ManifestItem,
    now_iso,
    read_manifest,
    write_manifest,
)
from studylens.ingestion.panopto_agent import (
    DiscoveredVideo,
    discover_course_videos,
)
from studylens.ingestion.scientia_agent import (
    DiscoveredResource,
    discover_course_resources,
)
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
    ".srt",
    ".vtt",
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


# Injection hooks: tests replace these with fakes that return canned data.
ScientiaDiscoverer = Callable[
    ["CourseAutoIndexer", CourseSummary],
    Awaitable[tuple[list[DiscoveredResource], str | None]],
]
PanoptoDiscoverer = Callable[
    ["CourseAutoIndexer", CourseSummary],
    Awaitable[tuple[list[DiscoveredVideo], str | None]],
]
ExamsDiscoverer = Callable[
    ["CourseAutoIndexer", CourseSummary],
    Awaitable[tuple[list[Resource], str | None]],
]
EdStemDiscoverer = Callable[
    ["CourseAutoIndexer", CourseSummary],
    Awaitable[tuple[list[Resource], str | None]],
]


@dataclass(slots=True)
class CourseAutoIndexer:
    """Two-phase course ingestion: crawl → manifest → index_local."""

    settings: Settings
    rag: RAGService
    fetcher: AsyncFetcher
    course_extractor: LLMCourseExtractor
    session: BrowserSession | None = None
    enable_scientia: bool = True
    enable_panopto: bool = True
    enable_exams: bool = True
    enable_edstem: bool = True
    # Test seams.
    scientia_discoverer: ScientiaDiscoverer | None = None
    panopto_discoverer: PanoptoDiscoverer | None = None
    exams_discoverer: ExamsDiscoverer | None = None
    edstem_discoverer: EdStemDiscoverer | None = None
    panopto_caption_fetcher: Callable[..., Awaitable[bytes | None]] | None = None
    exams_downloader: Callable[..., Awaitable[tuple[bytes, str | None]]] | None = None

    # ----- Phase 1: crawl -----

    async def crawl_course(
        self,
        *,
        course_id: str,
        course_title: str,
    ) -> CourseManifest:
        summary = await self._resolve_course(
            course_id=course_id, course_title=course_title
        )
        manifest = CourseManifest(
            course_id=summary.id,
            course_title=summary.title,
            course_url=summary.url,
            crawled_at=now_iso(),
            items=[],
        )
        course_dir = self._course_dir(summary.id)

        # Each stage is wrapped so a single failure doesn't poison the rest.
        # The try/finally also guarantees the manifest reflects partial state
        # if the request is cancelled (browser fetch timeout, Ctrl+C, etc.),
        # so phase 2 can pick up what did get downloaded.
        try:
            if self.enable_scientia and summary.url:
                try:
                    manifest.items.extend(await self._crawl_scientia(summary, course_dir))
                except Exception as exc:  # pragma: no cover - stage-level recovery
                    manifest.items.append(_stage_error_item("scientia", exc))
            if self.enable_panopto:
                try:
                    manifest.items.extend(await self._crawl_panopto(summary, course_dir))
                except Exception as exc:  # pragma: no cover - stage-level recovery
                    manifest.items.append(_stage_error_item("panopto", exc))
            if self.enable_exams:
                try:
                    manifest.items.extend(await self._crawl_exams(summary, course_dir))
                except Exception as exc:  # pragma: no cover - stage-level recovery
                    manifest.items.append(_stage_error_item("exams", exc))
            if self.enable_edstem:
                try:
                    manifest.items.extend(await self._crawl_edstem(summary, course_dir))
                except Exception as exc:  # pragma: no cover - stage-level recovery
                    manifest.items.append(_stage_error_item("edstem", exc))
        finally:
            write_manifest(self.settings.raw_dir, manifest)
        return manifest

    # ----- Phase 2: index -----

    def index_local(self, course_id: str) -> AutoIndexReport:
        manifest = read_manifest(self.settings.raw_dir, course_id)
        if manifest is None:
            raise IngestionError(
                f"No crawl manifest for {course_id}. Run crawl_course first."
            )

        report = AutoIndexReport(
            course_id=manifest.course_id,
            course_title=manifest.course_title,
            source_url=manifest.course_url,
            discovered_resources=len(manifest.items),
        )
        course_dir = self._course_dir(manifest.course_id)

        for item in manifest.items:
            stage = str(item.metadata.get("stage", "scientia"))
            if not item.local_path:
                # Stage-level failure or download miss; surface in report as
                # failed/skipped but don't try to read a non-existent file.
                err = str(item.metadata.get("error") or "no local file")
                report.items.append(
                    AutoIndexItem(
                        title=item.title,
                        kind=item.kind,
                        status="failed" if "error" in item.metadata else "skipped",
                        stage=stage,
                        source_url=item.source_url,
                        local_path=None,
                        chunks=0,
                        error=err,
                    )
                )
                continue
            local_path = course_dir / item.local_path
            try:
                chunks = self._chunks_for_item(item, local_path, manifest.course_id)
            except UnsupportedDocumentError as exc:
                report.items.append(
                    AutoIndexItem(
                        title=item.title,
                        kind=item.kind,
                        status="skipped",
                        stage=stage,
                        source_url=item.source_url,
                        local_path=str(local_path),
                        chunks=0,
                        error=str(exc),
                    )
                )
                continue
            except Exception as exc:  # pragma: no cover - per-file failures noisy.
                report.items.append(
                    AutoIndexItem(
                        title=item.title,
                        kind=item.kind,
                        status="failed",
                        stage=stage,
                        source_url=item.source_url,
                        local_path=str(local_path),
                        chunks=0,
                        error=str(exc),
                    )
                )
                continue

            indexed = self.rag.index_chunks(chunks)
            report.items.append(
                AutoIndexItem(
                    title=item.title,
                    kind=item.kind,
                    status="indexed",
                    stage=stage,
                    source_url=item.source_url,
                    local_path=str(local_path),
                    chunks=indexed,
                )
            )

        report.indexed_resources = sum(1 for it in report.items if it.status == "indexed")
        report.indexed_chunks = sum(it.chunks for it in report.items)
        return report

    # ----- Combined entry point -----

    async def sync_course(
        self,
        *,
        course_id: str,
        course_title: str,
    ) -> AutoIndexReport:
        manifest = await self.crawl_course(course_id=course_id, course_title=course_title)
        return self.index_local(manifest.course_id)

    # Backwards-compat alias for callers (API handler, CLI) that still say
    # index_course. New code should call sync_course explicitly.
    async def index_course(
        self,
        *,
        course_id: str,
        course_title: str,
    ) -> AutoIndexReport:
        return await self.sync_course(course_id=course_id, course_title=course_title)

    # ----- Internals -----

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

    def _course_dir(self, course_id: str) -> Path:
        return self.settings.raw_dir / safe_path_part(course_id)

    async def _crawl_scientia(
        self,
        summary: CourseSummary,
        course_dir: Path,
    ) -> list[ManifestItem]:
        resources, error = await self._run_scientia_discoverer(summary)
        if error or not resources:
            return []
        items: list[ManifestItem] = []
        for resource in resources:
            try:
                content, content_type = await self.fetcher.download(resource.source_url)
            except Exception as exc:  # pragma: no cover - download errors are noisy.
                items.append(
                    _failed_manifest_item(
                        course_dir=course_dir,
                        resource=resource,
                        stage="scientia",
                        error=f"download failed: {exc}",
                    )
                )
                continue
            local_rel = _write_resource_to_disk(
                course_dir=course_dir,
                kind=resource.kind,
                title=resource.title,
                url=resource.source_url,
                content=content,
                content_type=content_type,
            )
            if local_rel is None:
                continue
            items.append(
                ManifestItem(
                    source_url=resource.source_url,
                    local_path=local_rel,
                    kind=resource.kind,
                    title=resource.title,
                    downloaded_at=now_iso(),
                    metadata={
                        "stage": "scientia",
                        "content_type": content_type,
                    },
                )
            )
        return items

    async def _run_scientia_discoverer(
        self,
        summary: CourseSummary,
    ) -> tuple[list[DiscoveredResource], str | None]:
        if self.scientia_discoverer is not None:
            return await self.scientia_discoverer(self, summary)
        if self.session is None or not summary.url:
            return [], "no session or URL for Scientia agent"
        report = await discover_course_resources(
            self.session,
            course_id=summary.id,
            course_title=summary.title,
            course_url=summary.url,
            settings=self.settings,
        )
        return report.resources, report.error

    async def _crawl_panopto(
        self,
        summary: CourseSummary,
        course_dir: Path,
    ) -> list[ManifestItem]:
        videos, error = await self._run_panopto_discoverer(summary)
        if error or not videos:
            return []
        items: list[ManifestItem] = []
        for video in videos:
            try:
                caption_text = await self._fetch_panopto_caption(video)
            except Exception as exc:  # pragma: no cover - per-video failures.
                items.append(
                    _failed_manifest_item(
                        course_dir=course_dir,
                        resource=_video_as_resource(summary.id, video),
                        stage="panopto",
                        error=f"caption fetch failed: {exc}",
                    )
                )
                continue
            if not caption_text:
                continue
            local_rel = _write_text_to_disk(
                course_dir=course_dir,
                kind="transcript",
                title=video.title,
                text=caption_text,
                suffix=".srt",
            )
            items.append(
                ManifestItem(
                    source_url=video.viewer_url,
                    local_path=local_rel,
                    kind="transcript",
                    title=video.title,
                    downloaded_at=now_iso(),
                    metadata={
                        "stage": "panopto",
                        "session_id": video.session_id,
                        "video_url": video.viewer_url,
                    },
                )
            )
        return items

    async def _run_panopto_discoverer(
        self,
        summary: CourseSummary,
    ) -> tuple[list[DiscoveredVideo], str | None]:
        if self.panopto_discoverer is not None:
            return await self.panopto_discoverer(self, summary)
        if self.session is None:
            return [], "no BrowserSession for Panopto agent"
        report = await discover_course_videos(
            self.session,
            course_id=summary.id,
            course_title=summary.title,
            settings=self.settings,
        )
        return report.videos, report.error

    async def _fetch_panopto_caption(self, video: DiscoveredVideo) -> str | None:
        if self.panopto_caption_fetcher is not None:
            result = await self.panopto_caption_fetcher(self, video)
            return result.decode("utf-8") if isinstance(result, bytes) else result
        if self.session is None:
            return None
        # Try caption URLs that Panopto exposes.
        candidate_urls = [
            f"{_panopto_origin(str(self.settings.panopto_base_url))}"
            f"/Panopto/Pages/Transcription/GenerateSRT.ashx?id={video.session_id}&language=1"
        ]
        for url in candidate_urls:
            try:
                text = await self.session.fetch_text(url)
            except Exception:
                continue
            if text and "-->" in text and len(text.strip()) > 20:
                return text
        return None

    async def _crawl_exams(
        self,
        summary: CourseSummary,
        course_dir: Path,
    ) -> list[ManifestItem]:
        resources, error = await self._run_exams_discoverer(summary)
        if error or not resources:
            return []
        items: list[ManifestItem] = []
        for resource in resources:
            try:
                content, content_type = await self._download_exam(resource)
            except Exception as exc:  # pragma: no cover.
                items.append(
                    _failed_manifest_item(
                        course_dir=course_dir,
                        resource=resource,
                        stage="exams",
                        error=f"download failed: {exc}",
                    )
                )
                continue
            local_rel = _write_resource_to_disk(
                course_dir=course_dir,
                kind="past_exam",
                title=resource.title,
                url=resource.source_url or "",
                content=content,
                content_type=content_type,
            )
            if local_rel is None:
                continue
            items.append(
                ManifestItem(
                    source_url=resource.source_url or "",
                    local_path=local_rel,
                    kind="past_exam",
                    title=resource.title,
                    downloaded_at=now_iso(),
                    metadata={"stage": "exams", "content_type": content_type},
                )
            )
        return items

    async def _run_exams_discoverer(
        self,
        summary: CourseSummary,
    ) -> tuple[list[Resource], str | None]:
        if self.exams_discoverer is not None:
            return await self.exams_discoverer(self, summary)
        if not self.settings.imperial_username or not self.settings.imperial_password:
            return [], None  # quietly skip when creds aren't configured
        client = ExamsClient(
            base_url=str(self.settings.exams_base_url),
            username=self.settings.imperial_username,
            password=self.settings.imperial_password,
        )
        try:
            resources = await client.discover_exam_papers(summary.id)
        except Exception as exc:  # pragma: no cover.
            return [], str(exc)
        return resources, None

    async def _download_exam(self, resource: Resource) -> tuple[bytes, str | None]:
        if self.exams_downloader is not None:
            return await self.exams_downloader(self, resource)
        if not resource.source_url:
            raise ValueError("exam resource has no source_url")
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            auth=(
                self.settings.imperial_username or "",
                self.settings.imperial_password or "",
            ),
        ) as client:
            response = await client.get(resource.source_url)
            response.raise_for_status()
            return response.content, response.headers.get("content-type")

    async def _crawl_edstem(
        self,
        summary: CourseSummary,
        course_dir: Path,
    ) -> list[ManifestItem]:
        resources, error = await self._run_edstem_discoverer(summary)
        if error or not resources:
            return []
        items: list[ManifestItem] = []
        for resource in resources:
            body = str(resource.metadata.get("body") or "").strip()
            if not body:
                continue
            local_rel = _write_text_to_disk(
                course_dir=course_dir,
                kind="edstem_note",
                title=resource.title,
                text=body,
                suffix=".txt",
            )
            items.append(
                ManifestItem(
                    source_url=resource.source_url or "",
                    local_path=local_rel,
                    kind="edstem_note",
                    title=resource.title,
                    downloaded_at=now_iso(),
                    metadata={"stage": "edstem"},
                )
            )
        return items

    async def _run_edstem_discoverer(
        self,
        summary: CourseSummary,
    ) -> tuple[list[Resource], str | None]:
        if self.edstem_discoverer is not None:
            return await self.edstem_discoverer(self, summary)
        if self.session is None:
            return [], None
        crawler = EdStemCrawler(
            session=self.session,
            base_url=str(self.settings.edstem_base_url),
        )
        try:
            resources = await crawler.collect_scope_notes(summary.id, summary.title)
        except Exception as exc:  # pragma: no cover.
            return [], str(exc)
        return resources, None

    def _chunks_for_item(
        self,
        item: ManifestItem,
        local_path: Path,
        course_id: str,
    ) -> list[DocumentChunk]:
        resource = Resource(
            course_id=course_id,
            title=item.title,
            kind=item.kind,
            source_url=item.source_url or None,
            local_path=local_path,
            metadata=dict(item.metadata or {}),
        )
        suffix = local_path.suffix.lower()
        if item.kind == "transcript" and suffix in {".srt", ".vtt"}:
            text = local_path.read_text(encoding="utf-8")
            segments = parse_caption_segments(text)
            chunks = build_caption_chunks(resource, segments)
            return chunks or build_chunks(resource, text)
        if suffix not in SUPPORTED_DOWNLOAD_SUFFIXES:
            raise UnsupportedDocumentError(
                f"Unsupported document type for {local_path.name}: {suffix or 'unknown'}"
            )
        text = extract_text(local_path)
        return build_chunks(resource, text)


def build_auto_indexer(
    settings: Settings,
    rag: RAGService,
    session: BrowserSession,
    *,
    include_panopto: bool = True,
    include_exams: bool = True,
    include_edstem: bool = True,
) -> CourseAutoIndexer:
    """Default wiring used by the API handler and CLI."""

    return CourseAutoIndexer(
        settings=settings,
        rag=rag,
        fetcher=BrowserFetcher(session),
        course_extractor=LLMCourseExtractor.from_settings(settings),
        session=session,
        enable_scientia=True,
        enable_panopto=include_panopto,
        enable_exams=include_exams,
        enable_edstem=include_edstem,
    )


# ----- pure helpers -----


_CODE_PREFIX_RE = re.compile(
    r"^\s*[A-Z]{2,5}\s*\d{3,5}(?:\.\d+)?\s*[:\-—]\s*", re.IGNORECASE
)


def _digit_tail(code: str) -> str:
    return re.sub(r"^[A-Za-z]+", "", code).strip()


def _strip_code_prefix(title: str) -> str:
    return _CODE_PREFIX_RE.sub("", title).strip()


def _match_course(
    courses: list[CourseSummary],
    *,
    course_id: str,
    course_title: str,
) -> CourseSummary | None:
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


def _panopto_origin(base_url: str) -> str:
    parsed = urlparse(base_url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _video_as_resource(course_id: str, video: DiscoveredVideo) -> Resource:
    return Resource(
        course_id=course_id,
        title=video.title,
        kind="transcript",
        source_url=video.viewer_url,
        metadata={"session_id": video.session_id},
    )


def _kind_dir(course_dir: Path, kind: ResourceKind) -> Path:
    out = course_dir / kind
    out.mkdir(parents=True, exist_ok=True)
    return out


def _write_resource_to_disk(
    *,
    course_dir: Path,
    kind: ResourceKind,
    title: str,
    url: str,
    content: bytes,
    content_type: str | None,
) -> str | None:
    suffix = infer_suffix(url, content_type)
    if suffix not in SUPPORTED_DOWNLOAD_SUFFIXES:
        return None
    folder = _kind_dir(course_dir, kind)
    safe_name = safe_path_part(title)
    # Strip a redundant trailing extension from the title — Scientia anchors
    # look like `Lecture 6.pdf file`, so the title already ends in `.pdf`
    # after the agent peels off the ` file` marker. Without this we end up
    # writing `Lecture-6.pdf.pdf` everywhere.
    if safe_name.lower().endswith(suffix):
        safe_name = safe_name[: -len(suffix)] or "resource"
    target = unique_path(folder / f"{safe_name}{suffix}")
    target.write_bytes(content)
    return str(target.relative_to(course_dir))


def _write_text_to_disk(
    *,
    course_dir: Path,
    kind: ResourceKind,
    title: str,
    text: str,
    suffix: str,
) -> str:
    folder = _kind_dir(course_dir, kind)
    safe_name = safe_path_part(title)
    if safe_name.lower().endswith(suffix):
        safe_name = safe_name[: -len(suffix)] or "resource"
    target = unique_path(folder / f"{safe_name}{suffix}")
    target.write_text(text, encoding="utf-8")
    return str(target.relative_to(course_dir))


def _failed_manifest_item(
    *,
    course_dir: Path,
    resource: Resource,
    stage: str,
    error: str,
) -> ManifestItem:
    """Record a discovery hit we couldn't download, so it shows up in the report."""
    return ManifestItem(
        source_url=resource.source_url or "",
        local_path="",  # no file
        kind=resource.kind,
        title=resource.title,
        downloaded_at=now_iso(),
        metadata={"stage": stage, "error": error},
    )


def _stage_error_item(stage: str, exc: BaseException) -> ManifestItem:
    """Record a whole stage that crashed before producing any item."""
    return ManifestItem(
        source_url="",
        local_path="",
        kind="material",  # placeholder; index_local will skip (no local_path)
        title=f"[{stage} stage failed]",
        downloaded_at=now_iso(),
        metadata={"stage": stage, "error": f"{type(exc).__name__}: {exc}"},
    )
