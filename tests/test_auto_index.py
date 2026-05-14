from __future__ import annotations

import asyncio
from pathlib import Path

from qdrant_client import QdrantClient

from studylens.config import Settings
from studylens.domain import CourseSummary
from studylens.ingestion._paths import safe_path_part
from studylens.ingestion.auto_index import CourseAutoIndexer, _match_course, infer_suffix
from studylens.ingestion.edstem import EdStemIndexResult
from studylens.ingestion.exams import ExamIndexResult
from studylens.ingestion.panopto import PanoptoVideoIndexResult
from studylens.retrieval import HashEmbeddingClient, QdrantVectorStore, RAGService
from studylens.retrieval.qa import TemplateLLM


def _wrap(real: str) -> str:
    return f"/external-resource?url={real}"


_LECTURE_NOTES = "https%3A%2F%2Fscientia.test%2Fapi%2Fresources%2F1%2Ffile%2FLecture-notes.txt"
_SLIDES = "https%3A%2F%2Fscientia.test%2Fapi%2Fresources%2F2%2Ffile%2Fslides.pptx"
_PROBLEM_SHEET = "https%3A%2F%2Fscientia.test%2Fapi%2Fresources%2F3%2Ffile%2FProblem-Sheet-1.html"

_MATERIALS_TAB = (
    f'<a href="{_wrap(_LECTURE_NOTES)}">Lecture notes</a>'
    f'<a href="{_wrap(_SLIDES)}">Unsupported slides</a>'
)
_EXERCISES_TAB = f'<a href="{_wrap(_PROBLEM_SHEET)}">Problem Sheet 1</a>'
_TUTORIALS_TAB = "<p>no tutorials</p>"


class FakeAsyncFetcher:
    def __init__(self) -> None:
        self.text = {
            "https://scientia.doc.ic.ac.uk/2526/modules": "<html>timeline placeholder</html>",
            "https://scientia.doc.ic.ac.uk/2526/modules/COMP70001/materials": _MATERIALS_TAB,
            "https://scientia.doc.ic.ac.uk/2526/modules/COMP70001/exercises": _EXERCISES_TAB,
            "https://scientia.doc.ic.ac.uk/2526/modules/COMP70001/tutorials": _TUTORIALS_TAB,
        }
        self.downloads = {
            "https://scientia.test/api/resources/1/file/Lecture-notes.txt": (
                b"Dynamic programming stores overlapping subproblems.",
                "text/plain",
            ),
            "https://scientia.test/api/resources/3/file/Problem-Sheet-1.html": (
                b"<html><body><p>Tutorial exercise: write a recurrence.</p></body></html>",
                "text/html",
            ),
            "https://scientia.test/api/resources/2/file/slides.pptx": (
                b"not parseable",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ),
        }

    async def get_text(self, url: str) -> str:
        return self.text[url]

    async def download(self, url: str) -> tuple[bytes, str | None]:
        return self.downloads[url]


class FakeCourseExtractor:
    """Stand-in that returns a fixed course list without calling Claude."""

    def __init__(self, courses: list[CourseSummary]) -> None:
        self._courses = courses
        self.calls = 0

    async def extract_courses(self, html: str, base_url: str) -> list[CourseSummary]:
        self.calls += 1
        return list(self._courses)


def make_extractor() -> FakeCourseExtractor:
    return FakeCourseExtractor(
        [
            CourseSummary(
                id="COMP70001",
                title="COMP70001 Advanced Algorithms",
                # The LLM extractor returns a tab-suffixed URL; auto-index
                # derives the other tabs from it.
                url="https://scientia.doc.ic.ac.uk/2526/modules/COMP70001/materials",
                metadata={"source": "scientia"},
            )
        ]
    )


class FakePanoptoIndexer:
    async def index_course_videos(
        self,
        *,
        course_id: str,
        course_title: str,
    ) -> list[PanoptoVideoIndexResult]:
        assert course_id == "COMP70001"
        assert "Advanced Algorithms" in course_title
        return [
            PanoptoVideoIndexResult(
                title="Lecture video",
                status="indexed",
                source_url="https://panopto.test/viewer?id=1",
                local_path="data/raw/COMP70001/panopto/lecture.srt",
                chunks=3,
            )
        ]


class FakeExamsIndexer:
    async def index_course_exams(self, *, course_id: str) -> list[ExamIndexResult]:
        assert course_id == "COMP70001"
        return [
            ExamIndexResult(
                title="2024 paper",
                status="indexed",
                source_url="https://exams.test/COMP70001/2024.pdf",
                local_path="data/raw/COMP70001/exams/2024.pdf",
                chunks=2,
            )
        ]


class FakeEdStemIndexer:
    async def index_course_scope_notes(
        self,
        *,
        course_id: str,
        course_title: str,
    ) -> list[EdStemIndexResult]:
        assert course_id == "COMP70001"
        assert "Advanced Algorithms" in course_title
        return [EdStemIndexResult(title="Exam scope", status="indexed", chunks=1)]


def make_service() -> RAGService:
    embeddings = HashEmbeddingClient(dimensions=64)
    store = QdrantVectorStore(
        collection_name="auto_index_test",
        dimensions=64,
        client=QdrantClient(":memory:"),
    )
    return RAGService(embeddings=embeddings, vector_store=store, llm=TemplateLLM())


def test_course_auto_indexer_downloads_extracts_and_indexes_supported_resources(
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        qdrant_path=tmp_path / "data" / "vector" / "qdrant",
        vector_db_path=tmp_path / "data" / "vector" / "fallback.sqlite3",
    )
    service = make_service()
    extractor = make_extractor()
    indexer = CourseAutoIndexer(
        settings=settings,
        rag=service,
        fetcher=FakeAsyncFetcher(),
        course_extractor=extractor,
    )

    report = asyncio.run(
        indexer.index_course(course_id="COMP70001", course_title="Advanced Algorithms")
    )

    assert report.course_title == "COMP70001 Advanced Algorithms"
    assert report.discovered_resources == 3
    assert report.indexed_resources == 2
    assert report.indexed_chunks == 2
    assert extractor.calls == 1  # timeline lookup ran
    assert {item.status for item in report.items} == {"indexed", "skipped"}
    assert service.vector_store.count(course_id="COMP70001") == 2
    assert (tmp_path / "data" / "raw" / "COMP70001" / "material" / "Lecture-notes.txt").exists()


def test_course_auto_indexer_includes_panopto_video_results(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        qdrant_path=tmp_path / "data" / "vector" / "qdrant",
        vector_db_path=tmp_path / "data" / "vector" / "fallback.sqlite3",
    )
    service = make_service()
    indexer = CourseAutoIndexer(
        settings=settings,
        rag=service,
        fetcher=FakeAsyncFetcher(),
        course_extractor=make_extractor(),
        panopto_indexer=FakePanoptoIndexer(),
    )

    report = asyncio.run(
        indexer.index_course(course_id="COMP70001", course_title="Advanced Algorithms")
    )

    assert report.discovered_resources == 4
    assert report.indexed_resources == 3
    assert report.indexed_chunks == 5
    assert any(item.stage == "panopto" and item.chunks == 3 for item in report.items)


def test_course_auto_indexer_runs_all_four_stages_when_all_attached(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        qdrant_path=tmp_path / "data" / "vector" / "qdrant",
        vector_db_path=tmp_path / "data" / "vector" / "fallback.sqlite3",
    )
    service = make_service()
    indexer = CourseAutoIndexer(
        settings=settings,
        rag=service,
        fetcher=FakeAsyncFetcher(),
        course_extractor=make_extractor(),
        panopto_indexer=FakePanoptoIndexer(),
        exams_indexer=FakeExamsIndexer(),
        edstem_indexer=FakeEdStemIndexer(),
    )

    report = asyncio.run(
        indexer.index_course(course_id="COMP70001", course_title="Advanced Algorithms")
    )

    stages = {item.stage for item in report.items}
    assert stages == {"scientia", "panopto", "exams", "edstem"}
    assert report.indexed_chunks == 8
    assert any(item.stage == "edstem" and item.status == "indexed" for item in report.items)


def test_course_auto_indexer_raises_when_course_missing_from_timeline(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        qdrant_path=tmp_path / "data" / "vector" / "qdrant",
        vector_db_path=tmp_path / "data" / "vector" / "fallback.sqlite3",
    )
    service = make_service()
    # Empty extractor → nothing matches → IngestionError.
    indexer = CourseAutoIndexer(
        settings=settings,
        rag=service,
        fetcher=FakeAsyncFetcher(),
        course_extractor=FakeCourseExtractor([]),
    )

    import pytest

    from studylens.errors import IngestionError

    with pytest.raises(IngestionError, match="Could not find"):
        asyncio.run(
            indexer.index_course(course_id="COMP70001", course_title="Advanced Algorithms")
        )


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
            id="50007.2",
            title="Computing Practical 2 (Intro to Compilers)",
            url="https://scientia.test/2526/modules/50007.2/materials",
        ),
        CourseSummary(
            id="COMPM0804",
            title="Student Support and Wellbeing",
            url="https://scientia.test/2526/modules/COMPM0804/materials",
        ),
    ]

    # COMP50001 (EdStem) → 50001 (Scientia) by digit-tail match
    match = _match_course(
        scientia_courses,
        course_id="COMP50001",
        course_title="COMP 50001: Algorithm Design and Analysis",
    )
    assert match is not None
    assert match.id == "50001"

    # COMP50007.1 should pick the .1 lab stream, not .2
    match = _match_course(
        scientia_courses,
        course_id="COMP50007.1",
        course_title="COMP 50007.1: Computing Practical 2 (Lab)",
    )
    assert match is not None
    assert match.id == "50007.1"

    # MSc COMPM0804 ↔ COMPM0804 via exact match
    match = _match_course(
        scientia_courses,
        course_id="COMPM0804",
        course_title="COMP COMPM0804: Student Support and Wellbeing",
    )
    assert match is not None
    assert match.id == "COMPM0804"

    # Title-only fallback when ID can't be derived
    match = _match_course(
        scientia_courses,
        course_id="UNKNOWN",
        course_title="Algorithm Design and Analysis",
    )
    assert match is not None
    assert match.id == "50001"

    # Truly unknown course → None
    assert (
        _match_course(
            scientia_courses,
            course_id="COMP99999",
            course_title="Nonexistent",
        )
        is None
    )
