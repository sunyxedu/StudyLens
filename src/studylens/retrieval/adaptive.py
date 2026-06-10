"""Adaptive retrieval: grow the search window while a relevance judge approves.

The loop retrieves ``initial_k`` chunks, asks a judge to grade each one, and
doubles the window (5 -> 10 -> 20 -> 40 ...) for as long as strictly more than
half of the newest batch is judged relevant. Only chunks judged relevant are
returned, in their original score order.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol

from studylens.domain import SearchResult
from studylens.retrieval.vector_store import VectorStore


class ChatClient(Protocol):
    def complete(self, *, system: str, prompt: str) -> str:
        ...


class RelevanceJudge(Protocol):
    def judge(self, question: str, results: list[SearchResult]) -> list[bool] | None:
        """Grade each result in order; ``None`` means no verdicts could be produced."""
        ...


_JUDGE_SYSTEM = (
    "You grade whether retrieved course-material excerpts are relevant to a "
    "student's question. Reply with ONLY a JSON array of booleans, one per "
    "excerpt and in the same order: true when the excerpt helps answer the "
    "question, false otherwise."
)


def _parse_verdicts(response: str, *, expected: int) -> list[bool] | None:
    match = re.search(r"\[.*?\]", response, flags=re.DOTALL)
    if match is None:
        return None
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, list) or len(raw) < expected:
        return None
    verdicts: list[bool] = []
    for value in raw[:expected]:
        if isinstance(value, bool):
            verdicts.append(value)
        elif isinstance(value, (int, float)):
            verdicts.append(bool(value))
        elif isinstance(value, str):
            verdicts.append(value.strip().lower() in {"true", "yes", "y", "1", "relevant"})
        else:
            return None
    return verdicts


@dataclass(slots=True)
class LLMRelevanceJudge:
    """Grades a whole batch of retrieved chunks with a single LLM call."""

    llm: ChatClient
    excerpt_chars: int = 700

    def judge(self, question: str, results: list[SearchResult]) -> list[bool] | None:
        if not results:
            return []
        blocks = []
        for index, result in enumerate(results, start=1):
            chunk = result.chunk
            title = chunk.title or chunk.resource_id
            blocks.append(
                f"[{index}] {title} ({chunk.kind})\n{chunk.text[: self.excerpt_chars]}"
            )
        prompt = (
            f"Question: {question}\n\n"
            "Excerpts:\n\n" + "\n\n".join(blocks) + "\n\n"
            f"Reply with a JSON array of exactly {len(results)} booleans."
        )
        try:
            response = self.llm.complete(system=_JUDGE_SYSTEM, prompt=prompt)
        except Exception:
            return None
        return _parse_verdicts(response, expected=len(results))


_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass(slots=True)
class KeywordRelevanceJudge:
    """Offline judge for runs without an LLM: a chunk is relevant when it
    shares enough of the question's keywords."""

    min_overlap: float = 0.2

    def judge(self, question: str, results: list[SearchResult]) -> list[bool] | None:
        keywords = {word for word in _WORD_RE.findall(question.lower()) if len(word) >= 4}
        if not keywords:
            return [True] * len(results)
        verdicts: list[bool] = []
        for result in results:
            text_words = set(_WORD_RE.findall(result.chunk.text.lower()))
            overlap = len(keywords & text_words) / len(keywords)
            verdicts.append(overlap >= self.min_overlap)
        return verdicts


def adaptive_search(
    *,
    vector_store: VectorStore,
    query_vector: list[float],
    question: str,
    judge: RelevanceJudge,
    course_id: str | None = None,
    kinds: set[str] | None = None,
    initial_k: int = 5,
    max_k: int = 80,
) -> list[SearchResult]:
    """Retrieve ``initial_k`` chunks, then keep doubling the window while the
    judge marks strictly more than half of each newly fetched batch relevant.

    Expansion also stops when the store runs out of results or the window
    reaches ``max_k``. When nothing at all is judged relevant the initial
    window is returned unfiltered, so an over-strict or broken judge can never
    leave callers with less context than classic top-k retrieval.
    """
    initial_k = max(1, initial_k)
    max_k = max(initial_k, max_k)
    requested = initial_k
    results = vector_store.search(
        query_vector, course_id=course_id, kinds=kinds, top_k=requested
    )
    relevant: list[SearchResult] = []
    judged_upto = 0
    while judged_upto < len(results):
        batch = results[judged_upto:]
        verdicts = judge.judge(question, batch)
        if verdicts is None:
            # Judge unavailable: keep the whole batch, stop expanding.
            relevant.extend(batch)
            break
        relevant.extend(
            result for result, keep in zip(batch, verdicts, strict=False) if keep
        )
        majority = sum(verdicts) * 2 > len(batch)
        exhausted = len(results) < requested
        if not majority or exhausted or requested >= max_k:
            break
        judged_upto = len(results)
        requested = min(requested * 2, max_k)
        results = vector_store.search(
            query_vector, course_id=course_id, kinds=kinds, top_k=requested
        )
    return relevant if relevant else results[:initial_k]
