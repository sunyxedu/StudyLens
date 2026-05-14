from pathlib import Path

from qdrant_client import QdrantClient

from studylens.config import Settings
from studylens.ingestion.auto_index import CourseAutoIndexer, infer_suffix, safe_path_part
from studylens.retrieval import HashEmbeddingClient, QdrantVectorStore, RAGService
from studylens.retrieval.qa import TemplateLLM


class FakeFetcher:
    def __init__(self) -> None:
        self.text = {
            "https://scientia.doc.ic.ac.uk/2526/timeline": """
                <a href="/2526/modules/COMP70001">COMP70001 Advanced Algorithms</a>
            """,
            "https://scientia.doc.ic.ac.uk/2526/modules/COMP70001": """
                <h2>Materials</h2><a href="notes.txt">Lecture notes</a>
                <h2>Exercises</h2><a href="exercise.html">Problem Sheet 1</a>
                <h2>Materials</h2><a href="slides.pptx">Unsupported slides</a>
            """,
        }
        self.downloads = {
            "https://scientia.doc.ic.ac.uk/2526/modules/notes.txt": (
                b"Dynamic programming stores overlapping subproblems.",
                "text/plain",
            ),
            "https://scientia.doc.ic.ac.uk/2526/modules/exercise.html": (
                b"<html><body><p>Tutorial exercise: write a recurrence.</p></body></html>",
                "text/html",
            ),
            "https://scientia.doc.ic.ac.uk/2526/modules/slides.pptx": (
                b"not parseable",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ),
        }

    def get_text(self, url: str) -> str:
        return self.text[url]

    def download(self, url: str) -> tuple[bytes, str | None]:
        return self.downloads[url]


def make_service(tmp_path: Path) -> RAGService:
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
    service = make_service(tmp_path)
    indexer = CourseAutoIndexer(settings=settings, rag=service, fetcher=FakeFetcher())

    report = indexer.index_course(course_id="COMP70001")

    assert report.course_title == "COMP70001 Advanced Algorithms"
    assert report.discovered_resources == 3
    assert report.indexed_resources == 2
    assert report.indexed_chunks == 2
    assert {item.status for item in report.items} == {"indexed", "skipped"}
    assert service.vector_store.count(course_id="COMP70001") == 2
    assert (tmp_path / "data" / "raw" / "COMP70001" / "material" / "Lecture-notes.txt").exists()


def test_auto_index_helpers_infer_suffix_and_safe_path_names() -> None:
    assert infer_suffix("https://example.test/file", "text/html; charset=utf-8") == ".html"
    assert infer_suffix("https://example.test/file.pdf", "text/plain") == ".pdf"
    assert safe_path_part(" Week 1: DP / graphs ") == "Week-1-DP-graphs"

