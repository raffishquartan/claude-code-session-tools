"""The fixed headless-review prompt, bundled as package data, and the levers it allows.

The prompt's ``## Levers`` list is the single source of the accepted levers: ``none`` is in
the taxonomy but never actionable, so it is never accepted in a proposal line.
"""
from __future__ import annotations

from importlib import resources

PROMPT_RESOURCE = "self-improvement-review-prompt.md"


def prompt_text() -> str:
    return (resources.files("cc_session_tools") / "config" / PROMPT_RESOURCE).read_text(
        encoding="utf-8"
    )


def levers(text: str) -> tuple[str, ...]:
    """The backticked bullets under ``## Levers``, in order."""
    out: list[str] = []
    in_levers = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_levers = line == "## Levers"
            continue
        if in_levers and line.startswith("- `"):
            out.append(line.split("`")[1])
    return tuple(out)


def actionable_levers(text: str) -> tuple[str, ...]:
    return tuple(lever for lever in levers(text) if lever != "none")
