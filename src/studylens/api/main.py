from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from studylens.api.schemas import (
    AskRequest,
    AskResponse,
    GeneratedLatexResponse,
    GenerateRequest,
    HealthResponse,
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
from studylens.ingestion.documents import build_chunks
from studylens.retrieval.qa import RAGService


def _service(request: Request) -> RAGService:
    return request.app.state.rag_service


def create_app(*, settings: Settings | None = None, rag_service: RAGService | None = None) -> FastAPI:
    settings = settings or get_settings()
    service = rag_service or build_rag_service(settings)

    application = FastAPI(title="StudyLens", version="0.1.0")
    application.state.settings = settings
    application.state.rag_service = service
    application.state.cheatsheet_generator = CheatsheetGenerator(rag=service, llm=service.llm)
    application.state.exam_generator = PredictedExamGenerator(rag=service, llm=service.llm)

    allow_all = "*" in settings.allowed_origins or "chrome-extension://*" in settings.allowed_origins
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


app = create_app()
