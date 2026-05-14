from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

from studylens.config import Settings
from studylens.domain import Resource
from studylens.ingestion._paths import safe_path_part, unique_path
from studylens.ingestion.browser_session import BrowserSession
from studylens.ingestion.captions import build_caption_chunks, parse_caption_segments
from studylens.ingestion.documents import build_chunks, extract_text
from studylens.ingestion.panopto_agent import discover_course_videos
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
class PanoptoVideoIndexer:
    """Index Panopto videos by combining session metadata with captions/transcripts."""

    settings: Settings
    rag: RAGService
    session: BrowserSession
    max_videos: int = 30

    async def index_course_videos(
        self,
        *,
        course_id: str,
        course_title: str,
    ) -> list[PanoptoVideoIndexResult]:
        try:
            report = await discover_course_videos(
                self.session,
                course_id=course_id,
                course_title=course_title,
                settings=self.settings,
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

        if report.error:
            return [
                PanoptoVideoIndexResult(
                    title="Panopto videos",
                    status="failed",
                    error=report.error,
                    discovered=bool(report.videos),
                )
            ]

        sessions = [
            PanoptoSession(id=v.session_id, title=v.title, viewer_url=v.viewer_url)
            for v in report.videos
        ]
        if not sessions:
            return [
                PanoptoVideoIndexResult(
                    title="Panopto videos",
                    status="skipped",
                    error="Agent returned no Panopto sessions for this course",
                    discovered=False,
                )
            ]

        results: list[PanoptoVideoIndexResult] = []
        for panopto_session in sessions[: self.max_videos]:
            try:
                results.append(await self._index_session(course_id, panopto_session))
            except Exception as exc:  # pragma: no cover - per-session failures.
                results.append(
                    PanoptoVideoIndexResult(
                        title=panopto_session.title,
                        status="failed",
                        source_url=panopto_session.viewer_url,
                        error=str(exc),
                    )
                )
        return results

    async def _index_session(
        self,
        course_id: str,
        panopto_session: PanoptoSession,
    ) -> PanoptoVideoIndexResult:
        details = await self._fetch_session_details(panopto_session)
        caption_text, caption_source = await self._fetch_caption_text(panopto_session, details)
        if caption_text:
            return self._index_caption_text(
                course_id=course_id,
                panopto_session=panopto_session,
                caption_text=caption_text,
                caption_source=caption_source,
            )
        return await self._index_video_transcription(course_id, panopto_session, details)

    async def _fetch_session_details(self, panopto_session: PanoptoSession) -> dict[str, Any]:
        api_url = panopto_session_api_url(str(self.settings.panopto_base_url), panopto_session.id)
        try:
            text = await self.session.fetch_text(api_url)
        except Exception:
            return {}
        import json

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    async def _fetch_caption_text(
        self,
        panopto_session: PanoptoSession,
        details: dict[str, Any],
    ) -> tuple[str | None, str | None]:
        urls = [
            *find_deep_urls(details, ("CaptionDownloadUrl", "captionDownloadUrl")),
            generated_srt_url(str(self.settings.panopto_base_url), panopto_session.id),
        ]
        seen: set[str] = set()
        for url in urls:
            absolute = urljoin(str(self.settings.panopto_base_url), url)
            if absolute in seen:
                continue
            seen.add(absolute)
            try:
                text = await self.session.fetch_text(absolute)
            except Exception:
                continue
            if "-->" in text and len(text.strip()) > 20:
                return text, absolute
        return None, None

    def _index_caption_text(
        self,
        *,
        course_id: str,
        panopto_session: PanoptoSession,
        caption_text: str,
        caption_source: str | None,
    ) -> PanoptoVideoIndexResult:
        output_path = self._write_text_artifact(
            course_id=course_id,
            title=panopto_session.title,
            suffix=".srt",
            text=caption_text,
        )
        resource = Resource(
            course_id=course_id,
            title=f"{panopto_session.title} captions",
            kind="transcript",
            source_url=panopto_session.viewer_url,
            local_path=output_path,
            metadata={
                "source": "panopto",
                "session_id": panopto_session.id,
                "caption_source": caption_source,
                "video_url": panopto_session.viewer_url,
            },
        )
        segments = parse_caption_segments(caption_text)
        chunks = build_caption_chunks(resource, segments)
        if not chunks:
            chunks = build_chunks(resource, caption_text)
        indexed = self.rag.index_chunks(chunks)
        return PanoptoVideoIndexResult(
            title=panopto_session.title,
            status="indexed",
            source_url=panopto_session.viewer_url,
            local_path=str(output_path),
            chunks=indexed,
        )

    async def _index_video_transcription(
        self,
        course_id: str,
        panopto_session: PanoptoSession,
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
                title=panopto_session.title,
                status="skipped",
                source_url=panopto_session.viewer_url,
                error="No captions or downloadable video URL found",
            )
        if not self.settings.openai_api_key:
            return PanoptoVideoIndexResult(
                title=panopto_session.title,
                status="skipped",
                source_url=panopto_session.viewer_url,
                error="No captions found and OpenAI API key is required for video transcription",
            )

        absolute = urljoin(str(self.settings.panopto_base_url), download_url)
        try:
            body, _ = await self.session.download(absolute)
        except Exception as exc:
            return PanoptoVideoIndexResult(
                title=panopto_session.title,
                status="failed",
                source_url=panopto_session.viewer_url,
                error=str(exc),
            )
        media_path = self._write_binary_artifact(
            course_id=course_id,
            title=panopto_session.title,
            suffix=".mp4",
            content=body,
        )
        transcript = TranscriptExtractor(
            transcript_dir=self.settings.processed_dir / "transcripts"
        ).transcribe_with_openai(course_id, media_path, self.settings.openai_api_key)
        transcript = transcript.model_copy(
            update={
                "title": f"{panopto_session.title} transcript",
                "source_url": panopto_session.viewer_url,
                "metadata": {
                    **transcript.metadata,
                    "source": "panopto_video_transcription",
                    "session_id": panopto_session.id,
                    "video_url": panopto_session.viewer_url,
                },
            }
        )
        chunks = build_chunks(transcript, extract_text(transcript.local_path or Path()))
        indexed = self.rag.index_chunks(chunks)
        return PanoptoVideoIndexResult(
            title=panopto_session.title,
            status="indexed",
            source_url=panopto_session.viewer_url,
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
