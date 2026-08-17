"""Tests for the install-sync auto-apply call site in ccst.cli.ccst.main().

Task-level counterpart to tests/test_install_sync.py: those prove the decision
functions and the executor are right in isolation; these prove main() calls
ensure_synced with the right noun/verb, at the right point, and dispatches the
requested command regardless of what auto-sync did.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from cc_session_tools.cli import ccst


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path))
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path / "data-home"))
    return tmp_path


def test_main_calls_ensure_synced_with_the_parsed_noun_and_verb(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    from cc_session_tools.lib import install_sync

    ensure = mocker.patch.object(install_sync, "ensure_synced")
    monkeypatch.setattr(ccst.sys, "argv", ["ccst", "skills", "install"])
    mocker.patch.object(ccst, "_cmd_skills_install", return_value=0)

    with pytest.raises(SystemExit):
        ccst.main()

    ensure.assert_called_once_with(
        noun="skills", verb="install", installed_version=ccst.__version__
    )


def test_main_dispatches_even_when_auto_sync_applies(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    """The whole point of replacing the 2.4.0 gate: a stale install no longer
    refuses to run the requested command."""
    from cc_session_tools.lib import install_sync

    mocker.patch.object(install_sync, "ensure_synced")
    monkeypatch.setattr(ccst.sys, "argv", ["ccst", "skills", "install"])
    dispatched = mocker.patch.object(ccst, "_cmd_skills_install", return_value=0)

    with pytest.raises(SystemExit) as exc:
        ccst.main()

    assert exc.value.code == 0
    dispatched.assert_called_once()


@pytest.mark.parametrize("rc", [0, 1, 3])
def test_exit_code_is_the_commands_own_regardless_of_auto_sync(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture, rc: int
) -> None:
    """ccsched's ledger auto-suspends a job after 10 consecutive failures. An
    install-sync failure leaking into $? would start suspending healthy jobs."""
    from cc_session_tools.lib import install_sync

    mocker.patch.object(install_sync, "ensure_synced")
    monkeypatch.setattr(ccst.sys, "argv", ["ccst", "skills", "install"])
    mocker.patch.object(ccst, "_cmd_skills_install", return_value=rc)

    with pytest.raises(SystemExit) as exc:
        ccst.main()

    assert exc.value.code == rc


def test_ensure_synced_runs_before_the_command_dispatches(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    """Ordering matters: a hook or skill registered by the apply must be in
    place before the requested command runs, not after."""
    from cc_session_tools.lib import install_sync

    order: list[str] = []
    mocker.patch.object(
        install_sync, "ensure_synced", side_effect=lambda **kw: order.append("sync")
    )
    monkeypatch.setattr(ccst.sys, "argv", ["ccst", "skills", "install"])
    mocker.patch.object(
        ccst, "_cmd_skills_install", side_effect=lambda a: (order.append("cmd"), 0)[1]
    )

    with pytest.raises(SystemExit):
        ccst.main()

    assert order == ["sync", "cmd"]


def test_bare_ccst_exits_on_usage_without_calling_ensure_synced(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    """The usage check runs first, so `ccst` with no arguments never triggers
    an apply."""
    from cc_session_tools.lib import install_sync

    ensure = mocker.patch.object(install_sync, "ensure_synced")
    monkeypatch.setattr(ccst.sys, "argv", ["ccst"])

    with pytest.raises(SystemExit) as exc:
        ccst.main()

    assert exc.value.code == 1
    ensure.assert_not_called()


def test_should_block_for_unsynced_install_is_gone() -> None:
    """Deleted outright rather than deprecated - it had one production caller
    (this call site) and its own tests, and 'deleting code is a feature'."""
    from cc_session_tools.lib import install_sync

    assert not hasattr(install_sync, "should_block_for_unsynced_install")
