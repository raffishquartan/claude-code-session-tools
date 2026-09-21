"""Terminal-width word wrapping shared by `ccst doctor` and the hooks install output."""
from __future__ import annotations

import shutil
import textwrap

FALLBACK_WIDTH = 80
MIN_WIDTH = 40


def effective_width() -> int:
    """Width to wrap to: the detected terminal width, never below MIN_WIDTH.

    `shutil.get_terminal_size` reads `COLUMNS`, then the real terminal via the process's stdout
    file descriptor (so an in-process `redirect_stdout` capture still sees the real terminal),
    and falls back to FALLBACK_WIDTH when neither is available. It is called through the module
    attribute so tests can patch it.
    """
    return max(shutil.get_terminal_size(fallback=(FALLBACK_WIDTH, 24)).columns, MIN_WIDTH)


def wrap_block(text: str, *, indent: str = "    ") -> str:
    """Word-wrap `text` to the effective width, indenting every line.

    Words are never split: a path, hyphenated name or backticked command that has no whitespace
    stays on one line, and a single token longer than the width overflows rather than breaking.
    """
    return textwrap.fill(
        text,
        width=effective_width(),
        initial_indent=indent,
        subsequent_indent=indent,
        break_long_words=False,
        break_on_hyphens=False,
    )
