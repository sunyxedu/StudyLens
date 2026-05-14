"""Claude Agent SDK driver for past-exam discovery on exams.doc.ic.ac.uk.

The site itself is a flat server-rendered index — a few years' archives
linked off the root, the rest behind /archive.html — and the file naming
is super regular (`pastpapers/papers.YY-YY/COMP50001.pdf`). Strictly,
this could be done with regex (and was, in earlier blocks); the user
prefers all crawling to be agent-driven for robustness, so this module
matches the scientia_agent / panopto_agent shape.

The agent walks the year archives, picks the most recent N (default 5),
filters anchors that mention the course code, and hands them back via a
submit_papers terminal tool. Authentication is HTTP Basic (Imperial
SSO doesn't gate this site), so we drive a vanilla httpx-backed Page —
not the SSO BrowserSession used elsewhere.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
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
from studylens.errors import ConfigurationError

YEAR_PATH_RE = re.compile(r"pastpapers/papers\.\d{2}-\d{2}")
YEAR_LABEL_RE = re.compile(r"papers\.(\d{2})-(\d{2})")
MAX_LINKS = 600
DEFAULT_MAX_YEARS = 5


@dataclass(frozen=True, slots=True)
class DiscoveredExam:
    title: str
    source_url: str
    academic_year: str  # e.g. "24-25"


@dataclass(frozen=True, slots=True)
class ExamsDiscoveryReport:
    exams: list[DiscoveredExam]
    rejected: list[str]
    num_turns: int
    total_cost_usd: float
    stop_reason: str | None
    error: str | None = None


QueryFn = Callable[..., Any]


def _academic_year_start(year_label: str) -> int:
    yy = int(year_label.split("-")[0])
    return 2000 + yy if yy < 50 else 1900 + yy


def _system_prompt(base_url: str, course_id: str, max_years: int) -> str:
    return (
        f"You are finding past exam papers for course {course_id} on Imperial's "
        f"exams site ({base_url}).\n\n"
        "Tools:\n"
        "- goto(url): navigate to a URL on this site\n"
        "- page_summary(): URL + a short text snapshot of the current page\n"
        "- list_year_archives(): list /pastpapers/papers.YY-YY links visible on "
        "the current page (root or /archive.html)\n"
        f"- list_course_papers(course_id): list PDFs on the current year-archive "
        f"page whose URL or text mentions {course_id}\n"
        "- submit_papers(papers): TERMINAL — call once with the final list\n\n"
        "Strategy:\n"
        f"1. goto {base_url} then list_year_archives() — these are recent years.\n"
        f"2. If fewer than {max_years} years appeared, goto {base_url}archive.html "
        "and list_year_archives() again to pick up older ones.\n"
        f"3. Pick the {max_years} most recent academic years.\n"
        "4. For each, goto the archive URL and call "
        f"list_course_papers({course_id!r}) to grab that year's paper.\n"
        "5. submit_papers with each item: title (str, the anchor text), "
        "source_url (str, ABSOLUTE), academic_year (e.g. '24-25'). Skip "
        "duplicates by source_url.\n\n"
        "Be efficient: ~2 + max_years navigations is the happy path. "
        "If a year archive 404s, just skip it."
    )


def _build_tools(client: httpx.AsyncClient, base_url: str) -> list[Any]:
    """Tools wrap an httpx client (not a Playwright page) since exams.doc.ic.ac.uk
    is server-rendered."""

    state: dict[str, str] = {"current_url": "", "current_html": ""}

    async def _fetch(url: str) -> tuple[int, str]:
        response = await client.get(url)
        return response.status_code, response.text

    @tool("goto", "Navigate to a URL on the exams site.", {"url": str})
    async def goto(args: dict[str, Any]) -> dict[str, Any]:
        url = args["url"]
        try:
            status, body = await _fetch(url)
        except Exception as exc:
            return {
                "content": [{"type": "text", "text": f"GET {url} failed: {exc}"}],
                "is_error": True,
            }
        if status >= 400:
            return {
                "content": [{"type": "text", "text": f"GET {url} → HTTP {status}"}],
                "is_error": True,
            }
        state["current_url"] = url
        state["current_html"] = body
        return {"content": [{"type": "text", "text": f"At {url} (HTTP {status})"}]}

    @tool("page_summary", "URL + a short text snapshot of the current page.", {})
    async def page_summary(args: dict[str, Any]) -> dict[str, Any]:
        if not state["current_html"]:
            return {
                "content": [{"type": "text", "text": "No current page; call goto first."}],
                "is_error": True,
            }
        soup = BeautifulSoup(state["current_html"], "html.parser")
        text = soup.get_text(" ", strip=True)[:2000]
        return {
            "content": [
                {"type": "text", "text": f"URL: {state['current_url']}\n\n{text}"}
            ]
        }

    @tool(
        "list_year_archives",
        "List `pastpapers/papers.YY-YY` anchors on the current page, sorted "
        "newest first.",
        {},
    )
    async def list_year_archives(args: dict[str, Any]) -> dict[str, Any]:
        if not state["current_html"]:
            return {
                "content": [{"type": "text", "text": "No current page."}],
                "is_error": True,
            }
        soup = BeautifulSoup(state["current_html"], "html.parser")
        seen: dict[str, str] = {}
        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href"))
            if not YEAR_PATH_RE.search(href):
                continue
            absolute = urljoin(state["current_url"] or base_url, href)
            if not absolute.endswith("/"):
                absolute = absolute + "/"
            match = YEAR_LABEL_RE.search(absolute)
            if not match:
                continue
            label = f"{match.group(1)}-{match.group(2)}"
            seen.setdefault(absolute, label)
        sorted_items = sorted(
            seen.items(), key=lambda pair: _academic_year_start(pair[1]), reverse=True
        )
        lines = [f"- {label} -> {url}" for url, label in sorted_items]
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"{len(sorted_items)} year archives:\n" + "\n".join(lines),
                }
            ]
        }

    @tool(
        "list_course_papers",
        "On the current year archive page, list PDFs whose URL or anchor text "
        "mentions the course code.",
        {"course_id": str},
    )
    async def list_course_papers(args: dict[str, Any]) -> dict[str, Any]:
        if not state["current_html"]:
            return {
                "content": [{"type": "text", "text": "No current page."}],
                "is_error": True,
            }
        soup = BeautifulSoup(state["current_html"], "html.parser")
        needle = str(args["course_id"]).lower()
        results: list[tuple[str, str]] = []
        seen_urls: set[str] = set()
        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href"))
            text = " ".join(anchor.get_text(" ").split())
            haystack = f"{href} {text}".lower()
            if not href.lower().endswith(".pdf") or needle not in haystack:
                continue
            absolute = urljoin(state["current_url"] or base_url, href)
            if absolute in seen_urls:
                continue
            seen_urls.add(absolute)
            results.append((text or href.rsplit("/", 1)[-1], absolute))
        results = results[:MAX_LINKS]
        if not results:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"No PDFs matching {needle!r} on {state['current_url']}",
                    }
                ]
            }
        lines = [f"- {text!r}  ->  {url}" for text, url in results]
        return {
            "content": [
                {"type": "text", "text": f"{len(results)} matches:\n" + "\n".join(lines)}
            ]
        }

    @tool(
        "submit_papers",
        (
            "Terminal tool. Submit the final list of past exam papers. Each "
            "item: title (str), source_url (str absolute), academic_year (e.g. "
            "'24-25')."
        ),
        {
            "type": "object",
            "properties": {
                "papers": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "source_url": {"type": "string"},
                            "academic_year": {"type": "string"},
                        },
                        "required": ["title", "source_url", "academic_year"],
                    },
                }
            },
            "required": ["papers"],
        },
    )
    async def submit_papers(args: dict[str, Any]) -> dict[str, Any]:
        accepted, _ = _collect_exams(args.get("papers") or [])
        return {
            "content": [{"type": "text", "text": f"Received {len(accepted)} papers."}]
        }

    return [goto, page_summary, list_year_archives, list_course_papers, submit_papers]


def _collect_exams(
    raw_papers: list[Any],
) -> tuple[list[DiscoveredExam], list[str]]:
    accepted: list[DiscoveredExam] = []
    rejected: list[str] = []
    seen_urls: set[str] = set()
    for item in raw_papers:
        if not isinstance(item, dict):
            rejected.append(repr(item))
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("source_url") or "").strip()
        year = str(item.get("academic_year") or "").strip()
        if not url or not year:
            rejected.append(title or url or repr(item))
            continue
        if urlparse(url).scheme not in {"http", "https"}:
            rejected.append(url)
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)
        accepted.append(DiscoveredExam(title=title or url, source_url=url, academic_year=year))
    return accepted, rejected


async def discover_past_exams(
    *,
    course_id: str,
    settings: Settings,
    max_years: int = DEFAULT_MAX_YEARS,
    query_fn: QueryFn | None = None,
) -> ExamsDiscoveryReport:
    """Run a Claude Agent SDK loop to list this course's past exam papers."""

    if not settings.imperial_username or not settings.imperial_password:
        raise ConfigurationError(
            "Past exams discovery requires IMPERIAL_USERNAME and IMPERIAL_PASSWORD"
        )

    runner: QueryFn = query_fn or query
    base_url = str(settings.exams_base_url)
    captured: list[DiscoveredExam] = []
    rejected: list[str] = []

    async with httpx.AsyncClient(
        timeout=30.0,
        follow_redirects=True,
        auth=(settings.imperial_username, settings.imperial_password),
    ) as client:
        tools = _build_tools(client, base_url)
        server = create_sdk_mcp_server(name="exams", version="0.1.0", tools=tools)
        allowed = [f"mcp__exams__{t.name}" for t in tools]

        options = ClaudeAgentOptions(
            mcp_servers={"exams": server},
            allowed_tools=allowed,
            disallowed_tools=[],
            max_turns=settings.agent_max_turns,
            model=settings.agent_model,
            permission_mode="bypassPermissions",
            system_prompt=_system_prompt(base_url, course_id, max_years),
        )

        task = (
            f"List past exam papers for course {course_id} on Imperial's exams "
            f"site, taking the {max_years} most recent academic years, then "
            "submit_papers."
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
                            and block.name == "mcp__exams__submit_papers"
                            and not submit_seen
                        ):
                            submit_seen = True
                            accepted, drops = _collect_exams(
                                (block.input or {}).get("papers") or []
                            )
                            captured.extend(accepted)
                            rejected.extend(drops)
                elif isinstance(message, ResultMessage):
                    num_turns = message.num_turns
                    cost = float(message.total_cost_usd or 0.0)
                    stop_reason = message.stop_reason
                    if message.is_error and not submit_seen:
                        return ExamsDiscoveryReport(
                            exams=captured,
                            rejected=rejected,
                            num_turns=num_turns,
                            total_cost_usd=cost,
                            stop_reason=stop_reason,
                            error=(
                                f"Agent ended with error: {message.result or 'unknown'}"
                            ),
                        )
        except Exception as exc:  # pragma: no cover - SDK / CLI failures.
            return ExamsDiscoveryReport(
                exams=captured,
                rejected=rejected,
                num_turns=num_turns,
                total_cost_usd=cost,
                stop_reason=stop_reason,
                error=f"Agent run raised: {exc}",
            )

    if not submit_seen:
        return ExamsDiscoveryReport(
            exams=captured,
            rejected=rejected,
            num_turns=num_turns,
            total_cost_usd=cost,
            stop_reason=stop_reason,
            error=f"Agent did not call submit_papers within {num_turns} turns.",
        )

    return ExamsDiscoveryReport(
        exams=captured,
        rejected=rejected,
        num_turns=num_turns,
        total_cost_usd=cost,
        stop_reason=stop_reason,
    )
