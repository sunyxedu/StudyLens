from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from studylens.config import Settings
from studylens.errors import ConfigurationError, IngestionError
from studylens.ingestion.llm_extractor import LLMCourseExtractor


@dataclass
class FakeToolUseBlock:
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


@dataclass
class FakeMessage:
    content: list[Any]


class FakeMessages:
    def __init__(self, response: FakeMessage) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> FakeMessage:
        self.calls.append(kwargs)
        return self.response


class FakeClient:
    def __init__(self, response: FakeMessage) -> None:
        self.messages = FakeMessages(response)


def make_response(courses: list[dict[str, Any]]) -> FakeMessage:
    block = FakeToolUseBlock(name="submit_courses", input={"courses": courses})
    return FakeMessage(content=[block])


def test_from_settings_requires_anthropic_key(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", anthropic_api_key=None)

    with pytest.raises(ConfigurationError, match="STUDYLENS_ANTHROPIC_API_KEY"):
        LLMCourseExtractor.from_settings(settings)


def test_extract_courses_parses_tool_use_into_course_summaries() -> None:
    response = make_response(
        [
            {
                "id": "comp70001",
                "title": "Advanced Algorithms",
                "url": "/2526/modules/COMP70001",
            },
            {
                "id": "MATH50001",
                "title": "Analysis",
                "url": "https://scientia.test/MATH50001",
            },
            # Duplicate IDs collapse case-insensitively, missing fields drop.
            {"id": "MATH50001", "title": "duplicate"},
            {"id": "", "title": "no id"},
        ]
    )
    extractor = LLMCourseExtractor(client=FakeClient(response), model="claude-sonnet-4-6")

    courses = asyncio.run(extractor.extract_courses("<html/>", "https://scientia.test/"))

    ids = [course.id for course in courses]
    titles = [course.title for course in courses]
    urls = [course.url for course in courses]
    assert ids == ["COMP70001", "MATH50001"]
    assert titles == ["Advanced Algorithms", "Analysis"]
    assert urls == [
        "https://scientia.test/2526/modules/COMP70001",
        "https://scientia.test/MATH50001",
    ]


def test_extract_courses_uses_caching_and_tool_choice_in_request() -> None:
    response = make_response([{"id": "COMP70001", "title": "Adv Algos"}])
    client = FakeClient(response)
    extractor = LLMCourseExtractor(client=client, model="claude-sonnet-4-6", max_html_chars=10)

    asyncio.run(extractor.extract_courses("a" * 50_000, "https://scientia.test/"))

    [call] = client.messages.calls
    assert call["model"] == "claude-sonnet-4-6"
    assert call["tool_choice"] == {"type": "tool", "name": "submit_courses"}
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert call["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    # HTML truncated to max_html_chars.
    body = call["messages"][0]["content"][0]["text"]
    assert "aaaaaaaaaa" in body
    assert "a" * 50_000 not in body


def test_extract_courses_raises_when_tool_call_missing() -> None:
    response = FakeMessage(content=[])  # no tool_use block at all
    extractor = LLMCourseExtractor(client=FakeClient(response), model="claude-sonnet-4-6")

    with pytest.raises(IngestionError, match="submit_courses"):
        asyncio.run(extractor.extract_courses("<html/>", "https://scientia.test/"))
