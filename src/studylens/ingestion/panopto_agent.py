"""Claude Agent SDK driver for Panopto folder navigation.

Background: Panopto's text search is global, not folder-scoped, so a naive
search for "{course_id} {course_title}" returns whatever was ever shared
with the student under that name — wrong year, unrelated meetings,
duplicates from older instances. To get the actual course folder for the
current year, we let an agent open Panopto, look at what's there, click
through, and list the right videos.

The agent uses a small fixed set of tools that drive a single Playwright
Page (captured by closure). A `submit_videos` terminal tool is the only
success signal; if the loop ends without it, we report failure.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
    tool,
)

from studylens.config import Settings
from studylens.errors import IngestionError
from studylens.ingestion.browser_session import BrowserSession

SESSION_ID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
PAGE_TEXT_LIMIT = 2_000
MAX_LINKS = 80


@dataclass(frozen=True, slots=True)
class DiscoveredVideo:
    """Plain shape returned by the agent's submit_videos terminal tool."""

    title: str
    viewer_url: str
    session_id: str


@dataclass(frozen=True, slots=True)
class AgentRunReport:
    """Telemetry from one agent run, useful for surfacing in the UI/logs."""

    videos: list[DiscoveredVideo]
    num_turns: int
    total_cost_usd: float
    stop_reason: str | None
    error: str | None = None


QueryFn = Callable[..., Any]  # claude_agent_sdk.query, swappable in tests


def _extract_session_id(url: str) -> str | None:
    """Pull a Panopto session UUID out of a viewer URL."""
    match = SESSION_ID_RE.search(url)
    return match.group(0).lower() if match else None


def _system_prompt(panopto_base_url: str, course_id: str, course_title: str) -> str:
    return (
        "You are navigating Imperial College's Panopto video portal to find the "
        f"official lecture-capture folder for course {course_id} ({course_title}) "
        "for the CURRENT academic year, and list every video session inside it.\n\n"
        "Tools available:\n"
        "- goto(url): navigate to a URL\n"
        "- page_summary(): get the current URL, title, and a short text snapshot of the page\n"
        "- list_links(): get every visible anchor (text + href) on the current page\n"
        "- click_text(text): click the first element whose visible text contains the substring\n"
        "- search_panopto(query): fill Panopto's search box and submit\n"
        "- submit_videos(videos): TERMINAL — call this exactly once with the final list\n\n"
        "Strategy:\n"
        f"1. goto {panopto_base_url}#isSharedWithMe=true to reach Shared with Me.\n"
        "2. Use page_summary() / list_links() to see what folders exist.\n"
        f"3. Find the folder whose name matches '{course_id}' or '{course_title}'. If "
        "multiple academic years exist, prefer the one without an old-year suffix.\n"
        "4. Enter it (click_text the folder name).\n"
        "5. List every video session in the folder. Each video has a viewer URL that "
        "contains a session UUID (e.g. .../Viewer.aspx?id=abcd1234-...).\n"
        "6. If folder navigation doesn't work, fall back to search_panopto with the "
        "course code and pick results whose URL is under the course folder.\n"
        "7. Call submit_videos with the complete list. Each item must have a title and "
        "a viewer_url containing a session UUID. Deduplicate by session UUID.\n\n"
        "Be decisive: do not over-explore. If you have a plausible folder, list and submit."
    )


def _build_tools(page: Any, panopto_base_url: str) -> list[Any]:
    """Define agent tools as closures over a live Playwright Page.

    Closure capture is required because the Agent SDK invokes tools with
    only the JSON args dict — there's no per-call context channel. Putting
    tools inside this factory function keeps `page` bound to each agent run.
    """

    @tool("goto", "Navigate to a URL inside Panopto.", {"url": str})
    async def goto(args: dict[str, Any]) -> dict[str, Any]:
        try:
            await page.goto(args["url"], wait_until="domcontentloaded", timeout=15_000)
            await page.wait_for_load_state("networkidle", timeout=5_000)
            return {
                "content": [{"type": "text", "text": f"At {page.url}"}],
            }
        except Exception as exc:
            return {
                "content": [{"type": "text", "text": f"Navigation failed: {exc}"}],
                "is_error": True,
            }

    @tool(
        "page_summary",
        "Return the current URL, page title, and a short text snapshot.",
        {},
    )
    async def page_summary(args: dict[str, Any]) -> dict[str, Any]:
        try:
            title = await page.title()
            body_text = await page.locator("body").inner_text(timeout=4_000)
            snippet = body_text.strip()[:PAGE_TEXT_LIMIT]
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"URL: {page.url}\nTitle: {title}\n\n{snippet}",
                    }
                ]
            }
        except Exception as exc:
            return {
                "content": [{"type": "text", "text": f"page_summary failed: {exc}"}],
                "is_error": True,
            }

    @tool(
        "list_links",
        "List visible anchors on the current page as JSON.",
        {},
    )
    async def list_links(args: dict[str, Any]) -> dict[str, Any]:
        try:
            raw = await page.locator("a").evaluate_all(
                "(nodes) => nodes.slice(0, 200).map(a => ({"
                "  text: (a.innerText || '').trim().slice(0, 120),"
                "  href: a.href"
                "})).filter(x => x.href)"
            )
            trimmed = raw[:MAX_LINKS]
            lines = [f"- {item['text']!r} -> {item['href']}" for item in trimmed]
            return {
                "content": [
                    {"type": "text", "text": f"{len(trimmed)} links:\n" + "\n".join(lines)}
                ]
            }
        except Exception as exc:
            return {
                "content": [{"type": "text", "text": f"list_links failed: {exc}"}],
                "is_error": True,
            }

    @tool(
        "click_text",
        "Click the first element whose visible text contains the given substring.",
        {"text": str},
    )
    async def click_text(args: dict[str, Any]) -> dict[str, Any]:
        try:
            await page.get_by_text(args["text"], exact=False).first.click(timeout=8_000)
            await page.wait_for_load_state("networkidle", timeout=5_000)
            return {
                "content": [{"type": "text", "text": f"Clicked {args['text']!r}; at {page.url}"}]
            }
        except Exception as exc:
            return {
                "content": [{"type": "text", "text": f"click_text failed: {exc}"}],
                "is_error": True,
            }

    @tool(
        "search_panopto",
        "Fill Panopto's search box and submit. Use only as a fallback.",
        {"query": str},
    )
    async def search_panopto(args: dict[str, Any]) -> dict[str, Any]:
        try:
            textbox = page.get_by_role("textbox").first
            await textbox.fill(args["query"], timeout=5_000)
            await page.keyboard.press("Enter")
            await page.wait_for_load_state("networkidle", timeout=8_000)
            return {
                "content": [
                    {"type": "text", "text": f"Searched for {args['query']!r}; at {page.url}"}
                ]
            }
        except Exception as exc:
            return {
                "content": [{"type": "text", "text": f"search_panopto failed: {exc}"}],
                "is_error": True,
            }

    @tool(
        "submit_videos",
        (
            "Terminal tool. Submit the final list of videos extracted from the course "
            "folder. Each item: title (str), viewer_url (str). Call this exactly once."
        ),
        {
            "type": "object",
            "properties": {
                "videos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "viewer_url": {"type": "string"},
                        },
                        "required": ["title", "viewer_url"],
                    },
                }
            },
            "required": ["videos"],
        },
    )
    async def submit_videos(args: dict[str, Any]) -> dict[str, Any]:
        # The runner is the source of truth for captured_videos (it reads
        # block.input directly). This handler only confirms to the agent that
        # the submission was received.
        accepted, _ = _collect_videos(args.get("videos") or [], panopto_base_url)
        return {
            "content": [
                {"type": "text", "text": f"Received {len(accepted)} videos. Done."}
            ]
        }

    return [goto, page_summary, list_links, click_text, search_panopto, submit_videos]


def _collect_videos(
    raw_videos: list[Any],
    panopto_base_url: str,
) -> tuple[list[DiscoveredVideo], list[Any]]:
    accepted: list[DiscoveredVideo] = []
    rejected: list[Any] = []
    seen: set[str] = set()
    for item in raw_videos:
        if not isinstance(item, dict):
            rejected.append(item)
            continue
        title = str(item.get("title") or "").strip()
        viewer_url = str(item.get("viewer_url") or "").strip()
        if not title or not viewer_url:
            rejected.append(item)
            continue
        absolute = urljoin(panopto_base_url, viewer_url)
        if urlparse(absolute).scheme not in {"http", "https"}:
            rejected.append(item)
            continue
        session_id = _extract_session_id(absolute)
        if not session_id or session_id in seen:
            rejected.append(item)
            continue
        seen.add(session_id)
        accepted.append(
            DiscoveredVideo(title=title, viewer_url=absolute, session_id=session_id)
        )
    return accepted, rejected


async def discover_course_videos(
    session: BrowserSession,
    course_id: str,
    course_title: str,
    settings: Settings,
    *,
    query_fn: QueryFn | None = None,
) -> AgentRunReport:
    """Run a Claude Agent SDK loop to find a course's Panopto videos.

    `query_fn` defaults to claude_agent_sdk.query; tests pass a fake to avoid
    spawning the real Claude Code CLI subprocess.
    """

    runner: QueryFn = query_fn or query
    panopto_base_url = str(settings.panopto_base_url)
    captured: list[DiscoveredVideo] = []

    async with session.page() as page:
        tools = _build_tools(page, panopto_base_url)
        server = create_sdk_mcp_server(name="panopto", version="0.1.0", tools=tools)
        allowed = [f"mcp__panopto__{t.name}" for t in tools]

        options = ClaudeAgentOptions(
            mcp_servers={"panopto": server},
            allowed_tools=allowed,
            disallowed_tools=[],
            max_turns=settings.panopto_agent_max_turns,
            model=settings.panopto_agent_model,
            permission_mode="bypassPermissions",
            system_prompt=_system_prompt(panopto_base_url, course_id, course_title),
        )

        task = (
            f"Find the Panopto folder for course {course_id} ({course_title}) for the "
            "current academic year and submit every video session inside it via "
            "submit_videos."
        )

        num_turns = 0
        cost = 0.0
        stop_reason: str | None = None
        submit_seen = False

        try:
            async for message in runner(prompt=task, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if (
                            isinstance(block, ToolUseBlock)
                            and block.name == "mcp__panopto__submit_videos"
                            and not submit_seen
                        ):
                            submit_seen = True
                            accepted, _ = _collect_videos(
                                (block.input or {}).get("videos") or [],
                                panopto_base_url,
                            )
                            captured.extend(accepted)
                elif isinstance(message, ResultMessage):
                    num_turns = message.num_turns
                    cost = float(message.total_cost_usd or 0.0)
                    stop_reason = message.stop_reason
                    if message.is_error and not submit_seen:
                        return AgentRunReport(
                            videos=captured,
                            num_turns=num_turns,
                            total_cost_usd=cost,
                            stop_reason=stop_reason,
                            error=f"Agent ended with error: {message.result or 'unknown'}",
                        )
        except Exception as exc:  # pragma: no cover - SDK / CLI failures.
            return AgentRunReport(
                videos=captured,
                num_turns=num_turns,
                total_cost_usd=cost,
                stop_reason=stop_reason,
                error=f"Agent run raised: {exc}",
            )

    if not submit_seen:
        return AgentRunReport(
            videos=captured,
            num_turns=num_turns,
            total_cost_usd=cost,
            stop_reason=stop_reason,
            error=(
                "Agent did not call submit_videos before the loop ended. "
                f"Reached {num_turns} turns."
            ),
        )

    if not captured:
        raise IngestionError(
            f"Panopto agent submitted an empty video list for {course_id}"
        )

    return AgentRunReport(
        videos=captured,
        num_turns=num_turns,
        total_cost_usd=cost,
        stop_reason=stop_reason,
    )
