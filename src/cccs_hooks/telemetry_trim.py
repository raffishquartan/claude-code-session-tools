"""CLI for explicit telemetry pruning: ccst telemetry trim.

Trims telemetry.db (see cc_session_tools.lib.telemetry_store) by:
  --max-age-days <N>   Delete rows older than N days from both
                        telemetry_events and catchup_events.
  --max-size <MB>       Delete the oldest rows (split across both tables)
                        until the on-disk file is at/under this size.
                        LOSSY — unlike the old JSONL scheme (which rotated
                        into up to 3 kept backup slots), a SQL DELETE is
                        permanent. Acceptable because this is observability
                        data, not irreplaceable content.

Both flags are optional and can be combined. Without any flags, no pruning is
done and the tool prints the current file size and row counts.

Designed to be invoked via ``ccst telemetry trim``; can also run directly as
``python -m cccs_hooks.telemetry_trim``.
"""
from __future__ import annotations

import argparse
import datetime
import sqlite3
import sys
from pathlib import Path

from cc_session_tools.lib import telemetry_store

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
_MAX_SIZE_ITERATIONS = 20


def _row_counts(conn: sqlite3.Connection) -> tuple[int, int]:
    events = conn.execute("SELECT COUNT(*) FROM telemetry_events").fetchone()[0]
    catchup = conn.execute("SELECT COUNT(*) FROM catchup_events").fetchone()[0]
    return events, catchup


def trim_by_age(conn: sqlite3.Connection, max_age_days: int) -> tuple[int, int]:
    """Delete rows older than max_age_days from both tables. Returns (kept, removed)."""
    cutoff = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=max_age_days)
    ).strftime(_TS_FMT)
    before_events, before_catchup = _row_counts(conn)
    conn.execute("DELETE FROM telemetry_events WHERE ts < ?", (cutoff,))
    conn.execute("DELETE FROM catchup_events WHERE ts < ?", (cutoff,))
    conn.commit()
    after_events, after_catchup = _row_counts(conn)
    removed = (before_events - after_events) + (before_catchup - after_catchup)
    kept = after_events + after_catchup
    return kept, removed


def enforce_max_size(
    conn: sqlite3.Connection, db_path: Path, max_size_mb: float,
    *, max_iterations: int = _MAX_SIZE_ITERATIONS,
) -> int:
    """Delete the oldest rows (by ts, id tie-break) — a quarter of the
    currently-remaining rows per iteration, split proportionally between
    telemetry_events and catchup_events — until the on-disk file size is
    at/under max_size_mb or there is nothing left to delete. Returns the
    total number of rows deleted."""
    max_bytes = max_size_mb * 1024 * 1024
    total_removed = 0
    for _ in range(max_iterations):
        telemetry_store.checkpoint_and_vacuum(conn)
        if not db_path.exists() or db_path.stat().st_size <= max_bytes:
            break
        events, catchup = _row_counts(conn)
        if events == 0 and catchup == 0:
            break
        events_batch = max(1, events // 4) if events else 0
        catchup_batch = max(1, catchup // 4) if catchup else 0
        if events_batch:
            conn.execute(
                "DELETE FROM telemetry_events WHERE id IN "
                "(SELECT id FROM telemetry_events ORDER BY ts, id LIMIT ?)",
                (events_batch,),
            )
            total_removed += events_batch
        if catchup_batch:
            conn.execute(
                "DELETE FROM catchup_events WHERE id IN "
                "(SELECT id FROM catchup_events ORDER BY ts, id LIMIT ?)",
                (catchup_batch,),
            )
            total_removed += catchup_batch
        conn.commit()
    telemetry_store.checkpoint_and_vacuum(conn)
    return total_removed


def trim(
    *,
    max_size_mb: float | None = None,
    max_age_days: int | None = None,
    hooks_dir: Path | None = None,
    dry_run: bool = False,
) -> dict[str, object]:
    """Run the trim operation. Returns a summary dict."""
    db_path = telemetry_store.db_path(hooks_dir)
    conn = telemetry_store.connect(hooks_dir)
    try:
        summary: dict[str, object] = {
            "path": str(db_path),
            "size_bytes": db_path.stat().st_size if db_path.exists() else 0,
            "rows_removed_by_age": 0,
            "rows_kept_after_age": None,
            "rows_removed_by_size": 0,
        }

        if dry_run:
            if max_age_days is not None:
                cutoff = (
                    datetime.datetime.now(datetime.timezone.utc)
                    - datetime.timedelta(days=max_age_days)
                ).strftime(_TS_FMT)
                would_remove = (
                    conn.execute(
                        "SELECT COUNT(*) FROM telemetry_events WHERE ts < ?", (cutoff,)
                    ).fetchone()[0]
                    + conn.execute(
                        "SELECT COUNT(*) FROM catchup_events WHERE ts < ?", (cutoff,)
                    ).fetchone()[0]
                )
                summary["would_remove_by_age"] = would_remove
            if max_size_mb is not None:
                summary["would_trim_by_size"] = (
                    db_path.exists() and db_path.stat().st_size > max_size_mb * 1024 * 1024
                )
            return summary

        if max_age_days is not None:
            kept, removed = trim_by_age(conn, max_age_days)
            summary["rows_kept_after_age"] = kept
            summary["rows_removed_by_age"] = removed

        if max_size_mb is not None:
            summary["rows_removed_by_size"] = enforce_max_size(conn, db_path, max_size_mb)

        summary["size_bytes"] = db_path.stat().st_size if db_path.exists() else 0
        return summary
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``ccst telemetry trim``."""
    p = argparse.ArgumentParser(
        prog="ccst telemetry trim",
        description="Trim telemetry.db by size and/or age.",
    )
    p.add_argument(
        "--max-size",
        type=float,
        metavar="MB",
        help="Delete the oldest rows until the DB is under this size in MB (lossy — see module docstring)",
    )
    p.add_argument(
        "--max-age-days",
        type=int,
        metavar="N",
        help="Delete rows older than N days",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be done without making changes (default: apply changes)",
    )
    p.add_argument(
        "--hooks-dir",
        default=None,
        metavar="DIR",
        help="telemetry.db directory (default: CCCS_HOOKS_DIR or ~/.local/share/claude/)",
    )
    args = p.parse_args(argv)

    hooks_dir = Path(args.hooks_dir) if args.hooks_dir else None
    db_path = telemetry_store.db_path(hooks_dir)

    print(f"Telemetry DB: {db_path}")
    if db_path.exists():
        size_bytes = db_path.stat().st_size
        print(f"Current size: {size_bytes:,} bytes ({size_bytes / 1024:.1f} KB)")

    if args.max_size is None and args.max_age_days is None:
        print("No trim flags specified. Use --max-size and/or --max-age-days.")
        return 0

    result = trim(
        max_size_mb=args.max_size,
        max_age_days=args.max_age_days,
        hooks_dir=hooks_dir,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        print("Dry run — no changes made.")
        if "would_remove_by_age" in result:
            print(f"  Would remove: {result['would_remove_by_age']} row(s) older than {args.max_age_days} day(s)")
        if "would_trim_by_size" in result:
            flag = result["would_trim_by_size"]
            print(f"  Would trim by size: {'yes' if flag else 'no (below threshold)'}")
    else:
        if args.max_age_days is not None:
            print(
                f"  Age trim: kept {result['rows_kept_after_age']} row(s), "
                f"removed {result['rows_removed_by_age']} row(s)"
            )
        if args.max_size is not None:
            print(f"  Size trim: removed {result['rows_removed_by_size']} row(s)")
        raw_size = result.get("size_bytes", 0)
        new_size = raw_size if isinstance(raw_size, int) else 0
        print(f"  New size: {new_size:,} bytes ({new_size / 1024:.1f} KB)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
