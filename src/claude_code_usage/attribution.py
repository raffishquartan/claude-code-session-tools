"""Tool / MCP / plugin classification and token attribution.

Tool names follow these conventions in Claude Code logs:

- Native tools: `Bash`, `Read`, `Edit`, `WebSearch`, `Skill`, `Agent`, ...
- MCP-server tools: `mcp__<server>__<tool>` (e.g. `mcp__opentabs__tesco_view_basket`).
- Plugin tools: `mcp__plugin_<plugin>_<plugin>__<tool>` (e.g. `mcp__plugin_github_github__list_issues`).

This module classifies a tool name into one of those three buckets and,
given an assistant message, splits its tokens across the tool_use blocks
it produced (evenly).
"""

from __future__ import annotations

from typing import Any


def classify_tool(name: str) -> tuple[str, str]:
    """Return `(kind, identifier)` for a tool name.

    `kind` is one of `"native"`, `"mcp"`, `"plugin"`.
    `identifier` is the raw tool name for native tools, the MCP server
    name for MCP tools, and the plugin name for plugin tools.
    """
    if name.startswith("mcp__"):
        rest = name[len("mcp__") :]
        server_end = rest.find("__")
        if server_end > 0:
            server = rest[:server_end]
            if server.startswith("plugin_"):
                inner = server[len("plugin_") :]
                plugin_name, _sep, _server_within = inner.rpartition("_")
                if plugin_name:
                    return ("plugin", plugin_name)
            return ("mcp", server)
    return ("native", name)


NO_TOOL = "<no-tool>"


def attribute_tokens(tools: list[str], tokens: int) -> list[dict[str, Any]]:
    """Split `tokens` evenly across `tools` and classify each.

    If `tools` is empty, all tokens go to a single `<no-tool>` bucket.
    """
    if not tools:
        return [
            {
                "tool": NO_TOOL,
                "kind": "no-tool",
                "identifier": NO_TOOL,
                "tokens": float(tokens),
            }
        ]
    share = float(tokens) / len(tools)
    rows: list[dict[str, Any]] = []
    for name in tools:
        kind, identifier = classify_tool(name)
        rows.append(
            {
                "tool": name,
                "kind": kind,
                "identifier": identifier,
                "tokens": share,
            }
        )
    return rows


def extract_tool_uses(message: dict[str, Any]) -> list[str]:
    """Return tool names from `tool_use` blocks in `message.content`, in order."""
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [
        block.get("name", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "tool_use"
    ]
