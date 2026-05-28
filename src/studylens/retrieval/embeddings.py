from __future__ import annotations

import math
import re
from collections.abc import Sequence
from hashlib import blake2b
from typing import Protocol

from studylens.errors import ConfigurationError

TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


class EmbeddingClient(Protocol):
    dimensions: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        ...


class HashEmbeddingClient:
    """Deterministic local embedding useful for tests and offline development."""

    def __init__(self, dimensions: int = 256) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self.dimensions = dimensions

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in TOKEN_RE.findall(text.lower()):
            digest = blake2b(token.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[bucket] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]


class OpenAIEmbeddingClient:
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str = "text-embedding-3-small",
        dimensions: int = 1536,
    ) -> None:
        if not api_key:
            raise ConfigurationError("OpenAI API key is required for OpenAI embeddings")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ConfigurationError("Install openai to use OpenAI embeddings") from exc
        self.client = OpenAI(api_key=api_key)
        self.model = model
        self.dimensions = dimensions

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        kwargs = (
            {"dimensions": self.dimensions}
            if self.model.startswith("text-embedding-3")
            else {}
        )
        response = self.client.embeddings.create(model=self.model, input=list(texts), **kwargs)
        return [list(item.embedding) for item in response.data]
