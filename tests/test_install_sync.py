"""Tests for cc_session_tools.lib.install_sync — the install-everything sync marker."""
from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib import install_sync, sessions_db


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


def test_get_synced_version_on_corrupt_db_returns_none(db_path: Path) -> None:
    """A sessions.db that exists but isn't a valid SQLite file at all (found
    during code-quality review of the main() gate: sqlite3.connect() opens
    lazily and succeeds even for a corrupt file, so the failure surfaces on
    the SELECT as sqlite3.DatabaseError, not sqlite3.OperationalError).
    get_synced_version() must survive this and return None like every other
    "not synced" state, since callers - main()'s interactive gate among them
    - must never crash on a corrupt store; a crash here would break even the
    exempt commands (install-everything, doctor) that are supposed to be the
    escape hatch."""
    db_path.write_bytes(b"this is not a sqlite database file")

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


def test_does_not_block_repair() -> None:
    """ccst repair ('Repair known sessions.db/store corruption') must stay
    reachable even when the sync marker's own store (sessions.db) is what's
    broken - a corrupt sessions.db degrades get_synced_version() to "never
    synced", which would otherwise permanently lock repair behind the very
    tool needed to fix it."""
    assert not install_sync.should_block_for_unsynced_install(
        noun="repair", verb="sessions",
        installed_version="2.4.0", synced_version="2.3.0",
        is_interactive=True,
    )


def test_does_not_block_migrate() -> None:
    """This repo's own pending-migration doctor output tells users to run
    'ccst migrate all' from a plain terminal - blocking that instruction
    with this gate would be self-defeating."""
    assert not install_sync.should_block_for_unsynced_install(
        noun="migrate", verb="all",
        installed_version="2.4.0", synced_version="2.3.0",
        is_interactive=True,
    )


# ---------- failed-attempt keys ----------

def test_get_failed_attempt_returns_none_when_never_recorded(db_path: Path) -> None:
    assert install_sync.get_failed_attempt(path=db_path) is None


def test_record_then_get_failed_attempt_round_trips(db_path: Path) -> None:
    install_sync.record_failed_attempt("2.5.0", rc=1, path=db_path)
    attempt = install_sync.get_failed_attempt(path=db_path)
    assert attempt is not None
    assert attempt.version == "2.5.0"
    assert attempt.rc == 1
    assert attempt.at.tzinfo is not None  # aware, so decide_auto_sync can subtract


def test_record_failed_attempt_overwrites_a_previous_one(db_path: Path) -> None:
    install_sync.record_failed_attempt("2.5.0", rc=1, path=db_path)
    install_sync.record_failed_attempt("2.6.0", rc=2, path=db_path)
    attempt = install_sync.get_failed_attempt(path=db_path)
    assert attempt is not None
    assert (attempt.version, attempt.rc) == ("2.6.0", 2)


def test_record_synced_clears_the_failure_keys(db_path: Path) -> None:
    """Spec section 5: cleared on ANY successful record_synced(), including
    the one an explicit `ccst install-everything --apply` writes - otherwise a
    user who fixes the broken step by hand still sees doctor FAIL forever."""
    install_sync.record_failed_attempt("2.5.0", rc=1, path=db_path)
    install_sync.record_synced("2.5.0", path=db_path)
    assert install_sync.get_failed_attempt(path=db_path) is None
    assert install_sync.get_synced_version(path=db_path) == "2.5.0"


def test_get_failed_attempt_on_nonexistent_db_returns_none(db_path: Path) -> None:
    assert install_sync.get_failed_attempt(path=db_path) is None
    assert not db_path.exists()


def test_get_failed_attempt_on_corrupt_db_returns_none(db_path: Path) -> None:
    """Same graceful degradation as get_synced_version: this is read on every
    non-exempt ccst invocation, so it must never be the thing that crashes a
    user's unrelated command."""
    db_path.write_bytes(b"this is not a sqlite database file")
    assert install_sync.get_failed_attempt(path=db_path) is None


def test_get_failed_attempt_with_an_unparseable_row_returns_none(db_path: Path) -> None:
    """The KV table is hand-editable with any sqlite3 shell, and a bad value
    would otherwise raise on every ccst invocation. Degrading to "no failed
    attempt" retries the apply, which is the safe direction."""
    install_sync.record_failed_attempt("2.5.0", rc=1, path=db_path)
    conn = sessions_db.connect(path=db_path)
    conn.execute(
        "UPDATE install_sync SET value = ? WHERE key = 'last_attempt_at'", ("not-a-timestamp",)
    )
    conn.commit()
    conn.close()

    assert install_sync.get_failed_attempt(path=db_path) is None


# ---------- test-suite safety ----------

def test_conftest_opts_every_test_out_of_auto_sync() -> None:
    """The autouse fixture in tests/conftest.py must set CCST_NO_AUTO_SYNC=1
    for every test in this suite. Without it, any test that reaches ccst's
    main() - in-process or via subprocess - runs a real five-step install
    against the developer's live ~/.claude. Tests that genuinely exercise
    auto-sync delete this var themselves and redirect HOME/CCST_DATA_HOME/
    CCST_SESSIONS_DIR to tmp_path first."""
    import os

    assert os.environ.get("CCST_NO_AUTO_SYNC") == "1"


# ---------- is_auto_sync_exempt ----------

def test_hooks_run_is_exempt() -> None:
    """The hot path: fires on every tool call in every open Claude Code
    session. Must never apply (rewriting settings.json from inside a hook
    Claude Code invoked from settings.json is a race no atomic write makes
    tidy) and must never pay for the check."""
    assert install_sync.is_auto_sync_exempt(noun="hooks", verb="run", opted_out=False)


@pytest.mark.parametrize("noun", ["install-everything", "doctor", "repair", "migrate"])
def test_exempt_nouns(noun: str) -> None:
    """install-everything would recurse; doctor must stay able to REPORT the
    out-of-sync state rather than silently erasing it; repair and migrate are
    the recovery tools for a broken store and must run under any store state."""
    assert install_sync.is_auto_sync_exempt(noun=noun, verb=None, opted_out=False)


@pytest.mark.parametrize(
    "noun,verb",
    [
        ("skills", "install"),
        ("skills", "uninstall"),
        ("hooks", "install"),
        ("hooks", "uninstall"),
        ("shell", "install"),
        ("claude-md", "install"),
        ("ccsched-jobs", "install"),
    ],
)
def test_install_and_uninstall_verbs_are_exempt(noun: str, verb: str) -> None:
    """The user is driving install state by hand. Running a full
    default-target auto-apply underneath them is both surprising and
    self-contradictory - `ccst skills install --target /tmp/x` must not also
    symlink into ~/.claude/skills."""
    assert install_sync.is_auto_sync_exempt(noun=noun, verb=verb, opted_out=False)


def test_env_opt_out_exempts_everything() -> None:
    assert install_sync.is_auto_sync_exempt(noun="pdata", verb="list", opted_out=True)


@pytest.mark.parametrize(
    "noun,verb",
    [("pdata", "list"), ("sessions", "list"), ("telemetry", "trim"), ("gc", "report")],
)
def test_carriers_are_not_exempt(noun: str, verb: str) -> None:
    assert not install_sync.is_auto_sync_exempt(noun=noun, verb=verb, opted_out=False)
