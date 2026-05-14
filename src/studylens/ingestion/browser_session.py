from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any, Protocol

from studylens.config import Settings
from studylens.errors import ConfigurationError, IngestionError

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page


class AsyncFetcher(Protocol):
    """Anything that can fetch text/binary from a URL with the right auth."""

    async def get_text(self, url: str) -> str: ...

    async def download(self, url: str) -> tuple[bytes, str | None]: ...


class BrowserSession:
    """Single Playwright context bound to a saved Imperial SSO storage state.

    Scientia, Panopto, EdStem all live behind Imperial SSO. Authenticating
    once and reusing the resulting BrowserContext keeps cookies, redirects,
    and per-tenant quirks consistent across ingestion. Static fetches go
    through the context's `request` API (no rendering, fast). DOM-driven
    flows ask for `page()` and drive a real Page.
    """

    def __init__(self, storage_state: Path, *, headless: bool = True) -> None:
        self._storage_state = storage_state
        self._headless = headless
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None

    @classmethod
    def from_settings(cls, settings: Settings) -> BrowserSession:
        if settings.browser_storage_state is None:
            raise ConfigurationError(
                "BrowserSession requires STUDYLENS_BROWSER_STORAGE_STATE "
                "with an authenticated Imperial session"
            )
        if not settings.browser_storage_state.exists():
            raise ConfigurationError(
                f"Browser storage state not found: {settings.browser_storage_state}"
            )
        return cls(settings.browser_storage_state)

    async def __aenter__(self) -> BrowserSession:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise ConfigurationError(
                "Install studylens[browser] to use BrowserSession"
            ) from exc

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self._headless)
        self._context = await self._browser.new_context(storage_state=str(self._storage_state))
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    @property
    def context(self) -> BrowserContext:
        if self._context is None:
            raise IngestionError("BrowserSession used outside `async with` block")
        return self._context

    async def fetch_text(self, url: str) -> str:
        response = await self.context.request.get(url)
        if not response.ok:
            raise IngestionError(
                f"GET {url} returned HTTP {response.status}; check SSO state freshness"
            )
        return await response.text()

    async def download(self, url: str) -> tuple[bytes, str | None]:
        response = await self.context.request.get(url)
        if not response.ok:
            raise IngestionError(
                f"GET {url} returned HTTP {response.status}; check SSO state freshness"
            )
        body = await response.body()
        headers = response.headers
        return body, headers.get("content-type")

    @asynccontextmanager
    async def page(self) -> AsyncIterator[Page]:
        page = await self.context.new_page()
        try:
            yield page
        finally:
            await page.close()


class BrowserFetcher:
    """AsyncFetcher impl backed by a BrowserSession."""

    def __init__(self, session: BrowserSession) -> None:
        self._session = session

    async def get_text(self, url: str) -> str:
        return await self._session.fetch_text(url)

    async def download(self, url: str) -> tuple[bytes, str | None]:
        return await self._session.download(url)


class HttpFetcher:
    """Async httpx fetcher with no auth.

    Useful for unit tests of the auto-index pipeline that don't want to
    spin up a browser, and for fetching genuinely public URLs (e.g. when
    a caller wants to ingest an arbitrary public PDF). Not suitable for
    Scientia / Panopto / EdStem in production — those need BrowserFetcher.
    """

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout

    async def get_text(self, url: str) -> str:
        import httpx

        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.text

    async def download(self, url: str) -> tuple[bytes, str | None]:
        import httpx

        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content, response.headers.get("content-type")
