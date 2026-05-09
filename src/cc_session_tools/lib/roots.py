from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT_ENV = "CLAUDE_SESSION_TOOLS_REPO_ROOT"
PROJ_ROOT_ENV = "CLAUDE_SESSION_TOOLS_PROJ_ROOT"


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
    """All configured session roots, in REPO,PROJ order, deduped, with
    unconfigured / nonexistent entries silently skipped."""
    out: list[Path] = []
    seen: set[Path] = set()
    for r in (repo_root(), proj_root()):
        if r is not None and r not in seen:
            out.append(r)
            seen.add(r)
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
