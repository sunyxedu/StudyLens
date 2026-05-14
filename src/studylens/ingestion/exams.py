from __future__ import annotations

import re
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


YEAR_ARCHIVE_RE = re.compile(r"pastpapers/papers\.\d{2}-\d{2}")
YEAR_LABEL_RE = re.compile(r"papers\.(\d{2})-(\d{2})")
ARCHIVE_INDEX_PATH = "archive.html"
DEFAULT_MAX_YEARS = 10


def find_year_archive_urls(html: str, base_url: str) -> list[str]:
    """Year-archive URLs linked from an exams page (root or archive)."""
    soup = BeautifulSoup(html, "html.parser")
    seen: dict[str, None] = {}
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href"))
        if not YEAR_ARCHIVE_RE.search(href):
            continue
        absolute = urljoin(base_url, href)
        if not absolute.endswith("/"):
            absolute = absolute + "/"  # urljoin treats the last segment as a dir
        seen.setdefault(absolute, None)
    return list(seen)


def _academic_year_start(year_url: str) -> int:
    """Map `.../papers.YY-YY/` to a full 4-digit start year for sorting."""
    match = YEAR_LABEL_RE.search(year_url)
    if not match:
        return 0
    yy = int(match.group(1))
    return 2000 + yy if yy < 50 else 1900 + yy


@dataclass(slots=True)
class ExamsClient:
    """HTTP-Basic client for https://exams.doc.ic.ac.uk/.

    The site is a flat server-rendered index. The root page links to each
    academic year's archive (`pastpapers/papers.YY-YY/`); each archive
    page in turn lists every paper for every course as
    `COMP{course}.pdf`. Discovery walks the root → year archives → filters
    PDFs whose URL or anchor text mentions the course code.
    """

    base_url: str
    username: str | None = None
    password: str | None = None
    timeout: float = 30.0
    max_years: int = DEFAULT_MAX_YEARS

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
            # Recent years live on the root index; older ones are linked from
            # /archive.html. We merge both, sort by recency, and walk the most
            # recent N. Going further back than ~10 years rarely helps since
            # the syllabus drifts and old papers stop being representative.
            root_response = await client.get(self.base_url)
            if root_response.status_code == 401:
                raise ConfigurationError(
                    "Exams credentials rejected (HTTP 401); refresh "
                    "IMPERIAL_USERNAME / IMPERIAL_PASSWORD"
                )
            root_response.raise_for_status()
            year_urls = list(find_year_archive_urls(root_response.text, self.base_url))

            archive_url = urljoin(self.base_url, ARCHIVE_INDEX_PATH)
            try:
                archive_response = await client.get(archive_url)
            except httpx.HTTPError:
                archive_response = None
            if archive_response is not None and archive_response.is_success:
                year_urls.extend(
                    find_year_archive_urls(archive_response.text, archive_url)
                )

            unique_years = list(dict.fromkeys(year_urls))
            unique_years.sort(key=_academic_year_start, reverse=True)
            target_years = unique_years[: self.max_years]

            seen_pdf_urls: set[str] = set()
            resources: list[Resource] = []
            for year_url in target_years:
                year_match = YEAR_LABEL_RE.search(year_url)
                year_label = (
                    f"{year_match.group(1)}-{year_match.group(2)}" if year_match else None
                )
                try:
                    year_response = await client.get(year_url)
                except httpx.HTTPError:
                    continue
                if not year_response.is_success:
                    continue
                for resource in parse_exam_links(
                    year_response.text, year_url, course_id
                ):
                    if resource.source_url in seen_pdf_urls:
                        continue
                    seen_pdf_urls.add(resource.source_url or "")
                    if year_label:
                        resource = resource.model_copy(
                            update={
                                "metadata": {
                                    **resource.metadata,
                                    "academic_year": year_label,
                                },
                            }
                        )
                    resources.append(resource)
            return resources

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
