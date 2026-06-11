from pathlib import Path

from qdrant_client import QdrantClient

from studylens.bootstrap import build_rag_service
from studylens.config import Settings
from studylens.domain import DocumentChunk, SearchResult
from studylens.retrieval import (
    HashEmbeddingClient,
    KeywordRelevanceJudge,
    LLMRelevanceJudge,
    QdrantVectorStore,
    RAGService,
    adaptive_search,
)
from studylens.retrieval.qa import TemplateLLM


def make_result(
    text: str, *, score: float, kind: str = "material", position: int = 0
) -> SearchResult:
    chunk = DocumentChunk(
        course_id="COMP70001",
        resource_id=f"COMP70001-{kind}",
        kind=kind,
        text=text,
        position=position,
        title=f"COMP70001 {kind}",
    )
    return SearchResult(chunk=chunk, score=score)


def ranked_results(texts: list[str]) -> list[SearchResult]:
    # Distinct positions give every chunk a distinct stable id, matching real
    # stores where identical ids collapse into a single row at upsert time.
    return [
        make_result(text, score=1.0 - index * 0.01, position=index)
        for index, text in enumerate(texts)
    ]


class FakeVectorStore:
    """Score-ordered in-memory store that records every requested top_k."""

    def __init__(self, results: list[SearchResult]) -> None:
        self.results = results
        self.requested: list[int] = []

    def search(
        self,
        query_vector: list[float],
        *,
        course_id: str | None = None,
        kinds: set[str] | None = None,
        top_k: int = 5,
    ) -> list[SearchResult]:
        self.requested.append(top_k)
        return self.results[:top_k]


class ReshufflingVectorStore:
    """Returns a different ranking on every call, like an approximate index."""

    def __init__(self, rounds: list[list[SearchResult]]) -> None:
        self.rounds = rounds
        self.requested: list[int] = []

    def search(
        self,
        query_vector: list[float],
        *,
        course_id: str | None = None,
        kinds: set[str] | None = None,
        top_k: int = 5,
    ) -> list[SearchResult]:
        self.requested.append(top_k)
        round_index = min(len(self.requested), len(self.rounds)) - 1
        return self.rounds[round_index][:top_k]


class MarkerJudge:
    """Marks a result relevant when its text contains the word 'relevant'."""

    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def judge(self, question: str, results: list[SearchResult]) -> list[bool] | None:
        self.batch_sizes.append(len(results))
        return ["relevant" in result.chunk.text for result in results]


class BrokenJudge:
    def judge(self, question: str, results: list[SearchResult]) -> list[bool] | None:
        return None


class FakeLLM:
    def __init__(self, response: str | Exception) -> None:
        self.response = response
        self.prompts: list[str] = []

    def complete(self, *, system: str, prompt: str) -> str:
        self.prompts.append(prompt)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_adaptive_search_expands_then_stops_on_minority() -> None:
    texts = ["relevant"] * 5 + ["relevant", "noise", "noise", "noise", "noise"] + ["relevant"] * 10
    store = FakeVectorStore(ranked_results(texts))
    judge = MarkerJudge()

    results = adaptive_search(
        vector_store=store,
        query_vector=[0.0],
        question="q",
        judge=judge,
        initial_k=5,
        max_k=80,
    )

    assert store.requested == [5, 10]
    assert judge.batch_sizes == [5, 5]
    assert len(results) == 6
    assert all("relevant" in result.chunk.text for result in results)


def test_adaptive_search_doubles_until_max_k() -> None:
    store = FakeVectorStore(ranked_results(["relevant"] * 60))
    judge = MarkerJudge()

    results = adaptive_search(
        vector_store=store,
        query_vector=[0.0],
        question="q",
        judge=judge,
        initial_k=5,
        max_k=40,
    )

    assert store.requested == [5, 10, 20, 40]
    assert judge.batch_sizes == [5, 5, 10, 20]
    assert len(results) == 40


def test_adaptive_search_stops_when_store_is_exhausted() -> None:
    store = FakeVectorStore(ranked_results(["relevant"] * 8))
    judge = MarkerJudge()

    results = adaptive_search(
        vector_store=store,
        query_vector=[0.0],
        question="q",
        judge=judge,
        initial_k=5,
        max_k=80,
    )

    assert store.requested == [5, 10]
    assert judge.batch_sizes == [5, 3]
    assert len(results) == 8


def test_adaptive_search_falls_back_to_initial_window_when_nothing_relevant() -> None:
    store = FakeVectorStore(ranked_results(["noise"] * 12))
    judge = MarkerJudge()

    results = adaptive_search(
        vector_store=store,
        query_vector=[0.0],
        question="q",
        judge=judge,
        initial_k=5,
        max_k=80,
    )

    assert store.requested == [5]
    assert len(results) == 5


def test_adaptive_search_keeps_batch_and_stops_when_judge_fails() -> None:
    store = FakeVectorStore(ranked_results(["noise"] * 12))

    results = adaptive_search(
        vector_store=store,
        query_vector=[0.0],
        question="q",
        judge=BrokenJudge(),
        initial_k=5,
        max_k=80,
    )

    assert store.requested == [5]
    assert len(results) == 5


def test_adaptive_search_tolerates_reshuffled_rankings() -> None:
    """A reshuffling store (approximate index) must not cause duplicate or
    skipped judgements when the window grows."""
    first = [
        make_result(f"relevant {letter}", score=0.9 - index * 0.01, position=index)
        for index, letter in enumerate("ABCDE")
    ]
    newcomer = make_result("relevant N", score=0.95, position=99)
    tail = [
        make_result(f"relevant {letter}", score=0.8 - index * 0.01, position=10 + index)
        for index, letter in enumerate("FGHI")
    ]
    store = ReshufflingVectorStore(rounds=[first, [newcomer, *first, *tail]])
    judge = MarkerJudge()

    results = adaptive_search(
        vector_store=store,
        query_vector=[0.0],
        question="q",
        judge=judge,
        initial_k=5,
        max_k=10,
    )

    ids = [result.chunk.id for result in results]
    assert len(ids) == len(set(ids)) == 10
    assert judge.batch_sizes == [5, 5]
    assert results[0].chunk.id == newcomer.chunk.id
    assert [result.score for result in results] == sorted(
        (result.score for result in results), reverse=True
    )


def test_llm_relevance_judge_parses_mixed_verdicts() -> None:
    llm = FakeLLM('Here you go: [true, false, "yes", 0, 1]')
    judge = LLMRelevanceJudge(llm=llm)

    verdicts = judge.judge("q", ranked_results(["a", "b", "c", "d", "e"]))

    assert verdicts == [True, False, True, False, True]
    assert "[1] COMP70001 material" in llm.prompts[0]


def test_llm_relevance_judge_ignores_echoed_excerpt_markers() -> None:
    llm = FakeLLM("Excerpt [1] helps, [2] does not. Verdict: [true, false]")
    judge = LLMRelevanceJudge(llm=llm)

    assert judge.judge("q", ranked_results(["a", "b"])) == [True, False]


def test_llm_relevance_judge_prefers_boolean_array_for_single_excerpt() -> None:
    llm = FakeLLM("[1] is unrelated to the question: [false]")
    judge = LLMRelevanceJudge(llm=llm)

    assert judge.judge("q", ranked_results(["a"])) == [False]


def test_llm_relevance_judge_returns_none_on_garbage_or_errors() -> None:
    assert LLMRelevanceJudge(llm=FakeLLM("no json here")).judge("q", ranked_results(["a"])) is None
    assert LLMRelevanceJudge(llm=FakeLLM("[true]")).judge("q", ranked_results(["a", "b"])) is None
    assert (
        LLMRelevanceJudge(llm=FakeLLM(RuntimeError("boom"))).judge("q", ranked_results(["a"]))
        is None
    )


def test_keyword_relevance_judge_uses_question_overlap() -> None:
    judge = KeywordRelevanceJudge()
    results = ranked_results(
        [
            "Dynamic programming stores solutions to overlapping subproblems.",
            "Binary search trees support ordered lookup.",
        ]
    )

    verdicts = judge.judge("How does dynamic programming avoid repeated work?", results)

    assert verdicts == [True, False]


def test_rag_service_uses_adaptive_retrieval_when_judge_is_set() -> None:
    embeddings = HashEmbeddingClient(dimensions=64)
    store = QdrantVectorStore(
        collection_name="adaptive_rag_chunks",
        dimensions=64,
        client=QdrantClient(":memory:"),
    )
    service = RAGService(
        embeddings=embeddings,
        vector_store=store,
        llm=TemplateLLM(),
        judge=KeywordRelevanceJudge(),
    )
    chunks = [
        DocumentChunk(
            course_id="COMP70001",
            resource_id="COMP70001-material",
            kind="material",
            text="Dynamic programming stores solutions to overlapping subproblems.",
            position=0,
            title="Lecture 1",
        ),
        DocumentChunk(
            course_id="COMP70001",
            resource_id="COMP70001-material",
            kind="material",
            text="Completely unrelated administrivia about room bookings.",
            position=1,
            title="Admin",
        ),
    ]
    assert service.index_chunks(chunks) == 2

    results = service.retrieve("How does dynamic programming work?", course_id="COMP70001")

    assert [result.chunk.title for result in results] == ["Lecture 1"]

    answer = service.answer("How does dynamic programming work?", course_id="COMP70001")
    assert len(answer.citations) == 1


def test_build_rag_service_wires_judge_from_settings(tmp_path: Path) -> None:
    base = {
        "data_dir": tmp_path,
        "database_url": f"sqlite:///{tmp_path / 'studylens.db'}",
        "vector_store": "sqlite",
        "vector_db_path": tmp_path / "vectors.sqlite3",
        "openai_api_key": None,
    }

    adaptive = build_rag_service(Settings(**base))
    assert isinstance(adaptive.judge, KeywordRelevanceJudge)
    assert adaptive.max_k == 80

    classic = build_rag_service(Settings(**base, adaptive_retrieval=False))
    assert classic.judge is None
