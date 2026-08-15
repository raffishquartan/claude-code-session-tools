"""Tests for cc_session_tools.lib.install_sync — the install-everything sync marker."""
from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib import install_sync


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "sessions.db"


def test_get_synced_version_returns_none_when_never_recorded(db_path: Path) -> None:
    assert install_sync.get_synced_version(path=db_path) is None


def test_record_then_get_round_trips(db_path: Path) -> None:
    install_sync.record_synced("2.4.0", path=db_path)
    assert install_sync.get_synced_version(path=db_path) == "2.4.0"


def test_record_synced_upserts_on_second_call(db_path: Path) -> None:
    install_sync.record_synced("2.4.0", path=db_path)
    install_sync.record_synced("2.5.0", path=db_path)
    assert install_sync.get_synced_version(path=db_path) == "2.5.0"


def test_get_synced_version_on_nonexistent_db_returns_none(db_path: Path) -> None:
    """db_path is never created by this test - get_synced_version must not
    raise or create the file just to read from it (matches the established
    doctor_mutes.load_mutes graceful-degradation pattern)."""
    assert install_sync.get_synced_version(path=db_path) is None
    assert not db_path.exists()


def test_get_synced_version_on_pre_upgrade_db_missing_table_returns_none(db_path: Path) -> None:
    """The realistic first-run-after-upgrade case: every existing installation
    already has a sessions.db (session_tags/sessions/doctor_mutes), just not
    the install_sync table this feature adds. connect(readonly=True) skips
    DDL by design, so the SELECT itself must handle 'no such table', not just
    a missing file - a naive implementation that only wraps connect() in
    try/except raises here instead of returning None."""
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE session_tags (uuid TEXT PRIMARY KEY, tag TEXT, updated_at TEXT)")
    conn.commit()
    conn.close()

    assert install_sync.get_synced_version(path=db_path) is None


# ---------- should_block_for_unsynced_install ----------

def test_blocks_when_versions_differ_and_interactive() -> None:
    assert install_sync.should_block_for_unsynced_install(
        noun="pdata", verb="list",
        installed_version="2.4.0", synced_version="2.3.0",
        is_interactive=True,
    )


def test_blocks_when_never_synced_and_interactive() -> None:
    assert install_sync.should_block_for_unsynced_install(
        noun="skills", verb="install",
        installed_version="2.4.0", synced_version=None,
        is_interactive=True,
    )


def test_does_not_block_when_versions_match() -> None:
    assert not install_sync.should_block_for_unsynced_install(
        noun="pdata", verb="list",
        installed_version="2.4.0", synced_version="2.4.0",
        is_interactive=True,
    )


def test_does_not_block_when_not_interactive() -> None:
    """The core safety property: a stale install must never block a
    non-interactive caller (a hook, a ccsched job, any future automation),
    regardless of noun/verb."""
    assert not install_sync.should_block_for_unsynced_install(
        noun="pdata", verb="verify",
        installed_version="2.4.0", synced_version="2.3.0",
        is_interactive=False,
    )


def test_does_not_block_hooks_run_even_if_somehow_interactive() -> None:
    """Belt-and-braces: hooks run is exempt by name too, not just by the
    is_interactive=False it will always actually see in practice."""
    assert not install_sync.should_block_for_unsynced_install(
        noun="hooks", verb="run",
        installed_version="2.4.0", synced_version="2.3.0",
        is_interactive=True,
    )


def test_does_not_block_install_everything_itself() -> None:
    assert not install_sync.should_block_for_unsynced_install(
        noun="install-everything", verb=None,
        installed_version="2.4.0", synced_version="2.3.0",
        is_interactive=True,
    )


def test_does_not_block_doctor() -> None:
    assert not install_sync.should_block_for_unsynced_install(
        noun="doctor", verb=None,
        installed_version="2.4.0", synced_version="2.3.0",
        is_interactive=True,
    )
