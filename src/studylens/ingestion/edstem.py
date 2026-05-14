from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel

from studylens.config import Settings
from studylens.domain import Resource
from studylens.ingestion.browser_session import BrowserSession
from studylens.ingestion.documents import build_chunks
from studylens.retrieval.qa import RAGService

EXAM_SCOPE_RE = re.compile(
    r"\b(exam|assess|test|scope|included|excluded|not\s+examined|not\s+assessed|will\s+not)\b",
    re.IGNORECASE,
)


class EdStemIndexResult(BaseModel):
    title: str
    status: str
    chunks: int = 0
    error: str | None = None
    discovered: bool = True


def filter_exam_scope_posts(posts: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        post
        for post in posts
        if EXAM_SCOPE_RE.search(f"{post.get('title', '')}\n{post.get('body', '')}")
    ]


def posts_to_resources(
    posts: list[dict[str, str]],
    *,
    course_id: str,
    course_title: str,
) -> list[Resource]:
    """Turn scope-relevant EdStem posts into edstem_note resources."""

    resources: list[Resource] = []
    for index, post in enumerate(filter_exam_scope_posts(posts)):
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


@dataclass(slots=True)
class EdStemCrawler:
    """Drive a real Page to scrape scope-relevant posts for a course.

    The EdStem dashboard is JS-heavy and uses client-side routing, so DOM
    interaction (clicking through to the course) is unavoidable. Auth is
    handled entirely by the shared BrowserSession.
    """

    session: BrowserSession
    base_url: str

    # Re-exported for compatibility with earlier API. Use the module-level
    # function in new code.
    @staticmethod
    def filter_exam_scope_posts(posts: list[dict[str, str]]) -> list[dict[str, str]]:
        return filter_exam_scope_posts(posts)

    async def collect_scope_notes(self, course_id: str, course_title: str) -> list[Resource]:
        posts = await self._fetch_posts(course_title)
        return posts_to_resources(posts, course_id=course_id, course_title=course_title)

    async def _fetch_posts(self, course_title: str) -> list[dict[str, str]]:
        async with self.session.page() as page:
            await page.goto(self.base_url, wait_until="domcontentloaded")
            await page.get_by_text(course_title, exact=False).first.click()
            await page.wait_for_load_state("networkidle")
            return await page.locator("article, [data-post-id]").evaluate_all(
                """(nodes) => nodes.map((node) => ({
                    title: (node.querySelector('h1,h2,h3') || node).innerText || '',
                    body: node.innerText || ''
                }))"""
            )


@dataclass(slots=True)
class EdStemIndexer:
    """Index EdStem scope notes into the RAG store as `edstem_note` chunks."""

    settings: Settings
    rag: RAGService
    crawler: EdStemCrawler | None

    async def index_course_scope_notes(
        self,
        *,
        course_id: str,
        course_title: str,
    ) -> list[EdStemIndexResult]:
        if self.crawler is None:
            return [
                EdStemIndexResult(
                    title="EdStem scope notes",
                    status="skipped",
                    error=(
                        "Set BROWSER_STORAGE_STATE to index EdStem scope notes"
                    ),
                    discovered=False,
                )
            ]

        try:
            resources = await self.crawler.collect_scope_notes(course_id, course_title)
        except Exception as exc:  # pragma: no cover - tenant-specific DOM failures.
            return [
                EdStemIndexResult(
                    title="EdStem scope notes",
                    status="failed",
                    error=str(exc),
                    discovered=False,
                )
            ]

        if not resources:
            return [
                EdStemIndexResult(
                    title="EdStem scope notes",
                    status="skipped",
                    error="No scope-relevant EdStem posts found for this course",
                    discovered=False,
                )
            ]

        results: list[EdStemIndexResult] = []
        for resource in resources:
            body = str(resource.metadata.get("body") or "").strip()
            if not body:
                results.append(
                    EdStemIndexResult(
                        title=resource.title,
                        status="skipped",
                        error="Empty post body",
                    )
                )
                continue
            chunks = build_chunks(resource, body)
            indexed = self.rag.index_chunks(chunks)
            results.append(
                EdStemIndexResult(
                    title=resource.title,
                    status="indexed",
                    chunks=indexed,
                )
            )
        return results


def build_edstem_indexer(
    settings: Settings,
    rag: RAGService,
    session: BrowserSession | None,
) -> EdStemIndexer:
    crawler = (
        EdStemCrawler(session=session, base_url=str(settings.edstem_base_url))
        if session is not None
        else None
    )
    return EdStemIndexer(settings=settings, rag=rag, crawler=crawler)
