"""Tests for the ccst umbrella CLI entry point."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent),
    )


def _settings(**hooks) -> dict:
    return {"hooks": hooks} if hooks else {}


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2))


# ---------- top-level ----------

def test_no_args_prints_usage() -> None:
    result = _run()
    assert result.returncode != 0
    assert "usage" in result.stderr.lower() or "usage" in result.stdout.lower()


def test_version_flag() -> None:
    result = _run("--version")
    assert result.returncode == 0
    assert "ccst" in result.stdout


def test_hooks_no_subcommand_prints_usage() -> None:
    result = _run("hooks")
    assert result.returncode != 0


# ---------- hooks install ----------

def test_hooks_install_dry_run_default(tmp_path: Path) -> None:
    src = tmp_path / "source.json"
    tgt = tmp_path / "target.json"
    _write(src, {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "my-hook"}]}]}})
    _write(tgt, {})
    result = _run("hooks", "install", "--source", str(src), "--target", str(tgt))
    assert result.returncode == 0
    assert "my-hook" in result.stdout
    assert json.loads(tgt.read_text()) == {}  # unchanged in dry-run


def test_hooks_install_apply_writes(tmp_path: Path) -> None:
    src = tmp_path / "source.json"
    tgt = tmp_path / "target.json"
    _write(src, {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "my-hook"}]}]}})
    _write(tgt, {})
    result = _run("hooks", "install", "--source", str(src), "--target", str(tgt), "--apply")
    assert result.returncode == 0
    written = json.loads(tgt.read_text())
    assert written["hooks"]["Stop"][0]["hooks"][0]["command"] == "my-hook"


def test_hooks_install_nothing_to_add_reports_clean(tmp_path: Path) -> None:
    payload = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "my-hook"}]}]}}
    src = tmp_path / "source.json"
    tgt = tmp_path / "target.json"
    _write(src, payload)
    _write(tgt, payload)
    result = _run("hooks", "install", "--source", str(src), "--target", str(tgt))
    assert result.returncode == 0
    assert "nothing to add" in result.stdout.lower() or "up to date" in result.stdout.lower()


def test_hooks_install_missing_source_exits_nonzero(tmp_path: Path) -> None:
    tgt = tmp_path / "target.json"
    _write(tgt, {})
    result = _run("hooks", "install", "--source", str(tmp_path / "nope.json"), "--target", str(tgt))
    assert result.returncode != 0
