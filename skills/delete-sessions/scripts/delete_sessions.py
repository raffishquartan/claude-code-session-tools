#!/usr/bin/env python3
"""Delete one or more Claude Code sessions safely.

For each basename supplied:
  1. Validate the basename matches the session-name format.
  2. Locate the cc-sessions/<basename>/ directory.
  3. Refuse if any session is the currently running CC session.
  4. Refuse non-empty sessions unless --allow-non-empty.
  5. Print a deletion plan.
  6. In --execute mode: generate an 8-digit confirmation code, prompt for it,
     then delete (cc-sessions dir + JSONL transcript + tasks dir on disk, plus
     the sessions.db session_tags and sessions rows).

See ~/.claude/skills/delete-sessions/SKILL.md for full usage notes.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sqlite3
import sys
import time
from pathlib import Path

# cc_session_tools must be on the import path (installed via uv tool install
# or present via PYTHONPATH from the worktree src/ directory).
from cc_session_tools.lib.sessions import (
    SESSION_BASENAME_RE,
    find_jsonl_for_session,
    is_empty_session,
    session_is_empty_safe,
    transcript_dir_for_project,
)


HOOK_SECURITY_PREFIX = "Review this shell command for security risks"


# ---------------------------------------------------------------------------
# Session-root discovery (mirrors ccs._collect_pairs logic)
# ---------------------------------------------------------------------------

def _discover_project_for_basename(basename: str) -> tuple[Path, Path] | None:
    """Return (session_dir, project_dir) for basename or None if not found.

    Search order:
      1. cwd / cc-sessions / <basename>
      2. All roots from CLAUDE_SESSION_TOOLS_REPO_ROOT and
         CLAUDE_SESSION_TOOLS_PROJ_ROOT (one level down only).
    """
    # Local first.
    local_candidate = Path.cwd() / "cc-sessions" / basename
    if local_candidate.is_dir():
        return local_candidate, Path.cwd()

    # Configured roots.
    root_vars = [
        os.environ.get("CLAUDE_SESSION_TOOLS_REPO_ROOT", ""),
        os.environ.get("CLAUDE_SESSION_TOOLS_PROJ_ROOT", ""),
    ]
    for root_str in root_vars:
        if not root_str:
            continue
        root = Path(root_str).expanduser()
        if not root.is_dir():
            continue
        for proj in root.iterdir():
            if not proj.is_dir():
                continue
            candidate = proj / "cc-sessions" / basename
            if candidate.is_dir():
                return candidate, proj

    return None


# ---------------------------------------------------------------------------
# In-session detection (same signals as move-session)
# ---------------------------------------------------------------------------

def _is_hook_security_jsonl(path: Path) -> bool:
    """True if the JSONL's first user text starts with the hook-security prefix."""
    try:
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") == "user":
                    msg = rec.get("message") or {}
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        return content.startswith(HOOK_SECURITY_PREFIX)
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                return block.get("text", "").startswith(
                                    HOOK_SECURITY_PREFIX
                                )
                    return False
    except OSError:
        pass
    return False


def _is_active_session(session_dir: Path, project_dir: Path) -> tuple[bool, list[str]]:
    """Return (is_active, reasons). Mirrors move-session's detection logic."""
    reasons: list[str] = []
    in_cc = (
        os.environ.get("CLAUDECODE") == "1"
        or bool(os.environ.get("CLAUDE_PROJECT_DIR"))
    )
    if not in_cc:
        return False, []
    reasons.append("running inside Claude Code (CLAUDECODE / CLAUDE_PROJECT_DIR set)")

    jsonl = find_jsonl_for_session(session_dir.name, project_dir)
    if jsonl is None:
        return False, reasons

    transcript_dir = transcript_dir_for_project(project_dir)
    src_jsonl_active = False
    try:
        age = time.time() - jsonl.stat().st_mtime
        if age < 30:
            src_jsonl_active = True
            reasons.append(
                f"session JSONL {jsonl.name} modified {age:.1f}s ago (CC is appending)"
            )
    except OSError:
        pass

    cwd_and_freshest = False
    try:
        cwd_real = str(Path.cwd().resolve())
    except OSError:
        cwd_real = ""

    if cwd_real == str(project_dir.resolve()):
        try:
            all_jsonls = [
                p for p in transcript_dir.glob("*.jsonl")
                if p.is_file() and not _is_hook_security_jsonl(p)
            ]
        except Exception:
            all_jsonls = []
        if all_jsonls:
            freshest = max(all_jsonls, key=lambda p: p.stat().st_mtime)
            if freshest.resolve() == jsonl.resolve():
                cwd_and_freshest = True
                reasons.append(
                    f"cwd matches project dir AND {jsonl.name} is the freshest "
                    f"non-hook-security JSONL (this is the running session)"
                )

    is_active = src_jsonl_active or cwd_and_freshest
    return is_active, reasons


# ---------------------------------------------------------------------------
# 8-digit confirmation
# ---------------------------------------------------------------------------

def _generate_code() -> str:
    return f"{secrets.randbelow(100_000_000):08d}"


def _prompt_confirmation(code: str) -> str:
    """Print the code and read user input. Returns what was typed."""
    print()
    print("=" * 60)
    print("CONFIRMATION REQUIRED")
    print(f"  Type this code to confirm deletion: {code}")
    print("=" * 60)
    try:
        return input("Code: ").strip()
    except (EOFError, KeyboardInterrupt):
        return ""


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------

def _encode_path(path: Path) -> str:
    """Encode a path the same way CC does for project keys and task keys."""
    return str(path).replace("/", "-").replace(".", "-")


def _artefacts_for_session(
    basename: str,
    session_dir: Path,
    project_dir: Path,
    *,
    include_tasks: bool = True,
) -> list[tuple[str, Path]]:
    """Return [(label, path)] for all artefacts that should be deleted."""
    artefacts: list[tuple[str, Path]] = [
        ("session folder", session_dir),
    ]

    # JSONL transcript. The session's tag mapping now lives in sessions.db
    # (session_tags table, keyed by this jsonl's UUID) rather than in a flat
    # .tag file; it is removed via sessions_db.delete_tag() in the delete loop,
    # not as a filesystem artefact here.
    jsonl = find_jsonl_for_session(basename, project_dir)
    if jsonl is not None:
        artefacts.append(("JSONL transcript", jsonl))

    # Tasks directory.
    if include_tasks:
        task_key = _encode_path(session_dir.resolve())
        task_dir = Path.home() / ".claude" / "tasks" / task_key
        if task_dir.exists():
            artefacts.append(("tasks directory", task_dir))

    return artefacts


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Delete one or more Claude Code sessions (dry-run by default)."
    )
    ap.add_argument(
        "basenames",
        nargs="+",
        metavar="basename",
        help="YYYYMMDD-<tag> session basename(s) to delete.",
    )
    ap.add_argument(
        "--allow-non-empty",
        action="store_true",
        help="Also delete sessions that contain real user messages.",
    )
    ap.add_argument(
        "--no-tasks",
        action="store_true",
        help="Skip ~/.claude/tasks/<encoded>/ deletion.",
    )
    ap.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete. Default is dry-run (print plan only).",
    )
    args = ap.parse_args()

    errors: list[str] = []

    # ------------------------------------------------------------------
    # Pre-flight check 1: basename format
    # ------------------------------------------------------------------
    bad_format = [b for b in args.basenames if not SESSION_BASENAME_RE.match(b)]
    if bad_format:
        for b in bad_format:
            errors.append(f"  {b!r} does not match the session-name format (YYYYMMDD-<tag>)")
        print("ERROR: invalid basename(s):", file=sys.stderr)
        for e in errors:
            print(e, file=sys.stderr)
        return 1

    # ------------------------------------------------------------------
    # Pre-flight check 2: existence
    # ------------------------------------------------------------------
    resolved: list[tuple[str, Path, Path]] = []  # (basename, session_dir, project_dir)
    missing: list[str] = []

    for basename in args.basenames:
        found = _discover_project_for_basename(basename)
        if found is None:
            missing.append(basename)
        else:
            session_dir, project_dir = found
            resolved.append((basename, session_dir, project_dir))

    if missing:
        print("ERROR: the following session(s) could not be found:", file=sys.stderr)
        for m in missing:
            print(f"  {m}", file=sys.stderr)
        return 1

    # ------------------------------------------------------------------
    # Pre-flight check 3: in-session refusal
    # ------------------------------------------------------------------
    for basename, session_dir, project_dir in resolved:
        is_active, reasons = _is_active_session(session_dir, project_dir)
        if is_active:
            print("=" * 60, file=sys.stderr)
            print(
                f"REFUSED: cannot delete {basename!r} from within the session itself.",
                file=sys.stderr,
            )
            print("=" * 60, file=sys.stderr)
            print("Detection signals:", file=sys.stderr)
            for r in reasons:
                print(f"  - {r}", file=sys.stderr)
            print(file=sys.stderr)
            print(
                "Exit this CC session first, then re-run from a normal shell.",
                file=sys.stderr,
            )
            return 2

    # ------------------------------------------------------------------
    # Pre-flight check 4: empty-only guard
    # ------------------------------------------------------------------
    if not args.allow_non_empty:
        non_empty: list[str] = []
        for basename, session_dir, project_dir in resolved:
            result = session_is_empty_safe(basename, project_dir)
            if result is False:
                # Definitely non-empty (JSONL found and has real user messages).
                non_empty.append(basename)
        if non_empty:
            print(
                "ERROR: the following session(s) are not empty "
                "(contain real user messages):",
                file=sys.stderr,
            )
            for b in non_empty:
                print(f"  {b}", file=sys.stderr)
            print(
                "\nPass --allow-non-empty to also delete non-empty sessions.",
                file=sys.stderr,
            )
            return 1

    # ------------------------------------------------------------------
    # Build per-session artefact lists
    # ------------------------------------------------------------------
    # Each plan entry: (basename, project_dir, transcript_uuid, artefacts).
    # transcript_uuid (the jsonl stem) is the key for the sessions.db
    # session_tags row removed in the delete loop; None if no transcript found.
    plan: list[tuple[str, Path, str | None, list[tuple[str, Path]]]] = []
    for basename, session_dir, project_dir in resolved:
        artefacts = _artefacts_for_session(
            basename,
            session_dir,
            project_dir,
            include_tasks=not args.no_tasks,
        )
        jsonl = find_jsonl_for_session(basename, project_dir)
        transcript_uuid = jsonl.stem if jsonl is not None else None
        plan.append((basename, project_dir, transcript_uuid, artefacts))

    # ------------------------------------------------------------------
    # Print plan
    # ------------------------------------------------------------------
    print("=" * 60)
    print("DELETION PLAN")
    print("=" * 60)
    for basename, project_dir, transcript_uuid, artefacts in plan:
        print(f"\n  Session: {basename}")
        for label, path in artefacts:
            exists_note = "" if path.exists() else "  [already absent]"
            print(f"    {label}: {path}{exists_note}")
        print("    sessions.db records: sessions row + session_tags row (if present)")
    print()

    if not args.execute:
        print("DRY-RUN: nothing deleted. Pass --execute to proceed.")
        return 0

    # ------------------------------------------------------------------
    # 8-digit confirmation gate
    # ------------------------------------------------------------------
    code = _generate_code()
    entered = _prompt_confirmation(code)
    if entered != code:
        print("Confirmation code mismatch — aborting. Nothing was deleted.")
        return 1

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------
    print()
    print("=" * 60)
    print("DELETING")
    print("=" * 60)
    from cc_session_tools.lib import sessions_db

    had_error = False
    for basename, project_dir, transcript_uuid, artefacts in plan:
        print(f"\n  Session: {basename}")
        for label, path in artefacts:
            if not path.exists():
                print(f"    {label}: already absent — skipped")
                continue
            try:
                import shutil
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                print(f"    deleted {label}: {path}")
            except OSError as exc:
                print(f"    ERROR deleting {label} {path}: {exc}")
                had_error = True

        # Remove sessions.db records so the deleted session stops appearing in
        # ccs/ccr enumeration (no automatic GC — see design decision D6).
        try:
            sessions_db.delete_session_row(project_dir, basename)
            if transcript_uuid is not None:
                sessions_db.delete_tag(transcript_uuid)
            print("    deleted sessions.db records")
        except (OSError, sqlite3.Error) as exc:
            print(f"    ERROR deleting sessions.db records for {basename}: {exc}")
            had_error = True

    print()
    if had_error:
        print("Completed with errors (see above).")
        return 1
    print("Done. All artefacts deleted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
