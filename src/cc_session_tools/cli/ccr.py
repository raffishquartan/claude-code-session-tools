from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from cc_session_tools import __version__
from cc_session_tools.lib.claude_flags import get_claude_flags
from cc_session_tools.lib.roots import load_session_roots
from cc_session_tools.lib.sessions import SESSION_FULL_RE, SessionMatch, find_matching_sessions, find_orphan_transcripts, session_tag
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
    p.add_argument("--debug", action="store_true",
                   help="Enable debug output (also: CCX_DEBUG=1).")
    p.add_argument(
        "--include-orphans",
        action="store_true",
        help=(
            "Also search for orphan transcripts: sessions whose JSONL transcript "
            "exists in ~/.claude/projects/ but have no cc-sessions/ directory on disk."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args, remainder = _build_parser().parse_known_args(argv)

    if args.debug:
        os.environ["CCX_DEBUG"] = "1"
    from cc_session_tools.lib.debug import debug

    roots = load_session_roots()

    # Exact-match fast-path: if fragment looks like a full basename, try a
    # direct directory lookup before falling back to substring search.  This
    # prevents "20260504-foo" from being treated as ambiguous when
    # "20260504-foo-bar" also exists.
    exact_match: SessionMatch | None = None
    if SESSION_FULL_RE.fullmatch(args.fragment):
        for root in roots:
            if not root.is_dir():
                continue
            for proj in root.iterdir():
                if not proj.is_dir():
                    continue
                candidate = proj / "cc-sessions" / args.fragment
                if candidate.is_dir():
                    exact_match = SessionMatch(
                        basename=args.fragment,
                        project_dir=proj,
                        session_dir=candidate,
                    )
                    break
            if exact_match:
                break

    matches = [exact_match] if exact_match else find_matching_sessions(args.fragment, roots)
    if not exact_match and args.include_orphans:
        orphans = find_orphan_transcripts(args.fragment, roots)
        # De-duplicate: skip orphans whose basename already appears in on-disk matches.
        on_disk_basenames = {m.basename for m in matches}
        matches.extend(o for o in orphans if o.basename not in on_disk_basenames)
    debug(f"fragment: {args.fragment!r}")
    debug(f"matches: {[m.basename for m in matches]}")

    if not matches:
        print(f"ccr: no sessions match '{args.fragment}'", file=sys.stderr)
        return 1

    if len(matches) > 1:
        if len(matches) <= 10 and sys.stdin.isatty():
            from cc_session_tools.lib.picker import pick_from_list
            from cc_session_tools.lib.sessions import session_start_date
            matches.sort(key=lambda x: session_start_date(x.basename) or "", reverse=True)
            labels = [f"{m.basename} ({m.project_dir})" for m in matches]
            idx = pick_from_list(labels)
            if idx is None:
                return 0
            m = matches[idx]
            # Fall through to single-match resume logic below
        else:
            print("Multiple sessions match that name tag fragment:")
            for m in matches:
                print(f"  {m.basename} ({m.project_dir})")
            print(
                "Please re-run ccr with an unambiguous fragment of the name tag "
                "of the session you want to resume."
            )
            return 0
    else:
        m = matches[0]

    # single match (or picker selection) - variable m is set above
    if m.is_orphan:
        print(
            f"ccr: warning: no on-disk session directory for '{m.basename}' "
            f"(orphan transcript only)",
            file=sys.stderr,
        )

    tag = session_tag(m.basename)
    if tag is None:
        # Should not happen because find_matching_sessions only returns
        # basenames that match SESSION_BASENAME_RE, but fall back gracefully.
        tag = m.basename

    # Fail fast with a clear message when claude is not on PATH.
    if not shutil.which("claude"):
        print(
            "ccr: 'claude' not found on PATH - is Claude Code installed?",
            file=sys.stderr,
        )
        return 1

    # Validate and pass through any extra flags from remainder.
    # Long flags (--foo) are checked against claude's known flags.
    # Short flags (-f) pass through without validation.
    if remainder:
        valid_flags = get_claude_flags()
        for arg in remainder:
            if arg.startswith("--"):
                flag_name = arg.split("=")[0]
                if flag_name not in valid_flags:
                    print(
                        f"ccr: unknown flag '{arg}'; not a recognised claude option",
                        file=sys.stderr,
                    )
                    return 1

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
    if remainder:
        cmd.extend(remainder)
    debug(f"resuming: {m.basename} in {m.project_dir}")
    launch_claude_resume(cmd, env, cwd=m.project_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
