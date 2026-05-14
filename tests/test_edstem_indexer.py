from __future__ import annotations

import asyncio
from pathlib import Path

from qdrant_client import QdrantClient

from studylens.config import Settings
from studylens.domain import Resource
from studylens.ingestion.edstem import EdStemIndexer, posts_to_resources
from studylens.retrieval import HashEmbeddingClient, QdrantVectorStore, RAGService
from studylens.retrieval.qa import TemplateLLM


class FakeCrawler:
    def __init__(self, resources: list[Resource]) -> None:
        self._resources = resources

    async def collect_scope_notes(self, course_id: str, course_title: str) -> list[Resource]:
        return [r for r in self._resources if r.course_id == course_id]


def make_service() -> RAGService:
    return RAGService(
        embeddings=HashEmbeddingClient(dimensions=64),
        vector_store=QdrantVectorStore(
            collection_name="edstem_test",
            dimensions=64,
            client=QdrantClient(":memory:"),
        ),
        llm=TemplateLLM(),
    )


def test_posts_to_resources_only_keeps_scope_relevant_posts() -> None:
    posts = [
        {"title": "Exam scope", "body": "Lecture 9 will not be assessed."},
        {"title": "Social", "body": "Coffee after class."},
        {"title": "Examinable topics", "body": "All graph algorithms are in scope."},
    ]

    resources = posts_to_resources(
        posts,
        course_id="COMP70001",
        course_title="Advanced Algorithms",
    )

    assert [r.title for r in resources] == ["Exam scope", "Examinable topics"]
    assert all(r.kind == "edstem_note" for r in resources)


def test_indexer_skips_when_no_session_attached(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        qdrant_path=tmp_path / "data" / "vector" / "qdrant",
    )
    indexer = EdStemIndexer(settings=settings, rag=make_service(), crawler=None)

    results = asyncio.run(
        indexer.index_course_scope_notes(course_id="COMP70001", course_title="Algos")
    )

    assert len(results) == 1
    assert results[0].status == "skipped"
    assert "STUDYLENS_BROWSER_STORAGE_STATE" in (results[0].error or "")


def test_indexer_chunks_and_stores_each_note(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        qdrant_path=tmp_path / "data" / "vector" / "qdrant",
    )
    service = make_service()
    notes = [
        Resource(
            course_id="COMP70001",
            title="Exam scope",
            kind="edstem_note",
            metadata={
                "source": "edstem",
                "body": "Lecture 9 is not examinable. All else is in scope.",
                "course_title": "Algos",
            },
        ),
        Resource(
            course_id="COMP70001",
            title="Empty body",
            kind="edstem_note",
            metadata={"source": "edstem", "body": "", "course_title": "Algos"},
        ),
    ]
    indexer = EdStemIndexer(
        settings=settings,
        rag=service,
        crawler=FakeCrawler(notes),
    )

    results = asyncio.run(
        indexer.index_course_scope_notes(course_id="COMP70001", course_title="Algos")
    )

    statuses = [r.status for r in results]
    assert statuses == ["indexed", "skipped"]
    assert service.vector_store.count(course_id="COMP70001") >= 1


def test_indexer_reports_empty_when_crawler_finds_nothing(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        qdrant_path=tmp_path / "data" / "vector" / "qdrant",
    )
    indexer = EdStemIndexer(
        settings=settings,
        rag=make_service(),
        crawler=FakeCrawler([]),
    )

    results = asyncio.run(
        indexer.index_course_scope_notes(course_id="COMP70001", course_title="Algos")
    )

    assert len(results) == 1
    assert results[0].status == "skipped"
    assert "No scope-relevant EdStem posts" in (results[0].error or "")
