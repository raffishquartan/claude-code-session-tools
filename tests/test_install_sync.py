"""Tests for cc_session_tools.lib.install_sync — the install-everything sync marker."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

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


# ---------- decide_auto_sync ----------

_NOW = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc)


def test_synced_skips() -> None:
    assert install_sync.decide_auto_sync(
        installed_version="2.5.0", synced_version="2.5.0", last_failure=None, now=_NOW
    ) is install_sync.AutoSyncAction.SKIP_SYNCED


def test_stale_applies() -> None:
    assert install_sync.decide_auto_sync(
        installed_version="2.5.0", synced_version="2.4.0", last_failure=None, now=_NOW
    ) is install_sync.AutoSyncAction.APPLY


def test_never_synced_applies() -> None:
    assert install_sync.decide_auto_sync(
        installed_version="2.5.0", synced_version=None, last_failure=None, now=_NOW
    ) is install_sync.AutoSyncAction.APPLY


def test_recent_failure_for_this_version_backs_off() -> None:
    failure = install_sync.FailedAttempt(
        version="2.5.0", at=_NOW - timedelta(hours=1), rc=1
    )
    assert install_sync.decide_auto_sync(
        installed_version="2.5.0", synced_version="2.4.0", last_failure=failure, now=_NOW
    ) is install_sync.AutoSyncAction.SKIP_BACKOFF


def test_failure_outside_the_window_retries_once() -> None:
    failure = install_sync.FailedAttempt(
        version="2.5.0", at=_NOW - timedelta(hours=7), rc=1
    )
    assert install_sync.decide_auto_sync(
        installed_version="2.5.0", synced_version="2.4.0", last_failure=failure, now=_NOW
    ) is install_sync.AutoSyncAction.APPLY


def test_failure_for_a_different_version_does_not_back_off() -> None:
    """The key is version-scoped on purpose: a new release may well fix the
    failing step, so installing a different version resets the backoff."""
    failure = install_sync.FailedAttempt(
        version="2.4.0", at=_NOW - timedelta(minutes=5), rc=1
    )
    assert install_sync.decide_auto_sync(
        installed_version="2.5.0", synced_version="2.4.0", last_failure=failure, now=_NOW
    ) is install_sync.AutoSyncAction.APPLY


def test_synced_wins_over_a_stale_failure_record() -> None:
    """Belt-and-braces on the check order: if the marker says we're synced,
    a leftover failure row must not produce a spurious backoff warning."""
    failure = install_sync.FailedAttempt(
        version="2.5.0", at=_NOW - timedelta(minutes=5), rc=1
    )
    assert install_sync.decide_auto_sync(
        installed_version="2.5.0", synced_version="2.5.0", last_failure=failure, now=_NOW
    ) is install_sync.AutoSyncAction.SKIP_SYNCED


# ---------- ensure_synced (end-to-end, sandboxed) ----------

@pytest.fixture
def auto_sync_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Everything ensure_synced touches, redirected under tmp_path, and the
    suite-wide CCST_NO_AUTO_SYNC opt-out removed so the real code path runs.
    Returns the sandboxed HOME."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text("{}")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path / "data-home"))
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "scheduler"))
    monkeypatch.delenv("CCST_NO_AUTO_SYNC", raising=False)
    monkeypatch.setattr(install_sync, "_auto_sync_attempted", False)
    return home


def test_exempt_caller_never_opens_sessions_db(
    auto_sync_env: Path, mocker: MockerFixture
) -> None:
    """The property section 2's hot-path exemption depends on: `ccst hooks run
    <verb>` fires on every tool call in every open session and must not pay
    the 0.56 ms marker read, let alone the apply."""
    spy = mocker.patch.object(install_sync, "get_synced_version")

    install_sync.ensure_synced(noun="hooks", verb="run", installed_version="2.5.0")

    spy.assert_not_called()


def test_env_opt_out_never_opens_sessions_db(
    auto_sync_env: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    monkeypatch.setenv("CCST_NO_AUTO_SYNC", "1")
    spy = mocker.patch.object(install_sync, "get_synced_version")

    install_sync.ensure_synced(noun="pdata", verb="list", installed_version="2.5.0")

    spy.assert_not_called()


def test_applies_and_advances_the_marker(auto_sync_env: Path, capsys) -> None:
    from cc_session_tools import __version__ as version

    install_sync.ensure_synced(noun="pdata", verb="list", installed_version=version)

    assert install_sync.get_synced_version() == version
    captured = capsys.readouterr()
    assert captured.out == ""  # section 4: never stdout
    assert "out of sync" in captured.err
    assert f"synced to {version}" in captured.err


def test_already_synced_prints_nothing_and_does_not_apply(
    auto_sync_env: Path, mocker: MockerFixture, capsys
) -> None:
    install_sync.record_synced("2.5.0")
    capsys.readouterr()
    from cc_session_tools.cli import ccst

    runner = mocker.patch.object(ccst, "run_install_everything", return_value=0)

    install_sync.ensure_synced(noun="pdata", verb="list", installed_version="2.5.0")

    runner.assert_not_called()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_failed_apply_records_all_three_keys_and_does_not_advance_the_marker(
    auto_sync_env: Path, mocker: MockerFixture, capsys
) -> None:
    from cc_session_tools.cli import ccst

    mocker.patch.object(ccst, "run_install_everything", return_value=1)

    install_sync.ensure_synced(noun="pdata", verb="list", installed_version="2.5.0")

    assert install_sync.get_synced_version() is None
    attempt = install_sync.get_failed_attempt()
    assert attempt is not None
    assert (attempt.version, attempt.rc) == ("2.5.0", 1)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "install-everything --apply" in captured.err


def test_failed_apply_emits_the_buffered_step_output_on_stderr(
    auto_sync_env: Path, mocker: MockerFixture, capsys
) -> None:
    """Section 4: on failure the buffered step output is emitted verbatim, so
    a user can see WHICH step failed without re-running anything."""
    from cc_session_tools.cli import ccst

    def failing(**kwargs: object) -> int:
        stream = kwargs["stream"]
        stream.write("=== 1/5  Skills ===\nerror: something specific\n")  # type: ignore[union-attr]
        return 1

    mocker.patch.object(ccst, "run_install_everything", side_effect=failing)

    install_sync.ensure_synced(noun="pdata", verb="list", installed_version="2.5.0")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error: something specific" in captured.err


def test_backoff_prints_one_line_and_does_not_apply(
    auto_sync_env: Path, mocker: MockerFixture, capsys
) -> None:
    install_sync.record_failed_attempt("2.5.0", rc=1)
    capsys.readouterr()
    from cc_session_tools.cli import ccst

    runner = mocker.patch.object(ccst, "run_install_everything", return_value=0)

    install_sync.ensure_synced(noun="pdata", verb="list", installed_version="2.5.0")

    runner.assert_not_called()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert len([line for line in captured.err.splitlines() if line.strip()]) == 1
    assert "last auto-sync failed" in captured.err


def test_successful_apply_clears_a_stale_failure_record(
    auto_sync_env: Path, capsys
) -> None:
    from cc_session_tools import __version__ as version

    install_sync.record_failed_attempt("0.0.0-stale", rc=1)  # different version, no backoff

    install_sync.ensure_synced(noun="pdata", verb="list", installed_version=version)

    assert install_sync.get_synced_version() == version
    assert install_sync.get_failed_attempt() is None


def test_lock_contention_skips_the_apply_silently(
    auto_sync_env: Path, tmp_path: Path, mocker: MockerFixture, capsys
) -> None:
    """Try-once, do not wait: blocking an unrelated CLI call on a lock would
    make auto-apply a latency hazard, and skipping is harmless - the winner is
    applying the same thing."""
    import json
    import os

    lock = tmp_path / "data-home" / ".install-sync.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": os.getpid(), "started": "x"}))  # live pid
    from cc_session_tools.cli import ccst

    runner = mocker.patch.object(ccst, "run_install_everything", return_value=0)

    install_sync.ensure_synced(noun="pdata", verb="list", installed_version="2.5.0")

    runner.assert_not_called()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_dead_pid_lock_is_reclaimed_and_the_apply_proceeds(
    auto_sync_env: Path, tmp_path: Path
) -> None:
    import json

    from cc_session_tools import __version__ as version

    lock = tmp_path / "data-home" / ".install-sync.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": 2_000_000_000, "started": "x"}))

    install_sync.ensure_synced(noun="pdata", verb="list", installed_version=version)

    assert install_sync.get_synced_version() == version


def test_second_call_in_the_same_process_does_not_reapply(
    auto_sync_env: Path, mocker: MockerFixture
) -> None:
    """Section 5's degenerate case: when the marker store itself is unwritable,
    nothing the first attempt records survives, so the decision comes out
    APPLY again. A single ccst process must never retry.

    record_failed_attempt is stubbed out precisely so the backoff can't be
    what makes this pass - without the stub, the second call would take the
    SKIP_BACKOFF branch and the in-process flag would be untested."""
    from cc_session_tools.cli import ccst

    runner = mocker.patch.object(ccst, "run_install_everything", return_value=1)
    mocker.patch.object(install_sync, "record_failed_attempt")

    install_sync.ensure_synced(noun="pdata", verb="list", installed_version="2.5.0")
    install_sync.ensure_synced(noun="pdata", verb="list", installed_version="2.5.0")

    assert runner.call_count == 1
    assert install_sync.get_failed_attempt() is None  # the stub really did no-op


def test_never_raises_when_the_failure_record_cannot_be_written(
    auto_sync_env: Path, tmp_path: Path, mocker: MockerFixture, capsys
) -> None:
    """Auto-apply is a side effect, never fatal: if even the bookkeeping
    fails, the caller's command must still run."""
    from cc_session_tools.cli import ccst

    mocker.patch.object(ccst, "run_install_everything", return_value=1)
    mocker.patch.object(
        install_sync, "record_failed_attempt", side_effect=sqlite3.DatabaseError("corrupt")
    )

    install_sync.ensure_synced(noun="pdata", verb="list", installed_version="2.5.0")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ccst repair sessions" in captured.err
