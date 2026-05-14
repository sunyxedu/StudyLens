from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

from studylens.config import Settings
from studylens.ingestion.edstem_agent import (
    EdStemCourse,
    discover_edstem_courses,
    extract_course_code,
)


@dataclass
class FakeSession:
    page_obj: Any = field(default_factory=MagicMock)

    def page(self) -> Any:
        page_obj = self.page_obj

        class _Ctx:
            async def __aenter__(self_inner: Any) -> Any:
                return page_obj

            async def __aexit__(self_inner: Any, *_: Any) -> None:
                return None

        return _Ctx()


def make_settings(tmp_path: Any) -> Settings:
    return Settings(data_dir=tmp_path / "data", agent_max_turns=10)


def _submit_msg(courses: list[dict[str, str]]) -> AssistantMessage:
    return AssistantMessage(
        content=[
            TextBlock(text="Here is the list."),
            ToolUseBlock(
                id="call_submit",
                name="mcp__edstem__submit_courses",
                input={"courses": courses},
            ),
        ],
        model="claude-sonnet-4-6",
        parent_tool_use_id=None,
        error=None,
        usage=None,
        message_id="m",
        stop_reason=None,
        session_id="s",
        uuid="u",
    )


def _result(*, num_turns: int = 3, is_error: bool = False, msg: str = "ok") -> ResultMessage:
    return ResultMessage(
        subtype="success" if not is_error else "error_max_turns",
        duration_ms=1000,
        duration_api_ms=900,
        is_error=is_error,
        num_turns=num_turns,
        session_id="s",
        stop_reason="end_turn",
        total_cost_usd=0.02,
        usage=None,
        result=msg,
        structured_output=None,
        model_usage=None,
        permission_denials=None,
        deferred_tool_use=None,
        errors=None,
        api_error_status=None,
        uuid="r",
    )


async def _run(tmp_path: Any, messages: list[Any]) -> Any:
    session = FakeSession()
    settings = make_settings(tmp_path)

    async def fake_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
        for message in messages:
            yield message

    return await discover_edstem_courses(session, settings, query_fn=fake_query)


def test_extract_course_code_handles_imperial_formats() -> None:
    assert extract_course_code("COMP 50002: Software Engineering Design") == "COMP50002"
    assert extract_course_code("COMP70001 Advanced Algorithms") == "COMP70001"
    assert extract_course_code("MATH50001 — Analysis") == "MATH50001"
    assert extract_course_code("BIOE70008: Modelling") == "BIOE70008"
    # Lab-stream sub-codes survive: 50007.1 vs 50007.2 are different modules
    assert extract_course_code("COMP 50007.1: Computing Practical 2 (Lab)") == "COMP50007.1"
    assert extract_course_code("Welcome to the dashboard") is None
    assert extract_course_code("Course 101 — General") is None


def test_discover_returns_courses_keeping_only_coded_titles(tmp_path: Any) -> None:
    submitted = [
        {"title": "COMP 50002: Software Engineering Design", "edstem_url": "/courses/1"},
        {"title": "COMP70001 Advanced Algorithms", "edstem_url": "/courses/2"},
        {"title": "Announcements", "edstem_url": "/announcements"},
        {"title": "Welcome 2025/26"},  # no code → dropped
        # duplicate code → ignored
        {"title": "COMP 50002 (also)", "edstem_url": "/courses/1-dup"},
    ]
    messages = [_submit_msg(submitted), _result()]

    report = asyncio.run(_run(tmp_path, messages))

    assert report.error is None
    assert [c.code for c in report.courses] == ["COMP50002", "COMP70001"]
    assert all(isinstance(c, EdStemCourse) for c in report.courses)
    assert "Announcements" in report.dropped_titles
    assert "Welcome 2025/26" in report.dropped_titles


def test_discover_reports_error_when_agent_never_submits(tmp_path: Any) -> None:
    chatty = AssistantMessage(
        content=[TextBlock(text="hmm…")],
        model="claude-sonnet-4-6",
        parent_tool_use_id=None,
        error=None,
        usage=None,
        message_id="m",
        stop_reason=None,
        session_id="s",
        uuid="u",
    )
    messages = [chatty, _result(num_turns=10, is_error=True, msg="max turns")]

    report = asyncio.run(_run(tmp_path, messages))

    assert report.courses == []
    assert report.error is not None


def test_runner_only_captures_first_submit_call(tmp_path: Any) -> None:
    first = [{"title": "COMP 50002: SED", "edstem_url": "/x"}]
    second = [{"title": "COMP 70001: Algos", "edstem_url": "/y"}]
    messages = [_submit_msg(first), _submit_msg(second), _result()]

    report = asyncio.run(_run(tmp_path, messages))

    assert [c.code for c in report.courses] == ["COMP50002"]
