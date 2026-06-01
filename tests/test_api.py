from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient

from studylens.api.browser_state import BrowserStateStatus, BrowserStateStep
from studylens.api.main import _cors_settings, create_app
from studylens.config import Settings
from studylens.errors import ConfigurationError
from studylens.ingestion.auto_index import AutoIndexReport
from studylens.ingestion.edstem import EdStemIndexResult
from studylens.ingestion.exams import ExamIndexResult
from studylens.retrieval import HashEmbeddingClient, QdrantVectorStore, RAGService
from studylens.retrieval.qa import TemplateLLM
from studylens.storage import AuthStore


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'studylens.db'}",
        qdrant_path=tmp_path / "data" / "vector" / "qdrant",
        qdrant_collection="api_test",
        allowed_origins=["http://localhost:5173"],
        auth_secret_key="test-secret",
        session_cookie_secure=False,
    )
    embeddings = HashEmbeddingClient(dimensions=64)
    store = QdrantVectorStore(
        collection_name="api_test",
        dimensions=64,
        client=QdrantClient(":memory:"),
    )
    service = RAGService(embeddings=embeddings, vector_store=store, llm=TemplateLLM())
    return TestClient(create_app(settings=settings, rag_service=service))


def register(client: TestClient, username: str = "alice") -> dict:
    response = client.post(
        "/auth/register",
        json={
            "username": username,
            "grade": "Year 3",
            "course": "Computing",
            "password": "correct horse battery staple",
        },
    )
    assert response.status_code == 200
    return response.json()


def login(client: TestClient, username: str = "alice") -> dict:
    response = client.post(
        "/auth/login",
        json={
            "username": username,
            "password": "correct horse battery staple",
        },
    )
    assert response.status_code == 200
    return response.json()


def make_service(collection_name: str = "api_test") -> RAGService:
    return RAGService(
        embeddings=HashEmbeddingClient(dimensions=64),
        vector_store=QdrantVectorStore(
            collection_name=collection_name,
            dimensions=64,
            client=QdrantClient(":memory:"),
        ),
        llm=TemplateLLM(),
    )


class FakeBrowserStateManager:
    def __init__(self, store: AuthStore) -> None:
        self.store = store
        self.started_for: int | None = None
        self.step = BrowserStateStep(
            key="scientia",
            title="Scientia",
            url="https://scientia.test",
            instruction="Log in.",
        )

    async def start(self, user):
        self.started_for = user.id
        return BrowserStateStatus(
            running=True,
            completed=False,
            ready=False,
            total_steps=1,
            step_index=0,
            step=self.step,
        )

    async def advance(self, user):
        self.store.save_browser_state(
            user.id,
            {
                "cookies": [
                    {
                        "name": "session",
                        "value": "cookie",
                        "domain": "scientia.test",
                        "path": "/",
                    }
                ],
                "origins": [],
            },
        )
        return BrowserStateStatus(
            running=False,
            completed=True,
            ready=True,
            total_steps=1,
        )

    async def status(self, user):
        ready = self.store.has_browser_state(user.id)
        return BrowserStateStatus(
            running=False,
            completed=ready,
            ready=ready,
            total_steps=0,
        )

    async def cancel(self, user):
        return await self.status(user)


def test_health_reports_vector_store(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "vector_store": "qdrant"}


def test_register_creates_session_and_reports_browser_state_needed(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    body = register(client)

    assert body["created"] is True
    assert body["user"]["username"] == "alice"
    assert body["needs_browser_state"] is True
    assert client.get("/auth/session").status_code == 200


def test_login_requires_existing_registered_user(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    missing = client.post(
        "/auth/login",
        json={
            "username": "alice",
            "password": "correct horse battery staple",
        },
    )
    register(client)
    logged_in = login(client)
    duplicate = client.post(
        "/auth/register",
        json={
            "username": "alice",
            "grade": "Year 3",
            "course": "Computing",
            "password": "correct horse battery staple",
        },
    )

    assert missing.status_code == 401
    assert logged_in["created"] is False
    assert duplicate.status_code == 400
    assert duplicate.json()["detail"] == "username is already registered"


def test_protected_routes_require_login(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.get("/courses")

    assert response.status_code == 401


def test_browser_state_setup_flow_saves_state_for_session_user(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'studylens.db'}",
        qdrant_path=tmp_path / "data" / "vector" / "qdrant",
        qdrant_collection="api_browser_state_test",
        auth_secret_key="test-secret",
        session_cookie_secure=False,
    )
    auth_store = AuthStore.from_database_url(
        settings.database_url,
        secret_key=settings.auth_secret_key or "",
    )
    manager = FakeBrowserStateManager(auth_store)
    client = TestClient(
        create_app(
            settings=settings,
            rag_service=make_service("api_browser_state_test"),
            auth_store=auth_store,
            browser_state_manager=manager,
        )
    )
    session = register(client)

    started = client.post("/browser-state/start")
    advanced = client.post("/browser-state/advance")
    refreshed_session = client.get("/auth/session")

    assert started.status_code == 200
    assert started.json()["step"]["key"] == "scientia"
    assert manager.started_for == session["user"]["id"]
    assert advanced.status_code == 200
    assert advanced.json()["ready"] is True
    assert refreshed_session.json()["browser_state_ready"] is True
    assert refreshed_session.json()["needs_browser_state"] is False


def test_wildcard_cors_is_rejected_for_non_local_sessions(tmp_path: Path) -> None:
    settings = Settings(
        app_env="production",
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'studylens.db'}",
        qdrant_path=tmp_path / "data" / "vector" / "qdrant",
        qdrant_collection="api_cors_test",
        allowed_origins=["*"],
        auth_secret_key="test-secret",
    )

    with pytest.raises(ConfigurationError, match="ALLOWED_ORIGINS"):
        create_app(settings=settings, rag_service=make_service("api_cors_test"))


def test_local_cors_includes_localhost_and_loopback(tmp_path: Path) -> None:
    settings = Settings(
        app_env="local",
        data_dir=tmp_path / "data",
        allowed_origins=["http://localhost:5173"],
    )

    origins, regex = _cors_settings(settings)

    assert "http://localhost:5173" in origins
    assert "http://127.0.0.1:5173" in origins
    assert regex is None


def test_index_retrieve_and_ask_flow(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    register(client)
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
        async def index_course(
            self,
            *,
            course_id: str,
            course_title: str,
        ):
            assert course_id == "COMP70001"
            assert course_title == "Advanced Algorithms"
            return AutoIndexReport(
                course_id=course_id,
                course_title=course_title,
                source_url="https://scientia.doc.ic.ac.uk/2526/modules/COMP70001",
                discovered_resources=2,
                indexed_resources=1,
                indexed_chunks=4,
            )

    settings = Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'studylens.db'}",
        qdrant_path=tmp_path / "data" / "vector" / "qdrant",
        qdrant_collection="api_auto_index_test",
        auth_secret_key="test-secret",
        session_cookie_secure=False,
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
    register(client)

    response = client.post(
        "/index/course",
        json={
            "course_id": "COMP70001",
            "course_title": "Advanced Algorithms",
        },
    )

    assert response.status_code == 200
    assert response.json()["indexed_chunks"] == 4


def test_ask_with_kinds_filters_retrieval(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    register(client)
    client.post(
        "/chunks",
        json={
            "course_id": "COMP70001",
            "title": "Lecture 1 notes",
            "kind": "material",
            "text": "Dynamic programming uses memoization to avoid repeated subproblems.",
        },
    )
    client.post(
        "/chunks",
        json={
            "course_id": "COMP70001",
            "title": "Lecture 1 transcript",
            "kind": "transcript",
            "text": "Today we will talk about dynamic programming and recurrences.",
        },
    )

    transcript_only = client.post(
        "/ask",
        json={
            "course_id": "COMP70001",
            "question": "What did the lecturer say about DP?",
            "kinds": ["transcript"],
            "include_exercises": False,
        },
    )

    assert transcript_only.status_code == 200
    body = transcript_only.json()
    citation_kinds = {citation["resource_id"] for citation in body["citations"]}
    assert citation_kinds  # has at least one citation
    # All citations are transcript-derived. We don't have kind on Citation, but
    # we can check titles since each chunk's title is distinct.
    citation_titles = {citation["title"] for citation in body["citations"]}
    assert citation_titles == {"Lecture 1 transcript"}


def test_index_exams_endpoint_uses_injected_indexer(tmp_path: Path) -> None:
    class FakeExamsIndexer:
        async def index_course_exams(self, *, course_id: str) -> list[ExamIndexResult]:
            assert course_id == "COMP70001"
            return [
                ExamIndexResult(
                    title="2024 paper",
                    status="indexed",
                    source_url="https://exams.test/2024.pdf",
                    local_path="data/raw/COMP70001/exams/2024.pdf",
                    chunks=2,
                )
            ]

    settings = Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'studylens.db'}",
        qdrant_path=tmp_path / "data" / "vector" / "qdrant",
        qdrant_collection="api_exams_test",
        auth_secret_key="test-secret",
        session_cookie_secure=False,
    )
    service = RAGService(
        embeddings=HashEmbeddingClient(dimensions=64),
        vector_store=QdrantVectorStore(
            collection_name="api_exams_test",
            dimensions=64,
            client=QdrantClient(":memory:"),
        ),
        llm=TemplateLLM(),
    )
    client = TestClient(
        create_app(
            settings=settings,
            rag_service=service,
            exams_indexer=FakeExamsIndexer(),
        )
    )
    register(client)

    response = client.post("/index/exams", json={"course_id": "COMP70001"})

    assert response.status_code == 200
    body = response.json()
    assert body["results"][0]["status"] == "indexed"
    assert body["results"][0]["chunks"] == 2


def test_courses_endpoint_returns_cached_courses(tmp_path: Path) -> None:
    from studylens.storage import CourseStore

    store = CourseStore(tmp_path / "studylens.db")

    settings = Settings(
        data_dir=tmp_path / "data",
        qdrant_path=tmp_path / "data" / "vector" / "qdrant",
        qdrant_collection="api_courses_test",
        database_url=f"sqlite:///{tmp_path / 'unused.db'}",
        auth_secret_key="test-secret",
        session_cookie_secure=False,
    )
    service = RAGService(
        embeddings=HashEmbeddingClient(dimensions=64),
        vector_store=QdrantVectorStore(
            collection_name="api_courses_test",
            dimensions=64,
            client=QdrantClient(":memory:"),
        ),
        llm=TemplateLLM(),
    )
    client = TestClient(
        create_app(settings=settings, rag_service=service, course_store=store)
    )
    body = register(client)
    store.replace_all(
        [
            ("COMP50001", "Algorithm Design and Analysis", "https://edstem.org/c/1"),
            ("COMP50002", "Software Engineering Design", None),
        ],
        user_id=body["user"]["id"],
    )

    response = client.get("/courses")

    assert response.status_code == 200
    body = response.json()
    codes = [c["code"] for c in body["courses"]]
    assert codes == ["COMP50001", "COMP50002"]
    assert body["courses"][0]["edstem_url"] == "https://edstem.org/c/1"
    assert body["courses"][0]["updated_at"]


def test_index_edstem_endpoint_uses_injected_indexer(tmp_path: Path) -> None:
    class FakeEdStemIndexer:
        async def index_course_scope_notes(
            self,
            *,
            course_id: str,
            course_title: str,
        ) -> list[EdStemIndexResult]:
            assert course_id == "COMP70001"
            assert course_title == "Advanced Algorithms"
            return [EdStemIndexResult(title="Exam scope", status="indexed", chunks=1)]

    settings = Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'studylens.db'}",
        qdrant_path=tmp_path / "data" / "vector" / "qdrant",
        qdrant_collection="api_edstem_test",
        auth_secret_key="test-secret",
        session_cookie_secure=False,
    )
    service = RAGService(
        embeddings=HashEmbeddingClient(dimensions=64),
        vector_store=QdrantVectorStore(
            collection_name="api_edstem_test",
            dimensions=64,
            client=QdrantClient(":memory:"),
        ),
        llm=TemplateLLM(),
    )
    client = TestClient(
        create_app(
            settings=settings,
            rag_service=service,
            edstem_indexer=FakeEdStemIndexer(),
        )
    )
    register(client)

    response = client.post(
        "/index/edstem",
        json={"course_id": "COMP70001", "course_title": "Advanced Algorithms"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["results"][0]["status"] == "indexed"


def test_generation_endpoints_return_latex(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    register(client)
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
