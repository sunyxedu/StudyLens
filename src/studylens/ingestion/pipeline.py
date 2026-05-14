from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from studylens.domain import DocumentChunk, Resource
from studylens.ingestion.documents import build_chunks, extract_text


@dataclass(slots=True)
class IngestionPipeline:
    """Convert local resources into chunks suitable for embedding."""

    max_chars: int = 1400
    overlap: int = 180

    def chunk_local_resource(self, resource: Resource) -> list[DocumentChunk]:
        if resource.local_path is None:
            return []
        text = extract_text(Path(resource.local_path))
        return build_chunks(resource, text, max_chars=self.max_chars, overlap=self.overlap)

    def chunk_resources(self, resources: list[Resource]) -> list[DocumentChunk]:
        chunks: list[DocumentChunk] = []
        for resource in resources:
            chunks.extend(self.chunk_local_resource(resource))
        return chunks

