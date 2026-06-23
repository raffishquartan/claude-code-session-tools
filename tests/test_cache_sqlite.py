from __future__ import annotations

import hashlib
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
