"""Scientia HTML parsing.

Scientia renders a SPA, so callers MUST hand us the fully-rendered HTML
(see BrowserSession.fetch_rendered_html). Even then, the pages are noisy:
the body has a top nav, sidebar, footer, breadcrumb links, and a 'download
all as zip' shortcut, all as <a> tags. Real downloadable resources are the
only anchors whose href goes through Scientia's `/external-resource?url=...`
proxy — the proxy URL itself returns the SPA shell, so we extract the
inner URL from the `url=` query param and download that directly.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from bs4 import BeautifulSoup

from studylens.domain import Course, CourseSummary, Resource
from studylens.domain.models import ResourceKind

EXTERNAL_RESOURCE_PATH = "/external-resource"
TRAILING_FILE_RE = re.compile(r"\s*file\s*$", re.IGNORECASE)


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _extract_real_url(wrapper_href: str, base_url: str) -> str | None:
    """Pull the `?url=...` payload out of a Scientia external-resource link."""
    absolute = urljoin(base_url, wrapper_href)
    parsed = urlparse(absolute)
    if EXTERNAL_RESOURCE_PATH not in parsed.path:
        return None
    candidates = parse_qs(parsed.query).get("url")
    if not candidates:
        return None
    return urljoin(base_url, unquote(candidates[0]))


def _anchor_title(anchor: object, fallback_url: str) -> str:
    raw = clean_text(anchor.get_text(" ") if hasattr(anchor, "get_text") else "")  # type: ignore[arg-type]
    raw = TRAILING_FILE_RE.sub("", raw)
    if raw:
        return raw
    return Path(urlparse(fallback_url).path).name or "resource"


def parse_course_tab(
    html: str,
    base_url: str,
    *,
    course_id: str,
    kind: ResourceKind,
) -> list[Resource]:
    """Extract real downloadable resources from a single Scientia tab page."""

    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    resources: list[Resource] = []
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href"))
        real = _extract_real_url(href, base_url)
        if real is None or real in seen:
            continue
        seen.add(real)
        title = _anchor_title(anchor, real)
        resources.append(
            Resource(
                course_id=course_id,
                title=title,
                kind=kind,
                source_url=real,
                metadata={"source": "scientia"},
            )
        )
    return resources


def parse_course_page(html: str, summary: CourseSummary, base_url: str) -> Course:
    """Single-tab parse, tagging everything as `material`.

    Kept for callers (and tests) that pass one HTML blob without specifying
    a tab. Production auto-index uses `parse_course_tab` once per tab.
    """
    materials = parse_course_tab(html, base_url, course_id=summary.id, kind="material")
    return Course(
        id=summary.id,
        title=summary.title,
        year=summary.year,
        source_url=summary.url,
        materials=materials,
        exercises=[],
        tutorials=[],
        metadata=summary.metadata,
    )


def derive_tab_urls(course_url: str) -> dict[ResourceKind, str]:
    """Given any Scientia course URL, return {kind: tab_url} for all 3 tabs."""

    base = course_url.rstrip("/")
    for suffix in ("/materials", "/exercises", "/tutorials"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return {
        "material": f"{base}/materials",
        "exercise": f"{base}/exercises",
        "tutorial": f"{base}/tutorials",
    }
