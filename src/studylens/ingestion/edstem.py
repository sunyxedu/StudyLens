from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from studylens.domain import Resource
from studylens.errors import ConfigurationError

EXAM_SCOPE_RE = re.compile(
    r"\b(exam|assess|test|scope|included|excluded|not\s+examined|not\s+assessed|will\s+not)\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class EdStemCrawler:
    base_url: str
    storage_state: Path | None = None

    def require_browser_state(self) -> None:
        if not self.storage_state:
            raise ConfigurationError(
                "EdStem access requires STUDYLENS_BROWSER_STORAGE_STATE "
                "with an authenticated session"
            )
        if not self.storage_state.exists():
            raise ConfigurationError(f"Browser storage state not found: {self.storage_state}")

    @staticmethod
    def filter_exam_scope_posts(posts: list[dict[str, str]]) -> list[dict[str, str]]:
        return [
            post
            for post in posts
            if EXAM_SCOPE_RE.search(f"{post.get('title', '')}\n{post.get('body', '')}")
        ]

    async def collect_scope_notes(self, course_id: str, course_title: str) -> list[Resource]:
        self.require_browser_state()
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise ConfigurationError("Install studylens[browser] to use EdStem ingestion") from exc

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(storage_state=str(self.storage_state))
            page = await context.new_page()
            await page.goto(self.base_url, wait_until="domcontentloaded")
            await page.get_by_text(course_title, exact=False).first.click()
            await page.wait_for_load_state("networkidle")
            posts = await page.locator("article, [data-post-id]").evaluate_all(
                """(nodes) => nodes.map((node) => ({
                    title: (node.querySelector('h1,h2,h3') || node).innerText || '',
                    body: node.innerText || ''
                }))"""
            )
            await browser.close()

        resources: list[Resource] = []
        for index, post in enumerate(self.filter_exam_scope_posts(posts)):
            title = post.get("title") or f"EdStem scope note {index + 1}"
            resources.append(
                Resource(
                    course_id=course_id,
                    title=title,
                    kind="edstem_note",
                    metadata={
                        "source": "edstem",
                        "body": post.get("body", ""),
                        "course_title": course_title,
                    },
                )
            )
        return resources
