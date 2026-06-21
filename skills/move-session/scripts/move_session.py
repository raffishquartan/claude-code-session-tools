#!/usr/bin/env python3
"""Move (copy) and/or rename a Claude Code session.

See ~/.claude/skills/move-session/SKILL.md for usage and design notes.

Three operations are supported, selected by which of --dst-cwd / --rename-tag are
given:
  - MOVE: copy cc-sessions/<tag>/ into a different project cwd, copy the jsonl
    transcript to the new project key dir, and rewrite path strings inside it.
  - RENAME: copy cc-sessions/<tag>/ to cc-sessions/<new-tag>/ in the same parent
    project. The jsonl is NOT touched - the project key is unchanged, and the
    source cc-sessions dir is preserved by the copy so old paths still resolve.
  - MOVE+RENAME: both at once.

Path-string rewrites in the destination jsonl (MOVE / MOVE+RENAME only - RENAME
does not touch the jsonl):
  - long path:     <src-cwd-abs>             -> <dst-cwd-abs>
  - encoded key:   <src-cwd-encoded>         -> <dst-cwd-encoded>
  - tilde form:    ~/<src-rel-to-home>       -> ~/<dst-rel-to-home>   (only if src-cwd-abs is under $HOME)
  - tag substring: cc-sessions/<old-tag>     -> cc-sessions/<new-tag> (MOVE+RENAME only)

The script never deletes from the source. In copy mode (default) with tombstoning
on (default):
  - For all operations, writes a TOMBSTONE.md alongside the source cc-sessions/<tag>/.
  - For MOVE / MOVE+RENAME only, ALSO appends a synthetic user/assistant exchange
    to the SOURCE jsonl announcing the move.
  - For RENAME-only, does NOT append jsonl tombstone records: src_jsonl ==
    dst_jsonl in that case (same project key, jsonl never copied) and appending
    would corrupt the live transcript the user resumes into.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
import uuid as uuidlib
from datetime import datetime, timezone
from pathlib import Path

# Shared session-rule validators (single source of truth with ccd).
# Import-* re-exports keep the existing test_validators.py module-attribute
# access (`ms.validate_new_tag`, `ms.matched_session_root`, ...) working without
# changes.
from cc_session_rules import (  # noqa: F401  (re-exported for tests)
    DATE_PREFIX_RE,
    PROJECT_NAME_STRICT_RE,
    PROJ_ROOT_ENV,
    REPO_ROOT_ENV,
    TAG_NEW_RE,
    TAG_SUFFIX_FORMAT_RE,
    check_session_destination,
    check_session_init,
    encode_cwd,
    is_strict_root,
    is_valid_session_cwd,
    load_session_roots,
    matched_session_root,
    proj_root,
    repo_root,
    strict_root_path,
    validate_new_tag,
    validate_strict_project_name,
    validate_strict_tag_suffix,
    validate_tag_suffix_no_spaces,
)

HOOK_SECURITY_PREFIX = "Review this shell command for security risks"
SCRIPT_PATH = "~/.claude/skills/move-session/scripts/move_session.py"


# ---------- active-session detection ----------

def detect_active_source_session(
    src_cwd_abs: str,
    src_jsonl: Path | None,
    src_key_dir: Path,
) -> tuple[bool, list[str]]:
    """Return (is_active, reasons) for whether the *specific* source session
    being moved is the currently running CC session (i.e. we are being invoked
    from inside it).

    Triggers refusal if we're running inside CC AND either of:
      (a) the source jsonl was written within the last 30 seconds (CC is actively
          appending), OR
      (b) realpath(getcwd()) == src_cwd_abs AND the source jsonl is the
          most-recently-modified non-hook-security jsonl in src_key_dir
          (we're in the source project and the source is the freshest session
          there - by far the most likely interpretation: it IS this session).

    Sibling sessions in the same project key are still moveable from inside an
    unrelated active session: even if (b) fires its cwd check, the most-recent
    jsonl in the key dir will be the *running* one (this session), not the
    sibling source - so the second clause of (b) defuses it.
    """
    reasons: list[str] = []
    in_cc = os.environ.get("CLAUDECODE") == "1" or bool(os.environ.get("CLAUDE_PROJECT_DIR"))
    if in_cc:
        reasons.append("running inside Claude Code (CLAUDECODE / CLAUDE_PROJECT_DIR set)")

    src_jsonl_active = False
    if src_jsonl is not None and src_jsonl.exists():
        try:
            age = time.time() - src_jsonl.stat().st_mtime
            if age < 30:
                src_jsonl_active = True
                reasons.append(f"source jsonl {src_jsonl.name} modified {age:.1f}s ago (CC is actively appending)")
        except OSError:
            pass

    cwd_and_freshest = False
    if in_cc and src_jsonl is not None:
        try:
            cwd_real = str(Path.cwd().resolve())
        except OSError:
            cwd_real = ""
        if cwd_real == src_cwd_abs:
            try:
                jsonls = [p for p in src_key_dir.glob("*.jsonl") if p.is_file()]
                non_hook = [p for p in jsonls if not is_hook_security_check(jsonl_summary(p))]
            except Exception:
                non_hook = []
            if non_hook:
                freshest = max(non_hook, key=lambda p: p.stat().st_mtime)
                if freshest.resolve() == src_jsonl.resolve():
                    cwd_and_freshest = True
                    reasons.append(
                        f"cwd matches src_cwd AND src jsonl {src_jsonl.name} is the "
                        f"most-recently-modified jsonl in {src_key_dir.name} "
                        f"(this is almost certainly the running session)"
                    )

    is_active = in_cc and (src_jsonl_active or cwd_and_freshest)
    return is_active, reasons


def print_in_session_recipe(
    src_session_dir: Path,
    src_cwd_abs: str,
    dst_cwd_abs: str,
    src_tag: str,
    dst_tag: str,
    src_key_dir: Path,
    session_uuid: str | None,
    reasons: list[str],
    include_memory: bool,
    no_tombstone: bool,
    uuid_hint: str | None,
) -> None:
    cwd_changed = dst_cwd_abs != src_cwd_abs
    tag_changed = dst_tag != src_tag
    op = []
    if cwd_changed:
        op.append("MOVE")
    if tag_changed:
        op.append("RENAME")
    op_label = "+".join(op) or "NO-OP"

    print("=" * 72)
    print(f"REFUSED ({op_label}): cannot move/rename a session from within the session itself")
    print("=" * 72)
    print()
    print("Detection signals:")
    for r in reasons:
        print(f"  - {r}")
    print()
    print("Why this is impossible:")
    print("  Claude Code's process has its cwd and jsonl path fixed at startup.")
    print("  Moving the session while it is still running would create a frozen")
    print("  snapshot at this moment - any further messages in this conversation")
    print("  would still land in the SOURCE session, not the new location.")
    print("  The destination would silently diverge from the live conversation.")
    print()
    print("To do this safely:")
    print()
    print("  # 1. Exit this CC session  (Ctrl-D, or type /exit)")
    print()
    print("  # 2. From a normal shell, re-run with the same flags:")
    cmd_lines = [f"  python3 {SCRIPT_PATH}"]
    cmd_lines.append(f"      --src-session {src_session_dir}")
    if cwd_changed:
        cmd_lines.append(f"      --dst-cwd     {dst_cwd_abs}")
    if tag_changed:
        cmd_lines.append(f"      --rename-tag  {dst_tag}")
    if uuid_hint:
        cmd_lines.append(f"      --uuid        {uuid_hint}")
    if include_memory:
        cmd_lines.append(f"      --include-memory")
    if no_tombstone:
        cmd_lines.append(f"      --no-tombstone")
    cmd_lines.append(f"      --execute")
    print(" \\\n".join(cmd_lines))
    print()
    if session_uuid:
        print("  # 3. Resume from the new location:")
        print(f"  cd {dst_cwd_abs} && claude --resume {session_uuid}")
        if tag_changed:
            print()
            print("  # 3a. In the resumed session, update the picker display name")
            print("  #     to match the new cc-sessions directory:")
            print(f"  /rename {dst_tag}")
        print()
    print("  # 4. (Optional, only after you have verified the destination works)")
    print(f"  rm -rf {src_session_dir}")
    if cwd_changed and session_uuid:
        # MOVE / MOVE+RENAME: source jsonl is in a different project key dir from
        # the destination, so it can be safely deleted.
        print(f"  rm     {src_key_dir}/{session_uuid}.jsonl")
    elif tag_changed:
        # RENAME-only: project key unchanged, so the jsonl is shared between
        # source and destination. Deleting it would destroy the live transcript.
        print(f"  # (Do NOT delete the jsonl - rename-only kept the same project key,")
        print(f"  #  so the jsonl IS the destination transcript.)")


# ---------- helpers ----------

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def first_user_text(rec: dict) -> str:
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                return c.get("text", "")
    return ""


def jsonl_summary(path: Path) -> dict:
    """Return summary of a jsonl: first_ts, first_user_text, line_count, cwds, last_uuid, custom_titles."""
    first_ts = None
    first_user = None
    cwds = set()
    n = 0
    last_uuid = None
    last_record = None
    custom_titles: set[str] = set()
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            n += 1
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not first_ts and d.get("timestamp"):
                first_ts = d["timestamp"]
            if d.get("cwd"):
                cwds.add(d["cwd"])
            if first_user is None and d.get("type") == "user":
                first_user = first_user_text(d)
            if d.get("uuid"):
                last_uuid = d["uuid"]
                last_record = d
            if d.get("type") == "custom-title":
                # Title can live under several keys depending on CC version.
                for k in ("title", "customTitle", "content", "value"):
                    v = d.get(k)
                    if isinstance(v, str) and v:
                        custom_titles.add(v)
    return {
        "path": path,
        "first_ts": first_ts,
        "first_user": first_user or "",
        "lines": n,
        "cwds": cwds,
        "last_uuid": last_uuid,
        "last_record": last_record,
        "custom_titles": custom_titles,
    }


def is_hook_security_check(summary: dict) -> bool:
    return summary["first_user"].startswith(HOOK_SECURITY_PREFIX)


def list_candidate_jsonls(src_key_dir: Path) -> list[dict]:
    """All non-hook-security-check jsonls in the src project key dir, with summaries."""
    out = []
    if not src_key_dir.is_dir():
        return out
    for p in sorted(src_key_dir.glob("*.jsonl")):
        try:
            s = jsonl_summary(p)
        except Exception as e:
            print(f"  warn: could not summarise {p.name}: {e}", file=sys.stderr)
            continue
        if is_hook_security_check(s):
            continue
        out.append(s)
    return out


TAG_DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})-")


def discover_session_jsonl(src_key_dir: Path, src_session_dir: Path, uuid_hint: str | None) -> dict:
    """Return the single jsonl matching the source cc-sessions/<tag>/ dir.

    Resolution order:
      1. --uuid hint (exact match wins, even if it's a hook-security-check transcript).
      2. Single non-hook candidate -> use it.
      3. If the tag has a YYYYMMDD- prefix, filter candidates to those whose first
         timestamp falls on that calendar date.
      4. If still ambiguous, list candidates and require --uuid.
    """
    candidates = list_candidate_jsonls(src_key_dir)
    if uuid_hint:
        for c in candidates:
            if c["path"].stem == uuid_hint:
                return c
        target = src_key_dir / f"{uuid_hint}.jsonl"
        if target.exists():
            return jsonl_summary(target)
        raise SystemExit(f"--uuid {uuid_hint} not found under {src_key_dir}")
    if not candidates:
        raise SystemExit(
            f"No non-hook-security-check jsonls found under {src_key_dir}. "
            f"Supply --uuid <id> if you know it."
        )
    if len(candidates) == 1:
        return candidates[0]
    # Strongest discriminator: a custom-title record matching the cc-sessions tag
    # (set by `claude -n <tag>` at startup, or by /rename later).
    tag = src_session_dir.name
    by_title = [c for c in candidates if tag in c.get("custom_titles", set())]
    if len(by_title) == 1:
        return by_title[0]
    if by_title:
        candidates = by_title  # narrow before next discriminator
    # Fallback: tag date prefix vs jsonl first_ts date.
    m = TAG_DATE_RE.match(src_session_dir.name)
    if m:
        iso_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        on_date = [c for c in candidates if c["first_ts"] and c["first_ts"].startswith(iso_date)]
        if len(on_date) == 1:
            return on_date[0]
        if on_date:
            candidates = on_date
    listing = "\n".join(
        f"  {c['path'].stem}  first_ts={c['first_ts']}  "
        f"titles={sorted(c.get('custom_titles', set()))}  "
        f"first_user={c['first_user'][:50]!r}"
        for c in candidates
    )
    raise SystemExit(
        f"Multiple jsonl candidates for {src_session_dir.name}:\n{listing}\n"
        f"Re-run with --uuid <id> to pick one."
    )


# ---------- copy + rewrite ----------

def rewrite_jsonl_paths(dst_jsonl: Path, replacements: list[tuple[str, str]]) -> dict:
    """In-place string replace on a jsonl. Returns counts per pattern."""
    counts = {a: 0 for a, _ in replacements}
    text = dst_jsonl.read_text()
    for old, new in replacements:
        c = text.count(old)
        counts[old] = c
        if c:
            text = text.replace(old, new)
    dst_jsonl.write_text(text)
    return counts


def verify_dst_jsonl(src_jsonl: Path, dst_jsonl: Path, dst_cwd_abs: str, src_cwd_abs: str, src_key: str, strict: bool) -> dict:
    """Run verification checks. Returns dict with results and a bool 'ok'.

    strict=True (post-move, --execute path): require exact line parity and zero
    remaining src-path strings. The destination should be a byte-identical
    rewrite of the source.

    strict=False (--verify-only path): the destination may have grown since the
    move (further messages appended on resume). Only enforce that every line
    parses as JSON and every cwd field equals dst_cwd_abs. Line count and
    residual src-path strings are reported but do not fail.
    """
    src_lines = sum(1 for _ in src_jsonl.open())
    dst_lines = sum(1 for _ in dst_jsonl.open())
    json_ok = json_err = 0
    cwds = set()
    with dst_jsonl.open() as fh:
        for i, line in enumerate(fh, 1):
            line = line.rstrip("\n")
            if not line:
                continue
            try:
                d = json.loads(line)
                json_ok += 1
            except json.JSONDecodeError:
                json_err += 1
                continue
            if d.get("cwd"):
                cwds.add(d["cwd"])
    text = dst_jsonl.read_text()
    remaining_long = text.count(src_cwd_abs)
    remaining_key = text.count(src_key)
    cwds_clean = (cwds == {dst_cwd_abs}) if cwds else True
    if strict:
        ok = (
            src_lines == dst_lines
            and json_err == 0
            and remaining_long == 0
            and remaining_key == 0
            and cwds_clean
        )
    else:
        ok = (
            json_err == 0
            and src_lines <= dst_lines
            and cwds_clean
        )
    return {
        "src_lines": src_lines,
        "dst_lines": dst_lines,
        "json_ok": json_ok,
        "json_err": json_err,
        "remaining_long": remaining_long,
        "remaining_key": remaining_key,
        "cwds_in_dst": sorted(cwds),
        "strict": strict,
        "ok": ok,
    }


# ---------- tombstone ----------

def make_tombstone_records(last_record: dict | None, src_session_dir: Path, dst_session_dir: Path) -> list[dict]:
    """Build the user/assistant pair to append to the source jsonl.

    Defensive guard: callers should refuse early when last_record is None
    (empty/malformed source jsonl) - main() does this before any copy. This
    guard is a second line of defence in case a future caller forgets.
    """
    if last_record is None:
        raise ValueError(
            "make_tombstone_records: last_record is None (source jsonl had no "
            "parseable records with a uuid). Refuse the operation earlier or "
            "pass --no-tombstone."
        )
    src_cwd = last_record.get("cwd") or ""
    session_id = last_record.get("sessionId")
    version = last_record.get("version", "")
    git_branch = last_record.get("gitBranch", "")
    parent_uuid = last_record.get("uuid")
    user_uuid = str(uuidlib.uuid4())
    asst_uuid = str(uuidlib.uuid4())
    prompt_id = str(uuidlib.uuid4())
    request_id = str(uuidlib.uuid4())
    ts_user = now_iso()
    ts_asst = now_iso()
    notice = (
        f"[TOMBSTONE] This session has been copied to a new working directory by the "
        f"move-session skill on {ts_user}. The live transcript is now at:\n"
        f"  cc-sessions: {dst_session_dir}\n"
        f"  jsonl:       ~/.claude/projects/{encode_cwd(str(dst_session_dir.parent.parent.resolve()))}/{Path(last_record.get('sessionId', 'session')).name}.jsonl\n"
        f"Continue work in the new location. This source transcript is preserved as a record."
    )
    user_record = {
        "parentUuid": parent_uuid,
        "isSidechain": False,
        "promptId": prompt_id,
        "type": "user",
        "message": {"role": "user", "content": notice},
        "uuid": user_uuid,
        "timestamp": ts_user,
        "userType": "external",
        "entrypoint": "cli",
        "cwd": src_cwd,
        "sessionId": session_id,
        "version": version,
        "gitBranch": git_branch,
    }
    asst_text = (
        f"Acknowledged. This session has been moved to {dst_session_dir}. "
        f"This transcript is now a tombstone record - resume work from the new location."
    )
    asst_record = {
        "parentUuid": user_uuid,
        "isSidechain": False,
        "message": {
            "model": "tombstone",
            "id": f"msg_{asst_uuid}",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": asst_text}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "stop_details": None,
            "usage": {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
            "diagnostics": {},
        },
        "requestId": request_id,
        "type": "assistant",
        "uuid": asst_uuid,
        "timestamp": ts_asst,
        "userType": "external",
        "entrypoint": "cli",
        "cwd": src_cwd,
        "sessionId": session_id,
        "version": version,
        "gitBranch": git_branch,
    }
    return [user_record, asst_record]


def append_tombstone(src_jsonl: Path, last_record: dict, src_session_dir: Path, dst_session_dir: Path) -> int:
    records = make_tombstone_records(last_record, src_session_dir, dst_session_dir)
    with src_jsonl.open("a") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(records)


def write_tombstone_md(src_session_dir: Path, dst_session_dir: Path, dst_jsonl: Path) -> Path:
    md = src_session_dir / "TOMBSTONE.md"
    md.write_text(
        f"# Session moved\n\n"
        f"This session was copied to a new project directory on {now_iso()} by the "
        f"`move-session` skill.\n\n"
        f"- New session folder: `{dst_session_dir}`\n"
        f"- New transcript: `{dst_jsonl}`\n\n"
        f"Continue work in the new location. The source files here are preserved "
        f"as a record and can be deleted manually once you have verified the new "
        f"location works.\n"
    )
    return md


# ---------- main ----------

def main() -> int:
    ap = argparse.ArgumentParser(description="Move and/or rename a Claude Code session")
    ap.add_argument("--src-session", required=True, help="Path to source cc-sessions/<tag>/ directory")
    ap.add_argument("--dst-cwd", help="Path to destination working directory. Optional if --rename-tag is given. Must be a direct subdir of a root listed in ~/.claude/cc-session-roots.txt.")
    ap.add_argument("--rename-tag", help="New tag for the cc-sessions directory. Original YYYYMMDD- prefix MUST be preserved. May be combined with --dst-cwd to rename and move at once.")
    ap.add_argument("--uuid", help="Disambiguate when multiple jsonls match")
    ap.add_argument("--include-memory", action="store_true", help="Also copy ~/.claude/projects/<src-key>/memory/")
    ap.add_argument("--no-tombstone", action="store_true", help="Skip the source-side tombstone records and TOMBSTONE.md")
    ap.add_argument("--execute", action="store_true", help="Actually perform the copy. Default is dry-run.")
    ap.add_argument("--verify-only", action="store_true", help="Skip copy/rewrite; only re-run verification (requires --dst-cwd).")
    ap.add_argument("--force", action="store_true",
                    help="Bypass root-membership and strict-root rules on the destination. "
                         "Tag-format checks (no spaces/underscores/double-dashes/trailing dash, "
                         "immutable YYYYMMDD prefix) still apply.")
    args = ap.parse_args()

    if args.verify_only and not args.dst_cwd:
        raise SystemExit("--verify-only requires --dst-cwd")
    if not args.dst_cwd and not args.rename_tag:
        raise SystemExit("Must supply at least one of --dst-cwd or --rename-tag")

    src_session_dir = Path(args.src_session).expanduser().resolve()
    if not src_session_dir.is_dir():
        raise SystemExit(f"Source session directory not found: {src_session_dir}")
    if src_session_dir.parent.name != "cc-sessions":
        raise SystemExit(
            f"Expected --src-session to point to a cc-sessions/<tag>/ directory; "
            f"got parent {src_session_dir.parent.name!r}"
        )

    src_cwd_abs = str(src_session_dir.parent.parent)
    src_tag = src_session_dir.name

    if args.dst_cwd:
        dst_cwd_path = Path(args.dst_cwd).expanduser().resolve()
        if not dst_cwd_path.is_dir():
            raise SystemExit(f"Destination cwd not found: {dst_cwd_path}")
        dst_cwd_abs = str(dst_cwd_path)
    else:
        dst_cwd_path = Path(src_cwd_abs)
        dst_cwd_abs = src_cwd_abs

    # Resolve dst tag before validation so the strict-root tag-prefix check
    # sees the value that will actually be on disk after the move.
    dst_tag = args.rename_tag if args.rename_tag else src_tag

    # Run all destination rules through the shared validator (single source of
    # truth with ccd). Always-on checks: --rename-tag format + immutable date
    # prefix when renaming. Bypassed by --force: root membership, strict-root
    # project-name and tag-prefix rules.
    ok, errors = check_session_destination(
        dst_cwd_abs=dst_cwd_path,
        dst_tag=dst_tag,
        src_tag=src_tag,
        force=args.force,
    )
    if not ok:
        msg = "Destination validation failed:\n" + "\n".join(f"  {e}" for e in errors)
        if not args.force:
            msg += "\n  (pass --force to bypass root and strict-root checks; tag-format checks still apply)"
        raise SystemExit(msg)

    # For the plan output below we still need the matched root (when there is
    # one). With --force on a non-root cwd, this is None and we skip the line.
    roots = load_session_roots()
    dst_root = matched_session_root(dst_cwd_path, roots)

    if dst_cwd_abs == src_cwd_abs and dst_tag == src_tag:
        raise SystemExit("Source and destination are identical - nothing to do.")

    src_key = encode_cwd(src_cwd_abs)
    dst_key = encode_cwd(dst_cwd_abs)
    projects_root = Path.home() / ".claude" / "projects"
    src_key_dir = projects_root / src_key
    dst_key_dir = projects_root / dst_key
    dst_session_dir = dst_cwd_path / "cc-sessions" / dst_tag

    summary = discover_session_jsonl(src_key_dir, src_session_dir, args.uuid)
    src_jsonl: Path = summary["path"]
    session_uuid = src_jsonl.stem
    dst_jsonl = dst_key_dir / f"{session_uuid}.jsonl"

    tasks_root = Path.home() / ".claude" / "tasks"
    src_task_key = encode_cwd(str(src_session_dir))
    dst_task_key = encode_cwd(str(dst_session_dir))
    src_task_dir = tasks_root / src_task_key
    dst_task_dir = tasks_root / dst_task_key
    src_task_json_count = len(list(src_task_dir.glob("*.json"))) if src_task_dir.is_dir() else 0

    # Refuse early on empty/malformed source jsonl when tombstoning is on.
    # The tombstone path needs the last record (parentUuid chain, sessionId,
    # cwd, version, gitBranch). If the source jsonl has no parseable records
    # with a uuid, last_record will be None and we'd crash deep inside
    # make_tombstone_records. Surface the problem before any copy happens.
    if not args.no_tombstone and summary.get("last_record") is None:
        raise SystemExit(
            f"Source jsonl has no parseable records with a uuid: {src_jsonl}\n"
            f"  lines in jsonl: {summary['lines']}\n"
            f"This usually means an empty or malformed transcript.\n"
            f"Either pick a different source, fix the transcript, or pass\n"
            f"--no-tombstone to skip the source-side tombstone step."
        )

    # Refuse if we're being invoked from inside the source CC session itself.
    is_active, reasons = detect_active_source_session(src_cwd_abs, src_jsonl, src_key_dir)
    if is_active and not args.verify_only:
        print_in_session_recipe(
            src_session_dir=src_session_dir,
            src_cwd_abs=src_cwd_abs,
            dst_cwd_abs=dst_cwd_abs,
            src_tag=src_tag,
            dst_tag=dst_tag,
            src_key_dir=src_key_dir,
            session_uuid=session_uuid,
            reasons=reasons,
            include_memory=args.include_memory,
            no_tombstone=args.no_tombstone,
            uuid_hint=args.uuid,
        )
        return 2

    # What kind of operation is this?
    cwd_changed = (dst_cwd_abs != src_cwd_abs)
    tag_changed = (dst_tag != src_tag)
    op = []
    if cwd_changed:
        op.append("MOVE")
    if tag_changed:
        op.append("RENAME")
    op_label = "+".join(op) or "NO-OP"

    # Build path-string replacements for the destination jsonl.
    # If cwd is not changing, the source jsonl IS the destination jsonl (same
    # project key) and we do NOT rewrite it - source cc-sessions/<old-tag>/ is
    # preserved by copy, so old paths still resolve.
    home = str(Path.home())
    replacements: list[tuple[str, str]] = []
    if cwd_changed:
        replacements.append((src_cwd_abs, dst_cwd_abs))
        # Defense in depth: the encoded project-key form ("-mnt-c-...") doesn't
        # currently appear inside jsonl payloads in any CC version we've seen,
        # but if it ever does (e.g. embedded in a tool-result string referencing
        # the projects dir) we want to catch it. Expect 0 hits in normal runs;
        # a non-zero count is informative, not alarming.
        replacements.append((src_key, dst_key))
        if src_cwd_abs.startswith(home + "/"):
            rel_src = src_cwd_abs[len(home) + 1:]
            rel_dst = dst_cwd_abs[len(home) + 1:] if dst_cwd_abs.startswith(home + "/") else dst_cwd_abs
            replacements.append((f"~/{rel_src}", f"~/{rel_dst}"))
        if tag_changed:
            # Combined move + rename: also rewrite cc-sessions/<old>/ -> cc-sessions/<new>/
            replacements.append((f"cc-sessions/{src_tag}", f"cc-sessions/{dst_tag}"))

    # ----- plan -----
    print("=" * 72)
    print(f"PLAN ({op_label})")
    print("=" * 72)
    print(f"  src cwd            : {src_cwd_abs}")
    print(f"  dst cwd            : {dst_cwd_abs}{' (same)' if not cwd_changed else ''}")
    if dst_root is not None:
        strict_note = " [strict]" if is_strict_root(dst_root) else ""
        print(f"  dst cwd valid      : yes (under root: {dst_root}{strict_note})")
    else:
        print(f"  dst cwd valid      : --force (cwd not under any configured root)")
    print(f"  src tag            : {src_tag}")
    print(f"  dst tag            : {dst_tag}{' (same)' if not tag_changed else ''}")
    print(f"  src session dir    : {src_session_dir}")
    print(f"  dst session dir    : {dst_session_dir}")
    print(f"  src jsonl          : {src_jsonl}")
    if cwd_changed:
        print(f"  dst jsonl          : {dst_jsonl}")
    else:
        print(f"  dst jsonl          : (same as src - rename does not move the jsonl)")
    print(f"  session uuid       : {session_uuid}")
    print(f"  jsonl lines        : {summary['lines']}")
    print(f"  jsonl first user   : {summary['first_user'][:80]!r}")
    if replacements:
        print(f"  string replacements:")
        for old, new in replacements:
            print(f"    {old!r}\n      -> {new!r}")
    else:
        print(f"  string replacements: none (rename-only, jsonl unchanged)")
    if src_task_json_count > 0:
        print(f"  src task dir       : {src_task_dir} ({src_task_json_count} .json files)")
        print(f"  dst task dir       : {dst_task_dir}")
    else:
        print(f"  task migration     : none (no task dir at {src_task_dir})")
    print(f"  include memory     : {args.include_memory}")
    print(f"  tombstone          : {not args.no_tombstone}")
    print(f"  mode               : {'EXECUTE' if args.execute else 'DRY-RUN' if not args.verify_only else 'VERIFY-ONLY'}")
    print()

    # Pre-flight clobber checks (skip in verify-only).
    if not args.verify_only:
        clobber_targets = [dst_session_dir]
        if cwd_changed:
            clobber_targets.append(dst_jsonl)
        if src_task_dir.is_dir():
            clobber_targets.append(dst_task_dir)
        for p in clobber_targets:
            if p.exists():
                raise SystemExit(f"ABORT: destination already exists: {p}")

    if args.verify_only:
        if not dst_jsonl.exists():
            raise SystemExit(f"--verify-only: destination jsonl not found: {dst_jsonl}")
        v = verify_dst_jsonl(src_jsonl, dst_jsonl, dst_cwd_abs, src_cwd_abs, src_key, strict=False)
        print_verify(v)
        return 0 if v["ok"] else 1

    if not args.execute:
        print("Dry-run complete. Pass --execute to perform the operation.")
        return 0

    # ----- execute -----
    print("=" * 72)
    print(f"EXECUTING ({op_label})")
    print("=" * 72)

    dst_session_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src_session_dir, dst_session_dir)
    print(f"  copied {src_session_dir} -> {dst_session_dir}")

    if cwd_changed:
        dst_key_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src_jsonl, dst_jsonl)
        print(f"  copied {src_jsonl} -> {dst_jsonl}")
        counts = rewrite_jsonl_paths(dst_jsonl, replacements)
        for old, new in replacements:
            print(f"  rewrote {counts[old]:>4} occurrences: {old!r} -> {new!r}")
    else:
        print(f"  jsonl NOT copied (rename-only; same project key)")

    # Memory handling (only meaningful when project key changes).
    # Auto-include if the source project key contains exactly one session
    # (the one being moved): the memory effectively belonged to it, and
    # leaving it behind would orphan the memory dir.
    src_mem = src_key_dir / "memory"
    dst_mem = dst_key_dir / "memory"
    sole_session_in_src_key = (
        cwd_changed
        and len(list_candidate_jsonls(src_key_dir)) == 1
    )
    effective_include_memory = args.include_memory or (
        sole_session_in_src_key and src_mem.is_dir() and not dst_mem.exists()
    )
    if not cwd_changed:
        if args.include_memory:
            # User explicitly asked for memory copy but the operation is
            # rename-only - there is no destination project key to copy into,
            # because the project key is unchanged. Warn loudly and continue.
            print(f"  WARNING: --include-memory ignored (rename-only; same project key, same memory).")
        else:
            print(f"  memory N/A (rename-only; same project key, same memory)")
    elif effective_include_memory:
        if src_mem.is_dir():
            if dst_mem.exists():
                print(f"  WARNING: destination memory exists; not overwriting: {dst_mem}")
            else:
                shutil.copytree(src_mem, dst_mem)
                why = "(--include-memory)" if args.include_memory else "(auto: source key had only this session)"
                print(f"  copied memory {why}: {src_mem} -> {dst_mem}")
        else:
            print(f"  no source memory to copy ({src_mem} does not exist)")
    else:
        if src_mem.is_dir():
            print(f"  memory NOT copied (default). Source memory exists at: {src_mem}")
            print(f"  pass --include-memory to copy it.")
        else:
            print(f"  memory NOT copied (default; source memory does not exist anyway).")

    # Verify (only when we actually wrote a destination jsonl).
    if cwd_changed:
        print()
        v = verify_dst_jsonl(src_jsonl, dst_jsonl, dst_cwd_abs, src_cwd_abs, src_key, strict=True)
        print_verify(v)
        if not v["ok"]:
            print()
            print("VERIFY: FAIL - destination jsonl did not pass all checks. NOT writing tombstone.")
            return 1

    # Tombstone (always informative when something changed).
    # IMPORTANT: jsonl tombstone records are appended to the SOURCE jsonl only
    # when the project key changes (MOVE / MOVE+RENAME). For RENAME-only the
    # source jsonl IS the destination jsonl (same project key, jsonl never
    # copied) - appending tombstone records to it would corrupt the live
    # transcript that the user resumes into. The TOMBSTONE.md file is still
    # written to the source cc-sessions/ dir in both cases, since that dir is
    # always preserved as a record after the copy.
    if not args.no_tombstone:
        if cwd_changed:
            n = append_tombstone(src_jsonl, summary["last_record"], src_session_dir, dst_session_dir)
            print(f"  appended {n} tombstone records to {src_jsonl}")
        else:
            print(f"  jsonl tombstone records skipped (rename-only; src jsonl is shared with dst - appending would corrupt the live transcript)")
        md = write_tombstone_md(src_session_dir, dst_session_dir, dst_jsonl)
        print(f"  wrote {md}")
    else:
        print("  tombstone skipped (--no-tombstone)")

    # Task file migration: ~/.claude/tasks/<src-session-key>/ -> ~/.claude/tasks/<dst-session-key>/
    # Applies to all operation types (MOVE, RENAME, MOVE+RENAME) because the session dir path
    # changes in all three cases, making the old task key unreachable.
    if src_task_dir.is_dir():
        shutil.copytree(src_task_dir, dst_task_dir)
        migrated_count = len(list(dst_task_dir.glob("*.json")))
        print(f"  copied task dir ({migrated_count} .json files): {src_task_dir} -> {dst_task_dir}")
    else:
        print(f"  task files: none (no task dir at {src_task_dir} - skipped)")

    # Messaging safety: refresh display tags in any pending messages that
    # reference the moved session's uuid, and re-anchor the cursor on a
    # project move. uuid routing is unaffected (messages are never orphaned
    # by a rename), so this is cosmetic + an explicit cursor call site.
    try:
        from cc_session_tools.lib.messaging.move_safety import (
            refresh_display_tags, relocate_cursor,
        )
        if session_uuid:
            refresh_display_tags(uuid=session_uuid, new_tag=dst_tag)
            if cwd_changed:
                relocate_cursor(
                    uuid=session_uuid,
                    old_partition="",  # source partition not needed (uuid-keyed)
                    new_partition="",
                )
    except ImportError:
        pass  # messaging lib not installed; nothing to refresh

    # Pending-rename marker: dropped into the destination cc-sessions dir on
    # tag change. The bundled SessionStart hook
    # (~/.claude/skills/move-session/hooks/sessionstart-pending-rename.sh)
    # surfaces these markers as a system reminder so the model runs /rename
    # on the next resume without the user having to remember.
    if tag_changed:
        marker = dst_session_dir / ".pending-rename"
        marker.write_text(
            f"uuid: {session_uuid}\n"
            f"tag: {dst_tag}\n"
            f"written_at: {now_iso()}\n"
        )
        print(f"  wrote pending-rename marker: {marker}")

        # Write/update the .tag file in the destination transcript directory so
        # that find_jsonl_for_session(dst_tag) resolves immediately. Without
        # this, ccr <new-name> finds the cc-sessions directory on disk but
        # claude --resume <new-name> falls back to the picker because the jsonl
        # custom-title still has the old name (RENAME-only never modifies the
        # jsonl, and /rename hasn't run yet). The .tag file gives find_jsonl_for_session
        # an alternative lookup path that works before the title is updated.
        # For RENAME-only dst_key_dir == src_key_dir; this overwrites any stale
        # .tag file with the new suffix. For MOVE+RENAME it creates the file in
        # the new project key dir.
        dst_tag_m = DATE_PREFIX_RE.match(dst_tag)
        dst_tag_suffix = dst_tag_m.group(2) if dst_tag_m else dst_tag
        tag_file = dst_key_dir / f"{session_uuid}.tag"
        tag_file.write_text(dst_tag_suffix + "\n")
        print(f"  wrote/updated .tag file: {tag_file}")

    # Cleanup script generation: the user can't easily delete the source via
    # `rm -rf` because the bash-hard-deny hook blocks local-file deletion. We
    # write a script they can `bash` themselves AFTER they have verified the
    # destination resumes correctly.
    cleanup_script = write_cleanup_script(
        src_session_dir=src_session_dir,
        src_jsonl=src_jsonl if cwd_changed else None,
        src_tag=src_tag,
        dst_cwd_abs=dst_cwd_abs,
        session_uuid=session_uuid,
        src_task_dir=src_task_dir if src_task_dir.is_dir() else None,
    )

    print()
    print("=" * 72)
    print("DONE")
    print("=" * 72)
    print(f"  Resume with:  cd {dst_cwd_abs} && claude --resume {session_uuid}")
    if tag_changed:
        print(f"  Picker label will be auto-fixed by the pending-rename hook on resume.")
        print(f"  (If the hook isn't installed yet, run: /rename {dst_tag})")
    print()
    print(f"  After verifying the destination resumes correctly, run this to")
    print(f"  delete the stale source files (script generated for you because")
    print(f"  bash-hard-deny blocks rm of local files from inside CC):")
    print(f"    bash {cleanup_script}")
    return 0


def write_cleanup_script(
    src_session_dir: Path,
    src_jsonl: Path | None,
    src_tag: str,
    dst_cwd_abs: str,
    session_uuid: str,
    src_task_dir: Path | None = None,
) -> Path:
    """Write a cleanup script to /tmp that removes the stale source files.

    src_jsonl is None for rename-only operations (jsonl shared between src and
    dst project keys; deleting it would destroy the live transcript).
    """
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    script_path = Path("/tmp") / f"move-session-cleanup-{src_tag}-{ts}.sh"
    lines = [
        "#!/bin/bash",
        "# Cleanup script generated by move-session.",
        f"# Source session: {src_session_dir}",
        f"# Destination cwd: {dst_cwd_abs}",
        f"# Session UUID:   {session_uuid}",
        "#",
        "# Run ONLY after you have verified the destination resumes correctly:",
        f"#   cd {dst_cwd_abs} && claude --resume {session_uuid}",
        "",
        "set -euo pipefail",
        "",
        f'echo "Removing source cc-sessions dir: {src_session_dir}"',
        f'rm -rf "{src_session_dir}"',
    ]
    if src_jsonl is not None:
        lines += [
            "",
            f'echo "Removing source jsonl: {src_jsonl}"',
            f'rm -f "{src_jsonl}"',
        ]
    else:
        lines += [
            "",
            "# No jsonl removal: rename-only kept the same project key, so the",
            "# jsonl IS the destination transcript. Deleting it would destroy",
            "# the live record.",
        ]
    if src_task_dir is not None:
        lines += [
            "",
            f'echo "Removing source task dir: {src_task_dir}"',
            f'rm -rf "{src_task_dir}"',
        ]
    lines.append("")
    lines.append('echo "Cleanup complete."')
    lines.append("")
    script_path.write_text("\n".join(lines))
    script_path.chmod(0o755)
    return script_path


def print_verify(v: dict) -> None:
    print("VERIFICATION")
    print(f"  src_lines           : {v['src_lines']}")
    print(f"  dst_lines           : {v['dst_lines']}")
    print(f"  json_valid_lines    : {v['json_ok']}")
    print(f"  json_invalid_lines  : {v['json_err']}")
    print(f"  remaining_src_long  : {v['remaining_long']}")
    print(f"  remaining_src_key   : {v['remaining_key']}")
    print(f"  cwd_distinct_in_dst : {v['cwds_in_dst']}")
    print(f"  RESULT              : {'PASS' if v['ok'] else 'FAIL'}")


if __name__ == "__main__":
    sys.exit(main())
