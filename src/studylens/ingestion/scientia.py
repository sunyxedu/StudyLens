from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from studylens.domain import Course, CourseSummary, Resource
from studylens.domain.models import ResourceKind

IGNORED_LINK_PREFIXES = ("mailto:", "javascript:", "#")


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def classify_resource(text: str, href: str, context: str = "") -> ResourceKind:
    haystack = f"{text} {href} {context}".lower()
    if "tutorial" in haystack:
        return "tutorial"
    if any(
        token in haystack
        for token in ("exercise", "exercises", "problem sheet", "problem-sheet")
    ):
        return "exercise"
    if any(
        token in haystack
        for token in ("lecture", "slides", "material", "note", "handout", ".pdf")
    ):
        return "material"
    return "material"


def _nearest_heading(anchor: Tag) -> str:
    current: Tag | None = anchor
    while current is not None:
        previous = current.find_previous(["h1", "h2", "h3", "h4", "strong"])
        if previous is None:
            return ""
        text = clean_text(previous.get_text(" "))
        if text:
            return text
        current = previous if isinstance(previous, Tag) else None
    return ""


def parse_course_page(html: str, summary: CourseSummary, base_url: str) -> Course:
    """Parse materials, exercises, and tutorials from a Scientia course page."""

    soup = BeautifulSoup(html, "html.parser")
    buckets: dict[ResourceKind, list[Resource]] = {
        "material": [],
        "exercise": [],
        "tutorial": [],
    }
    seen: set[tuple[str, str]] = set()

    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href"))
        if href.startswith(IGNORED_LINK_PREFIXES):
            continue
        label = clean_text(anchor.get_text(" ")) or href.rsplit("/", 1)[-1]
        absolute = urljoin(base_url, href)
        context = _nearest_heading(anchor)
        kind = classify_resource(label, absolute, context)
        if kind not in buckets:
            continue
        fingerprint = (kind, absolute)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        buckets[kind].append(
            Resource(
                course_id=summary.id,
                title=label,
                kind=kind,
                source_url=absolute,
                metadata={"section": context, "source": "scientia"},
            )
        )

    return Course(
        id=summary.id,
        title=summary.title,
        year=summary.year,
        source_url=summary.url,
        materials=buckets["material"],
        exercises=buckets["exercise"],
        tutorials=buckets["tutorial"],
        metadata=summary.metadata,
    )
