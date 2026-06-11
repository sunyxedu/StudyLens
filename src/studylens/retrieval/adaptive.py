"""Adaptive retrieval: grow the search window while a relevance judge approves.

The loop retrieves ``initial_k`` chunks, asks a judge to grade each one, and
doubles the window (5 -> 10 -> 20 -> 40 ...) for as long as strictly more than
half of the newest batch is judged relevant. Chunks with identical text are
collapsed before judging so duplicated index content cannot dilute the vote.
Only chunks judged relevant are returned, in their original score order.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Protocol

from studylens.domain import SearchResult
from studylens.retrieval.vector_store import VectorStore

logger = logging.getLogger(__name__)


def text_key(result: SearchResult) -> str:
    """Whitespace-normalized chunk text, used to spot duplicated content."""
    return " ".join(result.chunk.text.split())


def dedupe_results(results: list[SearchResult]) -> list[SearchResult]:
    """Keep only the highest-ranked result for each distinct chunk text."""
    seen: set[str] = set()
    unique: list[SearchResult] = []
    for result in results:
        key = text_key(result)
        if key in seen:
            continue
        seen.add(key)
        unique.append(result)
    return unique


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


def _coerce_verdicts(values: list[object]) -> list[bool] | None:
    verdicts: list[bool] = []
    for value in values:
        if isinstance(value, bool):
            verdicts.append(value)
        elif isinstance(value, (int, float)):
            verdicts.append(bool(value))
        elif isinstance(value, str):
            verdicts.append(value.strip().lower() in {"true", "yes", "y", "1", "relevant"})
        else:
            return None
    return verdicts


def _parse_verdicts(response: str, *, expected: int) -> list[bool] | None:
    # The prompt labels excerpts "[1]", "[2]", ... and models often echo those
    # markers before the verdict array, so taking the first bracketed span is
    # wrong. Scan every flat array, prefer ones that are purely JSON booleans,
    # and among those take the last — the instructed reply comes after any
    # echoed reasoning.
    candidates: list[list[object]] = []
    for match in re.finditer(r"\[[^\[\]]*\]", response):
        try:
            raw = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(raw, list) and len(raw) >= expected:
            candidates.append(raw[:expected])
    for values in reversed(candidates):
        if all(isinstance(value, bool) for value in values):
            return _coerce_verdicts(values)
    for values in reversed(candidates):
        verdicts = _coerce_verdicts(values)
        if verdicts is not None:
            return verdicts
    return None


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
    # Each expansion re-queries the store with a larger window. Approximate
    # indexes (e.g. server-side Qdrant HNSW) and concurrent upserts may
    # reshuffle the ranking between rounds, so the already-judged prefix is
    # tracked by chunk id rather than by position: no chunk is judged twice or
    # duplicated, and chunks that newly enter the window are always judged.
    # Identical texts are also collapsed (the store may hold the same content
    # under several resource ids), so duplicates neither dilute the majority
    # vote nor repeat in the returned context.
    judged_ids: set[str | None] = set()
    judged_texts: set[str] = set()
    while True:
        batch: list[SearchResult] = []
        batch_texts: set[str] = set()
        for result in results:
            key = text_key(result)
            if (
                result.chunk.id in judged_ids
                or key in judged_texts
                or key in batch_texts
            ):
                continue
            batch.append(result)
            batch_texts.add(key)
        if not batch:
            break
        verdicts = judge.judge(question, batch)
        if verdicts is None:
            # Judge unavailable: keep the whole batch, stop expanding.
            logger.warning(
                "adaptive_search: judge unavailable, keeping %d unjudged chunks",
                len(batch),
            )
            relevant.extend(batch)
            break
        judged_ids.update(result.chunk.id for result in batch)
        judged_texts.update(batch_texts)
        batch_relevant = sum(verdicts)
        relevant.extend(
            result for result, keep in zip(batch, verdicts, strict=False) if keep
        )
        majority = batch_relevant * 2 > len(batch)
        exhausted = len(results) < requested
        expand = majority and not exhausted and requested < max_k
        logger.info(
            "adaptive_search: window=%d fetched=%d distinct_batch=%d "
            "batch_relevant=%d total_relevant=%d -> %s",
            requested,
            len(results),
            len(batch),
            batch_relevant,
            len(relevant),
            "expand" if expand else "stop",
        )
        if not expand:
            break
        requested = min(requested * 2, max_k)
        results = vector_store.search(
            query_vector, course_id=course_id, kinds=kinds, top_k=requested
        )
    relevant.sort(key=lambda result: result.score, reverse=True)
    return relevant if relevant else dedupe_results(results)[:initial_k]
