from __future__ import annotations

import argparse
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from cc_session_tools import __version__
from cc_session_tools.lib.roots import load_session_roots
from cc_session_tools.lib.sessions import (
    grep_session,
    iter_sessions,
    session_start_date,
)


@dataclass
class _Result:
    date_key: str
    basename: str
    project_dir: Path
    context_lines: list[str]


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ccs",
        description="Search Claude Code sessions by name/date or file contents.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("query", help="Substring to match against session names (default) or contents.")
    p.add_argument("--contents", action="store_true",
                   help="Search session file contents (1 line of context).")
    p.add_argument("--global", dest="do_global", action="store_true",
                   help="Search all sessions on this machine, not just the current directory.")
    return p


def _collect_pairs(do_global: bool) -> list[tuple[Path, Path]]:
    """Return [(sessions_dir, project_dir), ...] for the search scope."""
    pairs: list[tuple[Path, Path]] = []
    if do_global:
        for root in load_session_roots():
            for proj in root.iterdir():
                if proj.is_dir():
                    cc = proj / "cc-sessions"
                    if cc.is_dir():
                        pairs.append((cc, proj))
    else:
        cwd = Path.cwd().resolve()
        cc = cwd / "cc-sessions"
        if not cc.is_dir():
            return []
        pairs.append((cc, cwd))
    return pairs


def _display_path(p: Path) -> str:
    home = Path.home()
    try:
        rel = p.relative_to(home)
    except ValueError:
        return str(p)
    return f"~/{rel}"


def _session_size(session_dir: Path) -> tuple[int, int]:
    """Return (file_count, total_bytes) for files under session_dir.
    Unreadable files are silently counted as 0 bytes / not counted."""
    files = 0
    total = 0
    for p in session_dir.rglob("*"):
        if p.is_file():
            files += 1
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return files, total


def _format_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.1f} GB"


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    pairs = _collect_pairs(args.do_global)
    if not pairs:
        if args.do_global:
            print("ccs: no sessions found in any configured root", file=sys.stderr)
        else:
            print("ccs: no cc-sessions/ in current directory", file=sys.stderr)
        return 1

    # Build the full session list once. For contents search we also use it to
    # report progress and emit a header.
    sessions: list[tuple[Path, Path]] = []  # (session_dir, project_dir)
    for cc, proj in pairs:
        for sess in iter_sessions(cc):
            if session_start_date(sess.name) is None:
                continue
            sessions.append((sess, proj))

    n = len(sessions)
    if args.contents:
        # Print the count-only header IMMEDIATELY so the user gets feedback
        # before any filesystem walking begins. The size is accumulated and
        # reported in a final summary line at the end.
        noun = "session" if n == 1 else "sessions"
        print(f"ccs: searching {n} {noun}...", file=sys.stderr)

    show_progress = args.contents and sys.stderr.isatty()
    progress_width = 0  # widest line printed so we can clear it later
    total_files = 0
    total_bytes = 0

    results: list[_Result] = []
    for i, (sess, proj) in enumerate(sessions, start=1):
        if args.contents:
            f, b = _session_size(sess)
            total_files += f
            total_bytes += b

        if show_progress:
            pct = int(100 * i / n)
            term_width = shutil.get_terminal_size((80, 20)).columns
            prefix = f"[{i}/{n}] ({pct}%) "
            suffix = f"  [{total_files} files, {_format_size(total_bytes)} so far]"
            budget = max(20, term_width - len(prefix) - len(suffix) - 1)
            name = sess.name if len(sess.name) <= budget else sess.name[: budget - 3] + "..."
            line = prefix + name + suffix
            # Pad with spaces so that a shorter line fully overwrites any
            # leftover characters from a previous wider line.
            padding = " " * max(0, progress_width - len(line))
            sys.stderr.write("\r" + line + padding)
            sys.stderr.flush()
            progress_width = max(progress_width, len(line))

        date_key = session_start_date(sess.name)
        # date_key is non-None - we filtered above.
        assert date_key is not None
        if args.contents:
            ctx = grep_session(sess, args.query)
            if ctx:
                results.append(_Result(date_key, sess.name, proj, ctx))
        else:
            if args.query in sess.name:
                results.append(_Result(date_key, sess.name, proj, []))

    if show_progress:
        # Clear the progress line so it doesn't sit above the results.
        sys.stderr.write("\r" + " " * progress_width + "\r")
        sys.stderr.flush()

    if args.contents:
        print(
            f"ccs: searched {total_files} files ({_format_size(total_bytes)}) "
            f"across {n} {noun}",
            file=sys.stderr,
        )

    if not results:
        print(f"ccs: no sessions match '{args.query}'", file=sys.stderr)
        return 1

    results.sort(key=lambda r: r.date_key, reverse=True)

    for i, r in enumerate(results):
        # Blank line separates content results; name-only results stay tight.
        if i > 0 and args.contents:
            print()
        if args.do_global:
            print(f"{r.basename} ({_display_path(r.project_dir)})")
        else:
            print(r.basename)
        for line in r.context_lines:
            print(f"  {line}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
