from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

from studylens.config import Settings
from studylens.ingestion.exams_agent import (
    DiscoveredExam,
    _academic_year_start,
    discover_past_exams,
)


def _submit_msg(papers: list[dict[str, str]]) -> AssistantMessage:
    return AssistantMessage(
        content=[
            TextBlock(text="here you go"),
            ToolUseBlock(
                id="x",
                name="mcp__exams__submit_papers",
                input={"papers": papers},
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


def _result(*, num_turns: int = 4) -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=1000,
        duration_api_ms=900,
        is_error=False,
        num_turns=num_turns,
        session_id="s",
        stop_reason="end_turn",
        total_cost_usd=0.04,
        usage=None,
        result="ok",
        structured_output=None,
        model_usage=None,
        permission_denials=None,
        deferred_tool_use=None,
        errors=None,
        api_error_status=None,
        uuid="r",
    )


def test_academic_year_start_handles_century_rollover() -> None:
    assert _academic_year_start("24-25") == 2024
    assert _academic_year_start("99-00") == 1999
    assert _academic_year_start("00-01") == 2000


def test_discover_past_exams_returns_papers_when_agent_submits(tmp_path: Any) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        imperial_username="user",
        imperial_password="pw",
        agent_max_turns=10,
    )
    submitted = [
        {
            "title": "COMP50001: Algorithm Design",
            "source_url": "https://exams.doc.ic.ac.uk/pastpapers/papers.24-25/COMP50001.pdf",
            "academic_year": "24-25",
        },
        {
            "title": "COMP50001: Algorithm Design",
            "source_url": "https://exams.doc.ic.ac.uk/pastpapers/papers.23-24/COMP50001.pdf",
            "academic_year": "23-24",
        },
        # duplicate URL — dropped
        {
            "title": "duplicate",
            "source_url": "https://exams.doc.ic.ac.uk/pastpapers/papers.24-25/COMP50001.pdf",
            "academic_year": "24-25",
        },
        # missing academic_year — dropped
        {
            "title": "no year",
            "source_url": "https://exams.doc.ic.ac.uk/pastpapers/papers.22-23/COMP50001.pdf",
            "academic_year": "",
        },
    ]
    messages = [_submit_msg(submitted), _result()]

    async def fake_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
        for message in messages:
            yield message

    report = asyncio.run(
        discover_past_exams(
            course_id="COMP50001",
            settings=settings,
            query_fn=fake_query,
        )
    )

    assert report.error is None
    assert [e.academic_year for e in report.exams] == ["24-25", "23-24"]
    assert all(isinstance(e, DiscoveredExam) for e in report.exams)


def test_discover_past_exams_reports_when_agent_never_submits(tmp_path: Any) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        imperial_username="user",
        imperial_password="pw",
        agent_max_turns=10,
    )
    chatty = AssistantMessage(
        content=[TextBlock(text="thinking…")],
        model="claude-sonnet-4-6",
        parent_tool_use_id=None,
        error=None,
        usage=None,
        message_id="m",
        stop_reason=None,
        session_id="s",
        uuid="u",
    )

    async def fake_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
        for message in [chatty, _result(num_turns=8)]:
            yield message

    report = asyncio.run(
        discover_past_exams(
            course_id="COMP50001",
            settings=settings,
            query_fn=fake_query,
        )
    )

    assert report.exams == []
    assert report.error and "submit_papers" in report.error
