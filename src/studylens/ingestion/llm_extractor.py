from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

from anthropic import AsyncAnthropic

from studylens.config import Settings
from studylens.domain import CourseSummary
from studylens.errors import ConfigurationError, IngestionError

SYSTEM_PROMPT = (
    "You extract Imperial College course entries from a Scientia timeline HTML page. "
    "Imperial courses have alphanumeric codes (typical patterns: COMP70001, EE3-19, "
    "MATH50001, BUSI60001, BIOE70008). Each course on the timeline appears as a link "
    "to its module page. Resolve relative hrefs against the provided base URL when "
    "the link looks course-like (path contains /modules/, /classes/, /timeline/, the "
    "course code, etc.); skip navigation, login, footer, and unrelated links. Prefer "
    "the most human-readable title (e.g. anchor text) and the most specific URL. "
    "Output every distinct course exactly once via the submit_courses tool."
)

EXTRACT_TOOL: dict[str, Any] = {
    "name": "submit_courses",
    "description": (
        "Submit the deduplicated list of Imperial courses found on the Scientia "
        "timeline HTML."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "courses": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": (
                                "Course code as it appears, e.g. COMP70001. Uppercase, "
                                "no spaces."
                            ),
                        },
                        "title": {
                            "type": "string",
                            "description": "Human-readable course title.",
                        },
                        "url": {
                            "type": "string",
                            "description": (
                                "URL to the course module page. Use the absolute URL "
                                "if it appears in the HTML, otherwise the href as it "
                                "appears (the caller resolves against the base URL)."
                            ),
                        },
                    },
                    "required": ["id", "title"],
                },
            },
        },
        "required": ["courses"],
    },
}


@dataclass(slots=True)
class LLMCourseExtractor:
    """Extract Scientia courses from raw timeline HTML using Claude.

    Static `system` and the tool schema are sent with `cache_control: ephemeral`
    so consecutive auto-index calls for the same student amortise to ~10% of
    the first call's input cost.
    """

    client: AsyncAnthropic
    model: str = "claude-sonnet-4-6"
    max_html_chars: int = 300_000
    max_tokens: int = 4096

    @classmethod
    def from_settings(cls, settings: Settings) -> LLMCourseExtractor:
        if not settings.anthropic_api_key:
            raise ConfigurationError(
                "LLM course extraction requires STUDYLENS_ANTHROPIC_API_KEY"
            )
        return cls(
            client=AsyncAnthropic(api_key=settings.anthropic_api_key),
            model=settings.anthropic_model,
        )

    async def extract_courses(self, html: str, base_url: str) -> list[CourseSummary]:
        truncated = html[: self.max_html_chars]
        message = await self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=[EXTRACT_TOOL],
            tool_choice={"type": "tool", "name": "submit_courses"},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"Base URL: {base_url}\n\nTimeline HTML:\n{truncated}",
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                }
            ],
        )

        payload = _extract_tool_payload(message)
        return _payload_to_courses(payload, base_url)


def _extract_tool_payload(message: Any) -> dict[str, Any]:
    for block in getattr(message, "content", []):
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == (
            "submit_courses"
        ):
            payload = getattr(block, "input", None)
            if isinstance(payload, dict):
                return payload
    raise IngestionError(
        "LLM did not invoke submit_courses; cannot extract timeline courses"
    )


def _payload_to_courses(payload: dict[str, Any], base_url: str) -> list[CourseSummary]:
    raw = payload.get("courses") or []
    if not isinstance(raw, list):
        raise IngestionError("submit_courses payload missing 'courses' list")

    seen: set[str] = set()
    courses: list[CourseSummary] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        course_id = str(item.get("id") or "").strip()
        title = str(item.get("title") or "").strip()
        if not course_id or not title:
            continue
        course_id_upper = course_id.upper()
        if course_id_upper in seen:
            continue
        seen.add(course_id_upper)

        url_value = item.get("url")
        url = urljoin(base_url, str(url_value)) if url_value else None

        courses.append(
            CourseSummary(
                id=course_id_upper,
                title=title,
                url=url,
                metadata={"source": "scientia"},
            )
        )
    return sorted(courses, key=lambda c: (c.id, c.title))
