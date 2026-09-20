"""Tests for locating bun-installed tools (bun itself, ccusage)."""
from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib import bun_tools


def _make_exe(directory: Path, name: str, body: str = "echo 1.2.3") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)
    return path


@pytest.fixture
def empty_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    return empty


def test_bun_bin_dir_honours_bun_install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BUN_INSTALL", str(tmp_path / "custom-bun"))
    assert bun_tools.bun_bin_dir() == tmp_path / "custom-bun" / "bin"


def test_bun_bin_dir_defaults_to_home_dot_bun(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("BUN_INSTALL", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert bun_tools.bun_bin_dir() == tmp_path / ".bun" / "bin"


def test_resolve_prefers_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    on_path = _make_exe(tmp_path / "onpath", "ccusage")
    monkeypatch.setenv("PATH", str(on_path.parent))
    bin_dir = tmp_path / "bunbin"
    _make_exe(bin_dir, "ccusage")

    resolved = bun_tools.resolve("ccusage", bin_dir=bin_dir)

    assert resolved == bun_tools.Resolved(path=str(on_path), on_path=True)


def test_resolve_falls_back_to_bun_bin_dir(tmp_path: Path, empty_path: Path) -> None:
    bin_dir = tmp_path / "bunbin"
    exe = _make_exe(bin_dir, "ccusage")

    resolved = bun_tools.resolve("ccusage", bin_dir=bin_dir)

    assert resolved == bun_tools.Resolved(path=str(exe), on_path=False)


def test_resolve_ignores_non_executable_in_bun_bin_dir(tmp_path: Path, empty_path: Path) -> None:
    bin_dir = tmp_path / "bunbin"
    exe = _make_exe(bin_dir, "ccusage")
    exe.chmod(0o644)

    assert bun_tools.resolve("ccusage", bin_dir=bin_dir) is None


def test_resolve_returns_none_when_nowhere(tmp_path: Path, empty_path: Path) -> None:
    assert bun_tools.resolve("ccusage", bin_dir=tmp_path / "bunbin") is None
