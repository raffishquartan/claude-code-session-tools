from __future__ import annotations

import hashlib
import sqlite3 as _sqlite3
import datetime as _datetime
from pathlib import Path

import pytest

import json as _json

from cccs_hooks.cache import CacheEntry, cache_lookup, cache_record, invocations_record, sha256_command


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CCCS_CACHE_DB", str(tmp_path / "cache.db"))
    monkeypatch.delenv("CCCS_CACHE_PATH", raising=False)
    return tmp_path / "cache.db"


def test_sha256_command_is_stable() -> None:
    assert sha256_command("ls") == sha256_command("ls")
    assert sha256_command("ls") != sha256_command("ls ")


def test_lookup_empty_returns_none(db: Path) -> None:
    assert cache_lookup("sha256:abc") is None


def test_record_then_lookup_returns_entry(db: Path) -> None:
    sha = sha256_command("git status")
    cache_record(sha, "safe", "none", "git status")
    entry = cache_lookup(sha)
    assert entry is not None
    assert entry.verdict == "safe"
    assert entry.fire_count == 1


def test_record_twice_increments_fire_count(db: Path) -> None:
    sha = sha256_command("git status")
    cache_record(sha, "safe", "none", "git status")
    cache_record(sha, "safe", "none", "git status")
    entry = cache_lookup(sha)
    assert entry is not None
    assert entry.fire_count == 2


def test_lookup_by_norm_hash_returns_entry(db: Path) -> None:
    exact = sha256_command("git checkout feature/a")
    norm = sha256_command("git checkout <ARGS>")
    cache_record(exact, "safe", "none", "git checkout feature/a", norm_sha=norm)
    # Different exact hash, same norm hash — should hit
    new_exact = sha256_command("git checkout feature/b")
    entry = cache_lookup(new_exact, norm_sha=norm)
    assert entry is not None
    assert entry.verdict == "safe"


def test_only_safe_verdicts_are_recorded(db: Path) -> None:
    sha = sha256_command("suspicious cmd")
    cache_record(sha, "suspicious", "risky", "suspicious cmd")
    assert cache_lookup(sha) is None


def test_dangerous_verdict_not_stored(db: Path) -> None:
    sha = sha256_command("rm -rf /")
    cache_record(sha, "dangerous", "destroys filesystem", "rm -rf /")
    assert cache_lookup(sha) is None


def _bootstrap_schema(db: Path) -> None:
    """Trigger schema creation by recording a dummy entry, then delete it."""
    sha = sha256_command("bootstrap-schema-dummy")
    cache_record(sha, "safe", "none", "bootstrap-schema-dummy")
    conn = _sqlite3.connect(str(db))
    conn.execute("DELETE FROM command_cache WHERE exact_hash=?", (sha,))
    conn.commit()
    conn.close()


def test_stale_entry_not_returned(db: Path) -> None:
    _bootstrap_schema(db)
    old_ts = (
        _datetime.datetime.now(_datetime.timezone.utc) - _datetime.timedelta(days=91)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Synthetic key (not a real sha256_command output) — sufficient for this test
    # since we insert and look up the same literal; format does not affect behaviour
    stale_key = "deadbeef" + "0" * 56  # 64-char hex string like a real sha256 digest
    conn = _sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO command_cache VALUES (?,NULL,'safe','none','cmd',1,?,?,'auto')",
        (stale_key, old_ts, old_ts),
    )
    conn.commit()
    conn.close()
    assert cache_lookup(stale_key) is None


def test_prune_removes_old_entries_on_write(db: Path) -> None:
    _bootstrap_schema(db)
    old_ts = (
        _datetime.datetime.now(_datetime.timezone.utc) - _datetime.timedelta(days=91)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    old_key = "cafebabe" + "0" * 56  # 64-char hex string like a real sha256 digest
    conn = _sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO command_cache VALUES (?,NULL,'safe','none','old',1,?,?,'auto')",
        (old_key, old_ts, old_ts),
    )
    conn.commit()
    conn.close()
    # Trigger a write — prune fires inside cache_record()
    sha = sha256_command("git status")
    cache_record(sha, "safe", "none", "git status")
    conn2 = _sqlite3.connect(str(db))
    rows = conn2.execute("SELECT exact_hash FROM command_cache").fetchall()
    conn2.close()
    hashes = [r[0] for r in rows]
    assert old_key not in hashes
    assert sha in hashes


def test_concurrent_writes_do_not_corrupt(db: Path) -> None:
    import threading
    errors: list[Exception] = []

    def write(i: int) -> None:
        try:
            sha = sha256_command(f"git status {i}")
            cache_record(sha, "safe", "none", f"git status {i}")
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # assert not errors catches non-sqlite exceptions raised outside cache_record
    # (cache_record itself swallows sqlite3.Error, so some writes may be silently
    # lost under contention — that is acceptable cache-miss behaviour, not corruption).
    assert not errors
    conn = _sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT exact_hash, fire_count FROM command_cache"
    ).fetchall()
    conn.close()
    # Corruption check: every successful write used a distinct SHA, so no row
    # should have fire_count > 1 (that would mean two writes merged incorrectly).
    assert all(fire == 1 for _, fire in rows), (
        f"fire_count > 1 found — rows were incorrectly merged: {rows}"
    )
    # Sanity: at least half the writes must have landed (not all silently lost).
    assert len(rows) >= 10, f"Too many writes silently lost: only {len(rows)}/20 written"


def test_invocations_record_basic(db: Path) -> None:
    invocations_record(0, "allow", exact_hash="abc123")
    conn = _sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT exit_tier, verdict, heuristic_fired, exact_hash, ms_elapsed "
        "FROM hook_invocations"
    ).fetchone()
    conn.close()
    assert row == (0, "allow", 0, "abc123", None)


def test_invocations_record_heuristic_names(db: Path) -> None:
    invocations_record(
        3, "suspicious",
        heuristic_fired=True,
        heuristic_names=["pipe_to_shell", "curl_pipe"],
    )
    conn = _sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT heuristic_fired, heuristic_names FROM hook_invocations"
    ).fetchone()
    conn.close()
    assert row[0] == 1
    assert _json.loads(row[1]) == ["pipe_to_shell", "curl_pipe"]


def test_invocations_prune_removes_old(db: Path) -> None:
    _bootstrap_schema(db)
    old_ts = (
        _datetime.datetime.now(_datetime.timezone.utc) - _datetime.timedelta(days=91)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = _sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO hook_invocations "
        "(ts,tool_name,exit_tier,heuristic_fired,verdict) VALUES (?,?,?,?,?)",
        (old_ts, "Bash", 3, 0, "safe"),
    )
    conn.commit()
    conn.close()
    # Verify row exists before pruning
    conn_check = _sqlite3.connect(str(db))
    before_count = conn_check.execute("SELECT COUNT(*) FROM hook_invocations").fetchone()[0]
    conn_check.close()
    assert before_count == 1
    # Trigger a write — prune fires inside cache_record()
    cache_record(sha256_command("git status"), "safe", "none", "git status")
    conn2 = _sqlite3.connect(str(db))
    after_count = conn2.execute("SELECT COUNT(*) FROM hook_invocations").fetchone()[0]
    conn2.close()
    assert after_count == 0


def test_stats_main_no_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """cccs-stats main() runs against an empty DB without error and prints expected headers."""
    monkeypatch.setenv("CCCS_CACHE_DB", str(tmp_path / "cache.db"))
    monkeypatch.delenv("CCCS_CACHE_PATH", raising=False)
    # Seed one row so the view returns data
    invocations_record(2, "safe", cache_source="exact", ms_elapsed=None)
    from cccs_hooks import stats as stats_mod
    stats_mod.main([])
    out = capsys.readouterr().out
    assert "Hook invocations" in out
    assert "Verdict breakdown" in out


def test_default_db_path_uses_data_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("CCCS_CACHE_DB", raising=False)
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path))
    from cccs_hooks.cache import _db_path
    assert _db_path() == tmp_path / "command-cache.db"


def test_stats_main_no_db_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """cccs-stats prints a friendly message (not a traceback) when the DB file doesn't exist."""
    monkeypatch.setenv("CCCS_CACHE_DB", str(tmp_path / "does-not-exist.db"))
    monkeypatch.delenv("CCCS_CACHE_PATH", raising=False)
    from cccs_hooks import stats as stats_mod
    stats_mod.main([])
    out = capsys.readouterr().out
    assert "No hook DB found" in out


def test_cache_efficiency_view(db: Path) -> None:
    # Seed: 2 trivial exits + 1 cache hit + 1 claude call (500ms)
    for _ in range(2):
        invocations_record(0, "allow")
    invocations_record(2, "safe", cache_source="exact")
    invocations_record(3, "safe", ms_elapsed=500)
    conn = _sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT total, trivial, cached, claude_calls, cache_hit_pct, avg_claude_ms "
        "FROM cache_efficiency"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    total, trivial, cached, claude, pct, avg_ms = rows[0]
    assert total == 4
    assert trivial == 2
    assert cached == 1
    assert claude == 1
    assert pct == 25.0
    assert avg_ms == 500.0
