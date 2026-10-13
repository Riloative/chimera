"""Tool interface for a future "let the model decide when to search" mode.

**Status: interface only — not exposed in the UI or used by the chat engine.**
The default pipeline (search when the user turns search on) works with every
model. Native tool calling with local models depends on the chat template
(llama-server needs ``--jinja`` and a template with tool support) and small
models frequently emit malformed calls, so this iteration ships the tool
definitions and a validated executor (tested), and documents the mode as
planned rather than shipping a half-working toggle. See docs/WEB.md.

Tool arguments come from the model and are therefore untrusted: they are
type-checked here and every URL still goes through the same SSRF checks,
quotas and domain rules as everything else.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .errors import WebError
from .service import WebService
from .settings import WebAdminSettings

MAX_TOOL_ITERATIONS = 4

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web. Returns titles, URLs and short snippets of the top results.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "A short, specific search query."}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Read a web page and return its main text as Markdown.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "An http(s) URL."}},
                "required": ["url"],
                "additionalProperties": False,
            },
        },
    },
]


@dataclass
class ToolResult:
    name: str
    ok: bool
    content: str  # what would be returned to the model (inside a source block)
    error_code: str | None = None


def parse_arguments(raw: str | dict | None, required: str, limit: int) -> str:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except ValueError as exc:
            raise WebError("web.tool_invalid", "The tool call had invalid arguments.") from exc
    if not isinstance(raw, dict) or set(raw) - {required}:
        raise WebError("web.tool_invalid", "The tool call had invalid arguments.")
    value = raw.get(required)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise WebError("web.tool_invalid", "The tool call had invalid arguments.")
    return value.strip()


class ToolExecutor:
    """Executes only the two known tools, with validated arguments, for one user."""

    def __init__(self, service: WebService, user: dict, settings: WebAdminSettings, max_calls: int = MAX_TOOL_ITERATIONS):
        self.service = service
        self.user = user
        self.settings = settings
        self.remaining = max_calls

    async def execute(self, name: str, arguments: str | dict | None) -> ToolResult:
        if self.remaining <= 0:
            return ToolResult(name, False, "Tool call limit reached.", "web.tool_limit")
        self.remaining -= 1
        try:
            if name == "web_search":
                query = parse_arguments(arguments, "query", 300)
                results = await self.service.search(self.user, query, min(5, self.settings.max_results), self.settings)
                lines = [f"[{r.rank}] {r.title} — {r.url}\n{r.snippet}" for r in results]
                return ToolResult(name, True, "\n\n".join(lines) or "No results.")
            if name == "fetch_url":
                url = parse_arguments(arguments, "url", 4096)
                data = await self.service.read(self.user, url, self.settings, robots=self.settings.robots_for_search)
                if not data.ok:
                    return ToolResult(name, False, f"The page could not be read ({data.error_code}).", data.error_code)
                return ToolResult(name, True, data.content[: self.settings.per_source_chars])
            return ToolResult(name, False, "Unknown tool.", "web.tool_unknown")
        except WebError as exc:
            return ToolResult(name, False, exc.message, exc.code)
