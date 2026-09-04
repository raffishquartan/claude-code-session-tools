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
    # Migration already ran (new store has rows) but old files linger: WARN,
    # not FAIL — no data at risk, so this hook should stay quiet.
    from cc_session_tools.lib import db as _db

    _wire_tmp_paths(monkeypatch, tmp_path)
    old_root = tmp_path / "cc-messages" / "projects" / "alpha" / "inbox"
    old_root.mkdir(parents=True)
    (old_root / "msg.md").write_text("x")
    data_home = tmp_path / "data-home"
    data_home.mkdir(parents=True)
    conn = _db.connect(
        data_home / "ccmsg.db",
        ddl="CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY);",
    )
    conn.execute("INSERT INTO messages (id) VALUES (1)")
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
