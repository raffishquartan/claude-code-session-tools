from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, TextIO

from .levenshtein import distance
from .roots import strict_root_path

TYPO_DISTANCE_LIMIT = 2


def _first_term(tag: str) -> str:
    return tag.split("-", 1)[0]


def maybe_correct_tag(
    real_pwd: Path,
    tag: str,
    *,
    input_fn: Callable[[], str] = input,
    stderr: TextIO | None = None,
) -> str:
    if stderr is None:
        stderr = sys.stderr

    sr = strict_root_path()
    if sr is None or real_pwd.parent != sr:
        return tag

    project_name = real_pwd.name
    first_term = _first_term(tag)
    if first_term == project_name:
        return tag

    d = distance(first_term, project_name)
    if 0 < d <= TYPO_DISTANCE_LIMIT:
        rest = tag[len(first_term):]
        suggested = f"{project_name}{rest}"
        print(
            f"ccd: '{first_term}' looks like a typo of project folder "
            f"'{project_name}' (Levenshtein {d}).",
            file=stderr,
        )
        print(
            f"ccd: Start session with tag '{suggested}' instead? [y/N] ",
            end="",
            file=stderr,
            flush=True,
        )
        ans = input_fn().strip().lower()
        if ans == "y":
            return suggested
        raise SystemExit(1)

    # Missing-prefix: first term doesn't match current project (typo gate
    # already failed) and is far from every other cc-claude-code project too.
    other_projects = [
        p.name for p in sr.iterdir()
        if p.is_dir() and p.name != project_name
    ]
    if all(distance(first_term, p) > TYPO_DISTANCE_LIMIT for p in other_projects):
        suggested = f"{project_name}-{tag}"
        print(
            f"ccd: '{first_term}' is not a recognised project under the strict "
            f"(PROJ) root.",
            file=stderr,
        )
        print(
            f"ccd: Did you mean to prepend the current project? Start with tag "
            f"'{suggested}'? [y/N] ",
            end="",
            file=stderr,
            flush=True,
        )
        ans = input_fn().strip().lower()
        if ans == "y":
            return suggested
        raise SystemExit(1)

    return tag
