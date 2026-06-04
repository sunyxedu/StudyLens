from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient

from studylens.config import Settings
from studylens.domain import CourseSummary, Resource
from studylens.ingestion._paths import safe_path_part
from studylens.ingestion.auto_index import (
    CourseAutoIndexer,
    _match_course,
    infer_suffix,
)
from studylens.ingestion.panopto_agent import DiscoveredVideo
from studylens.ingestion.scientia_agent import DiscoveredResource
from studylens.retrieval import HashEmbeddingClient, QdrantVectorStore, RAGService
from studylens.retrieval.qa import TemplateLLM


class FakeAsyncFetcher:
    def __init__(self) -> None:
        self.text = {
            "https://scientia.doc.ic.ac.uk/2526/modules": "<html>placeholder</html>",
        }
        self.downloads: dict[str, tuple[bytes, str | None]] = {}

    async def get_text(self, url: str) -> str:
        return self.text[url]

    async def download(self, url: str) -> tuple[bytes, str | None]:
        return self.downloads[url]


class FakeCourseExtractor:
    def __init__(self, courses: list[CourseSummary]) -> None:
        self._courses = courses
        self.calls = 0

    async def extract_courses(self, html: str, base_url: str) -> list[CourseSummary]:
        self.calls += 1
        return list(self._courses)


def make_service() -> RAGService:
    embeddings = HashEmbeddingClient(dimensions=64)
    store = QdrantVectorStore(
        collection_name="auto_index_test",
        dimensions=64,
        client=QdrantClient(":memory:"),
    )
    return RAGService(embeddings=embeddings, vector_store=store, llm=TemplateLLM())


def _build_indexer(
    tmp_path: Path,
    *,
    downloads: dict[str, tuple[bytes, str | None]] | None = None,
    sci: Any = None,
    pan: Any = None,
    exams: Any = None,
    caption: Any = None,
    exam_download: Any = None,
    course_url: str = "https://scientia.test/2526/modules/COMP70001/materials",
) -> CourseAutoIndexer:
    settings = Settings(
        data_dir=tmp_path / "data",
        qdrant_path=tmp_path / "data" / "vector" / "qdrant",
        vector_db_path=tmp_path / "data" / "vector" / "fallback.sqlite3",
    )
    fetcher = FakeAsyncFetcher()
    fetcher.downloads.update(downloads or {})
    extractor = FakeCourseExtractor(
        [
            CourseSummary(
                id="COMP70001",
                title="Advanced Algorithms",
                url=course_url,
                metadata={"source": "scientia"},
            )
        ]
    )

    async def _empty_resources(self: Any, summary: CourseSummary) -> tuple[list[Any], None]:
        return [], None

    return CourseAutoIndexer(
        settings=settings,
        rag=make_service(),
        fetcher=fetcher,
        course_extractor=extractor,
        scientia_discoverer=sci or _empty_resources,
        panopto_discoverer=pan or _empty_resources,
        exams_discoverer=exams or _empty_resources,
        panopto_caption_fetcher=caption,
        exams_downloader=exam_download,
    )


# ----- pure helpers -------------------------------------------------------


def test_auto_index_helpers_infer_suffix_and_safe_path_names() -> None:
    assert infer_suffix("https://example.test/file", "text/html; charset=utf-8") == ".html"
    assert infer_suffix("https://example.test/file.pdf", "text/plain") == ".pdf"
    assert safe_path_part(" Week 1: DP / graphs ") == "Week-1-DP-graphs"


def test_match_course_bridges_edstem_and_scientia_id_formats() -> None:
    scientia_courses = [
        CourseSummary(
            id="50001",
            title="Algorithm Design and Analysis",
            url="https://scientia.test/2526/modules/50001/materials",
        ),
        CourseSummary(
            id="50007.1",
            title="Computing Practical 2 (Lab)",
            url="https://scientia.test/2526/modules/50007.1/materials",
        ),
        CourseSummary(
            id="COMPM0804",
            title="Student Support and Wellbeing",
            url="https://scientia.test/2526/modules/COMPM0804/materials",
        ),
    ]
    assert (
        _match_course(
            scientia_courses,
            course_id="COMP50001",
            course_title="COMP 50001: Algorithm Design and Analysis",
        ).id
        == "50001"
    )
    assert (
        _match_course(
            scientia_courses,
            course_id="COMP50007.1",
            course_title="COMP 50007.1: Computing Practical 2 (Lab)",
        ).id
        == "50007.1"
    )
    assert (
        _match_course(
            scientia_courses,
            course_id="COMPM0804",
            course_title="COMP COMPM0804: Student Support and Wellbeing",
        ).id
        == "COMPM0804"
    )
    assert (
        _match_course(
            scientia_courses, course_id="UNKNOWN", course_title="Algorithm Design and Analysis"
        ).id
        == "50001"
    )
    assert (
        _match_course(scientia_courses, course_id="COMP99999", course_title="Nonexistent")
        is None
    )


# ----- crawl + index integration -----------------------------------------


def test_crawl_writes_scientia_files_with_original_filename(tmp_path: Path) -> None:
    pdf_url = "https://scientia.test/api/resources/1/file/COMP70001-Sheet01.pdf"

    async def fake_sci(self: Any, summary: CourseSummary):
        return [
            DiscoveredResource(
                title="Sheet 1.pdf",  # agent's normalised title
                source_url=pdf_url,
                kind="exercise",
            ),
        ], None

    indexer = _build_indexer(
        tmp_path,
        downloads={pdf_url: (b"%PDF-fake", "application/pdf")},
        sci=fake_sci,
    )

    manifest = asyncio.run(
        indexer.crawl_course(course_id="COMP70001", course_title="Advanced Algorithms")
    )

    assert [it.kind for it in manifest.items] == ["exercise"]
    course_root = tmp_path / "data" / "raw" / "COMP70001"
    # The on-disk filename comes from the URL path, not from the title — so
    # it matches what the lecturer uploaded on Scientia.
    assert (course_root / "exercise" / "COMP70001-Sheet01.pdf").exists()
    manifest_data = json.loads((course_root / "_crawl.json").read_text(encoding="utf-8"))
    assert manifest_data["items"][0]["local_path"] == "exercise/COMP70001-Sheet01.pdf"


def test_sync_course_chains_crawl_then_index(tmp_path: Path) -> None:
    txt_url = "https://scientia.test/api/resources/1/file/Lecture-1.txt"

    async def fake_sci(self: Any, summary: CourseSummary):
        return [
            DiscoveredResource(title="Lecture 1.txt", source_url=txt_url, kind="material"),
        ], None

    indexer = _build_indexer(
        tmp_path,
        downloads={txt_url: (b"Dynamic programming basics.", "text/plain")},
        sci=fake_sci,
    )

    report = asyncio.run(
        indexer.sync_course(course_id="COMP70001", course_title="Advanced Algorithms")
    )

    assert report.discovered_resources == 1
    assert report.indexed_resources == 1
    assert report.indexed_chunks >= 1
    assert report.items[0].status == "indexed"
    assert report.items[0].stage == "scientia"
    assert indexer.rag.vector_store.count(course_id="COMP70001") >= 1


def test_panopto_caption_writes_transcript_to_disk(tmp_path: Path) -> None:
    video = DiscoveredVideo(
        title="Lecture 1: Intro",
        viewer_url="https://panopto.test/viewer?id=abc",
        session_id="abc",
    )

    async def fake_pan(self: Any, summary: CourseSummary):
        return [video], None

    async def fake_caption(self: Any, v: DiscoveredVideo):
        return (
            "1\n"
            "00:00:00,000 --> 00:00:05,000\n"
            "Hello and welcome.\n\n"
            "2\n"
            "00:00:05,000 --> 00:00:10,000\n"
            "Today we cover dynamic programming.\n"
        )

    indexer = _build_indexer(tmp_path, pan=fake_pan, caption=fake_caption)

    report = asyncio.run(
        indexer.sync_course(course_id="COMP70001", course_title="Advanced Algorithms")
    )

    transcript_items = [it for it in report.items if it.kind == "transcript"]
    assert transcript_items, "expected at least one transcript item"
    assert transcript_items[0].status == "indexed"
    course_root = tmp_path / "data" / "raw" / "COMP70001"
    assert any(
        f.suffix == ".srt"
        for f in (course_root / "transcript").iterdir()
    )


def test_crawl_exams_downloads_agent_discovered_papers(tmp_path: Path) -> None:
    exam_url = "https://exams.doc.ic.ac.uk/pastpapers/papers.24-25/COMP70001.txt"

    async def fake_exams(self: Any, summary: CourseSummary):
        return [
            Resource(
                course_id=summary.id,
                title="COMP70001 24-25 paper",
                kind="past_exam",
                source_url=exam_url,
                metadata={"source": "exams", "academic_year": "24-25"},
            )
        ], None

    async def fake_exam_download(self: Any, resource: Resource):
        return b"Question 1: analyse a dynamic programming recurrence.", "text/plain"

    indexer = _build_indexer(
        tmp_path,
        exams=fake_exams,
        exam_download=fake_exam_download,
    )

    manifest = asyncio.run(
        indexer.crawl_course(course_id="COMP70001", course_title="Advanced Algorithms")
    )

    assert [item.kind for item in manifest.items] == ["past_exam"]
    item = manifest.items[0]
    assert item.metadata["stage"] == "exams"
    assert item.metadata["academic_year"] == "24-25"
    assert item.metadata["discovered_by"] == "exams_agent"
    assert item.local_path == "past_exam/COMP70001-24-25.txt"
    assert (
        tmp_path / "data" / "raw" / "COMP70001" / "past_exam" / "COMP70001-24-25.txt"
    ).exists()


def test_crawl_exams_reports_agent_errors(tmp_path: Path) -> None:
    async def fake_exams(self: Any, summary: CourseSummary):
        return [], "agent could not reach exams.doc.ic.ac.uk"

    indexer = _build_indexer(tmp_path, exams=fake_exams)

    manifest = asyncio.run(
        indexer.crawl_course(course_id="COMP70001", course_title="Advanced Algorithms")
    )
    report = indexer.index_local("COMP70001")

    assert manifest.items[0].kind == "past_exam"
    assert manifest.items[0].metadata["stage"] == "exams"
    assert report.items[0].status == "failed"
    assert "agent could not reach exams.doc.ic.ac.uk" in (report.items[0].error or "")


def test_index_local_skips_unsupported_files(tmp_path: Path) -> None:
    bad_url = "https://scientia.test/api/resources/1/file/slides.pptx"

    async def fake_sci(self: Any, summary: CourseSummary):
        return [
            DiscoveredResource(title="Slides", source_url=bad_url, kind="material"),
        ], None

    indexer = _build_indexer(
        tmp_path,
        downloads={bad_url: (b"binary pptx data", "application/vnd.ms-powerpoint")},
        sci=fake_sci,
    )

    report = asyncio.run(
        indexer.sync_course(course_id="COMP70001", course_title="Advanced Algorithms")
    )

    # crawl phase rejects unsupported suffixes outright (returns None local_path)
    # so the manifest contains 0 items, and the index report sees 0 discovered.
    assert report.discovered_resources == 0
    assert report.indexed_chunks == 0
