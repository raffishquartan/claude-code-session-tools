"""PostToolUse audit hook for Edit and Write tool calls.

Checks written paths for:
1. Sensitive-path patterns (credentials, keys, secrets) — warns to stderr.
2. Out-of-known-repo-root paths — warns to stderr.
3. WORKLOG.md in cc-sessions/*/working/ — stages via git add (best-effort).

Always exits 0. Never blocks.
"""
from __future__ import annotations

import dataclasses
import json
import re
import subprocess
import sys
from pathlib import Path

_SENSITIVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r'(^|/)\.env(\..+)?$'),
    re.compile(r'(^|/)id_rsa$'),
    re.compile(r'(^|/)id_ed25519$'),
    re.compile(r'(^|/)\.netrc$'),
    re.compile(r'(^|/)credentials\.json$'),
    re.compile(r'(^|/)secrets\..+'),
]

_WORKLOG_PATTERN = re.compile(r'cc-sessions/[^/]+/working/WORKLOG\.md$')

_DEFAULT_REPO_ROOTS = [
    Path.home() / "repos",
    Path("/mnt/c/Users/alice/OneDrive"),
]


@dataclasses.dataclass(frozen=True, slots=True)
class AuditResult:
    sensitive_warning: str | None
    out_of_repo_warning: str | None
    should_git_add: bool


def audit_path(path: Path, *, repo_roots: list[Path] | None = None) -> AuditResult:
    roots = repo_roots if repo_roots is not None else _DEFAULT_REPO_ROOTS
    path_str = str(path)

    sensitive: str | None = None
    for pattern in _SENSITIVE_PATTERNS:
        if pattern.search(path_str):
            sensitive = f"⚠ [edit-write-audit] Sensitive file written: {path}"
            break

    out_of_repo: str | None = None
    if not any(path_str.startswith(str(r)) for r in roots):
        out_of_repo = f"⚠ [edit-write-audit] Write outside known repo roots: {path}"

    should_add = bool(_WORKLOG_PATTERN.search(path_str))

    return AuditResult(
        sensitive_warning=sensitive,
        out_of_repo_warning=out_of_repo,
        should_git_add=should_add,
    )


def _git_add(path: Path) -> None:
    try:
        subprocess.run(
            ["git", "add", "--", str(path)],
            capture_output=True,
            timeout=5,
        )
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    raw = sys.stdin.read()
    try:
        data: dict[str, object] = json.loads(raw)
    except json.JSONDecodeError:
        return 0

    tool_name = str(data.get("tool_name", ""))
    if tool_name not in ("Edit", "Write"):
        return 0

    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return 0
    file_path_str = str(tool_input.get("file_path", ""))
    if not file_path_str:
        return 0

    path = Path(file_path_str)
    result = audit_path(path)

    if result.sensitive_warning:
        print(result.sensitive_warning, file=sys.stderr)
    if result.out_of_repo_warning:
        print(result.out_of_repo_warning, file=sys.stderr)
    if result.should_git_add:
        _git_add(path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
