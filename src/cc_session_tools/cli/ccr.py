from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from cc_session_tools import __version__
from cc_session_tools.lib.roots import load_session_roots
from cc_session_tools.lib.sessions import find_matching_sessions, session_tag
from cc_session_tools.lib.tasklist import id_for_project


def launch_claude_resume(cmd: list[str], env: dict[str, str], cwd: Path | None = None) -> None:
    """Replace the current process with `claude --resume`. Wrapped so tests
    can monkeypatch this without performing a real exec."""
    if cwd is not None:
        os.chdir(cwd)
    os.execvpe(cmd[0], cmd, env)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ccr",
        description="Resume a Claude Code session by name-tag fragment.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("fragment", help="Substring to match against session basenames.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    roots = load_session_roots()
    matches = find_matching_sessions(args.fragment, roots)

    if not matches:
        print(f"ccr: no sessions match '{args.fragment}'", file=sys.stderr)
        return 1

    if len(matches) > 1:
        print("Multiple sessions match that name tag fragment:")
        for m in matches:
            print(f"  {m.basename} ({m.project_dir})")
        print(
            "Please re-run ccr with an unambiguous fragment of the name tag "
            "of the session you want to resume."
        )
        return 0

    m = matches[0]
    tag = session_tag(m.basename)
    if tag is None:
        # Should not happen because find_matching_sessions only returns
        # basenames that match SESSION_BASENAME_RE, but fall back gracefully.
        tag = m.basename

    env = os.environ.copy()
    env.pop("CLAUDE_CODE_TASK_LIST_ID", None)
    env["CLD_SESSION_TAG"] = tag
    env["CLD_SESSION_DIR"] = str(m.session_dir)
    env["CLD_SESSION_MODE"] = "resume"
    task_list_id = id_for_project(m.project_dir)
    if task_list_id is not None:
        env["CLAUDE_CODE_TASK_LIST_ID"] = task_list_id

    cmd = [
        "claude",
        "--resume", m.basename,
        "--remote-control", m.basename,
    ]
    launch_claude_resume(cmd, env, cwd=m.project_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
