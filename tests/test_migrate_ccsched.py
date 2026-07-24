from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from cc_session_tools.cli import migrate_ccsched as mig
from cc_session_tools.lib.scheduler import cursor, registry, state, store, throttle


def _seed_old_dir(old: Path) -> None:
    old.mkdir(parents=True, exist_ok=True)
    (old / "jobs.toml").write_text(
        '# header\n[[job]]\nid = "tesco"\ncadence = "daily@09:00"\n'
        'coalesce = "one"\ncommand = ["true"]\nsurface = true\nenabled = true\n'
        'catchup_window = "7d"\ntimeout = "120s"\n'
    )
    (old / "state.json").write_text(json.dumps({
        "tesco": {
            "registered_at": "2026-06-17T09:00:00Z", "last_success": "2026-06-19T09:00:00Z",
            "last_attempt": "2026-06-19T09:00:00Z", "consecutive_failures": 0,
            "suspended": False, "in_flight": None,
        }
    }))
    curs = old / ".cursors"
    curs.mkdir()
    (curs / "sess-a.json").write_text(json.dumps({"offset": 4}))
    (curs / "sess-b.json").write_text(json.dumps({"offset": 9}))
    (old / ".reconcile.sess-a.ts").write_text("2026-06-20T10:00:00Z")
    (old / ".run.tesco.lock").write_text('{"pid": 1, "started": "x"}')  # must NOT migrate


def test_dry_run_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old = tmp_path / "old"
    _seed_old_dir(old)
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "new"))
    rc = mig.run_migration(old_dir=old, db_path=store.db_path(), dry_run=True,
                           backup_dir=tmp_path / "backups")
    assert rc == 0
    assert not store.db_path().exists()
    assert (old / "jobs.toml").is_file()  # untouched


def test_migrates_all_stores_and_verifies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old = tmp_path / "old"
    _seed_old_dir(old)
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "new"))
    rc = mig.run_migration(old_dir=old, db_path=store.db_path(), dry_run=False,
                           backup_dir=tmp_path / "backups")
    assert rc == 0
    # Registry.
    specs = registry.load_registry()
    assert [s.job_id for s in specs] == ["tesco"]
    assert specs[0].command == ("true",)
    # State.
    js = state.get_state("tesco")
    assert js.last_success == "2026-06-19T09:00:00Z"
    # Cursors.
    assert cursor.read_cursor("sess-a") == 4
    assert cursor.read_cursor("sess-b") == 9
    # Throttle.
    assert throttle.read_last_reconciled("sess-a") is not None
    # Backup exists and old tree removed.
    backups = list((tmp_path / "backups").glob("ccsched-*.tar.gz"))
    assert len(backups) == 1
    assert not (old / "jobs.toml").exists()
    assert not (old / ".cursors").exists()
    # The lock file is neither migrated nor deleted context-sensitively; it is
    # simply left out of the DB. (Old dir is tar'd wholesale, then removed.)


def test_backup_contains_old_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old = tmp_path / "old"
    _seed_old_dir(old)
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "new"))
    mig.run_migration(old_dir=old, db_path=store.db_path(), dry_run=False,
                      backup_dir=tmp_path / "backups")
    backup = next((tmp_path / "backups").glob("ccsched-*.tar.gz"))
    with tarfile.open(backup) as tf:
        names = tf.getnames()
    assert any(n.endswith("jobs.toml") for n in names)
    assert any(n.endswith("state.json") for n in names)


def test_missing_old_dir_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "new"))
    rc = mig.run_migration(old_dir=tmp_path / "does-not-exist", db_path=store.db_path(),
                           dry_run=False, backup_dir=tmp_path / "backups")
    assert rc == 0
    assert not store.db_path().exists()


def test_rerun_after_migration_is_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old = tmp_path / "old"
    _seed_old_dir(old)
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "new"))
    assert mig.run_migration(old_dir=old, db_path=store.db_path(), dry_run=False,
                             backup_dir=tmp_path / "backups") == 0
    # Old dir now gone -> second run is a no-op, not a crash.
    assert mig.run_migration(old_dir=old, db_path=store.db_path(), dry_run=False,
                             backup_dir=tmp_path / "backups") == 0
    assert [s.job_id for s in registry.load_registry()] == ["tesco"]  # not duplicated
