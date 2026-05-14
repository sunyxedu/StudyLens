from pathlib import Path

from qdrant_client import QdrantClient

from studylens.domain import DocumentChunk
from studylens.retrieval import (
    HashEmbeddingClient,
    QdrantVectorStore,
    RAGService,
    SQLiteVectorStore,
)
from studylens.retrieval.qa import TemplateLLM
from studylens.retrieval.vector_store import cosine_similarity


def make_chunk(text: str, *, course_id: str = "COMP70001", kind: str = "material") -> DocumentChunk:
    return DocumentChunk(
        course_id=course_id,
        resource_id=f"{course_id}-{kind}",
        kind=kind,
        text=text,
        position=0,
        title=f"{course_id} {kind}",
    )


def test_hash_embedding_is_deterministic_and_normalized() -> None:
    client = HashEmbeddingClient(dimensions=32)

    first, second = client.embed(["graph search", "graph search"])

    assert first == second
    assert abs(cosine_similarity(first, first) - 1.0) < 1e-9


def test_qdrant_vector_store_search_count_filter_and_clear() -> None:
    embeddings = HashEmbeddingClient(dimensions=64)
    store = QdrantVectorStore(
        collection_name="test_chunks",
        dimensions=64,
        client=QdrantClient(":memory:"),
    )
    chunks = [
        make_chunk("Dynamic programming uses memoization and optimal substructure."),
        make_chunk("Binary search trees support ordered lookup.", course_id="COMP70002"),
        make_chunk("Tutorial: solve a dynamic programming recurrence.", kind="tutorial"),
    ]

    store.upsert(zip(chunks, embeddings.embed([chunk.text for chunk in chunks]), strict=True))

    assert store.count() == 3
    assert store.count(course_id="COMP70001") == 2

    query_vector = embeddings.embed(["dynamic programming recurrence"])[0]
    results = store.search(query_vector, course_id="COMP70001", kinds={"tutorial"}, top_k=3)

    assert len(results) == 1
    assert results[0].chunk.kind == "tutorial"

    store.clear()
    assert store.count() == 0


def test_sqlite_vector_store_remains_available_as_fallback(tmp_path: Path) -> None:
    embeddings = HashEmbeddingClient(dimensions=32)
    store = SQLiteVectorStore(tmp_path / "vectors.sqlite3")
    chunk = make_chunk("Dijkstra computes shortest paths with non-negative weights.")

    assert store.upsert([(chunk, embeddings.embed([chunk.text])[0])]) == 1

    results = store.search(embeddings.embed(["shortest paths"])[0], course_id="COMP70001")

    assert results[0].chunk.id == chunk.id


def test_rag_service_indexes_retrieves_and_answers() -> None:
    embeddings = HashEmbeddingClient(dimensions=64)
    store = QdrantVectorStore(
        collection_name="rag_chunks",
        dimensions=64,
        client=QdrantClient(":memory:"),
    )
    service = RAGService(embeddings=embeddings, vector_store=store, llm=TemplateLLM())
    chunks = [
        make_chunk("Dynamic programming stores solutions to overlapping subproblems."),
        make_chunk("Tutorial exercise: compute Fibonacci with memoization.", kind="tutorial"),
    ]

    assert service.index_chunks(chunks) == 2
    answer = service.answer(
        "How does dynamic programming avoid repeated work?",
        course_id="COMP70001",
    )

    assert answer.citations
    assert answer.follow_up is not None
    assert "dynamic" in answer.answer.lower()
