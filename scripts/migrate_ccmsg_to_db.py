#!/usr/bin/env python3
"""One-shot migration of the flat-file message store into ccmsg.db.

Reads the OLD partition tree (default ~/.claude/cc-messages), parses each
message via message.parse(), inserts one row per message into ccmsg.db (under
CCST_MESSAGES_ROOT / data_home()), and migrates .cursors/*.json into the cursors
table. Non-destructive by construction: write -> verify (row count matches file
count, spot-check content) -> tar-backup the old tree -> only then delete it.
Re-runnable: INSERT OR IGNORE keeps it idempotent on message id.

Live .locks/*.lock files are transient (released on process exit); one present
here indicates an orphaned crash-gap lock (R4). It is reported and left for
manual cleanup, never migrated as data.

Usage:
    python3 scripts/migrate_ccmsg_to_db.py [--old-root PATH] [--backup-dir PATH] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

from cc_session_tools.lib import paths
from cc_session_tools.lib.messaging import message, repository, store

_DEFAULT_OLD_ROOT = Path.home() / ".claude" / "cc-messages"


def _default_backup_dir() -> Path:
    return paths.data_home() / "migration-backups"


def _iter_old_message_files(old_root: Path) -> list[Path]:
    return sorted(
        p for p in old_root.rglob("*.md")
        if p.is_file() and ".locks" not in p.parts
    )


def _insert_ignore(msg: message.Message) -> None:
    conn = repository.connect()
    try:
        with repository._immediate(conn):
            placeholders = ", ".join("?" for _ in repository._COLUMNS)
            conn.execute(
                f"INSERT OR IGNORE INTO messages "
                f"({', '.join(repository._COLUMNS)}) VALUES ({placeholders})",
                repository._insert_params(msg),
            )
    finally:
        conn.close()


def migrate(*, old_root: Path, backup_dir: Path, dry_run: bool) -> int:
    if not old_root.is_dir():
        print(f"Old store not found: {old_root} - nothing to migrate.", file=sys.stderr)
        return 1

    files = _iter_old_message_files(old_root)
    parsed: list[message.Message] = []
    skipped = 0
    for path in files:
        try:
            parsed.append(message.parse(path.read_text(encoding="utf-8")))
        except (ValueError, OSError) as exc:
            skipped += 1
            print(f"  skip {path.name}: {exc}", file=sys.stderr)

    locks = list((old_root / ".locks").glob("*.lock")) if (old_root / ".locks").is_dir() else []
    for lock in locks:
        print(f"  WARNING orphaned claim lock (R4), left in place: {lock}", file=sys.stderr)

    cursor_files = sorted((old_root / ".cursors").glob("*.json")) if (old_root / ".cursors").is_dir() else []

    print(f"Found {len(parsed)} message(s) ({skipped} skipped), {len(cursor_files)} cursor file(s).")
    if dry_run:
        print(f"[dry-run] would write {len(parsed)} row(s) to {store.db_path()}")
        return 0

    # 1. Write DB (no old files touched).
    for msg in parsed:
        _insert_ignore(msg)
    for cf in cursor_files:
        data = json.loads(cf.read_text(encoding="utf-8"))
        hw = data.get("high_water") if isinstance(data, dict) else None
        if isinstance(hw, dict):
            repository.save_cursor(cf.stem, {str(k): str(v) for k, v in hw.items()})

    # 2. Verify before any deletion.
    db_count = len(repository.list_rows())
    if db_count != len(parsed):
        print(f"ABORT: DB row count {db_count} != parsed message count {len(parsed)}; "
              "old files left intact.", file=sys.stderr)
        return 2
    for sample in parsed[:5]:
        got = repository.get_by_id(sample.id)
        if got is None or got.subject != sample.subject or got.body != sample.body:
            print(f"ABORT: spot-check mismatch on {sample.id}; old files left intact.",
                  file=sys.stderr)
            return 2

    # 3. Tar-backup the old tree (outside it) only after verification passes.
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_dir / f"ccmsg-{stamp}.tar.gz"
    with tarfile.open(backup, "w:gz") as tar:
        tar.add(old_root, arcname=old_root.name)
    print(f"Backed up old store to {backup}")

    # 4. Delete old flat files (message tree + cursors), leaving orphaned locks.
    for path in files:
        path.unlink(missing_ok=True)
    for cf in cursor_files:
        cf.unlink(missing_ok=True)
    # Remove now-empty partition/inbox/archive dirs, but keep .locks if it holds orphans.
    for d in sorted((p for p in old_root.rglob("*") if p.is_dir()), reverse=True):
        if ".locks" in d.parts:
            continue
        try:
            d.rmdir()
        except OSError:
            pass

    print(f"Migration complete: {len(parsed)} message(s), {len(cursor_files)} cursor(s). "
          f"DB: {store.db_path()}")
    if locks:
        print(f"{len(locks)} orphaned lock(s) left in {old_root / '.locks'} for manual review.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="migrate_ccmsg_to_db.py",
        description="Migrate the flat-file message store into ccmsg.db.",
    )
    p.add_argument("--old-root", default=None, metavar="PATH",
                   help=f"Old store root (default: {_DEFAULT_OLD_ROOT})")
    p.add_argument("--backup-dir", default=None, metavar="PATH",
                   help="Where the pre-deletion tar.gz backup is written "
                        "(default: <data_home>/migration-backups)")
    p.add_argument("--dry-run", action="store_true",
                   help="Report what would migrate without writing or deleting")
    args = p.parse_args(argv)
    return migrate(
        old_root=Path(args.old_root) if args.old_root else _DEFAULT_OLD_ROOT,
        backup_dir=Path(args.backup_dir) if args.backup_dir else _default_backup_dir(),
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
