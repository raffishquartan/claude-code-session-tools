from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

SESSION_BASENAME_RE = re.compile(r"^(\d{8})(?:-to-\d{8})?-")
SESSION_FULL_RE = re.compile(r"^(\d{8})(?:-to-\d{8})?-(.+)$")


@dataclass(frozen=True)
class SessionMatch:
    basename: str
    project_dir: Path
    session_dir: Path

    @property
    def start_date(self) -> str:
        m = SESSION_BASENAME_RE.match(self.basename)
        # iter_sessions guarantees the basename matched, so this is safe.
        assert m is not None
        return m.group(1)


def is_session_basename(name: str) -> bool:
    return SESSION_BASENAME_RE.match(name) is not None


def session_start_date(name: str) -> str | None:
    m = SESSION_BASENAME_RE.match(name)
    return m.group(1) if m else None


def session_tag(name: str) -> str | None:
    """Return the tag portion of a session basename (the part after the
    YYYYMMDD- prefix, or after the -to-YYYYMMDD- form), or None if the
    basename does not match the session-name format."""
    m = SESSION_FULL_RE.match(name)
    return m.group(2) if m else None


def iter_sessions(sessions_dir: Path) -> Iterator[Path]:
    if not sessions_dir.is_dir():
        return
    for child in sessions_dir.iterdir():
        if child.is_dir() and is_session_basename(child.name):
            yield child


def find_matching_sessions(fragment: str, roots: list[Path]) -> list[SessionMatch]:
    out: list[SessionMatch] = []
    for root in roots:
        if not root.is_dir():
            continue
        for proj in root.iterdir():
            if not proj.is_dir():
                continue
            cc = proj / "cc-sessions"
            for sess in iter_sessions(cc):
                if fragment in sess.name:
                    out.append(SessionMatch(
                        basename=sess.name,
                        project_dir=proj,
                        session_dir=sess,
                    ))
    return out


def _is_binary(sample: bytes) -> bool:
    return b"\x00" in sample


def enumerate_session_files(
    session_dir: Path, max_bytes: int | None = None
) -> tuple[list[Path], int, int]:
    """Walk session_dir and return (files, total_bytes, skipped). When
    max_bytes is set, files larger than that are excluded and counted in
    `skipped`. Cheap (stat only, no read); used by ccs to give the user
    an upfront indication of how much work the search will involve."""
    files: list[Path] = []
    total_bytes = 0
    skipped = 0
    for p in session_dir.rglob("*"):
        if not p.is_file():
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if max_bytes is not None and size > max_bytes:
            skipped += 1
            continue
        files.append(p)
        total_bytes += size
    return files, total_bytes, skipped


def grep_session(session_dir: Path, query: str, context: int = 1) -> list[str]:
    """Convenience wrapper: enumerate files in `session_dir` then grep them
    via `grep_files`. Preserved for callers that don't need the two-phase
    enumerate-then-grep flow."""
    files, _, _ = enumerate_session_files(session_dir)
    if not files:
        return []
    return grep_files(files, query, context=context, cwd=session_dir)


def grep_files(
    files: list[Path], query: str, context: int = 1, cwd: Path | None = None
) -> list[str]:
    """Grep `files` for `query` (fixed string, not regex). Returns a flat
    list of output lines including `path:line:content` for matches and
    `path-line-content` for ±context lines, with `--` separators between
    non-adjacent groups (standard grep -A/-B output).

    Prefers `rg` (ripgrep) > GNU `grep` > a pure-Python fallback.

    `cwd` controls how paths are reported in the output (paths in subprocess
    output are relative to cwd). Pass the session directory so output reads
    `working/WORKLOG.md:42:...` rather than absolute paths."""
    if not files:
        return []
    if shutil.which("rg"):
        out = _grep_files_with_rg(files, query, context, cwd)
        if out is not None:
            return out
    if shutil.which("grep"):
        out = _grep_files_with_grep(files, query, context, cwd)
        if out is not None:
            return out
    return _grep_files_python(files, query, context)


def _relativize(files: list[Path], cwd: Path | None) -> list[str]:
    if cwd is None:
        return [str(f) for f in files]
    out: list[str] = []
    for f in files:
        try:
            out.append(str(f.relative_to(cwd)))
        except ValueError:
            out.append(str(f))
    return out


def _grep_files_with_grep(
    files: list[Path], query: str, context: int, cwd: Path | None
) -> list[str] | None:
    """Run GNU grep over an explicit file list. Returns None on hard errors
    so the caller can fall through to the next runner."""
    paths = _relativize(files, cwd)
    cmd = [
        "grep", "-InHF", "--color=never",
        "-A", str(context), "-B", str(context),
        "--", query, *paths,
    ]
    try:
        res = subprocess.run(
            cmd, cwd=cwd, capture_output=True,
            text=True, errors="replace",
        )
    except OSError:
        return None
    # grep exit codes: 0=match, 1=no match, 2=error. Treat large file lists
    # that overrun ARG_MAX as a fall-through trigger (returncode 2 is also
    # what grep returns for that, so the existing >1 check handles it).
    if res.returncode > 1:
        return None
    if not res.stdout:
        return []
    return res.stdout.rstrip("\n").split("\n")


def _grep_files_with_rg(
    files: list[Path], query: str, context: int, cwd: Path | None
) -> list[str] | None:
    """Run ripgrep over an explicit file list."""
    paths = _relativize(files, cwd)
    cmd = [
        "rg", "--no-heading", "-n", "-H", "-F",
        "--color=never", "-C", str(context),
        "--", query, *paths,
    ]
    try:
        res = subprocess.run(
            cmd, cwd=cwd, capture_output=True,
            text=True, errors="replace",
        )
    except OSError:
        return None
    if res.returncode > 1:
        return None
    if not res.stdout:
        return []
    return res.stdout.rstrip("\n").split("\n")


def _grep_files_python(files: list[Path], query: str, context: int) -> list[str]:
    """Pure-Python fallback. Slower than grep/rg by 10-100x on large trees."""
    out: list[str] = []
    first_group = True
    for f in sorted(files):
        try:
            sample = f.read_bytes()[:8192]
        except OSError:
            continue
        if _is_binary(sample):
            continue
        try:
            lines = f.read_text(errors="replace").splitlines()
        except OSError:
            continue
        match_indices = [i for i, ln in enumerate(lines) if query in ln]
        if not match_indices:
            continue
        windows: list[tuple[int, int]] = []
        for idx in match_indices:
            lo = max(0, idx - context)
            hi = min(len(lines), idx + context + 1)
            if windows and lo <= windows[-1][1]:
                windows[-1] = (windows[-1][0], max(windows[-1][1], hi))
            else:
                windows.append((lo, hi))
        for lo, hi in windows:
            if not first_group:
                out.append("--")
            first_group = False
            out.extend(lines[lo:hi])
    return out


def transcript_dir_for_project(project_dir: Path) -> Path:
    """Return the ~/.claude/projects/<encoded> directory for a project.

    Encoding: each '/' and '.' in the absolute project path is replaced with '-'.
    Does not check whether the directory exists.
    """
    encoded = str(project_dir).replace("/", "-").replace(".", "-")
    return Path.home() / ".claude" / "projects" / encoded
