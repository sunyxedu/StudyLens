from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

ResourceKind = Literal[
    "material",
    "exercise",
    "tutorial",
    "video",
    "transcript",
    "edstem_note",
    "past_exam",
    "generated",
]


def stable_id(*parts: object) -> str:
    payload = "|".join("" if part is None else str(part) for part in parts)
    return sha256(payload.encode("utf-8")).hexdigest()[:24]


class CourseSummary(BaseModel):
    id: str
    title: str
    year: str = "2526"
    url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Resource(BaseModel):
    id: str | None = None
    course_id: str
    title: str
    kind: ResourceKind
    source_url: str | None = None
    local_path: Path | None = None
    mime_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_validator("id", mode="before")
    @classmethod
    def blank_id_to_none(cls, value: object) -> object:
        return value or None

    def model_post_init(self, __context: Any) -> None:
        if self.id is None:
            self.id = stable_id(
                self.course_id,
                self.kind,
                self.source_url,
                self.local_path,
                self.title,
            )


class Course(BaseModel):
    id: str
    title: str
    year: str = "2526"
    source_url: str | None = None
    materials: list[Resource] = Field(default_factory=list)
    exercises: list[Resource] = Field(default_factory=list)
    tutorials: list[Resource] = Field(default_factory=list)
    videos: list[Resource] = Field(default_factory=list)
    notes: list[Resource] = Field(default_factory=list)
    exams: list[Resource] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @computed_field
    @property
    def resources(self) -> list[Resource]:
        return [
            *self.materials,
            *self.exercises,
            *self.tutorials,
            *self.videos,
            *self.notes,
            *self.exams,
        ]


class DocumentChunk(BaseModel):
    id: str | None = None
    course_id: str
    resource_id: str
    kind: ResourceKind
    text: str
    position: int
    title: str | None = None
    source_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if self.id is None:
            self.id = stable_id(self.course_id, self.resource_id, self.position, self.text[:80])


class Citation(BaseModel):
    course_id: str
    resource_id: str
    title: str | None = None
    source_url: str | None = None
    position: int | None = None
    quote: str | None = None
    page: int | None = None
    start_seconds: float | None = None


class SearchResult(BaseModel):
    chunk: DocumentChunk
    score: float


class Answer(BaseModel):
    question: str
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    follow_up: str | None = None
