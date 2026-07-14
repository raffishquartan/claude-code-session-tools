"""One-shot migration of the ccsched flat-file stores into ccsched.db.

Exposed via `ccst migrate ccsched`. Non-destructive (overview §4): writes the
DB, verifies row counts against the source files, tar-backs-up the old tree,
and only then removes the old flat files. Never delete-as-you-go.

Reads the OLD scheduler directory (default ~/.claude/cc-scheduler); the DB is
written at the NEW location (store.db_path(), under paths.data_home()). The
.run.<job-id>.lock files are transient and are NOT migrated (they carry no
durable state) — they are simply not read into the DB; the whole old tree is
tar-backed-up before removal, so a lock present at migration time is captured in
the backup and then removed with the rest of the old directory.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tarfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from cc_session_tools.lib.scheduler import store
from cc_session_tools.lib.scheduler.jobspec import JobSpec, validate_job_fields
from cc_session_tools.lib.scheduler.state import InFlight, JobState


class MigrationError(RuntimeError):
    pass


def _default_old_dir() -> Path:
    return Path.home() / ".claude" / "cc-scheduler"


def _read_old_jobs(old_dir: Path) -> list[JobSpec]:
    path = old_dir / "jobs.toml"
    if not path.is_file():
        return []
    data = tomllib.loads(path.read_text())
    specs: list[JobSpec] = []
    for t in data.get("job", []):
        specs.append(validate_job_fields(
            job_id=str(t["id"]), cadence=str(t["cadence"]),
            coalesce=str(t.get("coalesce", "one")),
            command=[str(x) for x in t["command"]],
            surface=bool(t.get("surface", True)), enabled=bool(t.get("enabled", True)),
            catchup_window=str(t.get("catchup_window", "7d")),
            timeout=str(t.get("timeout", "120s")),
        ))
    return specs


def _read_old_state(old_dir: Path) -> dict[str, JobState]:
    path = old_dir / "state.json"
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text())
    out: dict[str, JobState] = {}
    for job_id, f in raw.items():
        infl = f.get("in_flight")
        out[job_id] = JobState(
            registered_at=str(f["registered_at"]),
            last_success=f.get("last_success"), last_attempt=f.get("last_attempt"),
            consecutive_failures=int(f.get("consecutive_failures", 0)),
            suspended=bool(f.get("suspended", False)),
            in_flight=None if not isinstance(infl, dict) else InFlight(
                pid=int(infl["pid"]), started_at=str(infl["started_at"]),
                instants=int(infl["instants"])),
        )
    return out


def _read_old_cursors(old_dir: Path) -> dict[str, int]:
    curs = old_dir / ".cursors"
    if not curs.is_dir():
        return {}
    return {p.stem: int(json.loads(p.read_text())["offset"])
            for p in curs.glob("*.json") if p.is_file()}


def _read_old_throttles(old_dir: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not old_dir.is_dir():
        return out
    for p in old_dir.glob(".reconcile.*.ts"):
        if p.is_file():
            uuid = p.name[len(".reconcile."):-len(".ts")]
            out[uuid] = p.read_text().strip()
    return out


def _write_db(specs, states, cursors, throttles, db_path: Path) -> None:
    conn = store.connect()  # creates schema
    try:
        for s in specs:
            conn.execute(
                "INSERT OR IGNORE INTO jobs (job_id, cadence, coalesce_kind, command, "
                "surface, enabled, catchup_window, timeout) VALUES (?,?,?,?,?,?,?,?)",
                (s.job_id, s.cadence, s.coalesce.value, json.dumps(list(s.command)),
                 int(s.surface), int(s.enabled), s.catchup_window, s.timeout),
            )
        for job_id, js in states.items():
            conn.execute(
                "INSERT OR IGNORE INTO job_state (job_id, registered_at, last_success, "
                "last_attempt, consecutive_failures, suspended, in_flight_pid, "
                "in_flight_started_at, in_flight_instants) VALUES (?,?,?,?,?,?,?,?,?)",
                (job_id, js.registered_at, js.last_success, js.last_attempt,
                 js.consecutive_failures, int(js.suspended),
                 None if js.in_flight is None else js.in_flight.pid,
                 None if js.in_flight is None else js.in_flight.started_at,
                 None if js.in_flight is None else js.in_flight.instants),
            )
        for uuid, offset in cursors.items():
            conn.execute("INSERT OR IGNORE INTO cursors (session_uuid, offset) VALUES (?,?)",
                         (uuid, offset))
        for uuid, ts in throttles.items():
            conn.execute("INSERT OR IGNORE INTO reconcile_throttle "
                         "(session_uuid, last_reconciled_at) VALUES (?,?)", (uuid, ts))
        conn.commit()
    finally:
        conn.close()


def _verify(specs, states, cursors, throttles) -> None:
    conn = store.connect()
    try:
        def count(table: str) -> int:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        checks = {
            "jobs": (count("jobs"), len(specs)),
            "job_state": (count("job_state"), len(states)),
            "cursors": (count("cursors"), len(cursors)),
            "reconcile_throttle": (count("reconcile_throttle"), len(throttles)),
        }
    finally:
        conn.close()
    for table, (got, want) in checks.items():
        if got < want:
            raise MigrationError(
                f"verify failed for {table}: DB has {got} rows, source had {want}")


def _backup_and_remove(old_dir: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = backup_dir / f"ccsched-{stamp}.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(old_dir, arcname=old_dir.name)
    shutil.rmtree(old_dir)
    return archive


def run_migration(
    *, old_dir: Path, db_path: Path, dry_run: bool, backup_dir: Path
) -> int:
    if not old_dir.is_dir():
        print(f"No old scheduler dir at {old_dir} — nothing to migrate.")
        return 0

    specs = _read_old_jobs(old_dir)
    states = _read_old_state(old_dir)
    cursors = _read_old_cursors(old_dir)
    throttles = _read_old_throttles(old_dir)

    print(f"Source : {old_dir}")
    print(f"Target : {db_path}")
    print(f"  jobs={len(specs)} state={len(states)} cursors={len(cursors)} "
          f"throttles={len(throttles)}")
    print("  (.run.<id>.lock files are transient and are not migrated)")

    if dry_run:
        print("(dry-run — nothing written)")
        return 0

    _write_db(specs, states, cursors, throttles, db_path)
    try:
        _verify(specs, states, cursors, throttles)
    except MigrationError as exc:
        print(f"ERROR: {exc}\nOld files left in place; DB written but not trusted.",
              file=sys.stderr)
        return 1

    archive = _backup_and_remove(old_dir, backup_dir)
    print(f"Migrated and verified. Old tree backed up to {archive} and removed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Migrate ccsched flat-file stores into ccsched.db "
                    "(non-destructive: verify + tar-backup before removing old files).")
    ap.add_argument("--old-dir", default=None, metavar="PATH",
                    help="Old scheduler dir (default: ~/.claude/cc-scheduler)")
    ap.add_argument("--backup-dir", default=None, metavar="PATH",
                    help="Backup dir (default: <data_home>/migration-backups)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would migrate without writing.")
    args = ap.parse_args(argv)

    old_dir = Path(args.old_dir) if args.old_dir else _default_old_dir()
    backup_dir = (Path(args.backup_dir) if args.backup_dir
                  else store.scheduler_dir() / "migration-backups")
    return run_migration(old_dir=old_dir, db_path=store.db_path(),
                         dry_run=args.dry_run, backup_dir=backup_dir)


if __name__ == "__main__":
    sys.exit(main())
