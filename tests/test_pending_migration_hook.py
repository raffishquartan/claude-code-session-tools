# tests/test_pending_migration_hook.py
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from hooks import pending_migration
from cc_session_tools.lib import doctor_mutes
from cc_session_tools.lib.doctor import LegacyMigrationPaths


def _legacy_paths(tmp_path: Path) -> LegacyMigrationPaths:
    return LegacyMigrationPaths(
        ccmsg_old_root=tmp_path / "cc-messages",
        ccsched_old_dir=tmp_path / "cc-scheduler",
        tags_dir=tmp_path / "session-tags",
        mutes_file=tmp_path / "cc-doctor-mutes.json",
        telemetry_old_dir=tmp_path / "logs",
        data_home=tmp_path / "data-home",
    )


def _wire_tmp_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(pending_migration, "_default_legacy_paths", lambda: _legacy_paths(tmp_path))
    monkeypatch.setattr(doctor_mutes, "default_mutes_path", lambda: tmp_path / "sessions.db")


def _stdin(monkeypatch: pytest.MonkeyPatch, payload: dict[str, object]) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))


def _capture_emit(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    out: list[str] = []
    monkeypatch.setattr(pending_migration, "_emit", lambda ctx, event: out.append(ctx))
    return out


def test_emits_empty_when_nothing_legacy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _wire_tmp_paths(monkeypatch, tmp_path)
    _stdin(monkeypatch, {"hook_event_name": "SessionStart", "session_id": "u"})
    emitted = _capture_emit(monkeypatch)
    assert pending_migration.main() == 0
    assert emitted == [""]


def test_emits_fail_digest_when_legacy_data_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_tmp_paths(monkeypatch, tmp_path)
    old_root = tmp_path / "cc-messages" / "projects" / "alpha" / "inbox"
    old_root.mkdir(parents=True)
    (old_root / "msg.md").write_text("x")

    _stdin(monkeypatch, {"hook_event_name": "SessionStart", "session_id": "u"})
    emitted = _capture_emit(monkeypatch)
    assert pending_migration.main() == 0
    assert len(emitted) == 1
    assert "migration-to-1.0.0:ccmsg" in emitted[0]
    assert "ccst migrate all" in emitted[0]


def test_respects_mute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _wire_tmp_paths(monkeypatch, tmp_path)
    old_root = tmp_path / "cc-messages" / "projects" / "alpha" / "inbox"
    old_root.mkdir(parents=True)
    (old_root / "msg.md").write_text("x")
    doctor_mutes.add_mute(tmp_path / "sessions.db", "migration-to-1.0.0:ccmsg", today="2026-07-24")

    _stdin(monkeypatch, {"hook_event_name": "SessionStart", "session_id": "u"})
    emitted = _capture_emit(monkeypatch)
    assert pending_migration.main() == 0
    assert emitted == [""]


def test_warn_only_findings_do_not_surface(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Migration already ran (marker recorded) but old files linger: WARN, not
    # FAIL — no data at risk, so this hook should stay quiet.
    from cc_session_tools.lib import db as _db
    from cc_session_tools.lib.messaging.repository import LEGACY_FLAT_FILE_MIGRATION

    _wire_tmp_paths(monkeypatch, tmp_path)
    old_root = tmp_path / "cc-messages" / "projects" / "alpha" / "inbox"
    old_root.mkdir(parents=True)
    (old_root / "msg.md").write_text("x")
    data_home = tmp_path / "data-home"
    data_home.mkdir(parents=True)
    conn = _db.connect(
        data_home / "ccmsg.db",
        ddl="CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY);\n" + _db.MIGRATIONS_DDL,
    )
    conn.execute("INSERT INTO messages (id) VALUES (1)")
    _db.record_migration(conn, LEGACY_FLAT_FILE_MIGRATION, applied_at="2026-08-05T00:00:00Z")
    conn.commit()
    conn.close()

    _stdin(monkeypatch, {"hook_event_name": "SessionStart", "session_id": "u"})
    emitted = _capture_emit(monkeypatch)
    assert pending_migration.main() == 0
    assert emitted == [""]


def test_degrades_on_bad_stdin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _wire_tmp_paths(monkeypatch, tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    emitted = _capture_emit(monkeypatch)
    assert pending_migration.main() == 0
    assert emitted == [""]


def test_degrades_when_check_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _wire_tmp_paths(monkeypatch, tmp_path)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(pending_migration, "check_pending_data_store_migration", _boom)
    _stdin(monkeypatch, {"hook_event_name": "SessionStart", "session_id": "u"})
    emitted = _capture_emit(monkeypatch)
    assert pending_migration.main() == 0
    assert emitted == [""]


def _wire_pdata_db_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db_dir = tmp_path / "project-db"
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(db_dir))
    return db_dir


def test_surfaces_a_format3_pdata_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pdata.legacy_fixture import make_format3_db

    from cc_session_tools.lib.pdata import formats

    _wire_tmp_paths(monkeypatch, tmp_path)
    make_format3_db(_wire_pdata_db_dir(monkeypatch, tmp_path) / "notes.db")

    _stdin(monkeypatch, {"hook_event_name": "SessionStart", "session_id": "u"})
    emitted = _capture_emit(monkeypatch)
    assert pending_migration.main() == 0
    [context] = emitted
    assert "migration-to-4.0.0:pdata-uuid:notes" in context
    assert "ccst pdata migrate-uuid --write --project notes" in context
    assert formats.RUNBOOK_PATH in context
    assert "ccst migrate all" not in context


def test_format4_pdata_project_is_quiet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cc_session_tools.lib.pdata import repository

    _wire_tmp_paths(monkeypatch, tmp_path)
    _wire_pdata_db_dir(monkeypatch, tmp_path)
    repository.connect("notes").close()

    _stdin(monkeypatch, {"hook_event_name": "SessionStart", "session_id": "u"})
    emitted = _capture_emit(monkeypatch)
    assert pending_migration.main() == 0
    assert emitted == [""]


def test_pdata_marker_mismatch_surfaces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlite3

    from cc_session_tools.lib.pdata import repository

    _wire_tmp_paths(monkeypatch, tmp_path)
    db_dir = _wire_pdata_db_dir(monkeypatch, tmp_path)
    repository.connect("notes").close()
    conn = sqlite3.connect(str(db_dir / "notes.db"))
    conn.execute("DELETE FROM migrations WHERE name = 'pdata-uuid-ids'")
    conn.commit()
    conn.close()

    _stdin(monkeypatch, {"hook_event_name": "SessionStart", "session_id": "u"})
    emitted = _capture_emit(monkeypatch)
    assert pending_migration.main() == 0
    [context] = emitted
    assert "migration-to-4.0.0:pdata-uuid:notes" in context
    assert "marker" in context


def test_muted_pdata_project_is_quiet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pdata.legacy_fixture import make_format3_db

    _wire_tmp_paths(monkeypatch, tmp_path)
    make_format3_db(_wire_pdata_db_dir(monkeypatch, tmp_path) / "notes.db")
    doctor_mutes.add_mute(
        tmp_path / "sessions.db", "migration-to-4.0.0:pdata-uuid:notes", today="2026-10-22"
    )

    _stdin(monkeypatch, {"hook_event_name": "SessionStart", "session_id": "u"})
    emitted = _capture_emit(monkeypatch)
    assert pending_migration.main() == 0
    assert emitted == [""]


def test_legacy_and_pdata_findings_each_get_their_remedy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pdata.legacy_fixture import make_format3_db

    _wire_tmp_paths(monkeypatch, tmp_path)
    old_root = tmp_path / "cc-messages" / "projects" / "alpha" / "inbox"
    old_root.mkdir(parents=True)
    (old_root / "msg.md").write_text("x")
    make_format3_db(_wire_pdata_db_dir(monkeypatch, tmp_path) / "notes.db")

    _stdin(monkeypatch, {"hook_event_name": "SessionStart", "session_id": "u"})
    emitted = _capture_emit(monkeypatch)
    assert pending_migration.main() == 0
    [context] = emitted
    assert "migration-to-1.0.0:ccmsg" in context
    assert "ccst migrate all" in context
    assert "migration-to-4.0.0:pdata-uuid:notes" in context


def test_degrades_when_pdata_check_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _wire_tmp_paths(monkeypatch, tmp_path)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(pending_migration, "check_pdata_uuid_migration", _boom)
    _stdin(monkeypatch, {"hook_event_name": "SessionStart", "session_id": "u"})
    emitted = _capture_emit(monkeypatch)
    assert pending_migration.main() == 0
    assert emitted == [""]
