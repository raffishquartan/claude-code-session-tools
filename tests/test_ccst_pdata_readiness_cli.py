from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", "pdata", "readiness-scan", *args],
        capture_output=True, text=True, cwd=str(Path(__file__).parent), env=env,
    )


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    e = os.environ.copy()
    e["CCST_PROJECTS_ROOT"] = str(tmp_path / "projects")
    e["CCST_PROJECT_DB_DIR"] = str(tmp_path / "project-db")
    e["CCST_NO_AUTO_SYNC"] = "1"
    return e


def _project(env: dict[str, str], name: str = "demo") -> Path:
    root = Path(env["CCST_PROJECTS_ROOT"]) / name
    root.mkdir(parents=True)
    (root / "a.csv").write_text("id,name\n1,x\n2\n")
    (root / "sub").mkdir()
    (root / "sub" / "b.csv").write_text("id\n1\n2\n")
    return root


def _snapshot(root: Path) -> list[tuple[str, int, int]]:
    return sorted(
        (p.relative_to(root).as_posix(), p.stat().st_size, p.stat().st_mtime_ns)
        for p in root.rglob("*")
    )


def test_exit_zero_with_findings_and_markdown_output(env: dict[str, str]) -> None:
    _project(env)
    r = _run(env, "--project", "demo")
    assert r.returncode == 0, r.stderr
    assert "ragged-rows" in r.stdout and "a.csv" in r.stdout


def test_json_format_parses(env: dict[str, str]) -> None:
    _project(env)
    r = _run(env, "--project", "demo", "--format", "json")
    assert r.returncode == 0, r.stderr
    doc = json.loads(r.stdout)
    assert {f["path"] for f in doc["files"]} == {"a.csv", "sub/b.csv"}
    assert any(f["kind"] == "ragged-rows" for f in doc["findings"])


def test_path_and_findings_only(env: dict[str, str]) -> None:
    _project(env)
    r = _run(env, "--project", "demo", "--path", "sub/", "--findings-only")
    assert r.returncode == 0, r.stderr
    assert "a.csv" not in r.stdout and "## Inventory" not in r.stdout
    assert "No findings." in r.stdout


def test_zero_csv_project_says_so_and_exits_zero(env: dict[str, str]) -> None:
    root = Path(env["CCST_PROJECTS_ROOT"]) / "empty"
    root.mkdir(parents=True)
    r = _run(env, "--project", "empty")
    assert r.returncode == 0
    assert "No CSV files found" in r.stdout


def test_missing_project_directory_exits_2_and_creates_nothing(env: dict[str, str]) -> None:
    r = _run(env, "--project", "ghost")
    assert r.returncode == 2
    assert "ghost" in r.stderr
    assert not (Path(env["CCST_PROJECTS_ROOT"]) / "ghost").exists()
    assert not Path(env["CCST_PROJECT_DB_DIR"]).exists()


def test_invalid_project_name_exits_2(env: dict[str, str]) -> None:
    r = _run(env, "--project", "../evil")
    assert r.returncode == 2
    assert r.stderr


def test_scan_changes_nothing_under_the_project(env: dict[str, str]) -> None:
    root = _project(env)
    before = _snapshot(root)
    assert _run(env, "--project", "demo").returncode == 0
    assert _snapshot(root) == before
    assert not Path(env["CCST_PROJECT_DB_DIR"]).exists()
