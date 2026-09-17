from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
_SCRIPT_PATH = _SCRIPT_DIR / "update_command_cache.py"
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from cc_session_tools.lib import telemetry_store  # noqa: E402
import update_command_cache as ucc  # noqa: E402


def _insert(
    hooks_dir: Path, *, hook: str, verdict: str, cache: str, input_hash: str,
    ts: str = "2026-07-01T00:00:00Z", session_id: str = "s1",
) -> None:
    conn = telemetry_store.connect(hooks_dir)
    conn.execute(
        "INSERT INTO telemetry_events "
        "(ts, hook, event, tool, session_id, cwd_short, decision, cache, verdict, input_hash) "
        "VALUES (?, ?, 'PreToolUse', 'Bash', ?, 'x', 'allow', ?, ?, ?)",
        (ts, hook, session_id, cache, verdict, input_hash),
    )
    conn.commit()
    conn.close()


def test_collect_candidates_finds_uncached_safe_fires(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ucc, "cache_lookup", lambda sha: None)
    _insert(tmp_path, hook="bash-security-review", verdict="safe", cache="miss", input_hash="sha256:aa")
    rows = ucc.read_telemetry_events(hooks_dir=tmp_path)
    candidates = ucc.collect_candidates(rows)
    assert [c["sha"] for c in candidates] == ["aa"]


def test_collect_candidates_skips_non_bash_security_review_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ucc, "cache_lookup", lambda sha: None)
    _insert(tmp_path, hook="bash-hard-deny", verdict="safe", cache="miss", input_hash="sha256:bb")
    rows = ucc.read_telemetry_events(hooks_dir=tmp_path)
    assert ucc.collect_candidates(rows) == []


def test_collect_candidates_skips_cache_hits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ucc, "cache_lookup", lambda sha: None)
    _insert(tmp_path, hook="bash-security-review", verdict="safe", cache="hit", input_hash="sha256:cc")
    rows = ucc.read_telemetry_events(hooks_dir=tmp_path)
    assert ucc.collect_candidates(rows) == []


def test_collect_candidates_skips_already_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ucc, "cache_lookup", lambda sha: object())  # already cached
    _insert(tmp_path, hook="bash-security-review", verdict="safe", cache="miss", input_hash="sha256:dd")
    rows = ucc.read_telemetry_events(hooks_dir=tmp_path)
    assert ucc.collect_candidates(rows) == []


def test_no_broken_repo_relative_sys_path_bootstrap() -> None:
    """Regression test for ccmsg 20260912T122716Z-c24b bug 1: the script computed its own
    `sys.path` entry as `Path(__file__).parents[3] / "src"`, which resolves to a nonexistent
    directory (`.../cc_session_tools/src`) and does nothing useful - `hooks` and
    `cc_session_tools.lib` are real top-level installed packages (like `hooks.stats`, exposed
    via the `cccs-stats` console-script entry point) that any interpreter with `cc-session-tools`
    installed already imports directly, with no bootstrap. A bare system `python3` that lacks the
    package installed fails with `ModuleNotFoundError` regardless of any path hack - that failure
    is expected and correct, not something to paper over with more path manipulation."""
    src = _SCRIPT_PATH.read_text()
    assert "sys.path" not in src
    assert "_REPO_ROOT" not in src


def test_script_runs_as_real_subprocess_with_package_installed(tmp_path: Path) -> None:
    """The script must run correctly with no bootstrap of its own, under any interpreter that
    actually has cc-session-tools installed - here, the same interpreter pytest itself runs
    under (the dev checkout's venv)."""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), "list"],
        env={"CCST_FIRES_ACCESS": "1", "CCST_DATA_HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "ModuleNotFoundError" not in result.stderr
