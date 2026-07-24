from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
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
