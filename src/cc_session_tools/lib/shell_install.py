"""Install and uninstall the ``ccl()`` shell function as an rc fragment file.

ccst manages ``ccl()`` as a standalone file inside a shell-rc fragments
directory (default ``~/.shellrc.d``) rather than editing ``~/.bashrc``/
``~/.zshrc`` directly — those files may be managed by something else (e.g.
chezmoi) and ccst has no business mutating them. Sourcing the fragments
directory is the consuming shell's responsibility; a chezmoi-managed
``.bashrc``/``.zshrc`` carries a loop like::

    for f in ~/.shellrc.d/*.sh; do [ -r "$f" ] && source "$f"; done

A shell with no such loop simply won't pick up ``ccl()`` — ccst does not
fall back to writing into ``~/.bashrc``/``~/.zshrc`` directly.

``ccl --help`` prints a short, ccl-specific help message instead of
delegating to ``ccs --help``.

Operations are idempotent: re-running install overwrites the fragment file;
uninstall removes it. All mutations are dry-run by default; pass
``apply=True`` to write.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

FRAGMENT_FILENAME = "ccl.sh"

_FRAGMENT = """\
# Managed by ccst (claude-code-session-tools) - do not hand-edit.
# Reinstall: ccst shell install --apply   Remove: ccst shell uninstall --apply
ccl() {
  local _saw_global _saw_order_by _a
  for _a in "$@"; do
    case "$_a" in
      --help|-h)
        cat <<'CCLHELP'
Usage: ccl [--global] [--order-by {start,update,opened,active}] [-n N | --limit N] [...]

List Claude Code sessions in the current project (wrapper around ccs).
Outside a directory with a known cc-sessions/, this errors with
"no cc-sessions/ in current directory" - pass --global to search every
configured root instead.

Options shown here (ccl intercepts these two; everything else, including
-n/--limit and every other ccs flag, passes straight through to ccs):
  --global                              List sessions across all configured roots
                                        (default: current directory only).
  --order-by {start,update,opened,active}
                             Sort order:
                               start  = newest-first by session start date
                               update = newest-first by last file modification
                                        (also prints the update timestamp)
                               opened = newest-first by last ccd/ccr invocation
                                        (also prints the opened timestamp)
                               active = newest-first by last Claude response
                                        (also prints the active timestamp)

Also commonly used (passed through to ccs unmodified):
  -n N, --limit N            Show only the N most recent sessions. Requires
                             --order-by opened or --order-by active (start/
                             update ordering is not database-indexed) -
                             e.g. ccl --order-by active --limit 5.

ccl is a shell function wrapper around 'ccs' (list mode only).
For the full ccs interface, run: ccs --help
CCLHELP
        return 0
        ;;
      --global)
        _saw_global=1
        ;;
      --order-by|--order-by=*)
        _saw_order_by=1
        ;;
    esac
  done
  if [[ -n "$_saw_global" && -z "$_saw_order_by" ]]; then
    set -- --order-by active "$@"
  fi
  ccs "$@"
}
ccl-global() { ccs --global "$@"; }
ccl-recent() { ccs --global --order-by active "$@"; }
"""


class RCAction(str, Enum):
    ADDED = "added"
    REPLACED = "replaced"
    REMOVED = "removed"
    ALREADY_PRESENT = "already-present"
    NOT_PRESENT = "not-present"  # uninstall when fragment not found


@dataclass(frozen=True)
class RCResult:
    path: Path
    action: RCAction
    message: str


def install_fragment(fragments_dir: Path, *, apply: bool = False) -> RCResult:
    """Write (or refresh) the ccl() fragment file in fragments_dir.

    Creates fragments_dir if it does not exist.
    """
    fragment_path = fragments_dir / FRAGMENT_FILENAME

    if fragment_path.exists():
        if fragment_path.read_text() == _FRAGMENT:
            return RCResult(
                path=fragment_path,
                action=RCAction.ALREADY_PRESENT,
                message="fragment already up to date",
            )
        if apply:
            fragment_path.write_text(_FRAGMENT)
        return RCResult(
            path=fragment_path,
            action=RCAction.REPLACED,
            message=f"{'replaced' if apply else 'would replace'} existing fragment",
        )

    if apply:
        fragments_dir.mkdir(parents=True, exist_ok=True)
        fragment_path.write_text(_FRAGMENT)
    return RCResult(
        path=fragment_path,
        action=RCAction.ADDED,
        message=f"{'added' if apply else 'would add'} fragment",
    )


def uninstall_fragment(fragments_dir: Path, *, apply: bool = False) -> RCResult:
    """Remove the ccl() fragment file from fragments_dir."""
    fragment_path = fragments_dir / FRAGMENT_FILENAME

    if not fragment_path.exists():
        return RCResult(
            path=fragment_path,
            action=RCAction.NOT_PRESENT,
            message="fragment not found",
        )

    if apply:
        fragment_path.unlink()
    return RCResult(
        path=fragment_path,
        action=RCAction.REMOVED,
        message=f"{'removed' if apply else 'would remove'} fragment",
    )


def install_all(
    fragments_dirs: list[Path] | None = None, *, apply: bool = False
) -> list[RCResult]:
    """Install the ccl() fragment into each given fragments directory.

    Default fragments_dirs: [~/.shellrc.d].
    """
    dirs = fragments_dirs if fragments_dirs is not None else _default_fragments_dirs()
    return [install_fragment(d, apply=apply) for d in dirs]


def uninstall_all(
    fragments_dirs: list[Path] | None = None, *, apply: bool = False
) -> list[RCResult]:
    """Remove the ccl() fragment from each given fragments directory.

    Default fragments_dirs: [~/.shellrc.d].
    """
    dirs = fragments_dirs if fragments_dirs is not None else _default_fragments_dirs()
    return [uninstall_fragment(d, apply=apply) for d in dirs]


def _default_fragments_dirs() -> list[Path]:
    return [Path.home() / ".shellrc.d"]
