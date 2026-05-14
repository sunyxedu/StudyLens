from qdrant_client import QdrantClient

from studylens.domain import DocumentChunk
from studylens.generation import CheatsheetGenerator, PredictedExamGenerator
from studylens.retrieval import HashEmbeddingClient, QdrantVectorStore, RAGService


class RecordingLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, *, system: str, prompt: str) -> str:
        self.prompts.append(prompt)
        return "\\subsection*{Core}\\begin{itemize}\\item Memoization avoids repeated work.\\end{itemize}"


def service_with_chunks(collection_name: str) -> tuple[RAGService, RecordingLLM]:
    embeddings = HashEmbeddingClient(dimensions=64)
    store = QdrantVectorStore(
        collection_name=collection_name,
        dimensions=64,
        client=QdrantClient(":memory:"),
    )
    llm = RecordingLLM()
    service = RAGService(embeddings=embeddings, vector_store=store, llm=llm)
    chunks = [
        DocumentChunk(
            course_id="COMP70001",
            resource_id="notes",
            kind="material",
            title="Lecture notes",
            text="Dynamic programming, recurrence relations, optimal substructure.",
            position=0,
        ),
        DocumentChunk(
            course_id="COMP70001",
            resource_id="exam-2024",
            kind="past_exam",
            title="2024 Paper",
            text="Question 1 asks for a recurrence and complexity analysis.",
            position=0,
        ),
    ]
    service.index_chunks(chunks)
    return service, llm


def test_cheatsheet_generator_wraps_latex_and_includes_scope_notes() -> None:
    service, llm = service_with_chunks("cheatsheet_test")
    generator = CheatsheetGenerator(rag=service, llm=llm)

    latex = generator.generate(
        course_id="COMP70001",
        course_title="Advanced Algorithms",
        scope_notes=["Network flow is not assessed."],
    )

    assert latex.startswith("\\documentclass")
    assert "\\begin{multicols*}{2}" in latex
    assert "Advanced Algorithms Cheatsheet" in latex
    assert "Network flow is not assessed" in llm.prompts[0]


def test_predicted_exam_generator_mentions_question_count_and_scope() -> None:
    service, llm = service_with_chunks("exam_test")
    generator = PredictedExamGenerator(rag=service, llm=llm)

    latex = generator.generate(
        course_id="COMP70001",
        course_title="Advanced Algorithms",
        scope_notes=["Only weeks 1-8 are examinable."],
        question_count=3,
    )

    assert "Predicted Paper" in latex
    assert "Produce 3 substantial questions" in llm.prompts[0]
    assert "Only weeks 1-8" in llm.prompts[0]

