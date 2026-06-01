from __future__ import annotations

import json
import secrets
from datetime import timedelta
from pathlib import Path
from typing import Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from studylens.api.browser_state import (
    BrowserStateManager,
    BrowserStateRouter,
    BrowserStateStatus,
    PlaywrightBrowserStateManager,
)
from studylens.api.schemas import (
    AskRequest,
    AskResponse,
    AuthSessionResponse,
    AuthUser,
    AutoIndexCourseRequest,
    AutoIndexCourseResponse,
    BrowserStateStatusResponse,
    BrowserStateStepResponse,
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
    LoginRequest,
    PredictedExamRequest,
    RetrieveRequest,
    RetrieveResponse,
)
from studylens.bootstrap import build_rag_service
from studylens.config import Settings, get_settings
from studylens.domain import Resource
from studylens.errors import ConfigurationError
from studylens.generation import CheatsheetGenerator, PredictedExamGenerator
from studylens.generation.common import ManifestCourseContextProvider
from studylens.ingestion.auto_index import AutoIndexReport, _normalize_course_id, build_auto_indexer
from studylens.ingestion.browser_session import BrowserSession
from studylens.ingestion.documents import build_chunks
from studylens.ingestion.edstem import EdStemIndexer, build_edstem_indexer
from studylens.ingestion.edstem_agent import discover_edstem_courses
from studylens.ingestion.exams import ExamsIndexer, build_exams_indexer
from studylens.retrieval.qa import RAGService
from studylens.storage import AuthStore, CourseRecord, CourseStore, UserRecord
from studylens.storage.auth import AuthStoreError, load_or_create_local_secret


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


def _auth_store(request: Request) -> AuthStore:
    return request.app.state.auth_store


def _browser_state_manager(request: Request) -> BrowserStateManager:
    return request.app.state.browser_state_manager


def _current_user(request: Request) -> UserRecord:
    settings: Settings = request.app.state.settings
    token = request.cookies.get(settings.session_cookie_name)
    user = _auth_store(request).user_for_session(token)
    if user is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return user


def _auth_secret(settings: Settings) -> str:
    if settings.auth_secret_key:
        return settings.auth_secret_key
    if settings.app_env == "local":
        return load_or_create_local_secret(settings.data_dir / "auth" / "secret.key")
    raise ConfigurationError("AUTH_SECRET_KEY must be configured outside local mode")


def _secure_cookie(settings: Settings) -> bool:
    if settings.session_cookie_secure is not None:
        return settings.session_cookie_secure
    return settings.app_env != "local"


def _auth_user_schema(user: UserRecord) -> AuthUser:
    return AuthUser(
        id=user.id,
        username=user.username,
        grade=user.grade,
        course=user.course,
    )


def _auth_session_response(
    *,
    store: AuthStore,
    user: UserRecord,
    created: bool = False,
) -> AuthSessionResponse:
    browser_state_ready = store.has_browser_state(user.id)
    return AuthSessionResponse(
        user=_auth_user_schema(user),
        created=created,
        browser_state_ready=browser_state_ready,
        needs_browser_state=not browser_state_ready,
    )


def _browser_state_status_schema(
    status: BrowserStateStatus,
) -> BrowserStateStatusResponse:
    step = None
    if status.step is not None:
        step = BrowserStateStepResponse(
            key=status.step.key,
            title=status.step.title,
            url=status.step.url,
            instruction=status.step.instruction,
        )
    return BrowserStateStatusResponse(
        running=status.running,
        completed=status.completed,
        ready=status.ready,
        total_steps=status.total_steps,
        step_index=status.step_index,
        step=step,
        error=status.error,
    )


def _set_session_cookie(
    response: Response,
    *,
    settings: Settings,
    token: str,
    max_age: int,
) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=_secure_cookie(settings),
        samesite="lax",
        path="/",
    )


def _clear_session_cookie(response: Response, *, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        httponly=True,
        secure=_secure_cookie(settings),
        samesite="lax",
        path="/",
    )


def _record_to_schema(record: CourseRecord) -> DiscoverCoursesCourse:
    return DiscoverCoursesCourse(
        code=record.code,
        title=record.title,
        edstem_url=record.edstem_url,
        updated_at=record.updated_at,
        indexed_at=record.indexed_at,
    )


def _cors_settings(settings: Settings) -> tuple[list[str], str | None]:
    origins = [
        origin
        for origin in settings.allowed_origins
        if origin not in {"*", "chrome-extension://*"}
    ]
    regexes: list[str] = []
    if "chrome-extension://*" in settings.allowed_origins:
        regexes.append(r"chrome-extension://.*")
    if "*" in settings.allowed_origins:
        if settings.app_env != "local":
            raise ConfigurationError(
                "ALLOWED_ORIGINS='*' cannot be used with credentialed sessions"
            )
        regexes.append(r"https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?")
    if not regexes:
        return origins, None
    return origins, "|".join(f"(?:{regex})" for regex in regexes)


async def _run_auto_index(
    request: Request,
    payload: AutoIndexCourseRequest,
    user: UserRecord,
) -> AutoIndexReport:
    injected: AutoIndexerLike | None = request.app.state.auto_indexer
    if injected is not None:
        return await injected.index_course(
            course_id=payload.course_id,
            course_title=payload.course_title,
        )
    settings: Settings = request.app.state.settings
    storage_state = _auth_store(request).get_browser_state(user.id)
    if storage_state is None:
        raise HTTPException(status_code=409, detail="browser state setup required")
    async with BrowserSession.from_storage_state(storage_state) as session:
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
    auth_store: AuthStore | None = None,
    browser_state_manager: BrowserStateManager | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    service = rag_service or build_rag_service(settings)

    application = FastAPI(title="StudyLens", version="0.1.0")
    application.state.settings = settings
    application.state.rag_service = service
    course_context = ManifestCourseContextProvider(settings.raw_dir)
    application.state.cheatsheet_generator = CheatsheetGenerator(
        context_provider=course_context,
        llm=service.llm,
    )
    application.state.exam_generator = PredictedExamGenerator(
        context_provider=course_context,
        llm=service.llm,
    )
    application.state.auto_indexer = auto_indexer
    application.state.exams_indexer = exams_indexer
    application.state.edstem_indexer = edstem_indexer
    application.state.course_store = course_store or CourseStore.from_database_url(
        settings.database_url
    )
    resolved_auth_store = auth_store or AuthStore.from_database_url(
        settings.database_url,
        secret_key=_auth_secret(settings),
    )
    application.state.auth_store = resolved_auth_store
    application.state.browser_state_manager = (
        browser_state_manager
        or PlaywrightBrowserStateManager(
            auth_store=resolved_auth_store,
            router=BrowserStateRouter(settings),
        )
    )

    cors_origins, cors_regex = _cors_settings(settings)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_origin_regex=cors_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", vector_store=settings.vector_store)

    @application.post("/auth/login", response_model=AuthSessionResponse)
    def login(
        payload: LoginRequest,
        request: Request,
        response: Response,
    ) -> AuthSessionResponse:
        store = _auth_store(request)
        try:
            result = store.authenticate_or_create(
                username=payload.username,
                grade=payload.grade,
                course=payload.course,
                password=payload.password,
            )
        except AuthStoreError as exc:
            status_code = 401 if "invalid username or password" in str(exc) else 400
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        ttl = timedelta(days=settings.session_ttl_days)
        session = store.create_session(result.user.id, ttl=ttl)
        _set_session_cookie(
            response,
            settings=settings,
            token=session.token,
            max_age=max(0, int(ttl.total_seconds())),
        )
        return _auth_session_response(
            store=store,
            user=result.user,
            created=result.created,
        )

    @application.get("/auth/session", response_model=AuthSessionResponse)
    def auth_session(
        request: Request,
        user: UserRecord = Depends(_current_user),
    ) -> AuthSessionResponse:
        return _auth_session_response(store=_auth_store(request), user=user)

    @application.post("/auth/logout")
    def logout(request: Request, response: Response) -> dict[str, str]:
        token = request.cookies.get(settings.session_cookie_name)
        _auth_store(request).revoke_session(token)
        _clear_session_cookie(response, settings=settings)
        return {"status": "ok"}

    @application.post("/browser-state/start", response_model=BrowserStateStatusResponse)
    async def browser_state_start(
        request: Request,
        user: UserRecord = Depends(_current_user),
    ) -> BrowserStateStatusResponse:
        try:
            status = await _browser_state_manager(request).start(user)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return _browser_state_status_schema(status)

    @application.post("/browser-state/advance", response_model=BrowserStateStatusResponse)
    async def browser_state_advance(
        request: Request,
        user: UserRecord = Depends(_current_user),
    ) -> BrowserStateStatusResponse:
        status = await _browser_state_manager(request).advance(user)
        if status.error and not status.running:
            raise HTTPException(status_code=409, detail=status.error)
        return _browser_state_status_schema(status)

    @application.get("/browser-state/status", response_model=BrowserStateStatusResponse)
    async def browser_state_status(
        request: Request,
        user: UserRecord = Depends(_current_user),
    ) -> BrowserStateStatusResponse:
        return _browser_state_status_schema(
            await _browser_state_manager(request).status(user)
        )

    @application.post("/browser-state/cancel", response_model=BrowserStateStatusResponse)
    async def browser_state_cancel(
        request: Request,
        user: UserRecord = Depends(_current_user),
    ) -> BrowserStateStatusResponse:
        return _browser_state_status_schema(
            await _browser_state_manager(request).cancel(user)
        )

    @application.post("/admin/browser-state", include_in_schema=False)
    def update_browser_state(
        payload: dict, x_admin_token: str = Header(default="")
    ) -> dict[str, str]:
        if not settings.admin_token:
            raise HTTPException(status_code=503, detail="admin_token not configured")
        if not secrets.compare_digest(x_admin_token, settings.admin_token):
            raise HTTPException(status_code=403, detail="invalid admin token")
        target = settings.browser_storage_state or (
            settings.data_dir / "auth" / "browser-state.json"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload), encoding="utf-8")
        return {"status": "ok", "path": str(target)}

    @application.post("/chunks", response_model=IndexTextResponse)
    def index_text(
        payload: IndexTextRequest,
        request: Request,
        user: UserRecord = Depends(_current_user),
    ) -> IndexTextResponse:
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
        user: UserRecord = Depends(_current_user),
    ) -> AutoIndexCourseResponse:
        report = await _run_auto_index(request, payload, user)
        store: CourseStore = request.app.state.course_store
        store.mark_indexed(_normalize_course_id(payload.course_id), user_id=user.id)
        return AutoIndexCourseResponse(**report.model_dump())

    @application.post("/index/exams", response_model=IndexExamsResponse)
    async def index_exams(
        payload: IndexExamsRequest,
        request: Request,
        user: UserRecord = Depends(_current_user),
    ) -> IndexExamsResponse:
        indexer: ExamsIndexer = (
            request.app.state.exams_indexer
            or build_exams_indexer(request.app.state.settings, _service(request))
        )
        results = await indexer.index_course_exams(course_id=payload.course_id)
        return IndexExamsResponse(results=results)

    @application.get("/courses", response_model=CoursesListResponse)
    def courses_list(
        request: Request,
        user: UserRecord = Depends(_current_user),
    ) -> CoursesListResponse:
        store: CourseStore = request.app.state.course_store
        return CoursesListResponse(
            courses=[_record_to_schema(r) for r in store.list_all(user_id=user.id)]
        )

    @application.post("/courses/discover", response_model=DiscoverCoursesResponse)
    async def courses_discover(
        request: Request,
        user: UserRecord = Depends(_current_user),
    ) -> DiscoverCoursesResponse:
        settings: Settings = request.app.state.settings
        store: CourseStore = request.app.state.course_store
        storage_state = _auth_store(request).get_browser_state(user.id)
        if storage_state is None:
            raise HTTPException(status_code=409, detail="browser state setup required")
        async with BrowserSession.from_storage_state(storage_state) as session:
            report = await discover_edstem_courses(session, settings)

        if report.courses:
            stored = store.replace_all(
                ((c.code, c.title, c.edstem_url) for c in report.courses),
                user_id=user.id,
            )
            payload = [_record_to_schema(r) for r in stored]
        else:
            payload = [_record_to_schema(r) for r in store.list_all(user_id=user.id)]

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
        user: UserRecord = Depends(_current_user),
    ) -> IndexEdStemResponse:
        injected: EdStemIndexer | None = request.app.state.edstem_indexer
        if injected is not None:
            results = await injected.index_course_scope_notes(
                course_id=payload.course_id,
                course_title=payload.course_title,
            )
            return IndexEdStemResponse(results=results)

        settings: Settings = request.app.state.settings
        storage_state = _auth_store(request).get_browser_state(user.id)
        if storage_state is None:
            raise HTTPException(status_code=409, detail="browser state setup required")
        async with BrowserSession.from_storage_state(storage_state) as session:
            indexer = build_edstem_indexer(settings, _service(request), session)
            results = await indexer.index_course_scope_notes(
                course_id=payload.course_id,
                course_title=payload.course_title,
            )
        return IndexEdStemResponse(results=results)

    @application.post("/retrieve", response_model=RetrieveResponse)
    def retrieve(
        payload: RetrieveRequest,
        request: Request,
        user: UserRecord = Depends(_current_user),
    ) -> RetrieveResponse:
        results = _service(request).retrieve(
            payload.query,
            course_id=payload.course_id,
            kinds=set(payload.kinds) if payload.kinds else None,
            top_k=payload.top_k,
        )
        return RetrieveResponse(results=results)

    @application.post("/ask", response_model=AskResponse)
    def ask(
        payload: AskRequest,
        request: Request,
        user: UserRecord = Depends(_current_user),
    ) -> AskResponse:
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
        user: UserRecord = Depends(_current_user),
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
        user: UserRecord = Depends(_current_user),
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
