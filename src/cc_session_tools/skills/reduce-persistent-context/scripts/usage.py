"""Per-server/plugin/tool call counts from the claude-code-usage CLI.

claude-code-usage warm-caches and reconciles against ccusage, so it is the
canonical usage source (the ~/.claude/usage-data/facets/ files are not used).
"""
import json
import subprocess


def _run(args: list[str]) -> str:
    return subprocess.run(
        ["claude-code-usage", "query", *args],
        capture_output=True, text=True, check=True,
    ).stdout


def query_usage(group_by: str, since: str | None) -> dict[str, int]:
    args = ["--group-by", group_by, "--format", "json"]
    if since:
        args += ["--since", since]
    try:
        raw = _run(args)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return {}
    rows = json.loads(raw or "[]")
    # The identifier column is "identifier" for mcp/plugin but the dimension
    # name itself for tool (key "tool").
    out: dict[str, int] = {}
    for r in rows:
        ident = r.get("identifier", r.get(group_by))
        if ident is None:
            continue
        out[ident] = int(r.get("tool_call_count", 0))
    return out
