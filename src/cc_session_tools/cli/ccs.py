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
from dataclasses import dataclass, field
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

# Sentinel used to distinguish "flag present with no value" from "no flag given".
_NO_VALUE: str = "\x00_NO_VALUE_\x00"


def _is_hook_session(basename: str) -> bool:
    from cc_session_tools.lib.sessions import session_tag
    tag = session_tag(basename)
    return tag is not None and "hook" in tag.lower()


def _parse_since_to_yyyymmdd(raw: str) -> str | None:
    """Parse --since value to YYYYMMDD string.

    Accepted formats (in order of attempt):
      - YYYYMMDD        (legacy, kept for backward compat)
      - YYYY-MM-DD      (ISO date)
      - YYYY-MM-DDTHH:MM
      - YYYY-MM-DDTHH:MM:SS

    The hour/minute/second are ignored: only the date portion is used for
    comparison against session start dates (which are stored as YYYYMMDD
    derived from the session basename, so any time on --since is treated
    as 'session start date >= the date portion of --since').

    Returns the YYYYMMDD string on success, None on failure.
    """
    _FORMATS = ["%Y%m%d", "%Y-%m-%d", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"]
    for fmt in _FORMATS:
        try:
            dt = datetime.datetime.strptime(raw, fmt)
            return dt.strftime("%Y%m%d")
        except ValueError:
            continue
    return None


def _parse_date_filter(args) -> tuple[str | None, str | None] | None:
    """Return (since_key, before_key) in YYYYMMDD format, or None on parse error."""
    since: str | None = None
    before: str | None = None
    if args.days is not None:
        cutoff = datetime.date.today() - datetime.timedelta(days=args.days)
        since = cutoff.strftime("%Y%m%d")
    if args.since is not None:
        result = _parse_since_to_yyyymmdd(args.since)
        if result is None:
            print(
                f"ccs: invalid date '{args.since}' "
                "(expected YYYYMMDD, YYYY-MM-DD, or YYYY-MM-DDTHH:MM[:SS])",
                file=sys.stderr,
            )
            return None
        since = result
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
    scope: str = "name"  # one of: "name", "contents", "messages"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ccs",
        description="Search Claude Code sessions by name/date or file contents.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    # Positional query is now optional; scope flags may supply their own queries.
    p.add_argument(
        "query",
        nargs="?",
        default=None,
        help=(
            "Substring to match against session names (default) or contents. "
            "Optional when a scope flag (--name, --contents, --messages) is given "
            "with its own value."
        ),
    )

    # Scope flags: each takes an optional value (nargs="?").
    # When the flag is present without a value, const=_NO_VALUE signals "use positional".
    p.add_argument(
        "--name",
        nargs="?",
        const=_NO_VALUE,
        default=None,
        metavar="QUERY",
        help=(
            "Search session basenames. If QUERY is omitted, uses the positional query. "
            "This is the default scope when no scope flag is given."
        ),
    )
    p.add_argument(
        "--contents",
        nargs="?",
        const=_NO_VALUE,
        default=None,
        metavar="QUERY",
        help=(
            "Search files under cc-sessions/<session>/working/ and out/ only. "
            "If QUERY is omitted, uses the positional query. "
            "(Legacy --contents with no value keeps the same behaviour.)"
        ),
    )
    p.add_argument(
        "--messages",
        nargs="?",
        const=_NO_VALUE,
        default=None,
        metavar="QUERY",
        help=(
            "Search JSONL transcript file(s) under "
            "~/.claude/projects/<encoded-cwd>/*.jsonl. "
            "If QUERY is omitted, uses the positional query."
        ),
    )

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
    p.add_argument(
        "--since",
        metavar="DATE",
        help=(
            "Include only sessions started on or after this date. "
            "Accepted: YYYYMMDD (legacy), YYYY-MM-DD, YYYY-MM-DDTHH:MM, YYYY-MM-DDTHH:MM:SS. "
            "Time portion is ignored (only the date part is compared against session start dates)."
        ),
    )
    p.add_argument("--before", metavar="YYYYMMDD",
                   help="Include only sessions started before this date (YYYYMMDD).")
    p.add_argument("--days", type=int, metavar="N",
                   help="Include only sessions started within the last N days.")
    fmt = p.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true",
                     help="Output results as a JSON array.")
    fmt.add_argument("--null", action="store_true",
                     help="Output null-delimited basenames (for xargs -0).")
    p.add_argument(
        "--sort",
        choices=["datetime", "alpha"],
        default="datetime",
        help=(
            "Sort order for results. "
            "'datetime' (default) = newest first by session start date. "
            "'alpha' = ascending alphabetical by session basename."
        ),
    )
    p.add_argument("--debug", action="store_true",
                   help="Enable debug output (also: CCX_DEBUG=1).")
    return p


def _resolve_scope_queries(args) -> dict[str, str] | None:
    """Resolve scope flags to a dict mapping scope name -> query string.

    Returns None if resolution fails (missing query for a scope). Prints a
    clear error message before returning None in that case.

    Possible scopes: "name", "contents", "messages".

    Logic:
      - If no scope flag is given → {"name": positional_query} (unchanged legacy).
      - If scope flags are given:
        - Flag with explicit value: use that value for this scope.
        - Flag with no value (const=_NO_VALUE): use positional query.
        - If positional is also None and any scope needs it → error.
    """
    flag_map = {
        "name": args.name,
        "contents": args.contents,
        "messages": args.messages,
    }
    active_scopes = {k: v for k, v in flag_map.items() if v is not None}

    if not active_scopes:
        # Legacy behaviour: no scope flags → name search using positional.
        if args.query is None:
            # Argparse should have caught this already, but be defensive.
            print("ccs: error: a query is required", file=sys.stderr)
            return None
        return {"name": args.query}

    # One or more scope flags given. Resolve each.
    resolved: dict[str, str] = {}
    for scope, val in active_scopes.items():
        if val == _NO_VALUE:
            # Flag given without its own value → use positional.
            if args.query is None:
                print(
                    f"ccs: error: --{scope} was given without a query value "
                    "and no positional query was provided.\n"
                    "Usage: ccs <query> --{scope}  OR  ccs --{scope} <query-value>",
                    file=sys.stderr,
                )
                return None
            resolved[scope] = args.query
        else:
            resolved[scope] = val

    return resolved


def _sort_results(results: list[_Result], sort: str) -> list[_Result]:
    """Sort results in place and return list. 'datetime' = newest first; 'alpha' = ascending."""
    if sort == "alpha":
        results.sort(key=lambda r: r.basename)
    else:
        results.sort(key=lambda r: r.date_key, reverse=True)
    return results


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
                "scope": r.scope,
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


_PICKER_MAX = 10


def _maybe_pick_and_resume(results: list[_Result], do_global: bool) -> int | None:
    """If <=10 results and stdin is a TTY, show picker and exec into ccr.

    Returns exit code, or None if picker was not shown (>10 results or not a TTY).
    """
    if len(results) > _PICKER_MAX or not sys.stdin.isatty():
        return None
    from cc_session_tools.lib.picker import pick_from_list
    labels = [
        f"{r.basename} ({_display_path(r.project_dir)})" if do_global else r.basename
        for r in results
    ]
    idx = pick_from_list(labels)
    if idx is None:
        return 0
    os.execvp("ccr", ["ccr", results[idx].basename])
    return 0  # unreachable; satisfies type checker


def _print_results(
    results: list[_Result],
    do_global: bool,
    *,
    multi_scope: bool = False,
) -> None:
    """Print results to stdout.

    When multi_scope is True, each context line is prefixed with the scope
    tag (e.g. "[name]", "[contents]", "[messages]") so the user can tell
    which scope produced each hit.

    A blank line is printed between successive results only when at least one
    of them carries context lines, so pure name-search results (no context)
    remain compact (one line per result).
    """
    prev_had_context = False
    for i, r in enumerate(results):
        has_context = bool(r.context_lines)
        if i > 0 and (prev_had_context or has_context):
            print()
        prev_had_context = has_context
        display_name = _maybe_link(r.basename, r.project_dir / "cc-sessions" / r.basename)
        # In multi-scope mode, tag the header line with the scope so name matches
        # are distinguishable from content/message matches.
        scope_prefix = f"[{r.scope}] " if multi_scope else ""
        if do_global:
            print(f"{scope_prefix}{display_name} ({_display_path(r.project_dir)})")
        else:
            print(f"{scope_prefix}{display_name}")
        for line in r.context_lines:
            if multi_scope:
                print(f"  [{r.scope}] {line}")
            else:
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


def _name_search(
    sessions: list[tuple[Path, Path]], query: str, do_global: bool,
    sort: str = "datetime",
    *,
    do_json: bool = False,
    do_null: bool = False,
    scope_tag: str = "name",
    multi_scope: bool = False,
) -> list[_Result]:
    """Search session basenames. Returns list of matching _Result objects."""
    results: list[_Result] = []
    for sess, proj in sessions:
        if query in sess.name:
            date_key = session_start_date(sess.name)
            assert date_key is not None
            results.append(_Result(date_key, sess.name, proj, [], scope=scope_tag))
    return results


def _build_contents_targets(
    sessions: list[tuple[Path, Path]],
    *,
    include_working: bool,
    include_transcripts: bool,
) -> tuple[dict[str, tuple[Path, Path]], list[str]]:
    """Build (sess_by_dir, target_paths) for rg/grep invocation.

    include_working: include cc-sessions/<basename>/ dirs
    include_transcripts: include ~/.claude/projects/<encoded>/ dirs
    """
    sess_by_dir: dict[str, tuple[Path, Path]] = {}
    target_dirs: list[str] = []

    for sess, proj in sessions:
        if include_working:
            key = str(sess.resolve())
            if key not in sess_by_dir:
                sess_by_dir[key] = (sess, proj)
                target_dirs.append(key)

        if include_transcripts:
            t_dir = transcript_dir_for_project(proj)
            if t_dir.is_dir():
                key = str(t_dir.resolve())
                if key not in sess_by_dir:
                    sess_by_dir[key] = (sess, proj)
                    target_dirs.append(key)

    return sess_by_dir, target_dirs


def _contents_search_with_rg(
    sessions: list[tuple[Path, Path]],
    query: str,
    do_global: bool,
    max_file_size_mb: float,
    *,
    include_working: bool = True,
    include_transcripts: bool = False,
    do_json: bool = False,
    do_null: bool = False,
    scope_tag: str = "contents",
) -> list[_Result]:
    """rg-based search. Returns list of matching _Result objects."""
    n = len(sessions)
    noun = "session" if n == 1 else "sessions"
    max_bytes = int(max_file_size_mb * 1024 * 1024)

    print(
        f"ccs: searching {n} {noun} for '{query}' "
        f"(skipping files > {max_file_size_mb:g} MB)...",
        file=sys.stderr,
    )

    sess_by_dir, _ = _build_contents_targets(
        sessions,
        include_working=include_working,
        include_transcripts=include_transcripts,
    )

    batches = _batch_sizes(n)
    if not batches:
        print(f"ccs: no sessions match '{query}'", file=sys.stderr)
        return []

    all_output_lines: list[str] = []
    total_start = time.monotonic()
    completed = 0

    for batch_idx, batch_size in enumerate(batches):
        batch_sessions = sessions[completed : completed + batch_size]

        batch_targets: list[str] = []
        for sess, proj in batch_sessions:
            if include_working:
                batch_targets.append(str(sess.resolve()))
            if include_transcripts:
                t_dir = transcript_dir_for_project(proj)
                if t_dir.is_dir():
                    batch_targets.append(str(t_dir.resolve()))

        if not batch_targets:
            completed += batch_size
            continue

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
                include_working=include_working,
                include_transcripts=include_transcripts,
                do_json=do_json, do_null=do_null,
                scope_tag=scope_tag,
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
                include_working=include_working,
                include_transcripts=include_transcripts,
                do_json=do_json, do_null=do_null,
                scope_tag=scope_tag,
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

    # Rebuild sess_by_dir with all sessions (not just batched targets) for grouping.
    sess_by_dir, _ = _build_contents_targets(
        sessions,
        include_working=include_working,
        include_transcripts=include_transcripts,
    )

    # Group output lines by session-dir prefix (longest-prefix wins).
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
        return []

    results: list[_Result] = []
    for k, lines in grouped.items():
        sess, proj = sess_by_dir[k]
        date_key = session_start_date(sess.name)
        assert date_key is not None
        results.append(_Result(date_key, sess.name, proj, lines, scope=scope_tag))

    # Deduplicate: merge context lines for the same session basename.
    seen: dict[str, _Result] = {}
    for r in results:
        if r.basename not in seen:
            seen[r.basename] = r
        else:
            seen[r.basename].context_lines.extend(r.context_lines)
    return list(seen.values())


def _contents_search_with_grep(
    sessions: list[tuple[Path, Path]],
    query: str,
    do_global: bool,
    max_file_size_mb: float,
    workers: int,
    *,
    include_working: bool = True,
    include_transcripts: bool = False,
    do_json: bool = False,
    do_null: bool = False,
    scope_tag: str = "contents",
) -> list[_Result]:
    """Fallback grep path. Returns list of matching _Result objects."""
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
        files: list[Path] = []
        bytes_: int = 0
        skipped: int = 0

        if include_working:
            w_files, w_bytes, w_skipped = enumerate_session_files(sess, max_bytes=max_bytes)
            files = files + w_files
            bytes_ = bytes_ + w_bytes
            skipped = skipped + w_skipped

        if include_transcripts:
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
        return []

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
                results.append(_Result(date_key, sess.name, proj, ctx, scope=scope_tag))
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

    return results


def _do_contents_search(
    sessions: list[tuple[Path, Path]],
    query: str,
    do_global: bool,
    max_file_size_mb: float,
    workers: int,
    *,
    include_working: bool = True,
    include_transcripts: bool = False,
    do_json: bool = False,
    do_null: bool = False,
    scope_tag: str = "contents",
) -> list[_Result]:
    """Dispatcher: use rg if available, else fall back to grep."""
    if shutil.which("rg"):
        return _contents_search_with_rg(
            sessions, query, do_global, max_file_size_mb,
            include_working=include_working,
            include_transcripts=include_transcripts,
            do_json=do_json, do_null=do_null,
            scope_tag=scope_tag,
        )
    return _contents_search_with_grep(
        sessions, query, do_global, max_file_size_mb, workers,
        include_working=include_working,
        include_transcripts=include_transcripts,
        do_json=do_json, do_null=do_null,
        scope_tag=scope_tag,
    )


def _merge_results(results_by_scope: list[list[_Result]]) -> list[_Result]:
    """Merge results from multiple scopes by session basename.

    Sessions that appear in multiple scopes get multiple _Result entries
    (one per scope) so the scope tag and context lines remain distinct.
    """
    merged: list[_Result] = []
    for scope_results in results_by_scope:
        merged.extend(scope_results)
    return merged


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.debug:
        os.environ["CCX_DEBUG"] = "1"
    from cc_session_tools.lib.debug import debug

    # Resolve scope flags and queries.
    scope_queries = _resolve_scope_queries(args)
    if scope_queries is None:
        return 1

    multi_scope = len(scope_queries) > 1

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

    debug(f"scope: {'global' if effective_global else f'cwd={Path.cwd()}'}")
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
    debug(f"sessions found: {len(sessions)}")

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

    # Execute each active scope search and collect results.
    all_results: list[_Result] = []

    for scope, query in scope_queries.items():
        if scope == "name":
            scope_results = _name_search(
                sessions, query, effective_global,
                sort=args.sort,
                do_json=args.json, do_null=args.null,
                scope_tag="name",
                multi_scope=multi_scope,
            )
            all_results.extend(scope_results)

        elif scope == "contents":
            scope_results = _do_contents_search(
                sessions, query, effective_global,
                args.max_file_size, args.workers,
                include_working=True,
                include_transcripts=False,
                do_json=args.json, do_null=args.null,
                scope_tag="contents",
            )
            all_results.extend(scope_results)

        elif scope == "messages":
            scope_results = _do_contents_search(
                sessions, query, effective_global,
                args.max_file_size, args.workers,
                include_working=False,
                include_transcripts=True,
                do_json=args.json, do_null=args.null,
                scope_tag="messages",
            )
            all_results.extend(scope_results)

    # Sort the combined results.
    all_results = _sort_results(all_results, args.sort)

    if args.json or args.null:
        _output_machine_readable(all_results, args.null)
        return 0

    if not all_results:
        print(f"ccs: no sessions match", file=sys.stderr)
        # Only show did-you-mean for pure name searches.
        if not multi_scope and "name" in scope_queries:
            query = scope_queries["name"]
            all_basenames = [s.name for s, _ in sessions]
            suggestions = difflib.get_close_matches(query, all_basenames, n=3, cutoff=0.4)
            if suggestions:
                print(f"ccs: did you mean: {', '.join(suggestions)}?", file=sys.stderr)
        return 1

    pick_rc = _maybe_pick_and_resume(all_results, effective_global)
    if pick_rc is not None:
        return pick_rc

    _print_results(all_results, effective_global, multi_scope=multi_scope)
    return 0


if __name__ == "__main__":
    sys.exit(main())
