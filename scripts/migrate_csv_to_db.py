#!/usr/bin/env python3
"""One-shot import of the legacy command-cache CSV into the SQLite cache DB.

Reads ~/.claude/hooks/command-cache.csv (the retired CSV cache) and inserts/
updates rows into the SQLite cache at ~/.cache/claude/logs/command-cache.db,
preserving fire_count, last_seen and validated_at. norm_hash is set to NULL
(the CSV has no normalised-hash column).

This deliberately does NOT use cccs_hooks.cache.cache_record(): that helper
resets fire_count to 1 and runs a 90-day prune on every write. The migration
opens the DB directly and uses INSERT ... ON CONFLICT(exact_hash) DO UPDATE so
the historical counters survive and re-running is idempotent.

Usage:
    python3 scripts/migrate_csv_to_db.py [--csv PATH] [--db PATH] [--dry-run]

Defaults:
    --csv   ~/.claude/hooks/command-cache.csv
    --db    $CCCS_CACHE_DB, else ~/.cache/claude/logs/command-cache.db
"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
from pathlib import Path

_DEFAULT_CSV = Path.home() / ".claude" / "hooks" / "command-cache.csv"
_DEFAULT_DB = Path.home() / ".cache" / "claude" / "logs" / "command-cache.db"

# Exact schema mirrored from cccs_hooks.cache._DDL (command_cache table only).
_DDL = """
CREATE TABLE IF NOT EXISTS command_cache (
    exact_hash    TEXT PRIMARY KEY,
    norm_hash     TEXT,
    verdict       TEXT    NOT NULL,
    risks_summary TEXT    NOT NULL,
    preview       TEXT    NOT NULL,
    fire_count    INTEGER NOT NULL DEFAULT 1,
    last_seen     TEXT    NOT NULL,
    validated_at  TEXT    NOT NULL,
    cache_source  TEXT    NOT NULL DEFAULT 'auto'
);
CREATE INDEX IF NOT EXISTS idx_norm ON command_cache(norm_hash)
    WHERE norm_hash IS NOT NULL;
"""

_UPSERT = """
INSERT INTO command_cache
    (exact_hash, norm_hash, verdict, risks_summary, preview,
     fire_count, last_seen, validated_at, cache_source)
VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(exact_hash) DO UPDATE SET
    norm_hash     = excluded.norm_hash,
    verdict       = excluded.verdict,
    risks_summary = excluded.risks_summary,
    preview       = excluded.preview,
    fire_count    = excluded.fire_count,
    last_seen     = excluded.last_seen,
    validated_at  = excluded.validated_at,
    cache_source  = excluded.cache_source;
"""


def _db_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("CCCS_CACHE_DB", "").strip()
    return Path(env) if env else _DEFAULT_DB


def _open_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.executescript(_DDL)
    return conn


def migrate(csv_path: Path, db_path: Path, dry_run: bool) -> int:
    if not csv_path.exists():
        print(f"CSV not found: {csv_path} - nothing to migrate.", file=sys.stderr)
        return 1

    rows: list[tuple[str, str, str, str, int, str, str, str]] = []
    errors = 0
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for lineno, row in enumerate(reader, start=2):
            try:
                exact_hash = row["hash"].strip()
                if not exact_hash:
                    raise ValueError("empty hash")
                fire_count = int(row.get("fire_count") or 1)
                rows.append(
                    (
                        exact_hash,
                        row.get("verdict", ""),
                        row.get("risks_summary", ""),
                        row.get("command_preview", ""),
                        fire_count,
                        row.get("last_seen", ""),
                        row.get("last_validated_at", ""),
                        row.get("cache_source", "auto") or "auto",
                    )
                )
            except (KeyError, ValueError) as exc:
                errors += 1
                print(f"  skip line {lineno}: {exc}", file=sys.stderr)

    print(f"Read {len(rows)} valid row(s) from {csv_path} ({errors} skipped).")

    if dry_run:
        print(f"[dry-run] would upsert {len(rows)} row(s) into {db_path}")
        for r in rows:
            print(f"  {r[0][:12]}...  fire_count={r[4]}  validated_at={r[6]}")
        return 0

    conn = _open_db(db_path)
    try:
        inserted = 0
        updated = 0
        for r in rows:
            cur = conn.execute(
                "SELECT 1 FROM command_cache WHERE exact_hash = ?", (r[0],)
            )
            existed = cur.fetchone() is not None
            conn.execute(_UPSERT, r)
            if existed:
                updated += 1
            else:
                inserted += 1
        conn.commit()
    finally:
        conn.close()

    print(
        f"Migration complete: {len(rows)} processed, "
        f"{inserted} inserted, {updated} updated, {errors} errors. DB: {db_path}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="migrate_csv_to_db.py",
        description="Import the legacy command-cache CSV into the SQLite cache DB.",
    )
    p.add_argument("--csv", default=None, metavar="PATH", help=f"CSV path (default: {_DEFAULT_CSV})")
    p.add_argument("--db", default=None, metavar="PATH", help=f"DB path (default: {_DEFAULT_DB})")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be inserted without writing to the DB",
    )
    args = p.parse_args(argv)

    csv_path = Path(args.csv) if args.csv else _DEFAULT_CSV
    db_path = _db_path(args.db)
    return migrate(csv_path, db_path, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
