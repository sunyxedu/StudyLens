from __future__ import annotations

import json
import math
import sqlite3
import warnings
from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import blake2b
from pathlib import Path
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient
from qdrant_client.http import models

from studylens.domain import DocumentChunk, SearchResult


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("Vectors must have the same dimensions")
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


class VectorStore(Protocol):
    def upsert(self, items: Iterable[tuple[DocumentChunk, list[float]]]) -> int:
        ...

    def count(self, course_id: str | None = None) -> int:
        ...

    def clear(self) -> None:
        ...

    def dedupe_texts(self) -> int:
        """Drop chunks whose (course_id, kind, text) duplicates a kept chunk.

        Returns the number of removed chunks. Historic id-scheme changes left
        the same content stored under several resource ids; this collapses
        those copies in place.
        """
        ...

    def search(
        self,
        query_vector: list[float],
        *,
        course_id: str | None = None,
        kinds: set[str] | None = None,
        top_k: int = 5,
    ) -> list[SearchResult]:
        ...


def _dedupe_key(course_id: object, kind: object, text: object) -> bytes:
    normalized = " ".join(str(text or "").split())
    digest = blake2b(
        f"{course_id}|{kind}|{normalized}".encode(), digest_size=16
    )
    return digest.digest()


def _payload_from_chunk(chunk: DocumentChunk) -> dict[str, object]:
    return {
        "chunk_id": chunk.id,
        "course_id": chunk.course_id,
        "resource_id": chunk.resource_id,
        "kind": chunk.kind,
        "title": chunk.title,
        "source_url": chunk.source_url,
        "position": chunk.position,
        "text": chunk.text,
        "metadata": chunk.metadata,
    }


def _chunk_from_payload(payload: dict[str, object]) -> DocumentChunk:
    return DocumentChunk(
        id=str(payload["chunk_id"]),
        course_id=str(payload["course_id"]),
        resource_id=str(payload["resource_id"]),
        kind=str(payload["kind"]),
        title=payload.get("title") if isinstance(payload.get("title"), str) else None,
        source_url=(
            payload.get("source_url") if isinstance(payload.get("source_url"), str) else None
        ),
        position=int(payload["position"]),
        text=str(payload["text"]),
        metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
    )


def _qdrant_point_id(chunk_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"studylens:{chunk_id}"))


def _qdrant_filter(
    course_id: str | None = None,
    kinds: set[str] | None = None,
) -> models.Filter | None:
    conditions: list[models.FieldCondition] = []
    if course_id:
        conditions.append(
            models.FieldCondition(key="course_id", match=models.MatchValue(value=course_id))
        )
    if kinds:
        conditions.append(
            models.FieldCondition(key="kind", match=models.MatchAny(any=sorted(kinds)))
        )
    return models.Filter(must=conditions) if conditions else None


def _qdrant_vector_size(vectors_config: object) -> int | None:
    if isinstance(vectors_config, models.VectorParams):
        return int(vectors_config.size)
    if isinstance(vectors_config, dict):
        unnamed_vector = vectors_config.get("") or vectors_config.get("default")
        return (
            int(unnamed_vector.size)
            if isinstance(unnamed_vector, models.VectorParams)
            else None
        )
    size = getattr(vectors_config, "size", None)
    return int(size) if isinstance(size, int) else None


@dataclass(slots=True)
class QdrantVectorStore:
    collection_name: str
    dimensions: int
    path: Path | None = None
    url: str | None = None
    api_key: str | None = None
    client: QdrantClient | None = None

    def __post_init__(self) -> None:
        if self.dimensions <= 0:
            raise ValueError("dimensions must be positive")
        if self.client is None:
            if self.url:
                self.client = QdrantClient(url=self.url, api_key=self.api_key)
            else:
                qdrant_path = self.path or Path("data/vector/qdrant")
                qdrant_path.mkdir(parents=True, exist_ok=True)
                self.client = QdrantClient(path=str(qdrant_path))
        self.initialize()

    def initialize(self) -> None:
        assert self.client is not None
        if self.client.collection_exists(self.collection_name):
            collection = self.client.get_collection(self.collection_name)
            existing_dimensions = _qdrant_vector_size(collection.config.params.vectors)
            if existing_dimensions == self.dimensions:
                return
            warnings.warn(
                "Recreating Qdrant collection "
                f"{self.collection_name!r}: existing vector size "
                f"{existing_dimensions or 'unknown'} does not match configured size "
                f"{self.dimensions}.",
                RuntimeWarning,
                stacklevel=2,
            )
            self.client.delete_collection(self.collection_name)
        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=models.VectorParams(
                size=self.dimensions,
                distance=models.Distance.COSINE,
            ),
        )
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Payload indexes have no effect.*")
            self.client.create_payload_index(
                collection_name=self.collection_name,
                field_name="course_id",
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
            self.client.create_payload_index(
                collection_name=self.collection_name,
                field_name="kind",
                field_schema=models.PayloadSchemaType.KEYWORD,
            )

    def upsert(self, items: Iterable[tuple[DocumentChunk, list[float]]]) -> int:
        points = [
            models.PointStruct(
                id=_qdrant_point_id(chunk.id or ""),
                vector=vector,
                payload=_payload_from_chunk(chunk),
            )
            for chunk, vector in items
        ]
        if not points:
            return 0
        assert self.client is not None
        self.client.upsert(collection_name=self.collection_name, points=points)
        return len(points)

    def count(self, course_id: str | None = None) -> int:
        assert self.client is not None
        return int(
            self.client.count(
                collection_name=self.collection_name,
                count_filter=_qdrant_filter(course_id=course_id),
                exact=True,
            ).count
        )

    def clear(self) -> None:
        assert self.client is not None
        if self.client.collection_exists(self.collection_name):
            self.client.delete_collection(self.collection_name)
        self.initialize()

    def dedupe_texts(self) -> int:
        assert self.client is not None
        seen: set[bytes] = set()
        doomed: list[models.ExtendedPointId] = []
        offset: models.ExtendedPointId | None = None
        while True:
            points, offset = self.client.scroll(
                collection_name=self.collection_name,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in points:
                payload = point.payload or {}
                key = _dedupe_key(
                    payload.get("course_id"), payload.get("kind"), payload.get("text")
                )
                if key in seen:
                    doomed.append(point.id)
                else:
                    seen.add(key)
            if offset is None:
                break
        for start in range(0, len(doomed), 512):
            self.client.delete(
                collection_name=self.collection_name,
                points_selector=models.PointIdsList(points=doomed[start : start + 512]),
            )
        return len(doomed)

    def search(
        self,
        query_vector: list[float],
        *,
        course_id: str | None = None,
        kinds: set[str] | None = None,
        top_k: int = 5,
    ) -> list[SearchResult]:
        if top_k <= 0:
            return []
        assert self.client is not None
        response = self.client.query_points(
            collection_name=self.collection_name,
            query=query_vector,
            query_filter=_qdrant_filter(course_id=course_id, kinds=kinds),
            limit=top_k,
            with_payload=True,
        )
        return [
            SearchResult(chunk=_chunk_from_payload(point.payload or {}), score=float(point.score))
            for point in response.points
        ]


@dataclass(slots=True)
class SQLiteVectorStore:
    path: Path

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY,
                    course_id TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    title TEXT,
                    source_url TEXT,
                    position INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    vector TEXT NOT NULL
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_chunks_course ON chunks(course_id)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_chunks_kind ON chunks(kind)")

    def upsert(self, items: Iterable[tuple[DocumentChunk, list[float]]]) -> int:
        rows = [
            (
                chunk.id,
                chunk.course_id,
                chunk.resource_id,
                chunk.kind,
                chunk.title,
                chunk.source_url,
                chunk.position,
                chunk.text,
                json.dumps(chunk.metadata, sort_keys=True),
                json.dumps(vector),
            )
            for chunk, vector in items
        ]
        if not rows:
            return 0
        with self.connect() as connection:
            connection.executemany(
                """
                INSERT INTO chunks (
                    id, course_id, resource_id, kind, title,
                    source_url, position, text, metadata, vector
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    course_id=excluded.course_id,
                    resource_id=excluded.resource_id,
                    kind=excluded.kind,
                    title=excluded.title,
                    source_url=excluded.source_url,
                    position=excluded.position,
                    text=excluded.text,
                    metadata=excluded.metadata,
                    vector=excluded.vector
                """,
                rows,
            )
        return len(rows)

    def count(self, course_id: str | None = None) -> int:
        with self.connect() as connection:
            if course_id:
                row = connection.execute(
                    "SELECT COUNT(*) AS count FROM chunks WHERE course_id = ?", (course_id,)
                ).fetchone()
            else:
                row = connection.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()
        return int(row["count"])

    def clear(self) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM chunks")

    def dedupe_texts(self) -> int:
        seen: set[bytes] = set()
        doomed: list[str] = []
        with self.connect() as connection:
            for row in connection.execute(
                "SELECT id, course_id, kind, text FROM chunks ORDER BY id"
            ):
                key = _dedupe_key(row["course_id"], row["kind"], row["text"])
                if key in seen:
                    doomed.append(row["id"])
                else:
                    seen.add(key)
            for start in range(0, len(doomed), 512):
                batch = doomed[start : start + 512]
                placeholders = ",".join("?" for _ in batch)
                connection.execute(
                    f"DELETE FROM chunks WHERE id IN ({placeholders})", batch
                )
        return len(doomed)

    def search(
        self,
        query_vector: list[float],
        *,
        course_id: str | None = None,
        kinds: set[str] | None = None,
        top_k: int = 5,
    ) -> list[SearchResult]:
        if top_k <= 0:
            return []
        sql = "SELECT * FROM chunks"
        params: list[str] = []
        clauses: list[str] = []
        if course_id:
            clauses.append("course_id = ?")
            params.append(course_id)
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            clauses.append(f"kind IN ({placeholders})")
            params.extend(sorted(kinds))
        if clauses:
            sql = f"{sql} WHERE {' AND '.join(clauses)}"

        scored: list[SearchResult] = []
        with self.connect() as connection:
            for row in connection.execute(sql, params):
                vector = json.loads(row["vector"])
                score = cosine_similarity(query_vector, vector)
                chunk = DocumentChunk(
                    id=row["id"],
                    course_id=row["course_id"],
                    resource_id=row["resource_id"],
                    kind=row["kind"],
                    title=row["title"],
                    source_url=row["source_url"],
                    position=row["position"],
                    text=row["text"],
                    metadata=json.loads(row["metadata"]),
                )
                scored.append(SearchResult(chunk=chunk, score=score))
        scored.sort(key=lambda result: result.score, reverse=True)
        return scored[:top_k]
