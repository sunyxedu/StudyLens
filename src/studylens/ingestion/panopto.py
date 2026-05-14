from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from studylens.domain import Resource
from studylens.errors import ConfigurationError


@dataclass(slots=True)
class PanoptoDownloader:
    """Thin browser-automation boundary for Panopto.

    The exact Panopto DOM can vary by tenant and login state, so this class keeps
    network/session concerns outside the retrieval pipeline. Tests should target
    callers with fake downloaders; live runs require Playwright and a saved
    browser storage state.
    """

    base_url: str
    storage_state: Path | None = None
    download_dir: Path = Path("data/raw/panopto")

    def require_browser_state(self) -> None:
        if not self.storage_state:
            raise ConfigurationError(
                "Panopto access requires STUDYLENS_BROWSER_STORAGE_STATE "
                "with an authenticated session"
            )
        if not self.storage_state.exists():
            raise ConfigurationError(f"Browser storage state not found: {self.storage_state}")

    async def search_course_videos(self, course_id: str, course_title: str) -> list[Resource]:
        self.require_browser_state()
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise ConfigurationError("Install studylens[browser] to use Panopto ingestion") from exc

        query = f"{course_id} {course_title}".strip()
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(storage_state=str(self.storage_state))
            page = await context.new_page()
            await page.goto(f"{self.base_url}#isSharedWithMe=true", wait_until="domcontentloaded")
            search = page.get_by_role("textbox").first
            await search.fill(query)
            await page.keyboard.press("Enter")
            await page.wait_for_load_state("networkidle")
            links = await page.locator("a").evaluate_all(
                "(nodes) => nodes.map(a => ({text: a.innerText, href: a.href}))"
                ".filter(x => x.text && x.href)"
            )
            await browser.close()

        resources: list[Resource] = []
        for item in links:
            title = str(item.get("text", "")).strip()
            href = str(item.get("href", "")).strip()
            if not title or not href:
                continue
            resources.append(
                Resource(
                    course_id=course_id,
                    title=title,
                    kind="video",
                    source_url=href,
                    metadata={"source": "panopto", "query": query},
                )
            )
        return resources
