from __future__ import annotations

import csv
import datetime
import threading
from pathlib import Path

import pytest

from cccs_hooks.cache import (
    CacheEntry,
    cache_age_days,
    cache_is_stale,
    cache_lookup,
    cache_record,
    sha256_command,
)


# ---------- helpers ----------

def _cache_file(tmp_path: Path) -> Path:
    return tmp_path / "command-cache.csv"


def _seed_entry(
    path: Path,
    *,
    sha: str = "deadbeef",
    verdict: str = "safe",
    last_validated_at: str | None = None,
    cache_source: str = "auto",
    fire_count: int = 1,
) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    last_validated_at = last_validated_at or now
    fields = [
        "hash",
        "verdict",
        "risks_summary",
        "command_preview",
        "fire_count",
        "last_seen",
        "last_validated_at",
        "cache_source",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerow(
            {
                "hash": sha,
                "verdict": verdict,
                "risks_summary": "none",
                "command_preview": "git status",
                "fire_count": str(fire_count),
                "last_seen": now,
                "last_validated_at": last_validated_at,
                "cache_source": cache_source,
            }
        )


# ---------- sha256_command ----------

def test_sha256_command_is_stable() -> None:
    assert sha256_command("ls") == sha256_command("ls")
    assert sha256_command("ls") != sha256_command("ls ")


# ---------- cache_lookup ----------

def test_lookup_returns_entry_on_hit(tmp_path: Path) -> None:
    cache = _cache_file(tmp_path)
    _seed_entry(cache, sha="abc")
    entry = cache_lookup("abc", cache_path=cache)
    assert entry is not None
    assert entry.hash == "abc"
    assert entry.verdict == "safe"


def test_lookup_returns_none_on_miss(tmp_path: Path) -> None:
    cache = _cache_file(tmp_path)
    _seed_entry(cache, sha="abc")
    assert cache_lookup("xyz", cache_path=cache) is None


def test_lookup_returns_none_when_file_absent(tmp_path: Path) -> None:
    assert cache_lookup("abc", cache_path=tmp_path / "missing.csv") is None


def test_lookup_treats_corruption_as_miss(tmp_path: Path) -> None:
    cache = _cache_file(tmp_path)
    cache.write_text("not,a,valid\ncsv,at,all\n")
    # No exception, returns None.
    assert cache_lookup("abc", cache_path=cache) is None


def test_lookup_skips_malformed_row_but_finds_valid(tmp_path: Path) -> None:
    cache = _cache_file(tmp_path)
    _seed_entry(cache, sha="abc")
    # Append a malformed row.
    with cache.open("a", newline="") as f:
        f.write("garbage,row,with,too,few\n")
    entry = cache_lookup("abc", cache_path=cache)
    assert entry is not None
    assert entry.hash == "abc"


def test_lookup_invalid_verdict_is_skipped(tmp_path: Path) -> None:
    cache = _cache_file(tmp_path)
    _seed_entry(cache, sha="abc", verdict="weird-value")
    # Row exists but verdict is invalid - treated as malformed -> miss.
    assert cache_lookup("abc", cache_path=cache) is None


# ---------- cache_record ----------

def test_record_writes_safe_entry(tmp_path: Path) -> None:
    cache = _cache_file(tmp_path)
    cache_record("abc", "safe", "none", "git log", cache_path=cache)
    entry = cache_lookup("abc", cache_path=cache)
    assert entry is not None
    assert entry.fire_count == 1
    assert entry.verdict == "safe"
    assert entry.cache_source == "auto"


def test_record_does_not_write_suspicious(tmp_path: Path) -> None:
    cache = _cache_file(tmp_path)
    cache_record("abc", "suspicious", "weird", "rm -rf x", cache_path=cache)
    assert cache_lookup("abc", cache_path=cache) is None
    # File may or may not exist depending on impl - but lookup must miss.


def test_record_does_not_write_dangerous(tmp_path: Path) -> None:
    cache = _cache_file(tmp_path)
    cache_record("abc", "dangerous", "very bad", "rm -rf /", cache_path=cache)
    assert cache_lookup("abc", cache_path=cache) is None


def test_record_increments_fire_count_on_repeat(tmp_path: Path) -> None:
    cache = _cache_file(tmp_path)
    cache_record("abc", "safe", "none", "ls", cache_path=cache)
    cache_record("abc", "safe", "none", "ls", cache_path=cache)
    cache_record("abc", "safe", "none", "ls", cache_path=cache)
    entry = cache_lookup("abc", cache_path=cache)
    assert entry is not None
    assert entry.fire_count == 3


def test_record_file_mode_is_0600(tmp_path: Path) -> None:
    import stat as _stat
    cache = _cache_file(tmp_path)
    cache_record("abc", "safe", "none", "ls", cache_path=cache)
    mode = _stat.S_IMODE(cache.stat().st_mode)
    assert mode == 0o600


# ---------- cache_age_days ----------

def test_age_days_recent_returns_small_value(tmp_path: Path) -> None:
    cache = _cache_file(tmp_path)
    _seed_entry(cache, sha="abc")
    age = cache_age_days("abc", cache_path=cache)
    assert age is not None
    assert age < 1.0


def test_age_days_returns_none_for_missing(tmp_path: Path) -> None:
    cache = _cache_file(tmp_path)
    _seed_entry(cache, sha="abc")
    assert cache_age_days("xyz", cache_path=cache) is None


# ---------- cache_is_stale: 90-day boundary ----------

def _ts_days_ago(days: float) -> str:
    when = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_age_89_days_not_stale(tmp_path: Path) -> None:
    cache = _cache_file(tmp_path)
    _seed_entry(cache, sha="abc", last_validated_at=_ts_days_ago(89))
    age = cache_age_days("abc", cache_path=cache)
    assert age is not None
    assert not cache_is_stale(age)


def test_age_90_days_stale(tmp_path: Path) -> None:
    cache = _cache_file(tmp_path)
    _seed_entry(cache, sha="abc", last_validated_at=_ts_days_ago(90.1))
    age = cache_age_days("abc", cache_path=cache)
    assert age is not None
    assert cache_is_stale(age)


def test_age_91_days_stale(tmp_path: Path) -> None:
    cache = _cache_file(tmp_path)
    _seed_entry(cache, sha="abc", last_validated_at=_ts_days_ago(91))
    age = cache_age_days("abc", cache_path=cache)
    assert age is not None
    assert cache_is_stale(age)


# ---------- concurrent writes ----------

def test_concurrent_writes_no_corruption(tmp_path: Path) -> None:
    cache = _cache_file(tmp_path)
    errors: list[Exception] = []

    def write(i: int) -> None:
        try:
            cache_record(
                f"hash-{i:02d}",
                "safe",
                "none",
                f"cmd-{i}",
                cache_path=cache,
            )
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    # All 10 entries present.
    found = [cache_lookup(f"hash-{i:02d}", cache_path=cache) for i in range(10)]
    assert all(e is not None for e in found), found


# ---------- env override ----------

def test_cccs_cache_path_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "via-env.csv"
    monkeypatch.setenv("CCCS_CACHE_PATH", str(cache))
    cache_record("abc", "safe", "none", "ls")
    assert cache.exists()
    entry = cache_lookup("abc")
    assert entry is not None
