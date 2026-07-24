from __future__ import annotations

import json
import sqlite3
import sys
import tarfile
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import migrate_fires_jsonl_to_telemetry_db as mig  # noqa: E402


def _generic_line(ts: str, hook: str = "bash-security-review") -> str:
    return json.dumps({
        "v": 1, "ts": ts, "hook": hook, "event": "PreToolUse", "tool": "Bash",
        "session_id": "s1", "cwd": "repos/x", "decision": "allow", "cache": "none",
        "verdict": "safe", "input_hash": "sha256:aa",
    })


def _catchup_line(ts: str, job_id: str) -> str:
    verdict = json.dumps({
        "job_id": job_id, "event": "run", "owed": 1, "ran": 1, "exit_code": 0,
        "duration_ms": 5, "error": None, "consecutive_failures": 0,
    })
    return json.dumps({
        "v": 1, "ts": ts, "hook": "catchup", "event": "", "tool": "", "session_id": "",
        "cwd": "", "decision": "annotate", "cache": "none", "verdict": verdict, "input_hash": "",
    })


def test_migrate_splits_generic_and_catchup_rows(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    dest = tmp_path / "dest"
    (source / "fires.jsonl").write_text(
        _generic_line("2026-07-01T00:00:00Z") + "\n" + _catchup_line("2026-07-01T00:00:01Z", "tesco") + "\n"
    )
    rc = mig.migrate(source_dir=source, dest_dir=dest, dry_run=False, force=False)
    assert rc == 0
    conn = sqlite3.connect(str(dest / "telemetry.db"))
    events = conn.execute("SELECT COUNT(*) FROM telemetry_events").fetchone()[0]
    catchup = conn.execute("SELECT COUNT(*) FROM catchup_events").fetchone()[0]
    conn.close()
    assert events == 1
    assert catchup == 1


def test_migrate_reads_rotated_slots_oldest_first(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    dest = tmp_path / "dest"
    (source / "fires.jsonl.2").write_text(_generic_line("2026-06-01T00:00:00Z") + "\n")
    (source / "fires.jsonl.1").write_text(_generic_line("2026-06-15T00:00:00Z") + "\n")
    (source / "fires.jsonl").write_text(_generic_line("2026-07-01T00:00:00Z") + "\n")
    mig.migrate(source_dir=source, dest_dir=dest, dry_run=False, force=False)
    conn = sqlite3.connect(str(dest / "telemetry.db"))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT ts FROM telemetry_events ORDER BY id").fetchall()
    conn.close()
    assert [r["ts"] for r in rows] == [
        "2026-06-01T00:00:00Z", "2026-06-15T00:00:00Z", "2026-07-01T00:00:00Z",
    ]


def test_migrate_skips_malformed_lines_without_failing(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    dest = tmp_path / "dest"
    (source / "fires.jsonl").write_text("not json\n" + _generic_line("2026-07-01T00:00:00Z") + "\n")
    rc = mig.migrate(source_dir=source, dest_dir=dest, dry_run=False, force=False)
    assert rc == 0
    conn = sqlite3.connect(str(dest / "telemetry.db"))
    events = conn.execute("SELECT COUNT(*) FROM telemetry_events").fetchone()[0]
    conn.close()
    assert events == 1


def test_migrate_dry_run_writes_nothing(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    dest = tmp_path / "dest"
    (source / "fires.jsonl").write_text(_generic_line("2026-07-01T00:00:00Z") + "\n")
    rc = mig.migrate(source_dir=source, dest_dir=dest, dry_run=True, force=False)
    assert rc == 0
    assert not (dest / "telemetry.db").exists()
    assert (source / "fires.jsonl").exists()


def test_migrate_backs_up_then_deletes_source_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    dest = tmp_path / "dest"
    (source / "fires.jsonl").write_text(_generic_line("2026-07-01T00:00:00Z") + "\n")
    (source / "fires.jsonl.1").write_text(_generic_line("2026-06-01T00:00:00Z") + "\n")
    mig.migrate(source_dir=source, dest_dir=dest, dry_run=False, force=False)
    assert not (source / "fires.jsonl").exists()
    assert not (source / "fires.jsonl.1").exists()
    backups = list((dest / "migration-backups").glob("fires-jsonl-*.tar.gz"))
    assert len(backups) == 1
    with tarfile.open(backups[0]) as tar:
        names = set(tar.getnames())
    assert names == {"fires.jsonl", "fires.jsonl.1"}


def test_migrate_refuses_to_double_insert_without_force(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    dest = tmp_path / "dest"
    (source / "fires.jsonl").write_text(_generic_line("2026-07-01T00:00:00Z") + "\n")
    mig.migrate(source_dir=source, dest_dir=dest, dry_run=False, force=False)
    # Re-run against the same (now-empty, already-migrated) source: nothing to do.
    source.mkdir(exist_ok=True)
    (source / "fires.jsonl").write_text(_generic_line("2026-07-02T00:00:00Z") + "\n")
    rc = mig.migrate(source_dir=source, dest_dir=dest, dry_run=False, force=False)
    assert rc == 1
    conn = sqlite3.connect(str(dest / "telemetry.db"))
    events = conn.execute("SELECT COUNT(*) FROM telemetry_events").fetchone()[0]
    conn.close()
    assert events == 1  # the second run's row was NOT inserted


def test_migrate_no_source_files_is_a_no_op(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    dest = tmp_path / "dest"
    rc = mig.migrate(source_dir=source, dest_dir=dest, dry_run=False, force=False)
    assert rc == 0
    assert not (dest / "telemetry.db").exists()
