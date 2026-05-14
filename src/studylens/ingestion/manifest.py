"""Per-course crawl manifest.

`data/raw/{course_id}/_crawl.json` records everything Phase 1 downloaded
to disk: which `source_url` produced which `local_path`, what `kind` it
is, the human title, and when. Phase 2 (indexing) reads only this file
— it never walks the filesystem blindly — so titles, source URLs, and
classification survive into Qdrant payloads.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from studylens.domain.models import ResourceKind

MANIFEST_FILENAME = "_crawl.json"


@dataclass(slots=True)
class ManifestItem:
    source_url: str
    local_path: str  # relative to data/raw/{course_id}/
    kind: ResourceKind
    title: str
    downloaded_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "source_url": self.source_url,
            "local_path": self.local_path,
            "kind": self.kind,
            "title": self.title,
            "downloaded_at": self.downloaded_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ManifestItem:
        return cls(
            source_url=str(data["source_url"]),
            local_path=str(data["local_path"]),
            kind=data["kind"],
            title=str(data["title"]),
            downloaded_at=str(data["downloaded_at"]),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(slots=True)
class CourseManifest:
    course_id: str
    course_title: str
    course_url: str | None
    crawled_at: str
    items: list[ManifestItem] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "course_id": self.course_id,
            "course_title": self.course_title,
            "course_url": self.course_url,
            "crawled_at": self.crawled_at,
            "items": [item.to_json() for item in self.items],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CourseManifest:
        return cls(
            course_id=str(data["course_id"]),
            course_title=str(data["course_title"]),
            course_url=data.get("course_url"),
            crawled_at=str(data["crawled_at"]),
            items=[ManifestItem.from_json(it) for it in data.get("items") or []],
        )


def manifest_path(raw_dir: Path, course_id: str) -> Path:
    return raw_dir / course_id / MANIFEST_FILENAME


def write_manifest(raw_dir: Path, manifest: CourseManifest) -> Path:
    path = manifest_path(raw_dir, manifest.course_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.to_json(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def read_manifest(raw_dir: Path, course_id: str) -> CourseManifest | None:
    path = manifest_path(raw_dir, course_id)
    if not path.exists():
        return None
    return CourseManifest.from_json(json.loads(path.read_text(encoding="utf-8")))


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
