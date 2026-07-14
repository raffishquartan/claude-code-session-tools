#!/usr/bin/env python3
# scripts/migrate_fires_jsonl_to_telemetry_db.py
"""One-shot migration: fires.jsonl (+ rotated .1/.2/.3 slots) → telemetry.db.

Reads ~/.cache/claude/logs/fires.jsonl and any of fires.jsonl.{1,2,3} that
exist (oldest slot first: .3, .2, .1, then the live file — so rows land in
telemetry.db in original chronological order), classifies each line as a
generic telemetry_events row or a catchup_events row (hook == "catchup"),
and inserts them into telemetry.db under the new data-home root.

Because catchup_events.id is INTEGER PRIMARY KEY AUTOINCREMENT and rows are
inserted in original chronological order into an initially-empty table, the
Nth catch-up row inserted gets id == N — the same integer the existing
row-count-based cursor files (<scheduler-dir>/.cursors/<uuid>.json,
{"offset": N}) already store. On a machine whose fires.jsonl never rotated,
no cursor-file rewrite is needed: an old stored offset of 42 continues to
mean "the 42nd catch-up row" post-migration. If rotation already discarded
old catch-up rows before this runs, the alignment is off by the number of
dropped rows — a bounded, self-healing limitation of migrating from a lossy
source (read_since clamps a stale offset rather than crashing). See the
"Migration seam caveat" in this task's plan notes.

Non-destructive: writes to telemetry.db, verifies the inserted row count
against the parsed row count, tar.gz-backs-up the source fires.jsonl* files
to <dest-dir>/migration-backups/, and only then deletes them from the source
directory. Malformed lines are skipped and counted, never silently dropped
from the summary — this is observability data, not irreplaceable content
(see docs/superpowers/plans/2026-07-13-data-store-uplift-00-overview.md §4).

Recovery from a partial run: safe to re-run in almost every failure case (it
writes to a fresh table, verifies row counts, and only deletes source files
after the tar-backup succeeds). The one window that needs a manual step is a
kill AFTER conn.commit() but BEFORE the tar-backup + source-delete: the dest
DB now holds the rows, so a plain re-run hits the "already has N row(s)" guard
(safe, but blocks), while --force would double-insert (AUTOINCREMENT id
alignment forbids INSERT OR IGNORE dedup-by-content). To recover, truncate the
dest tables and reset their id sequences, then re-run WITHOUT --force:

    sqlite3 ~/.local/share/claude/telemetry.db \\
      "DELETE FROM telemetry_events; DELETE FROM catchup_events; \\
       DELETE FROM sqlite_sequence WHERE name IN ('telemetry_events','catchup_events');"
    python3 scripts/migrate_fires_jsonl_to_telemetry_db.py

Resetting sqlite_sequence is required so re-inserted rows recover the id == N
alignment; the source files are still present in this window (deleted only
after the backup step the operator never reached), so nothing is lost.

Usage:
    python3 scripts/migrate_fires_jsonl_to_telemetry_db.py [--dry-run] [--force]
    python3 scripts/migrate_fires_jsonl_to_telemetry_db.py \\
        --source-dir ~/.cache/claude/logs --dest-dir ~/.local/share/claude

Run this manually, once per machine, after Phase 5 has been deployed —
not part of `ccst install` (see design-spec §8.3/§8.5).
"""
from __future__ import annotations

import argparse
import json
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from cc_session_tools.lib import paths, telemetry_store  # noqa: E402

_OLD_DEFAULT_SOURCE_DIR = Path.home() / ".cache" / "claude" / "logs"
_ROTATED_SLOTS_OLDEST_FIRST = (3, 2, 1)


def _source_files(source_dir: Path) -> list[Path]:
    files = [
        source_dir / f"fires.jsonl.{n}"
        for n in _ROTATED_SLOTS_OLDEST_FIRST
        if (source_dir / f"fires.jsonl.{n}").is_file()
    ]
    live = source_dir / "fires.jsonl"
    if live.is_file():
        files.append(live)
    return files


def _parse_lines(files: list[Path]) -> tuple[list[dict[str, object]], list[dict[str, object]], int]:
    """Returns (telemetry_rows, catchup_rows, malformed_count)."""
    telemetry_rows: list[dict[str, object]] = []
    catchup_rows: list[dict[str, object]] = []
    malformed = 0
    for f in files:
        for raw in f.read_text().splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                line = json.loads(raw)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(line, dict):
                malformed += 1
                continue
            if line.get("hook") == "catchup":
                try:
                    detail = json.loads(str(line.get("verdict", "{}")))
                except json.JSONDecodeError:
                    detail = {}
                catchup_rows.append({
                    "ts": line.get("ts", ""),
                    "job_id": str(detail.get("job_id", "")),
                    "event": str(detail.get("event", "")),
                    "owed": int(detail.get("owed", 0) or 0),
                    "ran": int(detail.get("ran", 0) or 0),
                    "exit_code": detail.get("exit_code"),
                    "duration_ms": int(detail.get("duration_ms", 0) or 0),
                    "error": detail.get("error"),
                    "consecutive_failures": int(detail.get("consecutive_failures", 0) or 0),
                })
            else:
                telemetry_rows.append({
                    "ts": line.get("ts", ""),
                    "hook": str(line.get("hook", "")),
                    "event": str(line.get("event", "")),
                    "tool": str(line.get("tool", "")),
                    "session_id": str(line.get("session_id", "")),
                    "cwd_short": str(line.get("cwd", "")),
                    "decision": str(line.get("decision", "")),
                    "cache": str(line.get("cache", "")),
                    "verdict": str(line.get("verdict", "")),
                    "input_hash": str(line.get("input_hash", "")),
                })
    return telemetry_rows, catchup_rows, malformed


def migrate(*, source_dir: Path, dest_dir: Path, dry_run: bool, force: bool) -> int:
    files = _source_files(source_dir)
    if not files:
        print(f"No fires.jsonl* files found under {source_dir} — nothing to migrate.")
        return 0

    telemetry_rows, catchup_rows, malformed = _parse_lines(files)
    print(
        f"Parsed {len(files)} file(s): {len(telemetry_rows)} telemetry row(s), "
        f"{len(catchup_rows)} catchup row(s), {malformed} malformed line(s) skipped."
    )

    dest_db = dest_dir / telemetry_store.DB_FILENAME
    if dry_run:
        print(f"[dry-run] would insert into {dest_db}")
        return 0

    conn = telemetry_store.connect(dest_dir)
    try:
        before_t = conn.execute("SELECT COUNT(*) FROM telemetry_events").fetchone()[0]
        before_c = conn.execute("SELECT COUNT(*) FROM catchup_events").fetchone()[0]
        if (before_t + before_c) > 0 and not force:
            print(
                f"ERROR: {dest_db} already has {before_t + before_c} row(s). Refusing to "
                "double-insert. Re-run with --force if this is intentional.",
                file=sys.stderr,
            )
            return 1

        for r in telemetry_rows:
            conn.execute(
                "INSERT INTO telemetry_events "
                "(ts, hook, event, tool, session_id, cwd_short, decision, cache, verdict, input_hash) "
                "VALUES (:ts, :hook, :event, :tool, :session_id, :cwd_short, :decision, :cache, :verdict, :input_hash)",
                r,
            )
        for r in catchup_rows:
            conn.execute(
                "INSERT INTO catchup_events "
                "(ts, job_id, event, owed, ran, exit_code, duration_ms, error, consecutive_failures) "
                "VALUES (:ts, :job_id, :event, :owed, :ran, :exit_code, :duration_ms, :error, :consecutive_failures)",
                r,
            )
        conn.commit()

        after_t = conn.execute("SELECT COUNT(*) FROM telemetry_events").fetchone()[0]
        after_c = conn.execute("SELECT COUNT(*) FROM catchup_events").fetchone()[0]
    finally:
        conn.close()

    if after_t - before_t != len(telemetry_rows) or after_c - before_c != len(catchup_rows):
        print(
            "ERROR: verification failed — inserted row count does not match parsed "
            f"row count (telemetry: {after_t - before_t} vs {len(telemetry_rows)}, "
            f"catchup: {after_c - before_c} vs {len(catchup_rows)}). Source files left "
            "untouched.",
            file=sys.stderr,
        )
        return 1

    backup_dir = dest_dir / "migration-backups"
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"fires-jsonl-{stamp}.tar.gz"
    with tarfile.open(backup_path, "w:gz") as tar:
        for f in files:
            tar.add(f, arcname=f.name)
    print(f"Backed up {len(files)} source file(s) to {backup_path}")

    for f in files:
        f.unlink()
    print(f"Removed {len(files)} source file(s) from {source_dir}")

    print(
        f"Migration complete: {len(telemetry_rows)} telemetry row(s), "
        f"{len(catchup_rows)} catchup row(s) inserted into {dest_db}. "
        f"{malformed} malformed line(s) skipped."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--source-dir", default=None, metavar="PATH",
        help=f"Old JSONL directory (default: {_OLD_DEFAULT_SOURCE_DIR})",
    )
    p.add_argument(
        "--dest-dir", default=None, metavar="PATH",
        help="New telemetry.db directory (default: paths.data_home())",
    )
    p.add_argument("--dry-run", action="store_true", help="Report what would be migrated without writing anything")
    p.add_argument("--force", action="store_true", help="Allow inserting into a dest DB that already has rows")
    args = p.parse_args(argv)

    source_dir = Path(args.source_dir) if args.source_dir else _OLD_DEFAULT_SOURCE_DIR
    dest_dir = Path(args.dest_dir) if args.dest_dir else paths.data_home()

    print(f"Source: {source_dir}")
    print(f"Dest  : {dest_dir / telemetry_store.DB_FILENAME}")

    return migrate(source_dir=source_dir, dest_dir=dest_dir, dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
