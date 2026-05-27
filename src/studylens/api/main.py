from __future__ import annotations

from pathlib import Path
from typing import Protocol

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from studylens.api.schemas import (
    AskRequest,
    AskResponse,
    AutoIndexCourseRequest,
    AutoIndexCourseResponse,
    CoursesListResponse,
    DiscoverCoursesCourse,
    DiscoverCoursesResponse,
    GeneratedLatexResponse,
    GenerateRequest,
    HealthResponse,
    IndexEdStemRequest,
    IndexEdStemResponse,
    IndexExamsRequest,
    IndexExamsResponse,
    IndexTextRequest,
    IndexTextResponse,
    PredictedExamRequest,
    RetrieveRequest,
    RetrieveResponse,
)
from studylens.bootstrap import build_rag_service
from studylens.config import Settings, get_settings
from studylens.domain import Resource
from studylens.generation import CheatsheetGenerator, PredictedExamGenerator
from studylens.ingestion.auto_index import AutoIndexReport, build_auto_indexer
from studylens.ingestion.browser_session import BrowserSession
from studylens.ingestion.documents import build_chunks
from studylens.ingestion.edstem import EdStemIndexer, build_edstem_indexer
from studylens.ingestion.edstem_agent import discover_edstem_courses
from studylens.ingestion.exams import ExamsIndexer, build_exams_indexer
from studylens.retrieval.qa import RAGService
from studylens.storage import CourseRecord, CourseStore


class AutoIndexerLike(Protocol):
    async def index_course(
        self,
        *,
        course_id: str,
        course_title: str,
    ) -> AutoIndexReport: ...


class LazyStudyLensApp:
    def __init__(self) -> None:
        self._app: FastAPI | None = None

    def get_app(self) -> FastAPI:
        if self._app is None:
            self._app = create_app()
        return self._app

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        await self.get_app()(scope, receive, send)


def _service(request: Request) -> RAGService:
    return request.app.state.rag_service


def _record_to_schema(record: CourseRecord) -> DiscoverCoursesCourse:
    return DiscoverCoursesCourse(
        code=record.code,
        title=record.title,
        edstem_url=record.edstem_url,
        updated_at=record.updated_at,
        indexed_at=record.indexed_at,
    )


async def _run_auto_index(
    request: Request,
    payload: AutoIndexCourseRequest,
) -> AutoIndexReport:
    injected: AutoIndexerLike | None = request.app.state.auto_indexer
    if injected is not None:
        return await injected.index_course(
            course_id=payload.course_id,
            course_title=payload.course_title,
        )
    settings: Settings = request.app.state.settings
    async with BrowserSession.from_settings(settings) as session:
        indexer = build_auto_indexer(settings, _service(request), session)
        return await indexer.index_course(
            course_id=payload.course_id,
            course_title=payload.course_title,
        )


def create_app(
    *,
    settings: Settings | None = None,
    rag_service: RAGService | None = None,
    auto_indexer: AutoIndexerLike | None = None,
    exams_indexer: ExamsIndexer | None = None,
    edstem_indexer: EdStemIndexer | None = None,
    course_store: CourseStore | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    service = rag_service or build_rag_service(settings)

    application = FastAPI(title="StudyLens", version="0.1.0")
    application.state.settings = settings
    application.state.rag_service = service
    application.state.cheatsheet_generator = CheatsheetGenerator(rag=service, llm=service.llm)
    application.state.exam_generator = PredictedExamGenerator(rag=service, llm=service.llm)
    application.state.auto_indexer = auto_indexer
    application.state.exams_indexer = exams_indexer
    application.state.edstem_indexer = edstem_indexer
    application.state.course_store = course_store or CourseStore.from_database_url(
        settings.database_url
    )

    allow_all = (
        "*" in settings.allowed_origins or "chrome-extension://*" in settings.allowed_origins
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if allow_all else settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", vector_store=settings.vector_store)

    @application.post("/chunks", response_model=IndexTextResponse)
    def index_text(payload: IndexTextRequest, request: Request) -> IndexTextResponse:
        resource = Resource(
            id=payload.resource_id,
            course_id=payload.course_id,
            title=payload.title,
            kind=payload.kind,
            source_url=payload.source_url,
            metadata=payload.metadata,
        )
        chunks = build_chunks(resource, payload.text)
        indexed = _service(request).index_chunks(chunks)
        return IndexTextResponse(indexed_chunks=indexed)

    @application.post("/index/course", response_model=AutoIndexCourseResponse)
    async def auto_index_course(
        payload: AutoIndexCourseRequest,
        request: Request,
    ) -> AutoIndexCourseResponse:
        report = await _run_auto_index(request, payload)
        store: CourseStore = request.app.state.course_store
        store.mark_indexed(payload.course_id)
        return AutoIndexCourseResponse(**report.model_dump())

    @application.post("/index/exams", response_model=IndexExamsResponse)
    async def index_exams(
        payload: IndexExamsRequest,
        request: Request,
    ) -> IndexExamsResponse:
        indexer: ExamsIndexer = (
            request.app.state.exams_indexer
            or build_exams_indexer(request.app.state.settings, _service(request))
        )
        results = await indexer.index_course_exams(course_id=payload.course_id)
        return IndexExamsResponse(results=results)

    @application.get("/courses", response_model=CoursesListResponse)
    def courses_list(request: Request) -> CoursesListResponse:
        store: CourseStore = request.app.state.course_store
        return CoursesListResponse(
            courses=[_record_to_schema(r) for r in store.list_all()]
        )

    @application.post("/courses/discover", response_model=DiscoverCoursesResponse)
    async def courses_discover(request: Request) -> DiscoverCoursesResponse:
        settings: Settings = request.app.state.settings
        store: CourseStore = request.app.state.course_store
        async with BrowserSession.from_settings(settings) as session:
            report = await discover_edstem_courses(session, settings)

        if report.courses:
            stored = store.replace_all(
                (c.code, c.title, c.edstem_url) for c in report.courses
            )
            payload = [_record_to_schema(r) for r in stored]
        else:
            payload = [_record_to_schema(r) for r in store.list_all()]

        return DiscoverCoursesResponse(
            courses=payload,
            dropped_titles=report.dropped_titles,
            num_turns=report.num_turns,
            total_cost_usd=report.total_cost_usd,
            error=report.error,
        )

    @application.post("/index/edstem", response_model=IndexEdStemResponse)
    async def index_edstem(
        payload: IndexEdStemRequest,
        request: Request,
    ) -> IndexEdStemResponse:
        injected: EdStemIndexer | None = request.app.state.edstem_indexer
        if injected is not None:
            results = await injected.index_course_scope_notes(
                course_id=payload.course_id,
                course_title=payload.course_title,
            )
            return IndexEdStemResponse(results=results)

        settings: Settings = request.app.state.settings
        async with BrowserSession.from_settings(settings) as session:
            indexer = build_edstem_indexer(settings, _service(request), session)
            results = await indexer.index_course_scope_notes(
                course_id=payload.course_id,
                course_title=payload.course_title,
            )
        return IndexEdStemResponse(results=results)

    @application.post("/retrieve", response_model=RetrieveResponse)
    def retrieve(payload: RetrieveRequest, request: Request) -> RetrieveResponse:
        results = _service(request).retrieve(
            payload.query,
            course_id=payload.course_id,
            kinds=set(payload.kinds) if payload.kinds else None,
            top_k=payload.top_k,
        )
        return RetrieveResponse(results=results)

    @application.post("/ask", response_model=AskResponse)
    def ask(payload: AskRequest, request: Request) -> AskResponse:
        answer = _service(request).answer(
            payload.question,
            course_id=payload.course_id,
            kinds=set(payload.kinds) if payload.kinds else None,
            top_k=payload.top_k,
            include_exercises=payload.include_exercises,
        )
        return AskResponse(**answer.model_dump())

    @application.post("/generate/cheatsheet", response_model=GeneratedLatexResponse)
    def generate_cheatsheet(
        payload: GenerateRequest,
        request: Request,
    ) -> GeneratedLatexResponse:
        latex = request.app.state.cheatsheet_generator.generate(
            course_id=payload.course_id,
            course_title=payload.course_title,
            scope_notes=payload.scope_notes,
            top_k=payload.top_k,
        )
        return GeneratedLatexResponse(latex=latex)

    @application.post("/generate/predicted-exam", response_model=GeneratedLatexResponse)
    def generate_predicted_exam(
        payload: PredictedExamRequest,
        request: Request,
    ) -> GeneratedLatexResponse:
        latex = request.app.state.exam_generator.generate(
            course_id=payload.course_id,
            course_title=payload.course_title,
            scope_notes=payload.scope_notes,
            question_count=payload.question_count,
            top_k=payload.top_k,
        )
        return GeneratedLatexResponse(latex=latex)

    web_dist = Path(__file__).resolve().parents[3] / "web" / "dist"
    if web_dist.exists():
        application.mount("/app", StaticFiles(directory=web_dist, html=True), name="app")

        @application.get("/", include_in_schema=False)
        def app_index() -> RedirectResponse:
            return RedirectResponse(url="/app")

    return application


app = LazyStudyLensApp()
