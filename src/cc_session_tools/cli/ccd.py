from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from cc_session_tools.lib import prompts, rules
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
    p.add_argument("--force", action="store_true",
                   help="Skip root, project-name, and tag-prefix checks.")
    p.add_argument("tag", help="Name tag (no spaces; use dashes).")
    p.add_argument("extra", nargs=argparse.REMAINDER,
                   help="Additional args passed through to claude.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    real_pwd = Path.cwd().resolve()

    # Validate tag/cwd via shared rules.
    ok, errors = rules.check_session_init(real_pwd, args.tag, force=args.force)
    if not ok:
        print("ccd: validation failed:", file=sys.stderr)
        for e in errors:
            for line in e.splitlines():
                print(f"  {line}", file=sys.stderr)
        if not args.force:
            print("  (use --force to bypass root and strict-root checks)",
                  file=sys.stderr)
        return 1

    # Levenshtein typo / missing-prefix prompts (only under cc-claude-code).
    tag = args.tag
    if not args.force:
        tag = prompts.maybe_correct_tag(real_pwd, tag)

    date_str = datetime.now().strftime("%Y%m%d")
    session_name = f"{date_str}-{tag}"
    session_dir = real_pwd / "cc-sessions" / session_name

    if session_dir.exists():
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

    (session_dir / "working").mkdir(parents=True)
    (session_dir / "out").mkdir(parents=True)

    # Build env for the SessionStart hook + task list. Drop any inherited
    # CLAUDE_CODE_TASK_LIST_ID so the new one (or absence of one) is authoritative.
    env = os.environ.copy()
    env.pop("CLAUDE_CODE_TASK_LIST_ID", None)
    env["CLD_SESSION_TAG"] = tag
    env["CLD_SESSION_DIR"] = str(session_dir.relative_to(real_pwd))
    env["CLD_SESSION_MODE"] = "new"
    task_list_id = id_for_project(real_pwd)
    if task_list_id is not None:
        env["CLAUDE_CODE_TASK_LIST_ID"] = task_list_id

    cmd = [
        "claude",
        "-n", session_name,
        "--remote-control", session_name,
        *(args.extra or []),
    ]
    # Chdir to the resolved project path so Claude Code records its
    # ~/.claude/projects/<encoded-cwd>/ key against the canonical, symlink-
    # resolved path. Matches the original bash ccd's `cd "$real_pwd"` step.
    os.chdir(real_pwd)
    launch_claude(cmd, env)
    return 0


if __name__ == "__main__":
    sys.exit(main())
