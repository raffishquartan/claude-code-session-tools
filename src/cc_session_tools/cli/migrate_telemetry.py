"""One-shot migration: fires.jsonl (+ rotated .1/.2/.3 slots) -> telemetry.db.

Exposed via `ccst migrate telemetry` (and as part of `ccst migrate all`).

Reads ~/.cache/claude/logs/fires.jsonl and any of fires.jsonl.{1,2,3} that
exist (oldest slot first: .3, .2, .1, then the live file — so rows land in
telemetry.db in original chronological order), classifies each line as a
generic telemetry_events row or a catchup_events row (hook == "catchup"),
and inserts them into telemetry.db under the new data-home root.

The import APPENDS. telemetry.db is virtually never empty when this runs: the
hook writer (hooks.telemetry) starts inserting the moment CCST is
installed, so any operator who opens a single Claude Code session before
migrating already has rows. Refusing to import into a non-empty database
therefore blocked the normal upgrade path rather than a rare edge case, and
the "re-run with --force" it suggested was both unreachable from `ccst
migrate all` (no such flag there) and the wrong remedy anyway.

Whether the import has run is tracked by an explicit marker
(telemetry_store.LEGACY_JSONL_MIGRATION in the migrations table), never
inferred from row counts. Row count cannot answer the question — a non-empty
table means "the hook writer has been running", which says nothing about
whether fires.jsonl was ever imported. The marker also makes a genuine
double-insert impossible: a second run is refused because the marker is
present, not because the table is non-empty. This closes the old
kill-after-commit-before-backup window, which previously needed a manual
truncate-and-reinsert to recover from.

Appending costs chronological id order: imported rows are older than
everything already stored but AUTOINCREMENT hands them the highest ids. Two
consequences, both handled:

- Anything wanting time order must sort by ts. telemetry_trim already does;
  telemetry_query was changed to.
- catchup_events ids are the scheduler's surfacing cursor (`WHERE id > ?`),
  so appended history would otherwise read as brand-new activity to every
  existing session. After a successful import every cursor is advanced past
  the imported rows (cursor.advance_all_cursors_to), applying the same rule
  seed_new_session applies to a new session: pre-existing history is not
  news. This supersedes the old id == N alignment with the pre-1.0.0
  row-count cursor files, which append necessarily breaks.

Non-destructive: writes to telemetry.db, verifies the inserted row count
against the parsed row count, tar.gz-backs-up the source fires.jsonl* files
to <dest-dir>/migration-backups/, and only then deletes them from the source
directory. Malformed lines are skipped and counted, never silently dropped
from the summary — this is observability data, not irreplaceable content.

Safe to re-run in every failure case. A kill before the marker is written
leaves the source files in place and re-imports cleanly; a kill after it is
written means the rows are in and the next run says so.

This script's final step deletes already-backed-up-and-verified source files,
so `bash-hard-deny` statically blocks it (and `ccst migrate telemetry`) from
running inside a Claude Code session — run it from a plain terminal instead
(see docs/data-store-migration-steps.md).

Usage:
    ccst migrate telemetry [--dry-run]
    ccst migrate telemetry --source-dir ~/.cache/claude/logs --dest-dir ~/.local/share/claude
"""
from __future__ import annotations

import argparse
import json
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

from cc_session_tools.lib import db, paths, telemetry_store
from cc_session_tools.lib.scheduler import cursor

DEFAULT_OLD_SOURCE_DIR = Path.home() / ".cache" / "claude" / "logs"
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


def _already_imported(dest_dir: Path) -> bool:
    """True if this store records the fires.jsonl import as done.

    Never creates the store: a dry run, and a run with nothing to migrate,
    must both leave the filesystem exactly as they found it.
    """
    if not (dest_dir / telemetry_store.DB_FILENAME).exists():
        return False
    conn = telemetry_store.connect(dest_dir)
    try:
        return db.migration_applied(conn, telemetry_store.LEGACY_JSONL_MIGRATION)
    finally:
        conn.close()


def migrate(*, source_dir: Path, dest_dir: Path, dry_run: bool) -> int:
    dest_db = dest_dir / telemetry_store.DB_FILENAME

    files = _source_files(source_dir)
    if not files:
        print(f"No fires.jsonl* files found under {source_dir} — nothing to migrate.")
        return 0

    if _already_imported(dest_dir):
        print(
            f"ERROR: {dest_db} already records the fires.jsonl import "
            f"({telemetry_store.LEGACY_JSONL_MIGRATION}). Refusing to import a second "
            "time — those rows are already in. The fires.jsonl* files still under "
            f"{source_dir} are leftovers the delete step never reached; check them "
            "against the tar.gz in migration-backups/ and remove them by hand.",
            file=sys.stderr,
        )
        return 1

    telemetry_rows, catchup_rows, malformed = _parse_lines(files)
    print(
        f"Parsed {len(files)} file(s): {len(telemetry_rows)} telemetry row(s), "
        f"{len(catchup_rows)} catchup row(s), {malformed} malformed line(s) skipped."
    )

    if dry_run:
        print(f"[dry-run] would append into {dest_db}")
        return 0

    conn = telemetry_store.connect(dest_dir)
    try:
        before_rows = conn.execute(
            "SELECT (SELECT COUNT(*) FROM telemetry_events) "
            "     + (SELECT COUNT(*) FROM catchup_events) AS n"
        ).fetchone()["n"]
        if before_rows > 0:
            print(
                f"{dest_db} already holds {before_rows} row(s) written by the hooks "
                "since CCST was installed; appending the imported rows alongside them."
            )

        # total_changes counts only what THIS connection wrote, so the
        # verification below is immune to a hook firing mid-import. A
        # before/after COUNT(*) delta is not: telemetry.db has live writers by
        # the time anyone runs this, and one concurrent fire would fail an
        # otherwise-correct migration.
        changes_before = conn.total_changes

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

        inserted = conn.total_changes - changes_before
        expected = len(telemetry_rows) + len(catchup_rows)
        if inserted != expected:
            conn.rollback()
            print(
                f"ERROR: verification failed — inserted {inserted} row(s), expected "
                f"{expected}. Nothing was written and the source files are untouched.",
                file=sys.stderr,
            )
            return 1

        # Same transaction as the inserts, deliberately: a marker written in a
        # later commit would leave a window where the rows are in but nothing
        # records that, and the next run would import them again.
        db.record_migration(
            conn,
            telemetry_store.LEGACY_JSONL_MIGRATION,
            applied_at=telemetry_store.now_iso(),
        )
        conn.commit()

        new_offset = conn.execute(
            "SELECT COALESCE(MAX(id), 0) AS m FROM catchup_events"
        ).fetchone()["m"]
    finally:
        conn.close()

    if catchup_rows:
        moved = cursor.advance_all_cursors_to(int(new_offset))
        print(
            f"Advanced {moved} scheduler catch-up cursor(s) past the imported rows "
            f"(offset {new_offset}) so imported history is not resurfaced as new activity."
        )

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
    p = argparse.ArgumentParser(
        prog="ccst migrate telemetry",
        description="Migrate fires.jsonl (+ rotated slots) into telemetry.db.",
    )
    p.add_argument(
        "--source-dir", default=None, metavar="PATH",
        help=f"Old JSONL directory (default: {DEFAULT_OLD_SOURCE_DIR})",
    )
    p.add_argument(
        "--dest-dir", default=None, metavar="PATH",
        help="New telemetry.db directory (default: paths.data_home())",
    )
    p.add_argument("--dry-run", action="store_true", help="Report what would be migrated without writing anything")
    args = p.parse_args(argv)

    source_dir = Path(args.source_dir) if args.source_dir else DEFAULT_OLD_SOURCE_DIR
    dest_dir = Path(args.dest_dir) if args.dest_dir else paths.data_home()

    print(f"Source: {source_dir}")
    print(f"Dest  : {dest_dir / telemetry_store.DB_FILENAME}")

    return migrate(source_dir=source_dir, dest_dir=dest_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
