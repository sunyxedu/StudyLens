from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

from studylens.config import Settings
from studylens.domain import Resource
from studylens.errors import ConfigurationError
from studylens.ingestion.captions import build_caption_chunks, parse_caption_segments
from studylens.ingestion.documents import build_chunks, extract_text
from studylens.ingestion.video import TranscriptExtractor
from studylens.retrieval.qa import RAGService

SESSION_ID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)


@dataclass(frozen=True, slots=True)
class PanoptoSession:
    id: str
    title: str
    viewer_url: str
    folder_name: str | None = None


@dataclass(frozen=True, slots=True)
class PanoptoVideoIndexResult:
    title: str
    status: str
    source_url: str | None = None
    local_path: str | None = None
    chunks: int = 0
    error: str | None = None
    discovered: bool = True


@dataclass(slots=True)
class PanoptoDownloader:
    """Thin browser-automation boundary for Panopto.

    The exact Panopto DOM can vary by tenant and login state, so this class keeps
    network/session concerns outside the retrieval pipeline. Tests should target
    callers with fake downloaders; live runs require Playwright and a saved
    browser storage state.
    """

    base_url: str
    storage_state: Path | None = None
    download_dir: Path = Path("data/raw/panopto")

    def require_browser_state(self) -> None:
        if not self.storage_state:
            raise ConfigurationError(
                "Panopto access requires STUDYLENS_BROWSER_STORAGE_STATE "
                "with an authenticated session"
            )
        if not self.storage_state.exists():
            raise ConfigurationError(f"Browser storage state not found: {self.storage_state}")

    async def search_course_videos(self, course_id: str, course_title: str) -> list[Resource]:
        self.require_browser_state()
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise ConfigurationError("Install studylens[browser] to use Panopto ingestion") from exc

        query = f"{course_id} {course_title}".strip()
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(storage_state=str(self.storage_state))
            page = await context.new_page()
            await page.goto(f"{self.base_url}#isSharedWithMe=true", wait_until="domcontentloaded")
            search = page.get_by_role("textbox").first
            await search.fill(query)
            await page.keyboard.press("Enter")
            await page.wait_for_load_state("networkidle")
            links = await page.locator("a").evaluate_all(
                "(nodes) => nodes.map(a => ({text: a.innerText, href: a.href}))"
                ".filter(x => x.text && x.href)"
            )
            await browser.close()

        resources: list[Resource] = []
        for item in links:
            title = str(item.get("text", "")).strip()
            href = str(item.get("href", "")).strip()
            if not title or not href:
                continue
            resources.append(
                Resource(
                    course_id=course_id,
                    title=title,
                    kind="video",
                    source_url=href,
                    metadata={"source": "panopto", "query": query},
                )
            )
        return resources


@dataclass(slots=True)
class PanoptoVideoIndexer:
    """Index Panopto videos by combining session metadata with captions/transcripts."""

    settings: Settings
    rag: RAGService
    max_videos: int = 30

    def index_course_videos(
        self,
        *,
        course_id: str,
        course_title: str,
    ) -> list[PanoptoVideoIndexResult]:
        if not self.settings.browser_storage_state:
            return [
                PanoptoVideoIndexResult(
                    title="Panopto videos",
                    status="skipped",
                    error=(
                        "Set STUDYLENS_BROWSER_STORAGE_STATE to index Panopto "
                        "captions and video transcripts"
                    ),
                    discovered=False,
                )
            ]
        if not self.settings.browser_storage_state.exists():
            return [
                PanoptoVideoIndexResult(
                    title="Panopto videos",
                    status="failed",
                    error=f"Browser storage state not found: {self.settings.browser_storage_state}",
                    discovered=False,
                )
            ]

        try:
            return asyncio.run(
                self._index_course_videos_async(
                    course_id=course_id,
                    course_title=course_title,
                )
            )
        except Exception as exc:  # pragma: no cover - live Panopto failures are tenant-specific.
            return [
                PanoptoVideoIndexResult(
                    title="Panopto videos",
                    status="failed",
                    error=str(exc),
                    discovered=False,
                )
            ]

    async def _index_course_videos_async(
        self,
        *,
        course_id: str,
        course_title: str,
    ) -> list[PanoptoVideoIndexResult]:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise ConfigurationError("Install studylens[browser] to use Panopto ingestion") from exc

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                storage_state=str(self.settings.browser_storage_state)
            )
            page = await context.new_page()
            sessions = await self._search_sessions(
                page=page,
                course_id=course_id,
                course_title=course_title,
            )
            results: list[PanoptoVideoIndexResult] = []
            for session in sessions[: self.max_videos]:
                results.append(await self._index_session(context, course_id, session))
            await browser.close()

        if not results:
            return [
                PanoptoVideoIndexResult(
                    title="Panopto videos",
                    status="skipped",
                    error="No Panopto sessions found for this course query",
                    discovered=False,
                )
            ]
        return results

    async def _search_sessions(
        self,
        *,
        page: Any,
        course_id: str,
        course_title: str,
    ) -> list[PanoptoSession]:
        query = f"{course_id} {course_title}".strip()
        await page.goto(
            f"{self.settings.panopto_base_url}#isSharedWithMe=true",
            wait_until="domcontentloaded",
        )
        textbox = page.get_by_role("textbox").first
        await textbox.fill(query)
        await page.keyboard.press("Enter")
        await page.wait_for_load_state("networkidle")
        links = await page.locator("a").evaluate_all(
            "(nodes) => nodes.map(a => ({text: (a.innerText || '').trim(), href: a.href}))"
            ".filter(x => x.href)"
        )

        sessions: dict[str, PanoptoSession] = {}
        for item in links:
            href = str(item.get("href", "")).strip()
            session_id = extract_session_id(href)
            if not session_id or session_id in sessions:
                continue
            title = str(item.get("text", "")).strip() or f"Panopto session {session_id}"
            sessions[session_id] = PanoptoSession(
                id=session_id,
                title=title,
                viewer_url=href,
            )
        return list(sessions.values())

    async def _index_session(
        self,
        context: Any,
        course_id: str,
        session: PanoptoSession,
    ) -> PanoptoVideoIndexResult:
        details = await self._fetch_session_details(context, session)
        caption_text, caption_source = await self._fetch_caption_text(context, session, details)
        if caption_text:
            return self._index_caption_text(
                course_id=course_id,
                session=session,
                caption_text=caption_text,
                caption_source=caption_source,
            )
        return await self._index_video_transcription(context, course_id, session, details)

    async def _fetch_session_details(self, context: Any, session: PanoptoSession) -> dict[str, Any]:
        api_url = panopto_session_api_url(str(self.settings.panopto_base_url), session.id)
        response = await context.request.get(api_url)
        if not response.ok:
            return {}
        try:
            data = await response.json()
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    async def _fetch_caption_text(
        self,
        context: Any,
        session: PanoptoSession,
        details: dict[str, Any],
    ) -> tuple[str | None, str | None]:
        urls = [
            *find_deep_urls(details, ("CaptionDownloadUrl", "captionDownloadUrl")),
            generated_srt_url(str(self.settings.panopto_base_url), session.id),
        ]
        seen: set[str] = set()
        for url in urls:
            absolute = urljoin(str(self.settings.panopto_base_url), url)
            if absolute in seen:
                continue
            seen.add(absolute)
            response = await context.request.get(absolute)
            if not response.ok:
                continue
            text = await response.text()
            if "-->" in text and len(text.strip()) > 20:
                return text, absolute
        return None, None

    def _index_caption_text(
        self,
        *,
        course_id: str,
        session: PanoptoSession,
        caption_text: str,
        caption_source: str | None,
    ) -> PanoptoVideoIndexResult:
        output_path = self._write_text_artifact(
            course_id=course_id,
            title=session.title,
            suffix=".srt",
            text=caption_text,
        )
        resource = Resource(
            course_id=course_id,
            title=f"{session.title} captions",
            kind="transcript",
            source_url=session.viewer_url,
            local_path=output_path,
            metadata={
                "source": "panopto",
                "session_id": session.id,
                "caption_source": caption_source,
                "video_url": session.viewer_url,
            },
        )
        segments = parse_caption_segments(caption_text)
        chunks = build_caption_chunks(resource, segments)
        if not chunks:
            chunks = build_chunks(resource, caption_text)
        indexed = self.rag.index_chunks(chunks)
        return PanoptoVideoIndexResult(
            title=session.title,
            status="indexed",
            source_url=session.viewer_url,
            local_path=str(output_path),
            chunks=indexed,
        )

    async def _index_video_transcription(
        self,
        context: Any,
        course_id: str,
        session: PanoptoSession,
        details: dict[str, Any],
    ) -> PanoptoVideoIndexResult:
        download_url = first_deep_url(
            details,
            (
                "DownloadUrl",
                "downloadUrl",
                "PodcastDownloadUrl",
                "podcastDownloadUrl",
                "Mp4Url",
                "mp4Url",
            ),
        )
        if not download_url:
            return PanoptoVideoIndexResult(
                title=session.title,
                status="skipped",
                source_url=session.viewer_url,
                error="No captions or downloadable video URL found",
            )
        if not self.settings.openai_api_key:
            return PanoptoVideoIndexResult(
                title=session.title,
                status="skipped",
                source_url=session.viewer_url,
                error="No captions found and OpenAI API key is required for video transcription",
            )

        absolute = urljoin(str(self.settings.panopto_base_url), download_url)
        response = await context.request.get(absolute)
        if not response.ok:
            return PanoptoVideoIndexResult(
                title=session.title,
                status="failed",
                source_url=session.viewer_url,
                error=f"Video download failed with HTTP {response.status}",
            )
        media_path = self._write_binary_artifact(
            course_id=course_id,
            title=session.title,
            suffix=".mp4",
            content=await response.body(),
        )
        transcript = TranscriptExtractor(
            transcript_dir=self.settings.processed_dir / "transcripts"
        ).transcribe_with_openai(course_id, media_path, self.settings.openai_api_key)
        transcript = transcript.model_copy(
            update={
                "title": f"{session.title} transcript",
                "source_url": session.viewer_url,
                "metadata": {
                    **transcript.metadata,
                    "source": "panopto_video_transcription",
                    "session_id": session.id,
                    "video_url": session.viewer_url,
                },
            }
        )
        chunks = build_chunks(transcript, extract_text(transcript.local_path or Path()))
        indexed = self.rag.index_chunks(chunks)
        return PanoptoVideoIndexResult(
            title=session.title,
            status="indexed",
            source_url=session.viewer_url,
            local_path=str(transcript.local_path) if transcript.local_path else None,
            chunks=indexed,
        )

    def _write_text_artifact(self, *, course_id: str, title: str, suffix: str, text: str) -> Path:
        output = self._artifact_path(course_id=course_id, title=title, suffix=suffix)
        output.write_text(text, encoding="utf-8")
        return output

    def _write_binary_artifact(
        self,
        *,
        course_id: str,
        title: str,
        suffix: str,
        content: bytes,
    ) -> Path:
        output = self._artifact_path(course_id=course_id, title=title, suffix=suffix)
        output.write_bytes(content)
        return output

    def _artifact_path(self, *, course_id: str, title: str, suffix: str) -> Path:
        output_dir = self.settings.raw_dir / safe_path_part(course_id) / "panopto"
        output_dir.mkdir(parents=True, exist_ok=True)
        return unique_path(output_dir / f"{safe_path_part(title)}{suffix}")


def extract_session_id(url: str) -> str | None:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key in ("id", "sessionID", "sessionId", "deliveryId"):
        values = query.get(key)
        if values:
            match = SESSION_ID_RE.search(values[0])
            if match:
                return match.group(0).lower()
    match = SESSION_ID_RE.search(url)
    return match.group(0).lower() if match else None


def panopto_origin(base_url: str) -> str:
    parsed = urlparse(base_url)
    return f"{parsed.scheme}://{parsed.netloc}"


def panopto_session_api_url(base_url: str, session_id: str) -> str:
    return f"{panopto_origin(base_url)}/Panopto/api/v1/sessions/{session_id}"


def generated_srt_url(base_url: str, session_id: str) -> str:
    return (
        f"{panopto_origin(base_url)}/Panopto/Pages/Transcription/"
        f"GenerateSRT.ashx?id={session_id}&language=1"
    )


def find_deep_urls(data: Any, keys: tuple[str, ...]) -> list[str]:
    urls: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            if key in keys and isinstance(value, str) and value:
                urls.append(value)
            else:
                urls.extend(find_deep_urls(value, keys))
    elif isinstance(data, list):
        for item in data:
            urls.extend(find_deep_urls(item, keys))
    return urls


def first_deep_url(data: Any, keys: tuple[str, ...]) -> str | None:
    urls = find_deep_urls(data, keys)
    return urls[0] if urls else None


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
    raise ConfigurationError(f"Could not find available filename for {path}")
