"""Install and uninstall the ``ccl()`` shell function in ~/.bashrc and ~/.zshrc.

The function block is delimited by sentinel comment lines:

    # >>> ccst shell function (ccl) >>>
    ccl() { ccs "$@"; }
    ccl-global() { ccs --global "$@"; }
    # <<< ccst shell function (ccl) <<<

Operations are idempotent: re-running install replaces the block between the
sentinels; uninstall removes it.

All mutations are dry-run by default; pass ``apply=True`` to write.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

_SENTINEL_START = "# >>> ccst shell function (ccl) >>>"
_SENTINEL_END = "# <<< ccst shell function (ccl) <<<"

_BLOCK = """\
# >>> ccst shell function (ccl) >>>
ccl() { ccs "$@"; }
ccl-global() { ccs --global "$@"; }
# <<< ccst shell function (ccl) <<<
"""

# TODO(stream-E): add a note in ccst --help / docs that ccl is a ccs list-mode wrapper


class RCAction(str, Enum):
    ADDED = "added"
    REPLACED = "replaced"
    REMOVED = "removed"
    ALREADY_PRESENT = "already-present"
    NOT_PRESENT = "not-present"  # uninstall when block not found
    SKIPPED = "skipped"  # file does not exist


@dataclass(frozen=True)
class RCResult:
    path: Path
    action: RCAction
    message: str


def _find_block(lines: list[str]) -> tuple[int, int] | None:
    """Return (start_idx, end_idx) of the sentinel block, or None."""
    start = None
    for i, line in enumerate(lines):
        stripped = line.rstrip("\n").rstrip()
        if stripped == _SENTINEL_START:
            start = i
        elif stripped == _SENTINEL_END and start is not None:
            return (start, i)
    return None


def install_rc(rc_path: Path, *, apply: bool = False) -> RCResult:
    """Add or replace the ccl block in rc_path."""
    if not rc_path.exists():
        return RCResult(path=rc_path, action=RCAction.SKIPPED, message="file does not exist")

    content = rc_path.read_text()
    lines = content.splitlines(keepends=True)
    span = _find_block(lines)

    if span is not None:
        start, end = span
        existing_block = "".join(lines[start : end + 1])
        if existing_block.rstrip("\n") == _BLOCK.rstrip("\n"):
            return RCResult(
                path=rc_path,
                action=RCAction.ALREADY_PRESENT,
                message="block already up to date",
            )
        # Replace
        new_lines = lines[:start] + [_BLOCK] + lines[end + 1 :]
        new_content = "".join(new_lines)
        if apply:
            rc_path.write_text(new_content)
        return RCResult(
            path=rc_path,
            action=RCAction.REPLACED,
            message=f"{'replaced' if apply else 'would replace'} existing block",
        )

    # Append
    sep = "" if content.endswith("\n") or not content else "\n"
    new_content = content + sep + _BLOCK
    if apply:
        rc_path.write_text(new_content)
    return RCResult(
        path=rc_path,
        action=RCAction.ADDED,
        message=f"{'added' if apply else 'would add'} block",
    )


def uninstall_rc(rc_path: Path, *, apply: bool = False) -> RCResult:
    """Remove the ccl block from rc_path."""
    if not rc_path.exists():
        return RCResult(path=rc_path, action=RCAction.SKIPPED, message="file does not exist")

    content = rc_path.read_text()
    lines = content.splitlines(keepends=True)
    span = _find_block(lines)

    if span is None:
        return RCResult(
            path=rc_path,
            action=RCAction.NOT_PRESENT,
            message="block not found",
        )

    start, end = span
    new_lines = lines[:start] + lines[end + 1 :]
    new_content = "".join(new_lines)
    if apply:
        rc_path.write_text(new_content)
    return RCResult(
        path=rc_path,
        action=RCAction.REMOVED,
        message=f"{'removed' if apply else 'would remove'} block",
    )


def install_all(
    rc_paths: list[Path] | None = None, *, apply: bool = False
) -> list[RCResult]:
    """Install the ccl block in each rc file that exists.

    Default rc_paths: [~/.bashrc, ~/.zshrc].
    """
    paths = rc_paths if rc_paths is not None else _default_rc_paths()
    return [install_rc(p, apply=apply) for p in paths]


def uninstall_all(
    rc_paths: list[Path] | None = None, *, apply: bool = False
) -> list[RCResult]:
    """Remove the ccl block from each rc file that exists.

    Default rc_paths: [~/.bashrc, ~/.zshrc].
    """
    paths = rc_paths if rc_paths is not None else _default_rc_paths()
    return [uninstall_rc(p, apply=apply) for p in paths]


def _default_rc_paths() -> list[Path]:
    home = Path.home()
    return [home / ".bashrc", home / ".zshrc"]
