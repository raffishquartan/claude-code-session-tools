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


def grep_session(session_dir: Path, query: str, context: int = 1) -> list[str]:
    """Grep for `query` (fixed string, not regex) recursively in session_dir;
    return a flat list of output lines including `path:line:content` for
    matches and `path-line-content` for ±context lines, with `--` separators
    between non-adjacent groups (standard grep -A/-B output).

    Uses `rg` (ripgrep) or `grep` when available - both are 10-100x faster
    than the pure-Python fallback on large session trees. Falls back to a
    pure-Python implementation if neither external tool is on PATH (e.g. a
    locked-down CI environment) or the subprocess fails unexpectedly."""
    if shutil.which("rg"):
        out = _grep_with_rg(session_dir, query, context)
        if out is not None:
            return out
    if shutil.which("grep"):
        out = _grep_with_grep(session_dir, query, context)
        if out is not None:
            return out
    return _grep_python(session_dir, query, context)


def _grep_with_grep(session_dir: Path, query: str, context: int) -> list[str] | None:
    """Run GNU grep over the session tree. Returns None if grep failed in a
    way that suggests we should fall through to the next runner."""
    cmd = [
        "grep", "-rInHF", "--color=never",
        "-A", str(context), "-B", str(context),
        "--", query, ".",
    ]
    try:
        res = subprocess.run(
            cmd, cwd=session_dir, capture_output=True,
            text=True, errors="replace",
        )
    except OSError:
        return None
    # grep exit codes: 0=match, 1=no match, 2=error.
    if res.returncode > 1:
        return None
    if not res.stdout:
        return []
    # grep emits a trailing newline; rstrip then split keeps empty lines inside
    # context blocks but drops the trailing one.
    return res.stdout.rstrip("\n").split("\n")


def _grep_with_rg(session_dir: Path, query: str, context: int) -> list[str] | None:
    """Run ripgrep. Output format is the same as grep's (path:line:content,
    path-line-content for context, -- separators) when --no-heading is set."""
    cmd = [
        "rg", "--no-heading", "-n", "-H", "-F",
        "--color=never", "-C", str(context),
        "--", query, ".",
    ]
    try:
        res = subprocess.run(
            cmd, cwd=session_dir, capture_output=True,
            text=True, errors="replace",
        )
    except OSError:
        return None
    # rg exit codes: 0=match, 1=no match, 2=error.
    if res.returncode > 1:
        return None
    if not res.stdout:
        return []
    return res.stdout.rstrip("\n").split("\n")


def _grep_python(session_dir: Path, query: str, context: int) -> list[str]:
    """Pure-Python fallback. Slower than grep/rg by 10-100x on large trees,
    but always available."""
    out: list[str] = []
    files = sorted(p for p in session_dir.rglob("*") if p.is_file())
    first_group = True
    for f in files:
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
