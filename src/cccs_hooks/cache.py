"""SQLite-backed command cache for bash-security-review.

Keys:
  exact_hash  — SHA-256 of the exact command string (PRIMARY KEY)
  norm_hash   — SHA-256 of the normalised command form (secondary index, NULLable)

Only 'safe' verdicts are stored. Auto-prunes entries older than 90 days on
every write. WAL mode is set on open for concurrent read safety.

Timestamp format: ISO-8601 with T separator and Z suffix (%Y-%m-%dT%H:%M:%SZ).
The prune DELETE uses strftime() to produce a cutoff in the same format so the
text comparison works correctly — bare datetime('now', '-90 days') returns
'YYYY-MM-DD HH:MM:SS' (no T, no Z) and would never match stored rows.

DB path: CCCS_CACHE_DB env var, else ~/.cache/claude/logs/command-cache.db

Note: cache_revalidate from the previous CSV implementation is intentionally
absent. Stale entries are pruned automatically on every cache_record() write
(DELETE WHERE validated_at < 90 days ago). There are no valid stale entries
to revalidate.
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import os
import sqlite3
from pathlib import Path

_DEFAULT_DB = Path.home() / ".cache" / "claude" / "logs" / "command-cache.db"
_STALE_DAYS = 90.0

_DDL = """
CREATE TABLE IF NOT EXISTS command_cache (
    exact_hash    TEXT PRIMARY KEY,
    norm_hash     TEXT,
    verdict       TEXT    NOT NULL,
    risks_summary TEXT    NOT NULL,
    preview       TEXT    NOT NULL,
    fire_count    INTEGER NOT NULL DEFAULT 1,
    last_seen     TEXT    NOT NULL,
    validated_at  TEXT    NOT NULL,
    cache_source  TEXT    NOT NULL DEFAULT 'auto'
);
CREATE INDEX IF NOT EXISTS idx_norm ON command_cache(norm_hash)
    WHERE norm_hash IS NOT NULL;
CREATE TABLE IF NOT EXISTS hook_invocations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT    NOT NULL,
    session_id      TEXT,
    tool_name       TEXT    NOT NULL DEFAULT 'Bash',
    exit_tier       INTEGER NOT NULL,
    heuristic_fired INTEGER NOT NULL DEFAULT 0,
    heuristic_names TEXT,
    verdict         TEXT    NOT NULL,
    cache_source    TEXT,
    exact_hash      TEXT,
    ms_elapsed      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_inv_ts      ON hook_invocations(ts);
CREATE INDEX IF NOT EXISTS idx_inv_session ON hook_invocations(session_id);
CREATE VIEW IF NOT EXISTS cache_efficiency AS
SELECT
    DATE(ts)                                                 AS day,
    COUNT(*)                                                 AS total,
    SUM(exit_tier = 0)                                       AS trivial,
    SUM(exit_tier = 2)                                       AS cached,
    SUM(exit_tier = 3)                                       AS claude_calls,
    SUM(heuristic_fired)                                     AS heuristic_escalations,
    ROUND(100.0 * SUM(exit_tier = 2) / COUNT(*), 1)         AS cache_hit_pct,
    ROUND(AVG(CASE WHEN exit_tier=3 THEN ms_elapsed END), 0) AS avg_claude_ms
FROM hook_invocations
GROUP BY DATE(ts);
"""


@dataclasses.dataclass(frozen=True, slots=True)
class CacheEntry:
    exact_hash: str
    norm_hash: str | None
    verdict: str
    risks_summary: str
    command_preview: str
    fire_count: int
    last_seen: str
    last_validated_at: str
    cache_source: str


def _db_path() -> Path:
    env = os.environ.get("CCCS_CACHE_DB", "").strip()
    return Path(env) if env else _DEFAULT_DB


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = sqlite3.connect(str(path), timeout=5.0, check_same_thread=False)
    if sqlite3.sqlite_version_info < (3, 35, 0):
        raise RuntimeError(
            f"SQLite >= 3.35.0 required (got {sqlite3.sqlite_version}); "
            "'CREATE VIEW IF NOT EXISTS' is not supported on older versions."
        )
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_DDL)
    conn.commit()
    return conn


def sha256_command(command: str) -> str:
    return hashlib.sha256(command.encode()).hexdigest()


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_to_entry(row: tuple) -> CacheEntry:
    exact, norm, verdict, risks, preview, fires, last_seen, validated_at, source = row
    return CacheEntry(
        exact_hash=exact,
        norm_hash=norm,
        verdict=verdict,
        risks_summary=risks,
        command_preview=preview,
        fire_count=fires,
        last_seen=last_seen,
        last_validated_at=validated_at,
        cache_source=source,
    )


def cache_lookup(exact_sha: str, norm_sha: str | None = None) -> CacheEntry | None:
    """Return entry if exact_sha or norm_sha hits a fresh (non-stale) cache row."""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT exact_hash,norm_hash,verdict,risks_summary,preview,"
                "fire_count,last_seen,validated_at,cache_source "
                "FROM command_cache WHERE exact_hash=?",
                (exact_sha,),
            ).fetchone()
            if row is None and norm_sha:
                row = conn.execute(
                    "SELECT exact_hash,norm_hash,verdict,risks_summary,preview,"
                    "fire_count,last_seen,validated_at,cache_source "
                    "FROM command_cache WHERE norm_hash=? LIMIT 1",
                    (norm_sha,),
                ).fetchone()
            if row is None:
                return None
            entry = _row_to_entry(row)
            if cache_is_stale(cache_age_days_from_entry(entry)):
                return None
            return entry
    except sqlite3.Error:
        return None


def cache_record(
    exact_sha: str,
    verdict: str,
    risks_summary: str,
    command_preview: str,
    *,
    norm_sha: str | None = None,
) -> None:
    """Insert or update a cache entry. Only 'safe' verdicts are stored."""
    if verdict != "safe":
        return
    now = _now()
    try:
        with _connect() as conn:
            conn.execute(
                """INSERT INTO command_cache
                   (exact_hash,norm_hash,verdict,risks_summary,preview,
                    fire_count,last_seen,validated_at,cache_source)
                   VALUES (?,?,?,?,?,1,?,?,'auto')
                   ON CONFLICT(exact_hash) DO UPDATE SET
                       fire_count=fire_count+1,
                       last_seen=excluded.last_seen,
                       validated_at=excluded.validated_at,
                       norm_hash=coalesce(excluded.norm_hash, norm_hash)
                """,
                (exact_sha, norm_sha, verdict, risks_summary, command_preview, now, now),
            )
            _prune_stale(conn)
            conn.commit()
    except sqlite3.Error:
        pass  # never raise from cache — treat as no-op


def invocations_record(
    exit_tier: int,
    verdict: str,
    *,
    session_id: str | None = None,
    tool_name: str = "Bash",
    heuristic_fired: bool = False,
    heuristic_names: list[str] | None = None,
    cache_source: str | None = None,
    exact_hash: str | None = None,
    ms_elapsed: int | None = None,
) -> None:
    """Record one hook invocation for analytics. Never raises."""
    names_json = json.dumps(heuristic_names) if heuristic_names else None
    now = _now()
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO hook_invocations "
                "(ts,session_id,tool_name,exit_tier,heuristic_fired,"
                "heuristic_names,verdict,cache_source,exact_hash,ms_elapsed) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (now, session_id, tool_name, exit_tier,
                 1 if heuristic_fired else 0, names_json,
                 verdict, cache_source, exact_hash, ms_elapsed),
            )
            conn.commit()
    except sqlite3.Error:
        pass


def _prune_stale(conn: sqlite3.Connection) -> None:
    """Delete entries older than _STALE_DAYS from both cache tables.

    Uses strftime() to produce a cutoff string in %Y-%m-%dT%H:%M:%SZ format,
    matching the stored ts/validated_at format. bare datetime('now', '-90 days')
    returns 'YYYY-MM-DD HH:MM:SS' (no T, no Z) and would never match stored
    rows in a text comparison.
    """
    cutoff_expr = "strftime('%Y-%m-%dT%H:%M:%SZ', datetime('now', ?))"
    cutoff_param = (f"-{int(_STALE_DAYS)} days",)
    conn.execute(
        f"DELETE FROM command_cache WHERE validated_at < {cutoff_expr}",
        cutoff_param,
    )
    conn.execute(
        f"DELETE FROM hook_invocations WHERE ts < {cutoff_expr}",
        cutoff_param,
    )


def cache_age_days(sha: str) -> float | None:
    """Return age of entry in days, or None if not found."""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT validated_at FROM command_cache WHERE exact_hash=?", (sha,)
            ).fetchone()
        if row is None:
            return None
        return _days_since(row[0])
    except sqlite3.Error:
        return None


def cache_age_days_from_entry(entry: CacheEntry) -> float | None:
    return _days_since(entry.last_validated_at)


def _days_since(ts: str) -> float:
    try:
        dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return (datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds() / 86400
    except ValueError:
        return float("inf")


def cache_is_stale(age_days: float | None) -> bool:
    if age_days is None:
        return True
    return age_days >= _STALE_DAYS
