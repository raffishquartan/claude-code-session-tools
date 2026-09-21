"""Tests for `ccst hooks allowlist list|add|remove|verify`."""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hooks import cache as cache_mod


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setenv("CCST_CACHE_DB", str(tmp_path / "cache.db"))
    return {**os.environ, "CCST_CACHE_DB": str(tmp_path / "cache.db"), "CCST_NO_AUTO_SYNC": "1"}


@pytest.fixture
def script(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    path = root / "scripts" / "log.py"
    path.parent.mkdir()
    path.write_text("print('hi')\n")
    return path


def _ccst(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", "hooks", "allowlist", *args],
        capture_output=True, text=True, env=env, cwd=str(Path(__file__).parent.parent),
    )


def test_list_when_empty(env: dict[str, str]) -> None:
    result = _ccst(env, "list")

    assert result.returncode == 0
    assert "No allowlisted scripts" in result.stdout


def test_add_records_the_resolved_path_and_current_hash_as_manual(
    env: dict[str, str], script: Path
) -> None:
    result = _ccst(env, "add", str(script))

    assert result.returncode == 0, result.stderr
    entry = cache_mod.allowlist_get(str(script.resolve()))
    assert entry is not None
    assert entry.sha256 == hashlib.sha256(script.read_bytes()).hexdigest()
    assert entry.source == "manual"
    assert entry.project_root == str(script.parent.parent.resolve())


def test_list_shows_added_entries(env: dict[str, str], script: Path) -> None:
    _ccst(env, "add", str(script))

    result = _ccst(env, "list")

    assert str(script.resolve()) in result.stdout
    assert "manual" in result.stdout


def test_add_rejects_a_missing_file(env: dict[str, str], tmp_path: Path) -> None:
    result = _ccst(env, "add", str(tmp_path / "nope.py"))

    assert result.returncode != 0
    assert "not a file" in result.stderr
    assert cache_mod.allowlist_list() == []


def test_remove_deletes_the_entry(env: dict[str, str], script: Path) -> None:
    _ccst(env, "add", str(script))

    result = _ccst(env, "remove", str(script))

    assert result.returncode == 0
    assert cache_mod.allowlist_list() == []


def test_remove_reports_an_unknown_entry(env: dict[str, str], script: Path) -> None:
    result = _ccst(env, "remove", str(script))

    assert result.returncode == 1
    assert "not in the allowlist" in result.stderr


def test_verify_is_clean_when_nothing_changed(env: dict[str, str], script: Path) -> None:
    _ccst(env, "add", str(script))

    result = _ccst(env, "verify")

    assert result.returncode == 0
    assert "OK" in result.stdout


def test_verify_reports_changed_and_missing_scripts(env: dict[str, str], script: Path) -> None:
    other = script.parent / "other.py"
    other.write_text("x = 1\n")
    _ccst(env, "add", str(script))
    _ccst(env, "add", str(other))
    old = hashlib.sha256(script.read_bytes()).hexdigest()
    script.write_text("print('changed')\n")
    new = hashlib.sha256(script.read_bytes()).hexdigest()
    other.unlink()

    result = _ccst(env, "verify")

    assert result.returncode == 1
    assert f"CHANGED  {script.resolve()}  {old[:8]} -> {new[:8]}" in result.stdout
    assert f"MISSING  {other.resolve()}" in result.stdout
