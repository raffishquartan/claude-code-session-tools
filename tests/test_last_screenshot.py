"""Tests for cccs_hooks.last_screenshot."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cccs_hooks.last_screenshot import (
    build_context,
    find_token,
    newest_screenshot,
    resolve_screenshot_dir,
)


# ---------- token matching ----------

@pytest.mark.parametrize("text", [
    ">lss", ">lss?", ">lss.", "(>lss is interesting)", "loss at >lss.",
    "summarise >lss please",
])
def test_token_matches(text: str) -> None:
    assert find_token(text) is True


@pytest.mark.parametrize("text", [
    "", ">lssfoo", "process>lssbar", "no token here", "lss", ">LSS",
])
def test_token_does_not_match(text: str) -> None:
    assert find_token(text) is False


# ---------- screenshot resolution ----------

def test_newest_screenshot_picks_highest_mtime(tmp_path: Path) -> None:
    old = tmp_path / "Screenshot old.png"
    new = tmp_path / "Screenshot new.png"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    os.utime(old, (1000, 1000))
    os.utime(new, (2000, 2000))
    assert newest_screenshot(tmp_path) == new


def test_newest_screenshot_ignores_non_images(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_bytes(b"x")
    img = tmp_path / "shot.PNG"
    img.write_bytes(b"x")
    assert newest_screenshot(tmp_path) == img


def test_newest_screenshot_empty_dir_returns_none(tmp_path: Path) -> None:
    assert newest_screenshot(tmp_path) is None


# ---------- directory resolution ----------

def test_resolve_dir_uses_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CCST_SCREENSHOT_DIR", str(tmp_path))
    assert resolve_screenshot_dir() == tmp_path


def test_resolve_dir_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CCST_SCREENSHOT_DIR", raising=False)
    assert resolve_screenshot_dir() is None


def test_resolve_dir_single_missing_path_still_resolves(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A lone non-existent path must resolve, not become None.

    Callers report "unset" and "set but missing" differently, so collapsing a
    bad path to None would show the user the wrong diagnostic.
    """
    missing = tmp_path / "nope"
    monkeypatch.setenv("CCST_SCREENSHOT_DIR", str(missing))
    assert resolve_screenshot_dir() == missing


def test_resolve_dir_picks_first_existing_of_several(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Several os.pathsep-separated paths: the first that exists wins.

    This is what lets one synced settings.json serve both a macOS and a WSL
    machine, where the screenshot directory necessarily differs.
    """
    missing = tmp_path / "wsl-style-path"
    present = tmp_path / "mac-style-path"
    present.mkdir()
    monkeypatch.setenv(
        "CCST_SCREENSHOT_DIR", os.pathsep.join([str(missing), str(present)])
    )
    assert resolve_screenshot_dir() == present


def test_resolve_dir_falls_back_to_first_when_none_exist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When none of the listed paths exist, fall back to the first."""
    first = tmp_path / "a"
    second = tmp_path / "b"
    monkeypatch.setenv(
        "CCST_SCREENSHOT_DIR", os.pathsep.join([str(first), str(second)])
    )
    assert resolve_screenshot_dir() == first


def test_resolve_dir_ignores_empty_segments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Trailing or doubled separators must not yield a bogus Path('')."""
    present = tmp_path / "real"
    present.mkdir()
    monkeypatch.setenv(
        "CCST_SCREENSHOT_DIR", f"{os.pathsep}{present}{os.pathsep}{os.pathsep}"
    )
    assert resolve_screenshot_dir() == present


# ---------- context note ----------

def test_context_fresh_mentions_path_and_lss(tmp_path: Path) -> None:
    p = tmp_path / "shot.png"
    ctx = build_context(path=p, age_seconds=60, dir_configured=True)
    assert str(p) in ctx and ">lss" in ctx
    assert "older than" not in ctx


def test_context_stale_warns(tmp_path: Path) -> None:
    p = tmp_path / "shot.png"
    ctx = build_context(path=p, age_seconds=47 * 60, dir_configured=True)
    assert "older than" in ctx


def test_context_no_dir_configured() -> None:
    ctx = build_context(path=None, age_seconds=None, dir_configured=False)
    assert "CCST_SCREENSHOT_DIR" in ctx


def test_context_dir_but_no_image() -> None:
    ctx = build_context(path=None, age_seconds=None, dir_configured=True)
    assert "no screenshot" in ctx.lower()


# ---------- CLI entry point ----------

def _run(prompt: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(
        [sys.executable, "-m", "cccs_hooks.last_screenshot"],
        input=json.dumps({"prompt": prompt, "session_id": "t", "cwd": "/tmp"}),
        capture_output=True, text=True, env=full_env,
    )


def test_cli_silent_without_token(tmp_path: Path) -> None:
    r = _run("just a normal prompt", {"CCST_SCREENSHOT_DIR": str(tmp_path)})
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_cli_emits_context_with_token(tmp_path: Path) -> None:
    (tmp_path / "shot.png").write_bytes(b"x")
    r = _run("look at >lss", {"CCST_SCREENSHOT_DIR": str(tmp_path)})
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    assert "shot.png" in ctx


def test_cli_exits_0_on_bad_json() -> None:
    r = subprocess.run(
        [sys.executable, "-m", "cccs_hooks.last_screenshot"],
        input="not json", capture_output=True, text=True,
    )
    assert r.returncode == 0


def test_cli_unset_dir_is_visible_to_user() -> None:
    env = {k: v for k, v in os.environ.items() if k != "CCST_SCREENSHOT_DIR"}
    r = subprocess.run(
        [sys.executable, "-m", "cccs_hooks.last_screenshot"],
        input=json.dumps({"prompt": "look at >lss"}),
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0
    assert "CCST_SCREENSHOT_DIR" in r.stderr
