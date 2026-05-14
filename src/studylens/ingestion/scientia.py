from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from studylens.domain import Course, CourseSummary, Resource
from studylens.domain.models import ResourceKind, stable_id

COURSE_CODE_RE = re.compile(r"\b([A-Z]{3,5}\d{4,5}|COMP\d{5}|CO\d{3,5})\b")
IGNORED_LINK_PREFIXES = ("mailto:", "javascript:", "#")


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def infer_course_id(title: str, url: str | None = None) -> str:
    for candidate in (title, url or ""):
        match = COURSE_CODE_RE.search(candidate.upper())
        if match:
            return match.group(1)
    parsed = urlparse(url or "")
    tail = parsed.path.rstrip("/").split("/")[-1]
    if tail:
        normalized = re.sub(r"[^A-Za-z0-9]+", "-", tail).strip("-").upper()
        if normalized:
            return normalized[:32]
    return stable_id(title, url)[:12].upper()


def classify_resource(text: str, href: str, context: str = "") -> ResourceKind:
    haystack = f"{text} {href} {context}".lower()
    if "tutorial" in haystack:
        return "tutorial"
    if any(token in haystack for token in ("exercise", "exercises", "problem sheet", "problem-sheet")):
        return "exercise"
    if any(token in haystack for token in ("lecture", "slides", "material", "note", "handout", ".pdf")):
        return "material"
    return "material"


def parse_timeline(html: str, base_url: str) -> list[CourseSummary]:
    """Parse Scientia's timeline page into deduplicated course summaries."""

    soup = BeautifulSoup(html, "html.parser")
    courses: dict[str, CourseSummary] = {}

    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href"))
        if href.startswith(IGNORED_LINK_PREFIXES):
            continue
        label = clean_text(anchor.get_text(" "))
        if not label:
            continue

        absolute = urljoin(base_url, href)
        combined = f"{label} {href}"
        looks_like_course = bool(COURSE_CODE_RE.search(combined.upper())) or any(
            token in href.lower() for token in ("module", "course", "class")
        )
        if not looks_like_course:
            continue

        course_id = infer_course_id(label, absolute)
        courses.setdefault(
            course_id,
            CourseSummary(id=course_id, title=label, url=absolute, metadata={"source": "scientia"}),
        )

    return sorted(courses.values(), key=lambda course: (course.id, course.title))


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


@dataclass(slots=True)
class ScientiaClient:
    base_url: str
    timeout: float = 30.0

    def fetch_timeline(self) -> list[CourseSummary]:
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            response = client.get(self.base_url)
            response.raise_for_status()
        return parse_timeline(response.text, self.base_url)

    def fetch_course(self, summary: CourseSummary) -> Course:
        if not summary.url:
            raise ValueError(f"Course {summary.id} does not have a source URL")
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            response = client.get(summary.url)
            response.raise_for_status()
        return parse_course_page(response.text, summary, summary.url)

