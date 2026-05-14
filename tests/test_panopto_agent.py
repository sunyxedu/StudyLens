from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

from studylens.config import Settings
from studylens.errors import IngestionError
from studylens.ingestion.panopto_agent import (
    DiscoveredVideo,
    _build_tools,
    discover_course_videos,
)


class AsyncMockOK:
    async def __call__(self, *_: Any, **__: Any) -> Any:
        return None


class AsyncMockExc:
    def __init__(self, message: str) -> None:
        self.message = message

    async def __call__(self, *_: Any, **__: Any) -> Any:
        raise RuntimeError(self.message)


class AsyncMockReturn:
    def __init__(self, value: Any) -> None:
        self.value = value

    async def __call__(self, *_: Any, **__: Any) -> Any:
        return self.value


@dataclass
class FakeSession:
    """Stands in for BrowserSession; yields a MagicMock page from its context."""

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
    return Settings(data_dir=tmp_path / "data", panopto_agent_max_turns=10)


def _assistant_submit(videos: list[dict[str, str]]) -> AssistantMessage:
    return AssistantMessage(
        content=[
            TextBlock(text="Here are the videos."),
            ToolUseBlock(
                id="call_submit",
                name="mcp__panopto__submit_videos",
                input={"videos": videos},
            ),
        ],
        model="claude-sonnet-4-6",
        parent_tool_use_id=None,
        error=None,
        usage=None,
        message_id="msg_submit",
        stop_reason=None,
        session_id="sess",
        uuid="uuid_submit",
    )


def _result(*, num_turns: int = 3, is_error: bool = False, msg: str = "ok") -> ResultMessage:
    return ResultMessage(
        subtype="success" if not is_error else "error_max_turns",
        duration_ms=1000,
        duration_api_ms=900,
        is_error=is_error,
        num_turns=num_turns,
        session_id="sess",
        stop_reason="end_turn",
        total_cost_usd=0.05,
        usage=None,
        result=msg,
        structured_output=None,
        model_usage=None,
        permission_denials=None,
        deferred_tool_use=None,
        errors=None,
        api_error_status=None,
        uuid="uuid_result",
    )


async def _run(tmp_path: Any, messages: list[Any]) -> Any:
    session = FakeSession()
    settings = make_settings(tmp_path)

    async def fake_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
        for message in messages:
            yield message

    return await discover_course_videos(
        session,
        course_id="COMP70001",
        course_title="Advanced Algorithms",
        settings=settings,
        query_fn=fake_query,
    )


def test_discover_captures_videos_when_agent_submits(tmp_path: Any) -> None:
    submitted = [
        {
            "title": "Lecture 1: Intro",
            "viewer_url": (
                "https://imperial.cloud.panopto.eu/Panopto/Pages/Viewer.aspx"
                "?id=12345678-1234-1234-1234-123456789abc"
            ),
        },
        {
            "title": "Lecture 2: Recurrences",
            "viewer_url": (
                "https://imperial.cloud.panopto.eu/Panopto/Pages/Viewer.aspx"
                "?id=abcdef12-3456-7890-abcd-ef1234567890"
            ),
        },
        {  # duplicate session id is dropped
            "title": "duplicate",
            "viewer_url": (
                "https://imperial.cloud.panopto.eu/Panopto/Pages/Viewer.aspx"
                "?id=12345678-1234-1234-1234-123456789abc"
            ),
        },
    ]
    messages = [_assistant_submit(submitted), _result()]

    report = asyncio.run(_run(tmp_path, messages))

    assert report.error is None
    assert report.num_turns == 3
    assert [v.title for v in report.videos] == ["Lecture 1: Intro", "Lecture 2: Recurrences"]
    assert all(isinstance(v, DiscoveredVideo) for v in report.videos)
    assert report.videos[0].session_id == "12345678-1234-1234-1234-123456789abc"


def test_discover_reports_error_when_agent_never_submits(tmp_path: Any) -> None:
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
    messages = [chatty, _result(num_turns=10, is_error=True, msg="max turns")]

    report = asyncio.run(_run(tmp_path, messages))

    assert report.videos == []
    assert report.error is not None
    # Either "ended with error" (caught at ResultMessage) or "did not call submit_videos".
    assert "error" in report.error.lower() or "submit_videos" in report.error


def test_discover_raises_when_agent_submits_empty_list(tmp_path: Any) -> None:
    messages = [_assistant_submit([]), _result()]

    with pytest.raises(IngestionError, match="empty video list"):
        asyncio.run(_run(tmp_path, messages))


def test_runner_filters_submissions_without_session_id(tmp_path: Any) -> None:
    submitted = [
        {
            "title": "good",
            "viewer_url": (
                "https://imperial.cloud.panopto.eu/Panopto/Pages/Viewer.aspx"
                "?id=12345678-1234-1234-1234-123456789abc"
            ),
        },
        {"title": "no url", "viewer_url": ""},
        {"title": "junk url", "viewer_url": "https://example.test/no-uuid"},
    ]
    messages = [_assistant_submit(submitted), _result()]

    report = asyncio.run(_run(tmp_path, messages))

    assert [v.title for v in report.videos] == ["good"]


def _tools_by_name(page: Any) -> dict[str, Any]:
    tools = _build_tools(page, "https://imperial.cloud.panopto.eu/Panopto/Pages/Sessions/List.aspx")
    return {t.name: t for t in tools}


def test_goto_tool_wraps_playwright_errors(tmp_path: Any) -> None:
    page = MagicMock()
    page.goto = AsyncMockExc("network down")
    page.wait_for_load_state = AsyncMockOK()

    tools = _tools_by_name(page)

    result = asyncio.run(tools["goto"].handler({"url": "https://x"}))

    assert result["is_error"] is True
    assert "network down" in result["content"][0]["text"]


def test_list_links_returns_anchor_summary(tmp_path: Any) -> None:
    page = MagicMock()
    page.locator.return_value.evaluate_all = AsyncMockReturn(
        [
            {"text": "Shared with me", "href": "https://panopto.test/shared"},
            {"text": "COMP70001 Advanced Algorithms", "href": "https://panopto.test/folder/123"},
        ]
    )

    tools = _tools_by_name(page)

    result = asyncio.run(tools["list_links"].handler({}))

    text = result["content"][0]["text"]
    assert "COMP70001 Advanced Algorithms" in text
    assert "https://panopto.test/folder/123" in text


def test_runner_only_captures_first_submit_call(tmp_path: Any) -> None:
    """If the agent calls submit_videos twice, the second call is ignored."""
    first = [
        {
            "title": "first",
            "viewer_url": (
                "https://imperial.cloud.panopto.eu/Panopto/Pages/Viewer.aspx"
                "?id=12345678-1234-1234-1234-123456789abc"
            ),
        },
    ]
    second = [
        {
            "title": "second",
            "viewer_url": (
                "https://imperial.cloud.panopto.eu/Panopto/Pages/Viewer.aspx"
                "?id=abcdef12-3456-7890-abcd-ef1234567890"
            ),
        },
    ]
    messages = [_assistant_submit(first), _assistant_submit(second), _result()]

    report = asyncio.run(_run(tmp_path, messages))

    assert [v.title for v in report.videos] == ["first"]
