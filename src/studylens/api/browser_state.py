from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from studylens.config import Settings
from studylens.storage import AuthStore, UserRecord


@dataclass(frozen=True, slots=True)
class BrowserStateStep:
    key: str
    title: str
    url: str
    instruction: str


DEFAULT_BROWSER_STATE_STEPS: tuple[BrowserStateStep, ...] = (
    BrowserStateStep(
        key="scientia",
        title="Scientia",
        url="https://scientia.doc.ic.ac.uk/2526/timeline",
        instruction="Log into Imperial SSO and wait for the Scientia timeline to load.",
    ),
    BrowserStateStep(
        key="panopto",
        title="Panopto",
        url="https://imperial.cloud.panopto.eu/Panopto/Pages/Sessions/List.aspx#isSharedWithMe=true",
        instruction="Complete the Panopto sign-in flow and wait for the session list.",
    ),
    BrowserStateStep(
        key="exams",
        title="DOC Exams",
        url="https://exams.doc.ic.ac.uk/",
        instruction=(
            "Log into the Department of Computing exams site and wait for the "
            "past papers index."
        ),
    ),
    BrowserStateStep(
        key="edstem",
        title="EdStem",
        url="https://edstem.org/us/dashboard",
        instruction="Log into EdStem and wait for the dashboard to load.",
    ),
)


class BrowserStateRouter:
    """Select the login sites required for a user's grade/course.

    Today every route needs the same three sites. Keeping this as a dedicated
    router lets us branch later without changing the API flow.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def steps_for(self, *, grade: str, course: str) -> list[BrowserStateStep]:
        return list(DEFAULT_BROWSER_STATE_STEPS)

    def http_credentials(self) -> dict[str, str] | None:
        if not self._settings.imperial_username or not self._settings.imperial_password:
            return None
        return {
            "username": self._settings.imperial_username,
            "password": self._settings.imperial_password,
        }


@dataclass(frozen=True, slots=True)
class BrowserStateStatus:
    running: bool
    completed: bool
    ready: bool
    total_steps: int
    step_index: int | None = None
    step: BrowserStateStep | None = None
    error: str | None = None


class BrowserStateManager(Protocol):
    async def start(self, user: UserRecord) -> BrowserStateStatus: ...

    async def advance(self, user: UserRecord) -> BrowserStateStatus: ...

    async def status(self, user: UserRecord) -> BrowserStateStatus: ...

    async def cancel(self, user: UserRecord) -> BrowserStateStatus: ...


class PlaywrightBrowserStateManager:
    def __init__(self, *, auth_store: AuthStore, router: BrowserStateRouter) -> None:
        self._auth_store = auth_store
        self._router = router
        self._sessions: dict[int, _CaptureSession] = {}

    async def start(self, user: UserRecord) -> BrowserStateStatus:
        existing = self._sessions.get(user.id)
        if existing is not None:
            return await existing.status(ready=self._auth_store.has_browser_state(user.id))

        steps = self._router.steps_for(grade=user.grade, course=user.course)
        session = _CaptureSession(steps, http_credentials=self._router.http_credentials())
        self._sessions[user.id] = session
        try:
            await session.start()
            return await session.status(ready=self._auth_store.has_browser_state(user.id))
        except Exception:
            self._sessions.pop(user.id, None)
            await session.close()
            raise

    async def advance(self, user: UserRecord) -> BrowserStateStatus:
        session = self._sessions.get(user.id)
        if session is None:
            if self._auth_store.has_browser_state(user.id):
                return BrowserStateStatus(
                    running=False,
                    completed=True,
                    ready=True,
                    total_steps=0,
                )
            return BrowserStateStatus(
                running=False,
                completed=False,
                ready=False,
                total_steps=0,
                error="browser state setup is not running",
            )

        state = await session.advance()
        if state is None:
            return await session.status(ready=self._auth_store.has_browser_state(user.id))

        if not _has_auth_material(state):
            return await session.status(
                ready=False,
                error="No cookies were captured. Finish logging in before saving.",
            )

        self._auth_store.save_browser_state(user.id, state)
        self._sessions.pop(user.id, None)
        await session.close()
        return BrowserStateStatus(
            running=False,
            completed=True,
            ready=True,
            total_steps=len(session.steps),
        )

    async def status(self, user: UserRecord) -> BrowserStateStatus:
        session = self._sessions.get(user.id)
        if session is None:
            ready = self._auth_store.has_browser_state(user.id)
            return BrowserStateStatus(
                running=False,
                completed=ready,
                ready=ready,
                total_steps=0,
            )
        return await session.status(ready=self._auth_store.has_browser_state(user.id))

    async def cancel(self, user: UserRecord) -> BrowserStateStatus:
        session = self._sessions.pop(user.id, None)
        if session is not None:
            await session.close()
        ready = self._auth_store.has_browser_state(user.id)
        return BrowserStateStatus(
            running=False,
            completed=ready,
            ready=ready,
            total_steps=0,
        )


class _CaptureSession:
    def __init__(
        self,
        steps: list[BrowserStateStep],
        *,
        http_credentials: dict[str, str] | None = None,
    ) -> None:
        if not steps:
            raise ValueError("browser state flow requires at least one step")
        self.steps = steps
        self._http_credentials = http_credentials
        self._index = 0
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._last_error: str | None = None

    async def start(self) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("Install studylens[browser] to run browser setup") from exc

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/136.0.0.0 Safari/537.36"
            ),
            http_credentials=self._http_credentials,
        )
        self._page = await self._context.new_page()
        await self._goto_current_step()

    async def advance(self) -> dict[str, Any] | None:
        if self._context is None:
            raise RuntimeError("browser setup is not running")
        if self._index < len(self.steps) - 1:
            self._index += 1
            await self._goto_current_step()
            return None
        return await self._context.storage_state()

    async def status(
        self,
        *,
        ready: bool,
        error: str | None = None,
    ) -> BrowserStateStatus:
        if error is not None:
            self._last_error = error
        return BrowserStateStatus(
            running=True,
            completed=False,
            ready=ready,
            total_steps=len(self.steps),
            step_index=self._index,
            step=self.steps[self._index],
            error=self._last_error,
        )

    async def close(self) -> None:
        if self._page is not None:
            await self._page.close()
            self._page = None
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def _goto_current_step(self) -> None:
        if self._page is None:
            raise RuntimeError("browser setup page is not open")
        self._last_error = None
        await self._page.goto(self.steps[self._index].url, wait_until="domcontentloaded")


def _has_auth_material(state: dict[str, Any]) -> bool:
    cookies = state.get("cookies")
    origins = state.get("origins")
    return (
        isinstance(cookies, list)
        and len(cookies) > 0
        or isinstance(origins, list)
        and len(origins) > 0
    )
