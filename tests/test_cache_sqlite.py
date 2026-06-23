from __future__ import annotations

import hashlib
import sqlite3 as _sqlite3
import datetime as _datetime
from pathlib import Path

import pytest

from cccs_hooks.cache import CacheEntry, cache_lookup, cache_record, sha256_command


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CCCS_CACHE_DB", str(tmp_path / "cache.db"))
    monkeypatch.delenv("CCCS_CACHE_PATH", raising=False)
    return tmp_path / "cache.db"


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
    conn = _sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO command_cache VALUES (?,NULL,'safe','none','cmd',1,?,?,'auto')",
        ("sha256:stale", old_ts, old_ts),
    )
    conn.commit()
    conn.close()
    assert cache_lookup("sha256:stale") is None


def test_prune_removes_old_entries_on_write(db: Path) -> None:
    _bootstrap_schema(db)
    old_ts = (
        _datetime.datetime.now(_datetime.timezone.utc) - _datetime.timedelta(days=91)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = _sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO command_cache VALUES (?,NULL,'safe','none','old',1,?,?,'auto')",
        ("sha256:old", old_ts, old_ts),
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
    assert "sha256:old" not in hashes
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
    assert not errors
    conn = _sqlite3.connect(str(db))
    count = conn.execute("SELECT COUNT(*) FROM command_cache").fetchone()[0]
    conn.close()
    assert count == 20
