"""Tests for the install-sync interactive gate in ccst.cli.ccst.main()."""
from __future__ import annotations

from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from cc_session_tools.cli import ccst


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "sessions.db"
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path))
    return p


def test_blocks_interactive_command_on_stale_install(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    from cc_session_tools.lib import install_sync

    install_sync.record_synced("0.0.1-not-current", path=db_path)
    # doctor itself is exempt (see test_does_not_block_doctor_when_stale below) -
    # use a non-exempt noun/verb here:
    monkeypatch.setattr(ccst.sys, "argv", ["ccst", "skills", "install"])
    mocker.patch("sys.stderr.isatty", return_value=True)
    dispatched = mocker.patch.object(ccst, "_cmd_skills_install")

    with pytest.raises(SystemExit) as exc:
        ccst.main()

    assert exc.value.code == 1
    dispatched.assert_not_called()


def test_blocks_interactive_command_when_never_synced(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    """The branch every existing installation hits on its first ccst
    invocation after upgrading to a version that ships this feature: no
    record_synced() call has ever happened, so get_synced_version() returns
    None rather than a stale-but-present version string - distinct from
    test_blocks_interactive_command_on_stale_install's mismatched-but-present
    case, and the only case that exercises the "has never been run" message
    branch rather than the "was last synced at {version}" one."""
    monkeypatch.setattr(ccst.sys, "argv", ["ccst", "skills", "install"])
    mocker.patch("sys.stderr.isatty", return_value=True)
    dispatched = mocker.patch.object(ccst, "_cmd_skills_install")

    with pytest.raises(SystemExit) as exc:
        ccst.main()

    assert exc.value.code == 1
    dispatched.assert_not_called()


def test_does_not_block_when_synced(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    from cc_session_tools.lib import install_sync

    install_sync.record_synced(ccst.__version__, path=db_path)
    monkeypatch.setattr(ccst.sys, "argv", ["ccst", "skills", "install"])
    mocker.patch("sys.stderr.isatty", return_value=True)
    dispatched = mocker.patch.object(ccst, "_cmd_skills_install", return_value=0)

    with pytest.raises(SystemExit) as exc:
        ccst.main()

    assert exc.value.code == 0
    dispatched.assert_called_once()


def test_does_not_block_when_not_interactive(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    from cc_session_tools.lib import install_sync

    install_sync.record_synced("0.0.1-not-current", path=db_path)
    monkeypatch.setattr(ccst.sys, "argv", ["ccst", "skills", "install"])
    mocker.patch("sys.stderr.isatty", return_value=False)
    dispatched = mocker.patch.object(ccst, "_cmd_skills_install", return_value=0)

    with pytest.raises(SystemExit) as exc:
        ccst.main()

    assert exc.value.code == 0
    dispatched.assert_called_once()


def test_does_not_block_hooks_run_even_when_stale_and_interactive(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    from cc_session_tools.lib import install_sync

    install_sync.record_synced("0.0.1-not-current", path=db_path)
    monkeypatch.setattr(ccst.sys, "argv", ["ccst", "hooks", "run", "some-hook"])
    mocker.patch("sys.stderr.isatty", return_value=True)
    dispatched = mocker.patch.object(ccst, "_cmd_hooks_run", return_value=0)

    with pytest.raises(SystemExit) as exc:
        ccst.main()

    assert exc.value.code == 0
    dispatched.assert_called_once()


def test_does_not_block_install_everything_when_stale(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    from cc_session_tools.lib import install_sync

    install_sync.record_synced("0.0.1-not-current", path=db_path)
    monkeypatch.setattr(ccst.sys, "argv", ["ccst", "install-everything"])
    mocker.patch("sys.stderr.isatty", return_value=True)
    dispatched = mocker.patch.object(ccst, "_cmd_install_everything", return_value=0)

    with pytest.raises(SystemExit) as exc:
        ccst.main()

    assert exc.value.code == 0
    dispatched.assert_called_once()


def test_does_not_block_doctor_when_stale(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    from cc_session_tools.lib import install_sync

    install_sync.record_synced("0.0.1-not-current", path=db_path)
    monkeypatch.setattr(ccst.sys, "argv", ["ccst", "doctor"])
    mocker.patch("sys.stderr.isatty", return_value=True)
    dispatched = mocker.patch.object(ccst, "_cmd_doctor", return_value=0)

    with pytest.raises(SystemExit) as exc:
        ccst.main()

    assert exc.value.code == 0
    dispatched.assert_called_once()


def test_does_not_block_repair_when_stale(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    """ccst repair sessions must stay reachable even on a stale/corrupt
    install - it's the tool that fixes the exact class of store corruption
    that could otherwise leave a user with no interactive way out."""
    from cc_session_tools.lib import install_sync

    install_sync.record_synced("0.0.1-not-current", path=db_path)
    monkeypatch.setattr(ccst.sys, "argv", ["ccst", "repair", "sessions"])
    mocker.patch("sys.stderr.isatty", return_value=True)
    dispatched = mocker.patch.object(ccst, "_cmd_repair_sessions", return_value=0)

    with pytest.raises(SystemExit) as exc:
        ccst.main()

    assert exc.value.code == 0
    dispatched.assert_called_once()


def test_does_not_block_migrate_when_stale(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    """ccst migrate all must stay reachable - this repo's own pending-
    migration doctor output tells users to run it from a plain terminal."""
    from cc_session_tools.lib import install_sync

    install_sync.record_synced("0.0.1-not-current", path=db_path)
    monkeypatch.setattr(ccst.sys, "argv", ["ccst", "migrate", "all"])
    mocker.patch("sys.stderr.isatty", return_value=True)
    dispatched = mocker.patch.object(ccst, "_cmd_migrate_all", return_value=0)

    with pytest.raises(SystemExit) as exc:
        ccst.main()

    assert exc.value.code == 0
    dispatched.assert_called_once()


def test_block_message_mentions_install_everything(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture, capsys
) -> None:
    from cc_session_tools.lib import install_sync

    install_sync.record_synced("0.0.1-not-current", path=db_path)
    monkeypatch.setattr(ccst.sys, "argv", ["ccst", "skills", "install"])
    mocker.patch("sys.stderr.isatty", return_value=True)
    mocker.patch.object(ccst, "_cmd_skills_install")

    with pytest.raises(SystemExit):
        ccst.main()

    err = capsys.readouterr().err
    assert "install-everything --apply" in err
