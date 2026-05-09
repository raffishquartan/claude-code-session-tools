from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

SESSION_BASENAME_RE = re.compile(r"^(\d{8})(?:-to-\d{8})?-")


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
    """Grep for `query` recursively in session_dir; return a flat list of
    output lines (match + ±context lines, with `--` separators between
    non-adjacent groups). Skips binary files and unreadable files."""
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
        # Compute window indices around each match, merging adjacent ones.
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
