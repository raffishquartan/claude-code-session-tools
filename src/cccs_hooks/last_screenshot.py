"""UserPromptSubmit hook: resolve the user's latest screenshot for ">lss".

When a submitted prompt contains the token ">lss", inject the absolute path of
the newest screenshot plus a note telling Claude whether to read it. Text only;
the image enters context only if Claude then calls Read. Always exits 0.

The screenshot directory comes from the ``CCST_SCREENSHOT_DIR`` environment
variable. If ">lss" is used while it is unset, a visible message is printed to
stderr (which CC surfaces to the user) so they can set it.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

_TOKEN = re.compile(r"(?<![A-Za-z0-9])>lss(?![A-Za-z0-9])")
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
_STALE_SECONDS = 10 * 60


def find_token(text: str) -> bool:
    """True if the prompt contains a standalone ">lss" token."""
    return _TOKEN.search(text) is not None


def resolve_screenshot_dir() -> Path | None:
    """The configured screenshot directory, or None if unset."""
    raw = os.environ.get("CCST_SCREENSHOT_DIR")
    return Path(raw) if raw else None


def newest_screenshot(directory: Path) -> Path | None:
    """The newest image file in ``directory`` by mtime, or None."""
    images = [
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES
    ]
    if not images:
        return None
    return max(images, key=lambda p: p.stat().st_mtime)


def _format_age(age_seconds: float) -> str:
    minutes = int(age_seconds // 60)
    if minutes < 1:
        return "less than a minute ago"
    return f"{minutes}m ago"


def build_context(
    *, path: Path | None, age_seconds: float | None, dir_configured: bool
) -> str:
    """The additional-context note injected for a ">lss" prompt."""
    if not dir_configured:
        return (
            '[last-screenshot] The message contains ">lss" but no screenshot '
            "directory is configured. Set the CCST_SCREENSHOT_DIR environment "
            "variable to the folder where screenshots are saved."
        )
    if path is None:
        return (
            '[last-screenshot] The message contains ">lss" but no screenshot '
            "was found in the configured directory."
        )
    note = (
        '[last-screenshot] The user\'s message contains ">lss". If they are '
        f"asking you to look at their latest screenshot, it is at {path} "
        f"(taken {_format_age(age_seconds or 0)}). If they are only discussing "
        'the ">lss" feature itself, ignore this and do not read the file.'
    )
    if (age_seconds or 0) > _STALE_SECONDS:
        note += (
            f" Note: this screenshot is older than {_STALE_SECONDS // 60} min - "
            "confirm it is the one they meant."
        )
    return note


def _emit(context: str) -> None:
    json.dump(
        {"hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }},
        sys.stdout,
    )


def main(argv: list[str] | None = None) -> int:
    try:
        data = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        return 0

    prompt = str(data.get("prompt", ""))
    if not prompt or not find_token(prompt):
        return 0

    directory = resolve_screenshot_dir()
    if directory is None or not directory.is_dir():
        print(
            "⚠ [last-screenshot] >lss used but CCST_SCREENSHOT_DIR is not set "
            "(or is not a directory). Set it to your screenshots folder.",
            file=sys.stderr,
        )
        _emit(build_context(path=None, age_seconds=None, dir_configured=False))
        return 0

    shot = newest_screenshot(directory)
    if shot is None:
        _emit(build_context(path=None, age_seconds=None, dir_configured=True))
        return 0

    age = time.time() - shot.stat().st_mtime
    _emit(build_context(path=shot, age_seconds=age, dir_configured=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
