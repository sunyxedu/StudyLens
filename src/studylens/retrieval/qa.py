from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from studylens.domain import Answer, Citation, DocumentChunk, SearchResult
from studylens.retrieval.embeddings import EmbeddingClient
from studylens.retrieval.vector_store import VectorStore


class LLMClient(Protocol):
    def complete(self, *, system: str, prompt: str) -> str:
        ...


class TemplateLLM:
    """Small offline LLM substitute for tests and local smoke runs."""

    def complete(self, *, system: str, prompt: str) -> str:
        lines = [line.strip() for line in prompt.splitlines() if line.strip()]
        question = next((line for line in lines if line.lower().startswith("question:")), "Question:")
        context_lines = [line for line in lines if line.startswith("[")]
        if context_lines:
            return (
                f"{question.removeprefix('Question:').strip()}\n\n"
                f"Most relevant source: {context_lines[0][:220]}"
            )
        return "I do not have enough retrieved course context to answer confidently."


class OpenAIChatClient:
    def __init__(self, *, api_key: str, model: str) -> None:
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key)
        self.model = model

    def complete(self, *, system: str, prompt: str) -> str:
        response = self.client.responses.create(
            model=self.model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        return response.output_text


def _format_context(results: list[SearchResult]) -> str:
    blocks = []
    for index, result in enumerate(results, start=1):
        chunk = result.chunk
        title = chunk.title or chunk.resource_id
        blocks.append(
            f"[{index}] {title} ({chunk.kind}, score={result.score:.3f})\n{chunk.text}"
        )
    return "\n\n".join(blocks)


def _citations(results: list[SearchResult]) -> list[Citation]:
    return [
        Citation(
            course_id=result.chunk.course_id,
            resource_id=result.chunk.resource_id,
            title=result.chunk.title,
            source_url=result.chunk.source_url,
            position=result.chunk.position,
            quote=result.chunk.text[:240],
        )
        for result in results
    ]


@dataclass(slots=True)
class RAGService:
    embeddings: EmbeddingClient
    vector_store: VectorStore
    llm: LLMClient

    def index_chunks(self, chunks: list[DocumentChunk]) -> int:
        vectors = self.embeddings.embed([chunk.text for chunk in chunks])
        return self.vector_store.upsert(zip(chunks, vectors, strict=True))

    def retrieve(
        self,
        question: str,
        *,
        course_id: str | None = None,
        kinds: set[str] | None = None,
        top_k: int = 5,
    ) -> list[SearchResult]:
        query_vector = self.embeddings.embed([question])[0]
        return self.vector_store.search(query_vector, course_id=course_id, kinds=kinds, top_k=top_k)

    def answer(
        self,
        question: str,
        *,
        course_id: str | None = None,
        top_k: int = 5,
        include_exercises: bool = True,
    ) -> Answer:
        results = self.retrieve(question, course_id=course_id, top_k=top_k)
        system = (
            "You are StudyLens, a concise course tutor. Answer only from retrieved context. "
            "If the evidence is insufficient, say what is missing. "
            "When exercises or tutorials are present, use them to ground examples."
        )
        exercise_instruction = (
            "After the explanation, add one relevant exercise-style prompt if supported by context."
            if include_exercises
            else "Do not add exercises; explain the concept directly."
        )
        prompt = (
            f"Question: {question}\n\n"
            f"Retrieved context:\n{_format_context(results)}\n\n"
            f"Instruction: {exercise_instruction}"
        )
        answer = self.llm.complete(system=system, prompt=prompt)
        follow_up = "Do you want a worked example from the tutorials or exercises?"
        return Answer(
            question=question,
            answer=answer,
            citations=_citations(results),
            follow_up=follow_up if include_exercises else None,
        )
