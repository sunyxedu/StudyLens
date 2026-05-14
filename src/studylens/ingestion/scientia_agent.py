"""Claude Agent SDK driver for per-course Scientia resource discovery.

A Scientia module is organised into three tabs at the predictable URLs
`/2526/modules/<code>/{materials,exercises,tutorials}`. Each tab is a SPA
page whose real downloadables are <a> tags pointing at
`/external-resource?url=<actual>` — the wrapper returns the empty SPA
shell, so we have to peel the inner URL out of the `url=` query before
downloading.

The classification of items isn't strictly by tab: past mock papers and
sample exams routinely show up in the materials tab, and answer-key PDFs
mixed into exercises behave like materials. We let an agent walk the
three tabs, inspect filenames and the nearest section heading, and emit
a deduplicated list of (title, source_url, kind) ready for the crawler
to download.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

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
from studylens.domain.models import ResourceKind
from studylens.ingestion.browser_session import BrowserSession

PAGE_TEXT_LIMIT = 2_000
MAX_LINKS = 200
TRAILING_FILE_RE = re.compile(r"\s*file\s*$", re.IGNORECASE)
VALID_KINDS: tuple[ResourceKind, ...] = ("material", "exercise", "tutorial", "past_exam")


@dataclass(frozen=True, slots=True)
class DiscoveredResource:
    title: str
    source_url: str
    kind: ResourceKind


@dataclass(frozen=True, slots=True)
class ScientiaDiscoveryReport:
    resources: list[DiscoveredResource]
    rejected: list[str]
    num_turns: int
    total_cost_usd: float
    stop_reason: str | None
    error: str | None = None


QueryFn = Callable[..., Any]


def _system_prompt(course_id: str, course_title: str, course_url: str) -> str:
    base = course_url.rstrip("/")
    for suffix in ("/materials", "/exercises", "/tutorials"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return (
        f"You are listing every downloadable resource for course {course_id} "
        f"({course_title}) on Scientia.\n\n"
        "Tools:\n"
        "- goto(url): navigate to a URL\n"
        "- page_summary(): URL, title, and a short text snapshot of the page\n"
        "- list_external_resources(): every <a> on the current page whose href "
        "  goes through /external-resource — returns the unwrapped target URL, "
        "  the anchor text, and the nearest section heading\n"
        "- submit_resources(resources): TERMINAL — call once with the full list\n\n"
        "Steps:\n"
        f"1. goto {base}/materials, then call list_external_resources()\n"
        f"2. goto {base}/exercises, then call list_external_resources()\n"
        f"3. goto {base}/tutorials, then call list_external_resources()\n"
        "4. Build a deduplicated list. For each item, set `kind` based on the "
        "filename and the tab it was found on:\n"
        "   - kind='past_exam' if the filename or anchor text looks like a past "
        "paper / mock exam (e.g. 'mock', '2024-paper', 'exam', 'past paper'); "
        "this overrides the tab, so a mock found on the materials tab is still "
        "a past_exam.\n"
        "   - kind='exercise' for problem sheets, answer keys, or anything on "
        "the exercises tab that isn't a past paper.\n"
        "   - kind='tutorial' for tutorial sheets / tutorial materials.\n"
        "   - kind='material' for lecture notes, slides, handouts, and "
        "everything else on the materials tab.\n"
        "5. submit_resources(resources) — each item: title (str), source_url "
        "(str, the UNWRAPPED real URL), kind (one of material|exercise|tutorial"
        "|past_exam). Skip duplicates by source_url.\n\n"
        "Do not navigate inside resource pages or download files. Be efficient: "
        "three gotos plus one submit is the happy path."
    )


def _build_tools(page: Any) -> list[Any]:
    @tool("goto", "Navigate to a Scientia URL.", {"url": str})
    async def goto(args: dict[str, Any]) -> dict[str, Any]:
        try:
            await page.goto(args["url"], wait_until="domcontentloaded", timeout=15_000)
            await page.wait_for_load_state("networkidle", timeout=8_000)
            return {"content": [{"type": "text", "text": f"At {page.url}"}]}
        except Exception as exc:
            return {
                "content": [{"type": "text", "text": f"Navigation failed: {exc}"}],
                "is_error": True,
            }

    @tool(
        "page_summary",
        "URL, page title, and a short text snapshot of the current page.",
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
        "list_external_resources",
        (
            "List every anchor on the current page whose href goes through "
            "Scientia's /external-resource proxy. Returns text, the unwrapped "
            "target URL, and the nearest heading."
        ),
        {},
    )
    async def list_external_resources(args: dict[str, Any]) -> dict[str, Any]:
        try:
            raw = await page.locator('a[href*="/external-resource"]').evaluate_all(
                """
                (nodes) => nodes.slice(0, 400).map(a => {
                    let real = "";
                    try {
                        const u = new URL(a.href);
                        real = u.searchParams.get('url') || "";
                    } catch (e) {}
                    let section = "";
                    let cur = a.parentElement;
                    while (cur) {
                        const h = cur.querySelector
                            ? cur.querySelector('h1,h2,h3,h4')
                            : null;
                        if (h && (h.innerText || '').trim()) {
                            section = (h.innerText || '').trim().slice(0, 120);
                            break;
                        }
                        cur = cur.parentElement;
                    }
                    return {
                        text: (a.innerText || '').trim().slice(0, 200),
                        real,
                        section
                    };
                })
                """
            )
            trimmed = [item for item in raw[:MAX_LINKS] if item.get("real")]
            lines = [
                f"- {item['text']!r}  [section: {item['section'] or '?'}]  -> {item['real']}"
                for item in trimmed
            ]
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"{len(trimmed)} external resources on this page:\n"
                        + "\n".join(lines),
                    }
                ]
            }
        except Exception as exc:
            return {
                "content": [
                    {"type": "text", "text": f"list_external_resources failed: {exc}"}
                ],
                "is_error": True,
            }

    @tool(
        "submit_resources",
        (
            "Terminal tool. Submit the final deduplicated list of resources for "
            "this course. Each item: title (str), source_url (str, unwrapped), "
            "kind (material|exercise|tutorial|past_exam)."
        ),
        {
            "type": "object",
            "properties": {
                "resources": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "source_url": {"type": "string"},
                            "kind": {
                                "type": "string",
                                "enum": list(VALID_KINDS),
                            },
                        },
                        "required": ["title", "source_url", "kind"],
                    },
                }
            },
            "required": ["resources"],
        },
    )
    async def submit_resources(args: dict[str, Any]) -> dict[str, Any]:
        accepted, _ = _collect_resources(args.get("resources") or [])
        return {
            "content": [
                {"type": "text", "text": f"Received {len(accepted)} resources. Done."}
            ]
        }

    return [goto, page_summary, list_external_resources, submit_resources]


def _normalize_title(raw: str, fallback_url: str) -> str:
    cleaned = TRAILING_FILE_RE.sub("", raw.strip())
    if cleaned:
        return cleaned
    parsed = urlparse(fallback_url)
    return urlparse(parsed.path).path.rsplit("/", 1)[-1] or "resource"


def _unwrap_external_url(maybe_wrapped: str) -> str:
    """Some agents will hand us the wrapper URL anyway; unwrap defensively."""
    parsed = urlparse(maybe_wrapped)
    if "/external-resource" in parsed.path:
        candidates = parse_qs(parsed.query).get("url")
        if candidates:
            return unquote(candidates[0])
    return maybe_wrapped


def _collect_resources(
    raw_resources: list[Any],
) -> tuple[list[DiscoveredResource], list[str]]:
    accepted: list[DiscoveredResource] = []
    rejected: list[str] = []
    seen: set[str] = set()
    for item in raw_resources:
        if not isinstance(item, dict):
            rejected.append(repr(item))
            continue
        title = _normalize_title(
            str(item.get("title") or ""), str(item.get("source_url") or "")
        )
        url = _unwrap_external_url(str(item.get("source_url") or "").strip())
        kind = str(item.get("kind") or "").strip().lower()
        if not url or kind not in VALID_KINDS:
            rejected.append(title or url or repr(item))
            continue
        if urlparse(url).scheme not in {"http", "https"}:
            rejected.append(title or url)
            continue
        if url in seen:
            continue
        seen.add(url)
        accepted.append(DiscoveredResource(title=title, source_url=url, kind=kind))
    return accepted, rejected


async def discover_course_resources(
    session: BrowserSession,
    *,
    course_id: str,
    course_title: str,
    course_url: str,
    settings: Settings,
    query_fn: QueryFn | None = None,
) -> ScientiaDiscoveryReport:
    """Run a Claude Agent SDK loop to list this course's downloadable resources."""

    runner: QueryFn = query_fn or query
    captured: list[DiscoveredResource] = []
    rejected: list[str] = []

    # Make sure course_url has no tab suffix — the agent picks the tabs itself.
    base = course_url.rstrip("/")
    for suffix in ("/materials", "/exercises", "/tutorials"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    canonical_url = urljoin(base + "/", "materials")

    async with session.page() as page:
        tools = _build_tools(page)
        server = create_sdk_mcp_server(name="scientia", version="0.1.0", tools=tools)
        allowed = [f"mcp__scientia__{t.name}" for t in tools]

        options = ClaudeAgentOptions(
            mcp_servers={"scientia": server},
            allowed_tools=allowed,
            disallowed_tools=[],
            max_turns=settings.agent_max_turns,
            model=settings.agent_model,
            permission_mode="bypassPermissions",
            system_prompt=_system_prompt(course_id, course_title, canonical_url),
        )

        task = (
            f"List every downloadable resource for {course_id} ({course_title}) "
            f"by walking the three tabs under {base}, then submit_resources."
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
                            and block.name == "mcp__scientia__submit_resources"
                            and not submit_seen
                        ):
                            submit_seen = True
                            accepted, drops = _collect_resources(
                                (block.input or {}).get("resources") or []
                            )
                            captured.extend(accepted)
                            rejected.extend(drops)
                elif isinstance(message, ResultMessage):
                    num_turns = message.num_turns
                    cost = float(message.total_cost_usd or 0.0)
                    stop_reason = message.stop_reason
                    if message.is_error and not submit_seen:
                        return ScientiaDiscoveryReport(
                            resources=captured,
                            rejected=rejected,
                            num_turns=num_turns,
                            total_cost_usd=cost,
                            stop_reason=stop_reason,
                            error=f"Agent ended with error: {message.result or 'unknown'}",
                        )
        except Exception as exc:  # pragma: no cover - SDK / CLI failures.
            return ScientiaDiscoveryReport(
                resources=captured,
                rejected=rejected,
                num_turns=num_turns,
                total_cost_usd=cost,
                stop_reason=stop_reason,
                error=f"Agent run raised: {exc}",
            )

    if not submit_seen:
        return ScientiaDiscoveryReport(
            resources=captured,
            rejected=rejected,
            num_turns=num_turns,
            total_cost_usd=cost,
            stop_reason=stop_reason,
            error=(
                f"Agent did not call submit_resources before stopping "
                f"at {num_turns} turns."
            ),
        )

    return ScientiaDiscoveryReport(
        resources=captured,
        rejected=rejected,
        num_turns=num_turns,
        total_cost_usd=cost,
        stop_reason=stop_reason,
    )
