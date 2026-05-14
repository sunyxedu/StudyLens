from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel

from studylens.config import Settings
from studylens.domain import Resource
from studylens.errors import ConfigurationError, UnsupportedDocumentError
from studylens.ingestion._paths import safe_path_part, unique_path
from studylens.ingestion.documents import build_chunks, extract_text
from studylens.retrieval.qa import RAGService


class ExamIndexResult(BaseModel):
    title: str
    status: str
    source_url: str | None = None
    local_path: str | None = None
    chunks: int = 0
    error: str | None = None
    discovered: bool = True


def parse_all_pdfs(html: str, base_url: str, course_id: str) -> list[Resource]:
    """Parse every PDF anchor on a course-specific exam-paper page."""

    soup = BeautifulSoup(html, "html.parser")
    resources: list[Resource] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href"))
        if not href.lower().endswith(".pdf"):
            continue
        label = " ".join(anchor.get_text(" ").split()) or href.rsplit("/", 1)[-1]
        absolute = urljoin(base_url, href)
        if absolute in seen:
            continue
        seen.add(absolute)
        resources.append(
            Resource(
                course_id=course_id,
                title=label,
                kind="past_exam",
                source_url=absolute,
                metadata={"source": "exams"},
            )
        )
    return resources


def parse_exam_links(html: str, base_url: str, course_id: str) -> list[Resource]:
    """Parse the root exam index, keeping only PDFs that mention the course."""

    soup = BeautifulSoup(html, "html.parser")
    resources: list[Resource] = []
    seen: set[str] = set()
    needle = course_id.lower()
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href"))
        label = " ".join(anchor.get_text(" ").split()) or href.rsplit("/", 1)[-1]
        haystack = f"{href} {label}".lower()
        if needle not in haystack and not any(
            token in haystack for token in ("exam", "paper")
        ):
            continue
        if not href.lower().endswith((".pdf", ".html", ".htm")):
            continue
        if needle not in haystack:
            continue
        absolute = urljoin(base_url, href)
        if absolute in seen:
            continue
        seen.add(absolute)
        resources.append(
            Resource(
                course_id=course_id,
                title=label,
                kind="past_exam",
                source_url=absolute,
                metadata={"source": "exams"},
            )
        )
    return resources


@dataclass(slots=True)
class ExamsClient:
    """HTTP-Basic client for https://exams.doc.ic.ac.uk/.

    Discovery tries a course-specific page first (`{base}{course_id}/`),
    then falls back to filtering the root index. Any 401 propagates as a
    ConfigurationError because the only fix is fresh credentials.
    """

    base_url: str
    username: str | None = None
    password: str | None = None
    timeout: float = 30.0

    def _require_credentials(self) -> tuple[str, str]:
        if not self.username or not self.password:
            raise ConfigurationError(
                "Exams access requires IMPERIAL_USERNAME and IMPERIAL_PASSWORD"
            )
        return self.username, self.password

    async def discover_exam_papers(self, course_id: str) -> list[Resource]:
        username, password = self._require_credentials()
        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            auth=(username, password),
        ) as client:
            course_url = urljoin(self.base_url, f"{course_id}/")
            try:
                course_response = await client.get(course_url)
            except httpx.HTTPError:
                course_response = None

            if course_response is not None and course_response.status_code == 401:
                raise ConfigurationError(
                    "Exams credentials rejected (HTTP 401); refresh "
                    "IMPERIAL_USERNAME / IMPERIAL_PASSWORD"
                )
            if (
                course_response is not None
                and course_response.is_success
                and "html" in (course_response.headers.get("content-type") or "").lower()
            ):
                resources = parse_all_pdfs(course_response.text, course_url, course_id)
                if resources:
                    return resources

            root_response = await client.get(self.base_url)
            if root_response.status_code == 401:
                raise ConfigurationError(
                    "Exams credentials rejected (HTTP 401); refresh "
                    "IMPERIAL_USERNAME / IMPERIAL_PASSWORD"
                )
            root_response.raise_for_status()
            return parse_exam_links(root_response.text, self.base_url, course_id)

    async def download(self, url: str) -> tuple[bytes, str | None]:
        username, password = self._require_credentials()
        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            auth=(username, password),
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content, response.headers.get("content-type")


@dataclass(slots=True)
class ExamsIndexer:
    """Index past exam papers into the RAG store as `past_exam` chunks."""

    settings: Settings
    rag: RAGService
    client: ExamsClient

    async def index_course_exams(self, *, course_id: str) -> list[ExamIndexResult]:
        if not self.settings.imperial_username or not self.settings.imperial_password:
            return [
                ExamIndexResult(
                    title="Past exams",
                    status="skipped",
                    error=(
                        "Set IMPERIAL_USERNAME and IMPERIAL_PASSWORD "
                        "to index past exam papers"
                    ),
                    discovered=False,
                )
            ]

        try:
            resources = await self.client.discover_exam_papers(course_id)
        except ConfigurationError as exc:
            return [
                ExamIndexResult(
                    title="Past exams",
                    status="failed",
                    error=str(exc),
                    discovered=False,
                )
            ]
        except Exception as exc:  # pragma: no cover - exact network errors vary.
            return [
                ExamIndexResult(
                    title="Past exams",
                    status="failed",
                    error=str(exc),
                    discovered=False,
                )
            ]

        if not resources:
            return [
                ExamIndexResult(
                    title="Past exams",
                    status="skipped",
                    error=f"No past exam papers found for {course_id}",
                    discovered=False,
                )
            ]

        results: list[ExamIndexResult] = []
        for resource in resources:
            results.append(await self._index_one(resource))
        return results

    async def _index_one(self, resource: Resource) -> ExamIndexResult:
        if not resource.source_url:
            return ExamIndexResult(
                title=resource.title,
                status="skipped",
                error="Resource has no source URL",
            )
        try:
            content, content_type = await self.client.download(resource.source_url)
        except Exception as exc:  # pragma: no cover - per-paper download failures are noisy.
            return ExamIndexResult(
                title=resource.title,
                status="failed",
                source_url=resource.source_url,
                error=str(exc),
            )

        suffix = Path(resource.source_url).suffix.lower() or ".pdf"
        output_dir = self.settings.raw_dir / safe_path_part(resource.course_id) / "exams"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = unique_path(output_dir / f"{safe_path_part(resource.title)}{suffix}")
        output_path.write_bytes(content)
        local = resource.model_copy(
            update={
                "local_path": output_path,
                "mime_type": content_type,
                "metadata": {**resource.metadata, "auto_indexed": True},
            }
        )

        try:
            text = extract_text(output_path)
        except UnsupportedDocumentError as exc:
            return ExamIndexResult(
                title=resource.title,
                status="skipped",
                source_url=resource.source_url,
                local_path=str(output_path),
                error=str(exc),
            )
        chunks = build_chunks(local, text)
        indexed = self.rag.index_chunks(chunks)
        return ExamIndexResult(
            title=resource.title,
            status="indexed",
            source_url=resource.source_url,
            local_path=str(output_path),
            chunks=indexed,
        )


def build_exams_indexer(settings: Settings, rag: RAGService) -> ExamsIndexer:
    return ExamsIndexer(
        settings=settings,
        rag=rag,
        client=ExamsClient(
            base_url=str(settings.exams_base_url),
            username=settings.imperial_username,
            password=settings.imperial_password,
        ),
    )
