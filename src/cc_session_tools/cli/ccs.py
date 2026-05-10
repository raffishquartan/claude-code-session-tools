from __future__ import annotations

import argparse
import datetime
import difflib
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from cc_session_tools import __version__
from cc_session_tools.lib.roots import RootsConfigError, load_session_roots
from cc_session_tools.lib.sessions import (
    enumerate_session_files,
    grep_files,
    iter_sessions,
    session_start_date,
    transcript_dir_for_project,
)


def _is_hook_session(basename: str) -> bool:
    from cc_session_tools.lib.sessions import session_tag
    tag = session_tag(basename)
    return tag is not None and "hook" in tag.lower()


def _parse_date_filter(args) -> tuple[str | None, str | None] | None:
    """Return (since_key, before_key) in YYYYMMDD format, or None on parse error."""
    since: str | None = None
    before: str | None = None
    if args.days is not None:
        cutoff = datetime.date.today() - datetime.timedelta(days=args.days)
        since = cutoff.strftime("%Y%m%d")
    if args.since is not None:
        try:
            datetime.datetime.strptime(args.since, "%Y%m%d")
        except ValueError:
            print(f"ccs: invalid date '{args.since}' (expected YYYYMMDD)", file=sys.stderr)
            return None
        since = args.since
    if args.before is not None:
        try:
            datetime.datetime.strptime(args.before, "%Y%m%d")
        except ValueError:
            print(f"ccs: invalid date '{args.before}' (expected YYYYMMDD)", file=sys.stderr)
            return None
        before = args.before
    return since, before


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
    p.add_argument("--max-file-size", type=float, default=10.0, metavar="MB",
                   help="Skip files larger than this many MB (default: 10).")
    p.add_argument("--workers", type=int, default=0, metavar="N",
                   help="Parallel grep workers for the per-session fallback path "
                        "(default: number of CPU cores). Ignored when rg is available "
                        "since rg parallelises internally.")
    p.add_argument("--local", action="store_true",
                   help="Search only current directory's sessions "
                        "(overrides CCS_DEFAULT_GLOBAL=1).")
    p.add_argument(
        "--exclude-hooks",
        action="store_true",
        help="Exclude sessions whose tag contains 'hook' (e.g. hook-security-check sessions).",
    )
    p.add_argument("--since", metavar="YYYYMMDD",
                   help="Include only sessions started on or after this date (YYYYMMDD).")
    p.add_argument("--before", metavar="YYYYMMDD",
                   help="Include only sessions started before this date (YYYYMMDD).")
    p.add_argument("--days", type=int, metavar="N",
                   help="Include only sessions started within the last N days.")
    fmt = p.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true",
                     help="Output results as a JSON array.")
    fmt.add_argument("--null", action="store_true",
                     help="Output null-delimited basenames (for xargs -0).")
    return p


def _collect_pairs(do_global: bool) -> list[tuple[Path, Path]]:
    """Return [(sessions_dir, project_dir), ...] for the search scope."""
    pairs: list[tuple[Path, Path]] = []
    if do_global:
        try:
            roots = load_session_roots()
        except RootsConfigError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        for root in roots:
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


def _osc8_link(text: str, path: Path) -> str:
    uri = path.as_uri()
    return f"\033]8;;{uri}\033\\{text}\033]8;;\033\\"


def _maybe_link(text: str, path: Path) -> str:
    if (
        sys.stdout.isatty()
        and not os.environ.get("NO_COLOR")
        and os.environ.get("TERM") != "dumb"
    ):
        return _osc8_link(text, path)
    return text


def _format_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.1f} GB"


def _format_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{int(seconds * 1000)}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        m, s = divmod(int(seconds), 60)
        return f"{m}m{s:02d}s"
    h, rem = divmod(int(seconds), 3600)
    m = rem // 60
    return f"{h}h{m:02d}m"


def _output_machine_readable(results: list[_Result], do_null: bool) -> None:
    import json as _json
    if do_null:
        for r in results:
            sys.stdout.write(r.basename + "\x00")
    else:
        data = [
            {
                "basename": r.basename,
                "project_dir": str(r.project_dir),
                "context_lines": r.context_lines,
            }
            for r in results
        ]
        print(_json.dumps(data))


def _truncate_line(line: str, term_width: int | None = None) -> str:
    if term_width is None:
        term_width = shutil.get_terminal_size((80, 20)).columns
    cap = max(20, term_width - 1)
    if len(line) <= cap:
        return line
    return line[: cap - 3] + "..."


class _Progress:
    """TTY-aware single-line progress writer with auto-clear."""

    def __init__(self, enabled: bool, stream=None):
        self.enabled = enabled
        self.stream = stream if stream is not None else sys.stderr
        self._width = 0

    def update(self, line: str) -> None:
        if not self.enabled:
            return
        line = _truncate_line(line)
        padding = " " * max(0, self._width - len(line))
        self.stream.write("\r" + line + padding)
        self.stream.flush()
        self._width = max(self._width, len(line))

    def clear(self) -> None:
        if not self.enabled or self._width == 0:
            return
        self.stream.write("\r" + " " * self._width + "\r")
        self.stream.flush()
        self._width = 0


def _name_search(
    sessions: list[tuple[Path, Path]], query: str, do_global: bool,
    *, do_json: bool = False, do_null: bool = False,
) -> int:
    results: list[_Result] = []
    for sess, proj in sessions:
        if query in sess.name:
            date_key = session_start_date(sess.name)
            assert date_key is not None
            results.append(_Result(date_key, sess.name, proj, []))
    results.sort(key=lambda r: r.date_key, reverse=True)
    if do_json or do_null:
        _output_machine_readable(results, do_null)
        return 0
    if not results:
        print(f"ccs: no sessions match '{query}'", file=sys.stderr)
        all_basenames = [s.name for s, _ in sessions]
        suggestions = difflib.get_close_matches(query, all_basenames, n=3, cutoff=0.4)
        if suggestions:
            print(f"ccs: did you mean: {', '.join(suggestions)}?", file=sys.stderr)
        return 1
    for r in results:
        display_name = _maybe_link(r.basename, r.project_dir / "cc-sessions" / r.basename)
        if do_global:
            print(f"{display_name} ({_display_path(r.project_dir)})")
        else:
            print(display_name)
    return 0


def _print_results(results: list[_Result], do_global: bool) -> None:
    results.sort(key=lambda r: r.date_key, reverse=True)
    for i, r in enumerate(results):
        if i > 0:
            print()
        display_name = _maybe_link(r.basename, r.project_dir / "cc-sessions" / r.basename)
        if do_global:
            print(f"{display_name} ({_display_path(r.project_dir)})")
        else:
            print(display_name)
        for line in r.context_lines:
            print(f"  {line}")


def _compute_eta(elapsed: float, completed: int, total: int) -> float:
    """Total estimated time: X + (X/Y)*(Z-Y). Returns inf if completed is 0."""
    if completed <= 0:
        return float("inf")
    return elapsed + (elapsed / completed) * (total - completed)


def _batch_sizes(total: int) -> list[int]:
    """Return batch sizes for the three-phase rg strategy.

    <= 10: one batch; <= 110: two batches (10 + rest); > 110: three batches (10 + 100 + rest).
    """
    if total <= 0:
        return []
    if total <= 10:
        return [total]
    if total <= 110:
        return [10, total - 10]
    return [10, 100, total - 110]


def _rg_cmd(query: str, max_bytes: int, targets: list[str]) -> list[str]:
    return [
        "rg", "--no-heading", "-n", "-H", "-F",
        "--color=never", "-C", "1",
        f"--max-filesize={max_bytes}",
        "--", query, *targets,
    ]


def _contents_search_with_rg(
    sessions: list[tuple[Path, Path]],
    query: str,
    do_global: bool,
    max_file_size_mb: float,
    *,
    do_json: bool = False,
    do_null: bool = False,
) -> int:
    n = len(sessions)
    noun = "session" if n == 1 else "sessions"
    max_bytes = int(max_file_size_mb * 1024 * 1024)

    print(
        f"ccs: searching {n} {noun} for '{query}' "
        f"(skipping files > {max_file_size_mb:g} MB)...",
        file=sys.stderr,
    )

    # Build sess_by_dir (includes transcript dirs for JSONL search).
    sess_by_dir: dict[str, tuple[Path, Path]] = {}
    for sess, proj in sessions:
        key = str(sess.resolve())
        sess_by_dir[key] = (sess, proj)
        t_dir = transcript_dir_for_project(proj)
        if t_dir.is_dir():
            sess_by_dir[str(t_dir.resolve())] = (sess, proj)

    batches = _batch_sizes(n)
    if not batches:
        print(f"ccs: no sessions match '{query}'", file=sys.stderr)
        return 1

    all_output_lines: list[str] = []
    total_start = time.monotonic()
    completed = 0

    for batch_idx, batch_size in enumerate(batches):
        batch_sessions = sessions[completed : completed + batch_size]

        # Build target list for this batch (sessions + their transcript dirs).
        batch_targets: list[str] = []
        for sess, proj in batch_sessions:
            batch_targets.append(str(sess.resolve()))
            t_dir = transcript_dir_for_project(proj)
            if t_dir.is_dir():
                batch_targets.append(str(t_dir.resolve()))

        cmd = _rg_cmd(query, max_bytes, batch_targets)
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                errors="replace",
                bufsize=1,
            )
        except OSError:
            return _contents_search_with_grep(
                sessions, query, do_global, max_file_size_mb, workers=0,
                do_json=do_json, do_null=do_null,
            )

        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                all_output_lines.append(line.rstrip("\n"))
            proc.wait()
        except BaseException:
            proc.kill()
            proc.wait()
            raise

        if proc.returncode > 1:
            print("ccs: rg failed; falling back to per-session grep", file=sys.stderr)
            return _contents_search_with_grep(
                sessions, query, do_global, max_file_size_mb, workers=0,
                do_json=do_json, do_null=do_null,
            )

        completed += batch_size
        total_elapsed = time.monotonic() - total_start

        if batch_idx < len(batches) - 1:  # not the last batch
            eta = _compute_eta(total_elapsed, completed, n)
            eta_str = f"~{_format_duration(eta)}" if eta != float("inf") else "unknown"
            print(
                f"ccs: batch {batch_idx + 1}/{len(batches)} done "
                f"({completed}/{n} sessions, {_format_duration(total_elapsed)} elapsed). "
                f"Est total: {eta_str}",
                file=sys.stderr,
            )

    total_elapsed = time.monotonic() - total_start
    print(
        f"ccs: searched {n} {noun} in {_format_duration(total_elapsed)}",
        file=sys.stderr,
    )

    # Group output lines by session-dir prefix (longest-prefix wins so nested
    # session dirs don't grab matches that belong to a deeper sibling).
    sorted_keys = sorted(sess_by_dir.keys(), key=len, reverse=True)
    grouped: dict[str, list[str]] = {}
    for line in all_output_lines:
        if line == "--":
            continue
        match_key = None
        for k in sorted_keys:
            if line.startswith(k + "/"):
                match_key = k
                break
        if match_key is None:
            continue
        rel = line[len(match_key) + 1:]
        grouped.setdefault(match_key, []).append(rel)

    if not grouped:
        print(f"ccs: no sessions match '{query}'", file=sys.stderr)
        if do_json or do_null:
            _output_machine_readable([], do_null)
            return 0
        return 1

    results: list[_Result] = []
    for k, lines in grouped.items():
        sess, proj = sess_by_dir[k]
        date_key = session_start_date(sess.name)
        assert date_key is not None
        results.append(_Result(date_key, sess.name, proj, lines))

    # Deduplicate: a session's sess dir and transcript dir both map to the same
    # (sess, proj), so a match in either yields the same _Result. Without
    # dedup, the same session could appear twice.
    seen: set[str] = set()
    deduped: list[_Result] = []
    for r in results:
        if r.basename not in seen:
            seen.add(r.basename)
            deduped.append(r)
    results = deduped

    if do_json or do_null:
        _output_machine_readable(results, do_null)
        return 0
    _print_results(results, do_global)
    return 0


def _contents_search_with_grep(
    sessions: list[tuple[Path, Path]],
    query: str,
    do_global: bool,
    max_file_size_mb: float,
    workers: int,
    *,
    do_json: bool = False,
    do_null: bool = False,
) -> int:
    """Fallback path used when rg is unavailable. Plain grep has no
    --max-filesize equivalent, so we keep an indexing pre-pass to enforce
    the size cap at the Python layer."""
    n = len(sessions)
    noun = "session" if n == 1 else "sessions"
    max_bytes = int(max_file_size_mb * 1024 * 1024)
    show_progress = sys.stderr.isatty()

    print(
        f"ccs: indexing files in {n} {noun} "
        f"(install ripgrep for ~3x speedup)...",
        file=sys.stderr,
    )

    indexed: list[tuple[Path, Path, list[Path], int]] = []
    total_files = 0
    total_bytes = 0
    total_skipped = 0
    progress = _Progress(show_progress)
    for i, (sess, proj) in enumerate(sessions, start=1):
        files, bytes_, skipped = enumerate_session_files(sess, max_bytes=max_bytes)
        t_dir = transcript_dir_for_project(proj)
        if t_dir.is_dir():
            t_files, t_bytes, t_skipped = enumerate_session_files(t_dir, max_bytes=max_bytes)
            files = files + t_files
            bytes_ = bytes_ + t_bytes
            skipped = skipped + t_skipped
        indexed.append((sess, proj, files, bytes_))
        total_files += len(files)
        total_bytes += bytes_
        total_skipped += skipped
        progress.update(
            f"indexing [{i}/{n}] {total_files} files, {_format_size(total_bytes)} so far"
        )
    progress.clear()

    skipped_msg = (
        f" (skipped {total_skipped} files > {max_file_size_mb:g} MB)"
        if total_skipped else ""
    )
    print(
        f"ccs: indexed {total_files} files ({_format_size(total_bytes)}) "
        f"across {n} {noun}{skipped_msg}",
        file=sys.stderr,
    )
    if total_files == 0:
        print(f"ccs: no sessions match '{query}'", file=sys.stderr)
        return 1

    if workers <= 0:
        workers = os.cpu_count() or 4
    workers = max(1, min(workers, n or 1))
    print(
        f"ccs: searching with {workers} parallel "
        f"{'worker' if workers == 1 else 'workers'}...",
        file=sys.stderr,
    )

    start = time.monotonic()
    results: list[_Result] = []
    completed = 0
    completed_files = 0
    completed_bytes = 0
    progress = _Progress(show_progress)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(grep_files, files, query, 1, sess): (sess, proj, files, bytes_)
            for sess, proj, files, bytes_ in indexed
        }
        for future in as_completed(futures):
            sess, proj, files, bytes_ = futures[future]
            try:
                ctx = future.result()
            except Exception:  # noqa: BLE001
                ctx = []
            if ctx:
                date_key = session_start_date(sess.name)
                assert date_key is not None
                results.append(_Result(date_key, sess.name, proj, ctx))
            completed += 1
            completed_files += len(files)
            completed_bytes += bytes_
            elapsed = time.monotonic() - start
            total_est = _compute_eta(elapsed, completed, n)
            total_est_str = f"~{_format_duration(total_est)}" if total_est != float("inf") else "?"
            pct = int(100 * completed / n)
            progress.update(
                f"searching [{completed}/{n}] ({pct}%)  "
                f"{completed_files}/{total_files} files, "
                f"{_format_size(completed_bytes)}  "
                f"elapsed {_format_duration(elapsed)}  "
                f"est total {total_est_str}"
            )
    progress.clear()
    elapsed = time.monotonic() - start

    print(
        f"ccs: searched {total_files} files ({_format_size(total_bytes)}) "
        f"across {n} {noun} in {_format_duration(elapsed)}",
        file=sys.stderr,
    )

    if not results:
        if do_json or do_null:
            _output_machine_readable([], do_null)
            return 0
        print(f"ccs: no sessions match '{query}'", file=sys.stderr)
        return 1
    if do_json or do_null:
        _output_machine_readable(results, do_null)
        return 0
    _print_results(results, do_global)
    return 0


def _contents_search(
    sessions: list[tuple[Path, Path]],
    query: str,
    do_global: bool,
    max_file_size_mb: float,
    workers: int,
    *,
    do_json: bool = False,
    do_null: bool = False,
) -> int:
    if shutil.which("rg"):
        return _contents_search_with_rg(
            sessions, query, do_global, max_file_size_mb,
            do_json=do_json, do_null=do_null,
        )
    return _contents_search_with_grep(
        sessions, query, do_global, max_file_size_mb, workers,
        do_json=do_json, do_null=do_null,
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    # Validate date filter args eagerly so bad values are caught before any I/O.
    date_filter: tuple[str | None, str | None] | None = None
    if args.since or args.before or args.days:
        date_filter = _parse_date_filter(args)
        if date_filter is None:
            return 1

    effective_global = args.do_global or (
        os.environ.get("CCS_DEFAULT_GLOBAL", "").strip() not in ("", "0")
        and not args.local
    )

    pairs = _collect_pairs(effective_global)
    if not pairs:
        if effective_global:
            print("ccs: no sessions found in any configured root", file=sys.stderr)
        else:
            print("ccs: no cc-sessions/ in current directory", file=sys.stderr)
        return 1

    sessions: list[tuple[Path, Path]] = []
    for cc, proj in pairs:
        for sess in iter_sessions(cc):
            if session_start_date(sess.name) is None:
                continue
            sessions.append((sess, proj))

    if args.exclude_hooks:
        before = len(sessions)
        sessions = [(s, p) for s, p in sessions if not _is_hook_session(s.name)]
        excluded = before - len(sessions)
        if excluded:
            noun = "session" if excluded == 1 else "sessions"
            print(
                f"ccs: excluded {excluded} hook {noun}",
                file=sys.stderr,
            )

    if date_filter is not None:
        since_key, before_key = date_filter
        sessions = [
            (s, p) for s, p in sessions
            if (since_key is None or session_start_date(s.name) >= since_key)
            and (before_key is None or session_start_date(s.name) < before_key)
        ]

    if args.contents:
        return _contents_search(
            sessions, args.query, effective_global,
            args.max_file_size, args.workers,
            do_json=args.json, do_null=args.null,
        )
    return _name_search(
        sessions, args.query, effective_global,
        do_json=args.json, do_null=args.null,
    )


if __name__ == "__main__":
    sys.exit(main())
