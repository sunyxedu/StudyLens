"""Per-course ingestion orchestrator.

The pipeline is split into two phases on purpose:

  Phase 1 — crawl_course()
    Agent-driven discovery (Scientia, Panopto, past exams) per course.
    Everything that's found is downloaded to
    `data/raw/{course_id}/{kind}/...` and a manifest `_crawl.json` is
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
from studylens.ingestion.documents import build_chunks, build_pdf_chunks, extract_text
from studylens.ingestion.exams_agent import discover_past_exams
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
    # Test seams.
    scientia_discoverer: ScientiaDiscoverer | None = None
    panopto_discoverer: PanoptoDiscoverer | None = None
    exams_discoverer: ExamsDiscoverer | None = None
    panopto_caption_fetcher: Callable[..., Awaitable[bytes | None]] | None = None
    exams_downloader: Callable[..., Awaitable[tuple[bytes, str | None]]] | None = None

    # ----- Phase 1: crawl -----

    async def crawl_course(
        self,
        *,
        course_id: str,
        course_title: str,
    ) -> CourseManifest:
        course_id = _normalize_course_id(course_id)
        summary = await self._resolve_course(
            course_id=course_id, course_title=course_title
        )
        summary = summary.model_copy(update={"id": course_id})
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
        if error:
            return [_stage_error_item("exams", IngestionError(error), kind="past_exam")]
        if not resources:
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
            # Every year's paper is just `COMP50001.pdf`, so we'd collide on
            # disk; tag the filename with the academic year to keep them
            # distinct and human-recognisable.
            year = str(resource.metadata.get("academic_year") or "").strip()
            stem = _filename_from_url(resource.source_url or "") or summary.id
            stem_no_ext = stem.rsplit(".", 1)[0] if "." in stem else stem
            desired = f"{stem_no_ext}-{year}" if year else stem_no_ext
            local_rel = _write_resource_to_disk(
                course_dir=course_dir,
                kind="past_exam",
                title=resource.title,
                url=resource.source_url or "",
                content=content,
                content_type=content_type,
                desired_name=desired,
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
                    metadata={
                        **resource.metadata,
                        "stage": "exams",
                        "content_type": content_type,
                        "discovered_by": "exams_agent",
                    },
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
        # Use the digit tail (50001) for matching — Imperial exam PDFs are
        # named COMP50001.pdf so we still need to filter by it, but the
        # agent can also match the full Edstem-style code if asked.
        course_code = f"COMP{summary.id}" if summary.id.isdigit() else summary.id
        try:
            report = await discover_past_exams(
                course_id=course_code,
                settings=self.settings,
            )
        except Exception as exc:  # pragma: no cover.
            return [], str(exc)
        if report.error:
            return [], report.error
        resources = [
            Resource(
                course_id=summary.id,
                title=exam.title,
                kind="past_exam",
                source_url=exam.source_url,
                metadata={
                    "source": "exams",
                    "academic_year": exam.academic_year,
                },
            )
            for exam in report.exams
        ]
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
        if local_path.suffix.lower() == ".pdf":
            return build_pdf_chunks(resource, local_path)
        text = extract_text(local_path)
        return build_chunks(resource, text)


def build_auto_indexer(
    settings: Settings,
    rag: RAGService,
    session: BrowserSession,
    *,
    include_panopto: bool = True,
    include_exams: bool = True,
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
    )


# ----- pure helpers -----


_CODE_PREFIX_RE = re.compile(
    r"^\s*[A-Z]{2,5}\s*\d{3,5}(?:\.\d+)?\s*[:\-—]\s*", re.IGNORECASE
)


def _digit_tail(code: str) -> str:
    return re.sub(r"^[A-Za-z]+", "", code).strip()


def _normalize_course_id(code: str) -> str:
    """Canonical form: uppercase, no spaces, COMP prefix for bare digit codes."""
    code = code.strip().upper().replace(" ", "")
    if code.isdigit():
        return f"COMP{code}"
    return code


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


def _filename_from_url(url: str) -> str | None:
    """Pull the file's basename out of the URL path.

    Scientia and exams.doc.ic.ac.uk both serve files at paths like
    `/api/resources/20873/file/COMP50001-Sheet01-answers.pdf` — the last
    segment is the original filename the lecturer uploaded. We use it
    verbatim so the local copy is recognisable.
    """
    parsed = urlparse(url)
    last = unquote(parsed.path).rstrip("/").rsplit("/", 1)[-1]
    if not last:
        return None
    # Strip path separators defensively; otherwise leave the name alone.
    cleaned = last.replace("/", "_").replace("\\", "_").replace("\x00", "")
    return cleaned or None


def _write_resource_to_disk(
    *,
    course_dir: Path,
    kind: ResourceKind,
    title: str,
    url: str,
    content: bytes,
    content_type: str | None,
    desired_name: str | None = None,
) -> str | None:
    suffix = infer_suffix(url, content_type)
    if suffix not in SUPPORTED_DOWNLOAD_SUFFIXES:
        return None
    folder = _kind_dir(course_dir, kind)
    if desired_name:
        name = desired_name
        if not name.lower().endswith(suffix):
            name = f"{name}{suffix}"
    else:
        # Prefer the filename Scientia/exams serve us so the local copy
        # matches what the lecturer uploaded. Fall back to a safe title
        # only when the URL has no usable path segment (rare).
        name = _filename_from_url(url)
        if not name:
            safe_name = safe_path_part(title)
            if safe_name.lower().endswith(suffix):
                safe_name = safe_name[: -len(suffix)] or "resource"
            name = f"{safe_name}{suffix}"
        elif not name.lower().endswith(suffix):
            name = f"{name}{suffix}"
    target = unique_path(folder / name)
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


def _stage_error_item(
    stage: str,
    exc: BaseException,
    *,
    kind: ResourceKind = "material",
) -> ManifestItem:
    """Record a whole stage that crashed before producing any item."""
    return ManifestItem(
        source_url="",
        local_path="",
        kind=kind,  # placeholder; index_local will skip (no local_path)
        title=f"[{stage} stage failed]",
        downloaded_at=now_iso(),
        metadata={"stage": stage, "error": f"{type(exc).__name__}: {exc}"},
    )
