from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT_ENV = "CLAUDE_SESSION_TOOLS_REPO_ROOT"
PROJ_ROOT_ENV = "CLAUDE_SESSION_TOOLS_PROJ_ROOT"

_MARKER = "[CST-ROOTS-CONFIG-ERROR]"


class RootsConfigError(Exception):
    """Raised by load_session_roots() when env-var configuration is missing or invalid.

    The message always starts with _MARKER so downstream callers (skills, CLIs) can
    pattern-match on it without parsing natural language.
    """


def _resolve_env(name: str) -> Path | None:
    val = os.environ.get(name)
    if not val:
        return None
    p = Path(val).expanduser()
    try:
        real = p.resolve(strict=True)
    except (FileNotFoundError, OSError):
        return None
    if not real.is_dir():
        return None
    return real


def repo_root() -> Path | None:
    """Loose root from CLAUDE_SESSION_TOOLS_REPO_ROOT. No naming conventions
    are enforced for sessions started under this root beyond no-spaces."""
    return _resolve_env(REPO_ROOT_ENV)


def proj_root() -> Path | None:
    """Strict (namespaced) root from CLAUDE_SESSION_TOOLS_PROJ_ROOT. Sessions
    started under this root must use a `<project>-<label>` tag (see rules)."""
    return _resolve_env(PROJ_ROOT_ENV)


def load_session_roots() -> list[Path]:
    """All configured session roots, in REPO,PROJ order, deduped.

    Raises RootsConfigError (message starts with [CST-ROOTS-CONFIG-ERROR]) when:
    - Both env vars are unset or empty.
    - Any set env var points to a path that does not exist.
    - Any set env var points to a file rather than a directory.
    - One env var is valid and the other is set but invalid (the invalid one wins).

    On success returns a non-empty list of resolved, deduplicated Path objects.
    """
    repo_raw = os.environ.get(REPO_ROOT_ENV)
    proj_raw = os.environ.get(PROJ_ROOT_ENV)

    if not repo_raw and not proj_raw:
        raise RootsConfigError(
            f"{_MARKER} No session roots configured. "
            f"Set ${REPO_ROOT_ENV} and/or ${PROJ_ROOT_ENV} to a directory "
            f"whose direct children are your projects "
            f"(typical values: $HOME/repos and $HOME/cc-claude-code)."
        )

    out: list[Path] = []
    seen: set[Path] = set()
    for env_name, raw in ((REPO_ROOT_ENV, repo_raw), (PROJ_ROOT_ENV, proj_raw)):
        if not raw:
            continue
        p = Path(raw).expanduser()
        if not p.exists():
            raise RootsConfigError(
                f"{_MARKER} ${env_name} is set to '{raw}' but that path does not "
                f"exist. Either create the directory, point the env var at an "
                f"existing directory, or unset the env var."
            )
        if not p.is_dir():
            raise RootsConfigError(
                f"{_MARKER} ${env_name} is set to '{raw}' but that path is a file, "
                f"not a directory. Point the env var at a directory or unset it."
            )
        real = p.resolve()
        if real not in seen:
            out.append(real)
            seen.add(real)
    return out


def matched_session_root(cwd_abs: Path, roots: list[Path]) -> Path | None:
    parent = cwd_abs.parent
    for r in roots:
        if parent == r:
            return r
    return None


def is_valid_session_cwd(cwd_abs: Path, roots: list[Path]) -> bool:
    return matched_session_root(cwd_abs, roots) is not None


def is_strict_root(root: Path) -> bool:
    """A root is strict iff it equals the configured PROJ_ROOT."""
    pr = proj_root()
    return pr is not None and root == pr


def strict_root_path() -> Path | None:
    """Alias for proj_root(), preserved for callers that think in
    strict-root terminology."""
    return proj_root()
