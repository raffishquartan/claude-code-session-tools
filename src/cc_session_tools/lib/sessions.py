from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

SESSION_BASENAME_RE = re.compile(r"^(\d{8})(?:-to-\d{8})?-")
SESSION_FULL_RE = re.compile(r"^(\d{8})(?:-to-\d{8})?-(.+)$")

_ORPHAN_SENTINEL = "<orphan: no on-disk session dir>"


@dataclass(frozen=True)
class SessionMatch:
    basename: str
    project_dir: Path
    session_dir: Path
    is_orphan: bool = False

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


def find_orphan_transcripts(fragment: str, roots: list[Path]) -> list[SessionMatch]:
    """Find transcripts that exist under ~/.claude/projects/<encoded-cwd>/ but
    have no corresponding cc-sessions/<basename>/ directory on disk.

    These are sessions that were started outside of ccd (or whose session
    directory was deleted) but whose transcript is still resumable via
    ``claude --resume <basename>``.

    Steps per project under each root:
      1. Compute the encoded-cwd transcript dir for the project.
      2. List *.jsonl files there.
      3. Look up the display name from the JSONL custom-title record.
      4. If the display name matches ``fragment`` AND the cc-sessions/<name>/
         directory does NOT exist, yield it as an orphan SessionMatch.
      5. Skip transcripts whose display name is unresolvable.
    """
    from claude_code_usage.session_names import load_jsonl_titles

    out: list[SessionMatch] = []
    for root in roots:
        if not root.is_dir():
            continue
        for proj in root.iterdir():
            if not proj.is_dir():
                continue
            transcript_dir = transcript_dir_for_project(proj)
            if not transcript_dir.is_dir():
                continue
            # Build name map from JSONL records in this project's transcript dir.
            name_map = load_jsonl_titles(transcript_dir)
            for jsonl in transcript_dir.glob("*.jsonl"):
                uuid = jsonl.stem
                display_name = name_map.get(uuid)
                if not display_name:
                    continue  # unresolvable - skip silently
                if not is_session_basename(display_name):
                    continue  # not a session-format name - skip
                if fragment not in display_name:
                    continue  # doesn't match the requested fragment
                # Check whether an on-disk session dir exists - if it does,
                # find_matching_sessions will already return it, so skip here.
                session_dir = proj / "cc-sessions" / display_name
                if session_dir.is_dir():
                    continue
                out.append(SessionMatch(
                    basename=display_name,
                    project_dir=proj,
                    session_dir=Path(_ORPHAN_SENTINEL),
                    is_orphan=True,
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


def _jsonl_has_custom_title(jsonl: Path, basename: str, suffix: str) -> bool:
    """Return True if `jsonl` contains a custom-title record matching basename or suffix."""
    try:
        with jsonl.open() as f:
            for line in f:
                line = line.strip()
                if not line or '"custom-title"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") == "custom-title":
                    title = rec.get("customTitle") or rec.get("title") or rec.get("name") or ""
                    if title == basename or title == suffix:
                        return True
    except OSError:
        pass
    return False


def find_jsonl_for_session(basename: str, project_dir: Path) -> Path | None:
    """Locate the JSONL transcript for a cc-sessions/<basename>/ directory.

    Strategy:
      1. Batch-lookup every *.jsonl in the transcript dir against sessions.db's
         session_tags table (uuid -> tag). Defence-in-depth: if a tag match has
         no custom-title record (e.g. a hook sub-process transcript that
         inherited the parent tag via env-var inheritance), treat it as
         tentative and prefer a custom-title match from Strategy 2.
      2. Fall back to scanning JSONL `custom-title` records for a match. Also
         runs when Strategy 1 found only a tentative (unconfirmed) match.

    A third, file-based fallback strategy (scanning for legacy .tag files left
    directly in the transcript dir, from a since-retired pre-sessions.db
    migration) has been removed — see design decision D5 in
    docs/superpowers/plans/2026-07-13-data-store-uplift-04-sessions-db.md.

    Returns the resolved jsonl Path, or None if no match found.
    """
    from cc_session_tools.lib import sessions_db

    transcript_dir = transcript_dir_for_project(project_dir)
    if not transcript_dir.is_dir():
        return None

    suffix = session_tag(basename)
    if suffix is None:
        return None

    jsonls = list(transcript_dir.glob("*.jsonl"))
    tag_map = sessions_db.lookup_tags([j.stem for j in jsonls])

    # Strategy 1: sessions.db tag lookup.
    tag_match: Path | None = None
    for jsonl in jsonls:
        content = tag_map.get(jsonl.stem)
        if content is None:
            continue
        if content == suffix or content == basename:
            if _jsonl_has_custom_title(jsonl, basename, suffix):
                return jsonl  # confirmed: tag row and custom-title agree
            tag_match = tag_match or jsonl  # tentative: no custom-title yet

    # Strategy 2: scan JSONLs for custom-title records (slower fallback).
    # Runs even when Strategy 1 found a tentative match: a custom-title match
    # on a different JSONL overrides the unconfirmed tag-row match.
    for jsonl in jsonls:
        try:
            with jsonl.open() as f:
                for line in f:
                    line = line.strip()
                    if not line or '"custom-title"' not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("type") == "custom-title":
                        title = rec.get("customTitle") or rec.get("title") or rec.get("name") or ""
                        if title == basename or title == suffix:
                            return jsonl
        except OSError:
            continue

    return tag_match


# Substrings that mark a "user" record as not a free-typed message:
# slash-command invocations, hook-injected reminders, local command output.
_NON_USER_CONTENT_PREFIXES = (
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "<system-reminder>",
    "<local-command-stdout>",
    "<local-command-stderr>",
    "<local-command-caveat>",
    "Caveat: The messages below were generated by the user while running",
)


def _is_user_typed_content(content_text: str) -> bool:
    """True if a 'user' message's text looks like something the user actually
    typed (as opposed to a slash command, hook-injected reminder, or local
    command output)."""
    stripped = content_text.lstrip()
    if not stripped:
        return False
    for prefix in _NON_USER_CONTENT_PREFIXES:
        if stripped.startswith(prefix):
            return False
    return True


def is_empty_session(basename: str, project_dir: Path) -> bool:
    """Return True if the session's JSONL transcript contains no user-typed
    messages.

    "Empty" excludes:
      - SessionStart hook output (isMeta=True)
      - Compact summary records (isCompactSummary=True)
      - Tool results (content[i].type=='tool_result')
      - Slash-command invocations (content text starts with <command-name>)
      - System-reminder injections (content text starts with <system-reminder>)
      - Local command output (content text starts with <local-command-...>)

    Returns True (empty) if the JSONL cannot be located — callers that need
    "confident empty" should pair this with find_jsonl_for_session() first
    and treat None as "unknown" rather than "empty".

    Used by `ccs --emptiness only` and the `delete-sessions` skill.
    """
    jsonl = find_jsonl_for_session(basename, project_dir)
    if jsonl is None:
        return True

    try:
        with jsonl.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "user":
                    continue
                if rec.get("isMeta"):
                    continue
                if rec.get("isCompactSummary"):
                    continue
                msg = rec.get("message") or {}
                content = msg.get("content")
                if isinstance(content, str):
                    if _is_user_typed_content(content):
                        return False
                elif isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") != "text":
                            continue
                        if _is_user_typed_content(block.get("text", "")):
                            return False
    except OSError:
        return True

    return True


def session_is_empty_safe(basename: str, project_dir: Path) -> bool | None:
    """Variant of is_empty_session that returns None when the JSONL cannot be
    located (so callers can distinguish 'empty' from 'unknown').
    """
    if find_jsonl_for_session(basename, project_dir) is None:
        return None
    return is_empty_session(basename, project_dir)
