from __future__ import annotations

import argparse
import os
import shlex
import sys
from datetime import datetime
from pathlib import Path

from cc_session_tools import __version__
from cc_session_tools.lib import prompts, rules
from cc_session_tools.lib.roots import (
    RootsConfigError,
    is_strict_root,
    load_session_roots,
    matched_session_root,
)
from cc_session_tools.lib.sessions import is_empty_session
from cc_session_tools.lib.tasklist import id_for_project


def launch_claude(cmd: list[str], env: dict[str, str]) -> None:
    """Replace the current process with `claude`. Wrapped in a function so
    tests can monkeypatch this without performing a real exec."""
    os.execvpe(cmd[0], cmd, env)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ccd",
        description="Start a new Claude Code session with a pre-created cc-sessions/ dir.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--dry-run", action="store_true",
                   help="Print what ccd would do without creating dirs or launching claude.")
    p.add_argument("--force", action="store_true",
                   help="Skip root, project-name, and tag-prefix checks.")
    p.add_argument("--debug", action="store_true",
                   help="Enable debug output (also: CCX_DEBUG=1).")
    p.add_argument("tag", help="Name tag (no spaces; use dashes).")
    p.add_argument("extra", nargs=argparse.REMAINDER,
                   help="Additional args passed through to claude.")
    return p


def _describe_root(real_pwd: Path) -> str:
    """Return a human-readable description of which root the cwd matched.

    Returns a plain string so callers never need to handle errors - this is
    only used for the informational dry-run report.
    """
    try:
        roots = load_session_roots()
    except RootsConfigError:
        return "none (roots not configured)"
    root = matched_session_root(real_pwd, roots)
    if root is None:
        return "none"
    if is_strict_root(root):
        return f"PROJ_ROOT ({root})"
    return f"REPO_ROOT ({root})"


def _print_dry_run_report(
    real_pwd: Path,
    tag: str,
    raw_tag: str,
    session_name: str,
    session_dir: Path,
    task_list_id: str | None,
    force: bool,
    validation_ok: bool,
    validation_errors: list[str],
    cmd: list[str],
) -> None:
    """Print the YAML-ish dry-run report to stdout."""
    root_desc = _describe_root(real_pwd)

    if not validation_ok:
        validation_str = "\n".join(f"  - {e}" for e in validation_errors)
    else:
        validation_str = "ok"

    levenshtein_note = ""
    if tag != raw_tag:
        levenshtein_note = f" (Levenshtein correction would have prompted: raw={raw_tag!r})"

    print("ccd dry-run:")
    print(f"  cwd: {real_pwd}")
    print(f"  tree_root / proj_root: {root_desc}")
    print(f"  tag: {tag}{levenshtein_note}")
    print(f"  session_name: {session_name}")
    print(f"  session_dir: {session_dir}")
    print(f"  task_list_id: {task_list_id if task_list_id is not None else '(none)'}")
    print(f"  force: {str(force).lower()}")
    if validation_ok:
        print("  validation: ok")
    else:
        print("  validation:")
        for e in validation_errors:
            print(f"    - {e}")
    print(f"  launch_command: {shlex.join(cmd)}")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.debug:
        os.environ["CCX_DEBUG"] = "1"
    from cc_session_tools.lib.debug import debug

    real_pwd = Path.cwd().resolve()

    # In dry-run mode, skip the interactive Levenshtein/missing-prefix prompts.
    # We note whether a prompt WOULD have fired, but we do not ask the user.
    raw_tag = args.tag
    tag = raw_tag
    if not args.dry_run and not args.force:
        tag = prompts.maybe_correct_tag(real_pwd, tag)

    # Validate tag/cwd via shared rules (on the possibly-corrected tag).
    ok, errors = rules.check_session_init(real_pwd, tag, force=args.force)

    date_str = datetime.now().strftime("%Y%m%d")
    session_name = f"{date_str}-{tag}"
    session_dir = real_pwd / "cc-sessions" / session_name

    cmd = [
        "claude",
        "-n", session_name,
        "--remote-control", session_name,
        *(args.extra or []),
    ]

    if args.dry_run:
        # Derive task_list_id without modifying env (dry-run is read-only).
        # id_for_project returns None (not raises) when cwd is outside all roots.
        try:
            task_list_id: str | None = id_for_project(real_pwd)
        except RootsConfigError:
            task_list_id = None
        _print_dry_run_report(
            real_pwd=real_pwd,
            tag=tag,
            raw_tag=raw_tag,
            session_name=session_name,
            session_dir=session_dir,
            task_list_id=task_list_id,
            force=args.force,
            validation_ok=ok,
            validation_errors=errors,
            cmd=cmd,
        )
        return 0

    if not ok:
        print("ccd: validation failed:", file=sys.stderr)
        for e in errors:
            for line in e.splitlines():
                print(f"  {line}", file=sys.stderr)
        if not args.force:
            print("  (use --force to bypass root and strict-root checks)",
                  file=sys.stderr)
        return 1

    debug(f"tag: {tag!r}")
    debug(f"session_dir: {session_dir}")

    # A leftover session_dir is only a blocker if it belongs to a session that
    # actually received user input. A session that never started (e.g. claude
    # aborted on a malformed settings.json, so no transcript exists) or that
    # started but got no typed messages leaves behind only the empty scaffold
    # dirs - that is safe to reuse, and reusing it is the ONLY way to recover,
    # since ccr cannot resume a transcript that was never created.
    if session_dir.exists() and not is_empty_session(session_name, real_pwd):
        print(
            f"ccd: session '{session_name}' already started today in this directory.",
            file=sys.stderr,
        )
        print(f"ccd:   existing: {session_dir}", file=sys.stderr)
        print(
            f"ccd: Use a different name tag, or 'ccr {tag}' to resume the existing one.",
            file=sys.stderr,
        )
        return 1

    if session_dir.exists():
        debug(f"reusing empty session dir: {session_dir}")

    (session_dir / "working").mkdir(parents=True, exist_ok=True)
    (session_dir / "out").mkdir(parents=True, exist_ok=True)

    # Build env for the SessionStart hook + task list. Drop any inherited
    # CLAUDE_CODE_TASK_LIST_ID so the new one (or absence of one) is authoritative.
    env = os.environ.copy()
    env.pop("CLAUDE_CODE_TASK_LIST_ID", None)
    env["CLD_SESSION_TAG"] = tag
    env["CLD_SESSION_DIR"] = str(session_dir)
    env["CLD_SESSION_MODE"] = "new"
    task_list_id = id_for_project(real_pwd)
    if task_list_id is not None:
        env["CLAUDE_CODE_TASK_LIST_ID"] = task_list_id

    # Chdir to the resolved project path so Claude Code records its
    # ~/.claude/projects/<encoded-cwd>/ key against the canonical, symlink-
    # resolved path. Matches the original bash ccd's `cd "$real_pwd"` step.
    debug(f"launching: {cmd}")
    os.chdir(real_pwd)
    launch_claude(cmd, env)
    return 0


if __name__ == "__main__":
    sys.exit(main())
