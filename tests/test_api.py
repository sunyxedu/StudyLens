from pathlib import Path

from fastapi.testclient import TestClient
from qdrant_client import QdrantClient

from studylens.api.main import create_app
from studylens.config import Settings
from studylens.ingestion.auto_index import AutoIndexReport
from studylens.retrieval import HashEmbeddingClient, QdrantVectorStore, RAGService
from studylens.retrieval.qa import TemplateLLM


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        data_dir=tmp_path / "data",
        qdrant_path=tmp_path / "data" / "vector" / "qdrant",
        qdrant_collection="api_test",
        allowed_origins=["http://localhost:5173"],
    )
    embeddings = HashEmbeddingClient(dimensions=64)
    store = QdrantVectorStore(
        collection_name="api_test",
        dimensions=64,
        client=QdrantClient(":memory:"),
    )
    service = RAGService(embeddings=embeddings, vector_store=store, llm=TemplateLLM())
    return TestClient(create_app(settings=settings, rag_service=service))


def test_health_reports_vector_store(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "vector_store": "qdrant"}


def test_index_retrieve_and_ask_flow(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    index_response = client.post(
        "/chunks",
        json={
            "course_id": "COMP70001",
            "title": "Lecture 1",
            "kind": "material",
            "text": "Dynamic programming uses memoization to avoid repeated subproblems.",
        },
    )

    assert index_response.status_code == 200
    assert index_response.json()["indexed_chunks"] == 1

    retrieve_response = client.post(
        "/retrieve",
        json={"course_id": "COMP70001", "query": "memoization repeated work", "top_k": 1},
    )
    assert retrieve_response.status_code == 200
    assert retrieve_response.json()["results"][0]["chunk"]["title"] == "Lecture 1"

    ask_response = client.post(
        "/ask",
        json={
            "course_id": "COMP70001",
            "question": "Why does DP avoid repeated work?",
            "include_exercises": False,
        },
    )
    assert ask_response.status_code == 200
    body = ask_response.json()
    assert body["citations"]
    assert body["follow_up"] is None


def test_auto_index_endpoint_returns_report(tmp_path: Path) -> None:
    class FakeAutoIndexer:
        def index_course(self, *, course_id: str, course_title: str | None, course_url: str | None):
            assert course_id == "COMP70001"
            assert course_title == "Advanced Algorithms"
            assert course_url == "https://scientia.test/course"
            return AutoIndexReport(
                course_id=course_id,
                course_title=course_title or course_id,
                source_url=course_url,
                discovered_resources=2,
                indexed_resources=1,
                indexed_chunks=4,
            )

    settings = Settings(
        data_dir=tmp_path / "data",
        qdrant_path=tmp_path / "data" / "vector" / "qdrant",
        qdrant_collection="api_auto_index_test",
    )
    embeddings = HashEmbeddingClient(dimensions=64)
    service = RAGService(
        embeddings=embeddings,
        vector_store=QdrantVectorStore(
            collection_name="api_auto_index_test",
            dimensions=64,
            client=QdrantClient(":memory:"),
        ),
        llm=TemplateLLM(),
    )
    client = TestClient(
        create_app(
            settings=settings,
            rag_service=service,
            auto_indexer=FakeAutoIndexer(),
        )
    )

    response = client.post(
        "/index/course",
        json={
            "course_id": "COMP70001",
            "course_title": "Advanced Algorithms",
            "course_url": "https://scientia.test/course",
        },
    )

    assert response.status_code == 200
    assert response.json()["indexed_chunks"] == 4


def test_generation_endpoints_return_latex(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    client.post(
        "/chunks",
        json={
            "course_id": "COMP70001",
            "title": "Past Paper",
            "kind": "past_exam",
            "text": "Question 1 asks about dynamic programming recurrence design.",
        },
    )

    cheatsheet = client.post(
        "/generate/cheatsheet",
        json={"course_id": "COMP70001", "course_title": "Advanced Algorithms"},
    )
    predicted = client.post(
        "/generate/predicted-exam",
        json={
            "course_id": "COMP70001",
            "course_title": "Advanced Algorithms",
            "question_count": 2,
        },
    )

    assert cheatsheet.status_code == 200
    assert cheatsheet.json()["latex"].startswith("\\documentclass")
    assert predicted.status_code == 200
    assert "Predicted Paper" in predicted.json()["latex"]
