from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from studylens.domain import Resource
from studylens.errors import ConfigurationError


def parse_exam_links(html: str, base_url: str, course_id: str) -> list[Resource]:
    soup = BeautifulSoup(html, "html.parser")
    resources: list[Resource] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href"))
        label = " ".join(anchor.get_text(" ").split()) or href.rsplit("/", 1)[-1]
        haystack = f"{href} {label}".lower()
        if course_id.lower() not in haystack and not any(
            token in haystack for token in ("exam", "paper")
        ):
            continue
        if not href.lower().endswith((".pdf", ".html", ".htm")):
            continue
        absolute = urljoin(base_url, href)
        if absolute in seen:
            continue
        seen.add(absolute)
        resources.append(
            Resource(
                course_id=course_id,
                title=label,
                kind="past_exam",
                source_url=absolute,
                metadata={"source": "exams"},
            )
        )
    return resources


@dataclass(slots=True)
class ExamsClient:
    base_url: str
    username: str | None = None
    password: str | None = None
    timeout: float = 30.0

    def require_credentials(self) -> None:
        if not self.username or not self.password:
            raise ConfigurationError(
                "Exams access requires STUDYLENS_IMPERIAL_USERNAME and STUDYLENS_IMPERIAL_PASSWORD"
            )

    def fetch_course_exam_links(self, course_id: str) -> list[Resource]:
        self.require_credentials()
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            response = client.get(self.base_url, auth=(self.username, self.password))
            response.raise_for_status()
        return parse_exam_links(response.text, self.base_url, course_id)
