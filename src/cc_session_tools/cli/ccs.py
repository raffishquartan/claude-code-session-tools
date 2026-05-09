from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from cc_session_tools import __version__
from cc_session_tools.lib.roots import load_session_roots
from cc_session_tools.lib.sessions import (
    enumerate_session_files,
    grep_files,
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
    p.add_argument("--max-file-size", type=float, default=10.0, metavar="MB",
                   help="Skip files larger than this many MB (default: 10).")
    p.add_argument("--workers", type=int, default=0, metavar="N",
                   help="Parallel grep workers for the per-session fallback path "
                        "(default: number of CPU cores). Ignored when rg is available "
                        "since rg parallelises internally.")
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
    sessions: list[tuple[Path, Path]], query: str, do_global: bool
) -> int:
    results: list[_Result] = []
    for sess, proj in sessions:
        if query in sess.name:
            date_key = session_start_date(sess.name)
            assert date_key is not None
            results.append(_Result(date_key, sess.name, proj, []))
    if not results:
        print(f"ccs: no sessions match '{query}'", file=sys.stderr)
        return 1
    results.sort(key=lambda r: r.date_key, reverse=True)
    for r in results:
        if do_global:
            print(f"{r.basename} ({_display_path(r.project_dir)})")
        else:
            print(r.basename)
    return 0


def _print_results(results: list[_Result], do_global: bool) -> None:
    results.sort(key=lambda r: r.date_key, reverse=True)
    for i, r in enumerate(results):
        if i > 0:
            print()
        if do_global:
            print(f"{r.basename} ({_display_path(r.project_dir)})")
        else:
            print(r.basename)
        for line in r.context_lines:
            print(f"  {line}")


def _index_phase(
    sessions: list[tuple[Path, Path]], max_bytes: int, show_progress: bool
) -> tuple[list[tuple[Path, Path, list[Path], int]], int, int, int]:
    """Phase 1: walk every session, gathering files within the size cap.
    Returns (indexed_sessions, total_files, total_bytes, total_skipped)."""
    n = len(sessions)
    indexed: list[tuple[Path, Path, list[Path], int]] = []
    total_files = 0
    total_bytes = 0
    total_skipped = 0
    progress = _Progress(show_progress)
    for i, (sess, proj) in enumerate(sessions, start=1):
        files, bytes_, skipped = enumerate_session_files(sess, max_bytes=max_bytes)
        indexed.append((sess, proj, files, bytes_))
        total_files += len(files)
        total_bytes += bytes_
        total_skipped += skipped
        progress.update(
            f"indexing [{i}/{n}] {total_files} files, {_format_size(total_bytes)} so far"
        )
    progress.clear()
    return indexed, total_files, total_bytes, total_skipped


def _grep_with_streaming_rg(
    indexed: list[tuple[Path, Path, list[Path], int]],
    query: str,
    max_bytes: int,
    show_progress: bool,
) -> tuple[list[_Result], float] | None:
    """Single streaming rg invocation across all session dirs. Returns
    (results, elapsed_seconds), or None if rg failed and the caller should
    fall through to the per-session pool."""
    sess_by_dir: dict[str, tuple[Path, Path]] = {}
    sess_dirs: list[str] = []
    for sess, proj, _, _ in indexed:
        key = str(sess.resolve())
        sess_by_dir[key] = (sess, proj)
        sess_dirs.append(key)

    cmd = [
        "rg", "--no-heading", "-n", "-H", "-F",
        "--color=never", "-C", "1",
        f"--max-filesize={max_bytes}",
        "--", query, *sess_dirs,
    ]
    start = time.monotonic()
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
        return None

    progress = _Progress(show_progress)
    output_lines: list[str] = []
    last_update = start
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            output_lines.append(line.rstrip("\n"))
            now = time.monotonic()
            if show_progress and now - last_update >= 0.3:
                last_update = now
                progress.update(
                    f"searching with rg... [elapsed {_format_duration(now - start)}]  "
                    f"{len(output_lines)} match lines so far"
                )
        proc.wait()
    except BaseException:
        proc.kill()
        proc.wait()
        raise

    progress.clear()
    elapsed = time.monotonic() - start

    if proc.returncode > 1:
        # rg failed (not just "no matches"). Treat as fallback trigger.
        return None

    # Group output lines by session-dir prefix.
    sorted_keys = sorted(sess_by_dir.keys(), key=len, reverse=True)
    grouped: dict[str, list[str]] = {}
    for line in output_lines:
        if line == "--":
            continue
        # Strip leading "/abs/sess/" so output matches the per-session format.
        match_key = None
        for k in sorted_keys:
            if line.startswith(k + "/"):
                match_key = k
                break
        if match_key is None:
            continue
        rel = line[len(match_key) + 1:]
        grouped.setdefault(match_key, []).append(rel)

    results: list[_Result] = []
    for k, lines in grouped.items():
        sess, proj = sess_by_dir[k]
        date_key = session_start_date(sess.name)
        assert date_key is not None
        results.append(_Result(date_key, sess.name, proj, lines))
    return results, elapsed


def _grep_with_per_session_pool(
    indexed: list[tuple[Path, Path, list[Path], int]],
    query: str,
    workers: int,
    show_progress: bool,
    total_files: int,
) -> tuple[list[_Result], float]:
    """Fallback when rg isn't available: per-session grep_files via a thread
    pool. Slower than the streaming-rg path but works with plain GNU grep."""
    n = len(indexed)
    if workers <= 0:
        workers = os.cpu_count() or 4
    workers = max(1, min(workers, n or 1))

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
            eta = (elapsed / completed) * (n - completed) if completed else 0
            pct = int(100 * completed / n)
            progress.update(
                f"searching [{completed}/{n}] ({pct}%)  "
                f"{completed_files}/{total_files} files, "
                f"{_format_size(completed_bytes)}  "
                f"elapsed {_format_duration(elapsed)}  "
                f"ETA {_format_duration(eta)}"
            )
    progress.clear()
    return results, time.monotonic() - start


def _contents_search(
    sessions: list[tuple[Path, Path]],
    query: str,
    do_global: bool,
    max_file_size_mb: float,
    workers: int,
) -> int:
    n = len(sessions)
    noun = "session" if n == 1 else "sessions"
    max_bytes = int(max_file_size_mb * 1024 * 1024)
    show_progress = sys.stderr.isatty()

    # ---- Phase 1: indexing ----
    print(f"ccs: indexing files in {n} {noun}...", file=sys.stderr)
    indexed, total_files, total_bytes, total_skipped = _index_phase(
        sessions, max_bytes, show_progress
    )

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

    # ---- Phase 2: grep ----
    rg_path = shutil.which("rg")
    if rg_path:
        print("ccs: searching with rg (single streaming invocation)...", file=sys.stderr)
        outcome = _grep_with_streaming_rg(indexed, query, max_bytes, show_progress)
        if outcome is None:
            print("ccs: rg failed; falling back to per-session grep", file=sys.stderr)
            results, elapsed = _grep_with_per_session_pool(
                indexed, query, workers, show_progress, total_files
            )
        else:
            results, elapsed = outcome
    else:
        print(
            f"ccs: searching with per-session grep "
            f"(install ripgrep for ~3x speedup)...",
            file=sys.stderr,
        )
        results, elapsed = _grep_with_per_session_pool(
            indexed, query, workers, show_progress, total_files
        )

    print(
        f"ccs: searched {total_files} files ({_format_size(total_bytes)}) "
        f"across {n} {noun} in {_format_duration(elapsed)}",
        file=sys.stderr,
    )

    if not results:
        print(f"ccs: no sessions match '{query}'", file=sys.stderr)
        return 1

    _print_results(results, do_global)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    pairs = _collect_pairs(args.do_global)
    if not pairs:
        if args.do_global:
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

    if args.contents:
        return _contents_search(
            sessions, args.query, args.do_global,
            args.max_file_size, args.workers,
        )
    return _name_search(sessions, args.query, args.do_global)


if __name__ == "__main__":
    sys.exit(main())
