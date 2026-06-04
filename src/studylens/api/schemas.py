from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from studylens.domain import Answer, Citation, SearchResult
from studylens.domain.models import ResourceKind
from studylens.ingestion.auto_index import AutoIndexReport
from studylens.ingestion.edstem import EdStemIndexResult
from studylens.ingestion.exams import ExamIndexResult


class HealthResponse(BaseModel):
    status: Literal["ok"]
    vector_store: str


class RegisterRequest(BaseModel):
    username: str
    grade: str
    course: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class AuthUser(BaseModel):
    id: int
    username: str
    grade: str
    course: str
    is_admin: bool = False


class AuthSessionResponse(BaseModel):
    user: AuthUser
    created: bool = False
    browser_state_ready: bool
    needs_browser_state: bool


class BrowserStateStepResponse(BaseModel):
    key: str
    title: str
    url: str
    instruction: str


class BrowserStateStatusResponse(BaseModel):
    running: bool
    completed: bool
    ready: bool
    total_steps: int
    step_index: int | None = None
    step: BrowserStateStepResponse | None = None
    error: str | None = None


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
    course_title: str


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


class DiscoverCoursesCourse(BaseModel):
    code: str
    title: str
    edstem_url: str | None = None
    updated_at: str | None = None
    indexed_at: str | None = None


class DiscoverCoursesResponse(BaseModel):
    courses: list[DiscoverCoursesCourse]
    dropped_titles: list[str] = Field(default_factory=list)
    num_turns: int = 0
    total_cost_usd: float = 0.0
    error: str | None = None


class CoursesListResponse(BaseModel):
    courses: list[DiscoverCoursesCourse]


class AskRequest(BaseModel):
    question: str
    course_id: str | None = None
    kinds: set[ResourceKind] | None = None
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


class ForumCategoryCreateRequest(BaseModel):
    name: str
    description: str
    color: str | None = None


class ForumBoardCreateRequest(BaseModel):
    category_id: int
    name: str
    description: str


class ForumThreadCreateRequest(BaseModel):
    board_id: int
    title: str
    body: str
    course_id: str | None = None
    anonymous: bool = False


class ForumReplyCreateRequest(BaseModel):
    body: str
    anonymous: bool = False


class ForumCategory(BaseModel):
    id: int
    name: str
    slug: str
    description: str
    color: str
    created_by_username: str | None = None
    created_at: str
    updated_at: str


class ForumBoard(BaseModel):
    id: int
    category_id: int
    category_name: str
    name: str
    slug: str
    description: str
    created_by_username: str | None = None
    thread_count: int
    reply_count: int
    latest_activity_at: str | None = None
    created_at: str
    updated_at: str


class ForumCategoryWithBoards(ForumCategory):
    boards: list[ForumBoard] = Field(default_factory=list)


class ForumThreadSummary(BaseModel):
    id: int
    board_id: int
    board_name: str
    category_id: int
    category_name: str
    title: str
    body_preview: str
    course_id: str | None = None
    author_username: str
    author_role: str
    is_anonymous: bool = False
    reply_count: int
    dylen_replied: bool
    created_at: str
    updated_at: str
    latest_activity_at: str


class ForumReply(BaseModel):
    id: int
    thread_id: int
    author_username: str
    author_role: str
    is_anonymous: bool = False
    body: str
    citations: list[Citation] = Field(default_factory=list)
    created_at: str


class ForumThread(ForumThreadSummary):
    body: str
    replies: list[ForumReply] = Field(default_factory=list)


class ForumIndexResponse(BaseModel):
    categories: list[ForumCategoryWithBoards]
    can_create_categories: bool


class ForumBoardThreadsResponse(BaseModel):
    board: ForumBoard
    threads: list[ForumThreadSummary]
