from __future__ import annotations

import asyncio
from pathlib import Path

from qdrant_client import QdrantClient

from studylens.config import Settings
from studylens.domain import Resource
from studylens.ingestion.exams import ExamsIndexer
from studylens.retrieval import HashEmbeddingClient, QdrantVectorStore, RAGService
from studylens.retrieval.qa import TemplateLLM


class FakeExamsClient:
    def __init__(self, resources: list[Resource], downloads: dict[str, bytes]) -> None:
        self._resources = resources
        self._downloads = downloads

    async def discover_exam_papers(self, course_id: str) -> list[Resource]:
        return [r for r in self._resources if r.course_id == course_id]

    async def download(self, url: str) -> tuple[bytes, str | None]:
        return self._downloads[url], "text/plain"


def make_service() -> RAGService:
    return RAGService(
        embeddings=HashEmbeddingClient(dimensions=64),
        vector_store=QdrantVectorStore(
            collection_name="exams_test",
            dimensions=64,
            client=QdrantClient(":memory:"),
        ),
        llm=TemplateLLM(),
    )


def test_indexer_skips_when_credentials_missing(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        qdrant_path=tmp_path / "data" / "vector" / "qdrant",
        imperial_username=None,
        imperial_password=None,
    )
    indexer = ExamsIndexer(
        settings=settings,
        rag=make_service(),
        client=FakeExamsClient([], {}),
    )

    results = asyncio.run(indexer.index_course_exams(course_id="COMP70001"))

    assert len(results) == 1
    assert results[0].status == "skipped"
    assert "IMPERIAL_USERNAME" in (results[0].error or "")


def test_indexer_downloads_extracts_and_indexes_each_paper(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        qdrant_path=tmp_path / "data" / "vector" / "qdrant",
        imperial_username="user",
        imperial_password="pw",
    )
    service = make_service()
    resources = [
        Resource(
            course_id="COMP70001",
            title="2024 paper",
            kind="past_exam",
            source_url="https://exams.test/COMP70001/2024.txt",
        ),
        Resource(
            course_id="COMP70001",
            title="2023 paper",
            kind="past_exam",
            source_url="https://exams.test/COMP70001/2023.txt",
        ),
    ]
    downloads = {
        "https://exams.test/COMP70001/2024.txt": (
            b"Question 1: design a dynamic programming recurrence for edit distance."
        ),
        "https://exams.test/COMP70001/2023.txt": (
            b"Question 1: prove the greedy choice for interval scheduling is optimal."
        ),
    }
    indexer = ExamsIndexer(
        settings=settings,
        rag=service,
        client=FakeExamsClient(resources, downloads),
    )

    results = asyncio.run(indexer.index_course_exams(course_id="COMP70001"))

    assert [r.status for r in results] == ["indexed", "indexed"]
    assert sum(r.chunks for r in results) == 2
    assert service.vector_store.count(course_id="COMP70001") == 2
    assert (tmp_path / "data" / "raw" / "COMP70001" / "exams" / "2024-paper.txt").exists()


def test_indexer_reports_empty_when_discovery_finds_nothing(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        qdrant_path=tmp_path / "data" / "vector" / "qdrant",
        imperial_username="user",
        imperial_password="pw",
    )
    indexer = ExamsIndexer(
        settings=settings,
        rag=make_service(),
        client=FakeExamsClient([], {}),
    )

    results = asyncio.run(indexer.index_course_exams(course_id="COMP70001"))

    assert len(results) == 1
    assert results[0].status == "skipped"
    assert "No past exam papers found" in (results[0].error or "")
