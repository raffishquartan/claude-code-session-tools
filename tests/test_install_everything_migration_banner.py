"""Shell-level tests for install-everything.sh's pdata migration banner (task 6.1a).

The script runs against a fixture HOME with stub `uv` and `ccst` executables: `uv`, `ccst
install-everything` and `ccst doctor` are stubbed out (the doctor stub's exit status is set per
test), and every other `ccst` call runs the real CLI from this checkout, so the banner comes from
the real format detection over the fixture databases."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from cc_session_tools.lib.pdata import formats, repository, store

from pdata.legacy_fixture import make_format3_db

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "install-everything.sh"
_BANNER_HEADLINE = "pdata MIGRATION REQUIRED"


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CCST_DATA_HOME", str(home / ".local" / "share" / "claude"))
    monkeypatch.delenv(store.PROJECT_DB_DIR_ENV, raising=False)
    monkeypatch.setenv(store.PROJECTS_ROOT_ENV, str(home / "cc"))
    monkeypatch.setenv("CCST_MACHINE_NAME", "machine-a")
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    _stub(bin_dir, "uv", 'echo "stub uv $*"')
    _stub(bin_dir, "pgrep", "exit 1")
    real_ccst = (
        f'PYTHONPATH="{_REPO_ROOT / "src"}" exec "{sys.executable}" '
        '-m cc_session_tools.cli.ccst "$@"'
    )
    _stub(bin_dir, "ccst", "\n".join([
        'case "$1" in',
        '  install-everything) echo "stub ccst install-everything"; exit 0 ;;',
        '  doctor) echo "stub ccst doctor"; exit "${STUB_DOCTOR_STATUS:-0}" ;;',
        "esac",
        real_ccst,
    ]))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return home


def _run_script(doctor_status: int = 0) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["STUB_DOCTOR_STATUS"] = str(doctor_status)
    return subprocess.run(
        ["bash", str(_SCRIPT)], capture_output=True, text=True, env=env, cwd=str(_REPO_ROOT),
    )


def _format3(project: str) -> None:
    make_format3_db(store.db_path(project))


def _format4(project: str) -> None:
    repository.connect(project).close()


def test_banner_names_the_format3_project_commands_and_runbook(home: Path) -> None:
    _format3("notes")
    _format4("ledger")
    result = _run_script()
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert _BANNER_HEADLINE in out
    banner = out[out.index(_BANNER_HEADLINE):]
    assert "  - notes" in banner
    assert "ledger" not in banner
    assert "ccst pdata migrate-uuid --all-projects" in banner
    assert "ccst pdata migrate-uuid --all-projects --write" in banner
    assert formats.RUNBOOK_PATH in banner
    assert "before starting any claude session" in banner
    assert out.index(_BANNER_HEADLINE) < out.index("Done.")


def test_the_dry_run_report_precedes_the_banner(home: Path) -> None:
    _format3("notes")
    result = _run_script()
    combined = result.stdout + result.stderr
    assert "notes: " in combined
    assert result.stdout.index("ccst pdata migrate-uuid --all-projects (dry run)") < (
        result.stdout.index(_BANNER_HEADLINE)
    )


def test_no_banner_when_every_project_is_format4(home: Path) -> None:
    _format4("ledger")
    result = _run_script()
    assert result.returncode == 0, result.stderr
    assert _BANNER_HEADLINE not in result.stdout + result.stderr
    assert "Done." in result.stdout


def test_no_banner_when_there_are_no_project_databases(home: Path) -> None:
    result = _run_script()
    assert result.returncode == 0, result.stderr
    assert _BANNER_HEADLINE not in result.stdout + result.stderr


def test_a_failing_doctor_still_shows_the_banner_and_keeps_its_exit_status(home: Path) -> None:
    _format3("notes")
    result = _run_script(doctor_status=1)
    assert result.returncode == 1
    assert _BANNER_HEADLINE in result.stdout
    assert "Done." not in result.stdout


def test_a_failing_doctor_without_format3_keeps_its_exit_status(home: Path) -> None:
    _format4("ledger")
    result = _run_script(doctor_status=1)
    assert result.returncode == 1
    assert _BANNER_HEADLINE not in result.stdout


def test_migration_notice_alone_prints_nothing_when_nothing_is_format3(home: Path) -> None:
    _format4("ledger")
    result = subprocess.run(
        ["ccst", "pdata", "migration-notice"], capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_migration_notice_reports_an_unreadable_database_and_still_exits_0(home: Path) -> None:
    db_dir = store.project_db_dir()
    db_dir.mkdir(parents=True)
    (db_dir / "broken.db").write_bytes(b"not a sqlite database at all" * 100)
    _format3("notes")
    result = subprocess.run(
        ["ccst", "pdata", "migration-notice"], capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "broken" in result.stderr
    assert "  - notes" in result.stdout
