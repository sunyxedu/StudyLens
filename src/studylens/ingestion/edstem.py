from __future__ import annotations

import re
from dataclasses import dataclass

from studylens.domain import Resource
from studylens.ingestion.browser_session import BrowserSession

EXAM_SCOPE_RE = re.compile(
    r"\b(exam|assess|test|scope|included|excluded|not\s+examined|not\s+assessed|will\s+not)\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class EdStemCrawler:
    """Collect exam-scope posts from EdStem for a given course.

    Drives a real Page because EdStem's dashboard is heavily JS-rendered
    and uses client-side routing to navigate from dashboard → course →
    posts list. All auth comes from the BrowserSession's storage state.
    """

    session: BrowserSession
    base_url: str

    @staticmethod
    def filter_exam_scope_posts(posts: list[dict[str, str]]) -> list[dict[str, str]]:
        return [
            post
            for post in posts
            if EXAM_SCOPE_RE.search(f"{post.get('title', '')}\n{post.get('body', '')}")
        ]

    async def collect_scope_notes(self, course_id: str, course_title: str) -> list[Resource]:
        async with self.session.page() as page:
            await page.goto(self.base_url, wait_until="domcontentloaded")
            await page.get_by_text(course_title, exact=False).first.click()
            await page.wait_for_load_state("networkidle")
            posts = await page.locator("article, [data-post-id]").evaluate_all(
                """(nodes) => nodes.map((node) => ({
                    title: (node.querySelector('h1,h2,h3') || node).innerText || '',
                    body: node.innerText || ''
                }))"""
            )

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
