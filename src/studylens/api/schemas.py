from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from studylens.domain import Answer, SearchResult
from studylens.domain.models import ResourceKind
from studylens.ingestion.auto_index import AutoIndexReport
from studylens.ingestion.edstem import EdStemIndexResult
from studylens.ingestion.exams import ExamIndexResult


class HealthResponse(BaseModel):
    status: Literal["ok"]
    vector_store: str


class IndexTextRequest(BaseModel):
    course_id: str
    title: str
    text: str
    kind: ResourceKind = "material"
    resource_id: str | None = None
    source_url: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class IndexTextResponse(BaseModel):
    indexed_chunks: int


class AutoIndexCourseRequest(BaseModel):
    course_id: str
    course_title: str | None = None
    course_url: str | None = None


class AutoIndexCourseResponse(AutoIndexReport):
    pass


class IndexExamsRequest(BaseModel):
    course_id: str


class IndexExamsResponse(BaseModel):
    results: list[ExamIndexResult]


class IndexEdStemRequest(BaseModel):
    course_id: str
    course_title: str


class IndexEdStemResponse(BaseModel):
    results: list[EdStemIndexResult]


class AskRequest(BaseModel):
    question: str
    course_id: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)
    include_exercises: bool = True


class RetrieveRequest(BaseModel):
    query: str
    course_id: str | None = None
    kinds: set[ResourceKind] | None = None
    top_k: int = Field(default=5, ge=1, le=50)


class GenerateRequest(BaseModel):
    course_id: str
    course_title: str
    scope_notes: list[str] = Field(default_factory=list)
    top_k: int = Field(default=40, ge=1, le=80)


class PredictedExamRequest(GenerateRequest):
    question_count: int = Field(default=4, ge=1, le=8)


class GeneratedLatexResponse(BaseModel):
    latex: str


class RetrieveResponse(BaseModel):
    results: list[SearchResult]


class AskResponse(Answer):
    pass
