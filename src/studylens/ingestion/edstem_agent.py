"""Claude Agent SDK driver for EdStem course discovery.

Lists the student's enrolled courses for the current term by navigating
EdStem's dashboard. EdStem is the right source of truth for "which
courses am I actually taking right now" because Scientia's timeline
includes a lot of historical / inherited noise.

EdStem course cards use a fixed-ish title format like
"COMP 50002: Software Engineering Design" — we let the agent collect
titles + URLs, then peel the `COMP50002` code off in the runner. Entries
without a recognisable course code are dropped (the spec said so).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

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
from studylens.ingestion.browser_session import BrowserSession

# Match prefixes like "COMP 50002", "MATH50001", "COMPM0101", and lab-stream
# variants like "COMP 50007.1". Preserving the `.N` suffix matters: parallel
# streams (50007.1 / 50007.2) are separate Scientia modules and the wrong one
# is empty for the student.
COURSE_CODE_RE = re.compile(
    r"\b(?P<dept>[A-Z]{2,5})\s*[-\s]?\s*(?P<num>\d{3,5}(?:\.\d+)?)\b"
)
PAGE_TEXT_LIMIT = 2_000
MAX_LINKS = 80


@dataclass(frozen=True, slots=True)
class EdStemCourse:
    code: str
    title: str
    edstem_url: str | None = None


@dataclass(frozen=True, slots=True)
class EdStemDiscoveryReport:
    courses: list[EdStemCourse]
    dropped_titles: list[str]
    num_turns: int
    total_cost_usd: float
    stop_reason: str | None
    error: str | None = None


QueryFn = Callable[..., Any]


def _normalise_code(raw: str) -> str:
    """`COMP 50002` → `COMP50002`."""
    return re.sub(r"[\s\-]+", "", raw).upper()


def extract_course_code(title: str) -> str | None:
    match = COURSE_CODE_RE.search(title.upper())
    if not match:
        return None
    return _normalise_code(f"{match.group('dept')}{match.group('num')}")


def _system_prompt(edstem_base_url: str) -> str:
    return (
        "You are listing the student's enrolled courses on EdStem for the CURRENT "
        "academic year by looking at the dashboard.\n\n"
        "Tools:\n"
        "- goto(url): navigate to a URL\n"
        "- page_summary(): URL, title, and a short text snapshot of the page\n"
        "- list_links(): every visible anchor on the current page (text + href)\n"
        "- click_text(text): click the first element whose visible text contains the substring\n"
        "- submit_courses(courses): TERMINAL — call once with the final list\n\n"
        "Strategy:\n"
        f"1. goto {edstem_base_url} to reach the dashboard.\n"
        "2. Use page_summary() / list_links() to find the course cards.\n"
        "3. Each course card has a human title like 'COMP 50002: Software Engineering Design'. "
        "Collect every course visible on the dashboard for the current term.\n"
        "4. For each, record the exact title text and the URL the card links to.\n"
        "5. Skip any item that is obviously not a course (announcements, settings, profile, "
        "archived/inactive courses).\n"
        "6. Call submit_courses with the full list. Each item must have title (str) and may "
        "have edstem_url (str).\n\n"
        "Be efficient: a couple of tool calls is usually enough. Do not navigate inside "
        "individual courses."
    )


def _build_tools(page: Any, edstem_base_url: str) -> list[Any]:
    """Tools as closures over a live Playwright Page bound to this run."""

    @tool("goto", "Navigate to a URL inside EdStem.", {"url": str})
    async def goto(args: dict[str, Any]) -> dict[str, Any]:
        try:
            await page.goto(args["url"], wait_until="domcontentloaded", timeout=15_000)
            await page.wait_for_load_state("networkidle", timeout=5_000)
            return {"content": [{"type": "text", "text": f"At {page.url}"}]}
        except Exception as exc:
            return {
                "content": [{"type": "text", "text": f"Navigation failed: {exc}"}],
                "is_error": True,
            }

    @tool(
        "page_summary",
        "Return the current URL, title, and a short text snapshot.",
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
        "List visible anchors on the current page as text + href.",
        {},
    )
    async def list_links(args: dict[str, Any]) -> dict[str, Any]:
        try:
            raw = await page.locator("a").evaluate_all(
                "(nodes) => nodes.slice(0, 200).map(a => ({"
                "  text: (a.innerText || '').trim().slice(0, 200),"
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
                "content": [
                    {"type": "text", "text": f"Clicked {args['text']!r}; at {page.url}"}
                ]
            }
        except Exception as exc:
            return {
                "content": [{"type": "text", "text": f"click_text failed: {exc}"}],
                "is_error": True,
            }

    @tool(
        "submit_courses",
        (
            "Terminal tool. Submit the final list of courses visible on the dashboard. "
            "Each item: title (str, required), edstem_url (str, optional)."
        ),
        {
            "type": "object",
            "properties": {
                "courses": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "edstem_url": {"type": "string"},
                        },
                        "required": ["title"],
                    },
                }
            },
            "required": ["courses"],
        },
    )
    async def submit_courses(args: dict[str, Any]) -> dict[str, Any]:
        # Runner is the source of truth; this handler is acknowledgement only.
        courses, dropped = _collect_courses(args.get("courses") or [], edstem_base_url)
        note = f"Received {len(courses)} courses"
        if dropped:
            note += f"; dropped {len(dropped)} without a course code"
        return {"content": [{"type": "text", "text": note + "."}]}

    return [goto, page_summary, list_links, click_text, submit_courses]


def _collect_courses(
    raw_courses: list[Any],
    edstem_base_url: str,
) -> tuple[list[EdStemCourse], list[str]]:
    accepted: list[EdStemCourse] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for item in raw_courses:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        code = extract_course_code(title)
        if not code:
            dropped.append(title)
            continue
        if code in seen:
            continue
        seen.add(code)
        url_value = item.get("edstem_url")
        url = urljoin(edstem_base_url, str(url_value)) if url_value else None
        accepted.append(EdStemCourse(code=code, title=title, edstem_url=url))
    return accepted, dropped


async def discover_edstem_courses(
    session: BrowserSession,
    settings: Settings,
    *,
    query_fn: QueryFn | None = None,
) -> EdStemDiscoveryReport:
    """Run a Claude Agent SDK loop to list enrolled EdStem courses.

    `query_fn` defaults to claude_agent_sdk.query; tests pass a fake to
    avoid spawning the real `claude` CLI subprocess.
    """

    runner: QueryFn = query_fn or query
    edstem_base_url = str(settings.edstem_base_url)
    captured: list[EdStemCourse] = []
    dropped: list[str] = []

    async with session.page() as page:
        tools = _build_tools(page, edstem_base_url)
        server = create_sdk_mcp_server(name="edstem", version="0.1.0", tools=tools)
        allowed = [f"mcp__edstem__{t.name}" for t in tools]

        options = ClaudeAgentOptions(
            mcp_servers={"edstem": server},
            allowed_tools=allowed,
            disallowed_tools=[],
            max_turns=settings.agent_max_turns,
            model=settings.agent_model,
            permission_mode="bypassPermissions",
            system_prompt=_system_prompt(edstem_base_url),
        )

        task = (
            "List every course the student is enrolled in on the EdStem dashboard for the "
            "current academic year. Submit them via submit_courses."
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
                            and block.name == "mcp__edstem__submit_courses"
                            and not submit_seen
                        ):
                            submit_seen = True
                            accepted, drops = _collect_courses(
                                (block.input or {}).get("courses") or [],
                                edstem_base_url,
                            )
                            captured.extend(accepted)
                            dropped.extend(drops)
                elif isinstance(message, ResultMessage):
                    num_turns = message.num_turns
                    cost = float(message.total_cost_usd or 0.0)
                    stop_reason = message.stop_reason
                    if message.is_error and not submit_seen:
                        return EdStemDiscoveryReport(
                            courses=captured,
                            dropped_titles=dropped,
                            num_turns=num_turns,
                            total_cost_usd=cost,
                            stop_reason=stop_reason,
                            error=f"Agent ended with error: {message.result or 'unknown'}",
                        )
        except Exception as exc:  # pragma: no cover - SDK / CLI failures.
            return EdStemDiscoveryReport(
                courses=captured,
                dropped_titles=dropped,
                num_turns=num_turns,
                total_cost_usd=cost,
                stop_reason=stop_reason,
                error=f"Agent run raised: {exc}",
            )

    if not submit_seen:
        return EdStemDiscoveryReport(
            courses=captured,
            dropped_titles=dropped,
            num_turns=num_turns,
            total_cost_usd=cost,
            stop_reason=stop_reason,
            error=f"Agent did not call submit_courses before stopping at {num_turns} turns.",
        )

    return EdStemDiscoveryReport(
        courses=captured,
        dropped_titles=dropped,
        num_turns=num_turns,
        total_cost_usd=cost,
        stop_reason=stop_reason,
    )
