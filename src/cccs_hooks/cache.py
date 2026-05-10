"""SHA-256 command cache for bash-security-review.

Stores reviewed-safe commands in a CSV at ~/.claude/hooks/command-cache.csv so
the bash-security-review hook can skip the claude CLI escalation on repeats.

Cache write rules:
- Only verdict=="safe" entries are auto-recorded (suspicious/dangerous never
  cached).
- 90-day re-validation window. Older entries are stale and trigger another
  claude call on hit.
- flock-protected writes; corruption on read returns a cache miss.

NOTE: v1 uses exact-string SHA-256 hashing. A smarter matching strategy
(e.g. light normalisation of git branch names, file paths, UUIDs) is
planned for v2 but is deferred until we have telemetry data from
~/.claude/hooks/fires.jsonl showing how big the near-miss class actually
is. Do not add normalisation here without that data - the threat model
changes when commands collapse to a shared key.
"""
from __future__ import annotations

import csv
import dataclasses
import datetime
import fcntl
import hashlib
import os
import sys
from collections.abc import Callable
from pathlib import Path

_DEFAULT_CACHE_PATH = Path.home() / ".claude" / "hooks" / "command-cache.csv"
_REVALIDATION_DAYS = 90.0

_CSV_FIELDS = [
    "hash",
    "verdict",
    "risks_summary",
    "command_preview",
    "fire_count",
    "last_seen",
    "last_validated_at",
    "cache_source",
]

_VALID_VERDICTS = frozenset({"safe", "suspicious", "dangerous"})
_VALID_SOURCES = frozenset({"auto", "curated"})


@dataclasses.dataclass(frozen=True, slots=True)
class CacheEntry:
    hash: str
    verdict: str
    risks_summary: str
    command_preview: str
    fire_count: int
    last_seen: str
    last_validated_at: str
    cache_source: str


def _resolve_path(cache_path: Path | None) -> Path:
    if cache_path is not None:
        return cache_path
    env_override = os.environ.get("CCCS_CACHE_PATH")
    if env_override:
        return Path(env_override)
    return _DEFAULT_CACHE_PATH


def sha256_command(command: str) -> str:
    """SHA-256 hex digest of the exact command string."""
    return hashlib.sha256(command.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(ts: str) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _row_to_entry(row: dict[str, str]) -> CacheEntry | None:
    """Validate a CSV row and convert to a CacheEntry, or None if malformed."""
    try:
        for f in _CSV_FIELDS:
            if f not in row or row[f] is None:
                return None
        if row["verdict"] not in _VALID_VERDICTS:
            return None
        if row["cache_source"] not in _VALID_SOURCES:
            return None
        if _parse_iso(row["last_seen"]) is None:
            return None
        if _parse_iso(row["last_validated_at"]) is None:
            return None
        fire_count = int(row["fire_count"])
        if fire_count < 0:
            return None
        if not row["hash"]:
            return None
        return CacheEntry(
            hash=row["hash"],
            verdict=row["verdict"],
            risks_summary=row["risks_summary"],
            command_preview=row["command_preview"],
            fire_count=fire_count,
            last_seen=row["last_seen"],
            last_validated_at=row["last_validated_at"],
            cache_source=row["cache_source"],
        )
    except (ValueError, KeyError, TypeError):
        return None


def _parse_rows_from_text(text: str) -> list[CacheEntry]:
    """Parse the raw CSV text into valid CacheEntry rows; warn on malformed."""
    if not text.strip():
        return []
    entries: list[CacheEntry] = []
    saw_malformed = False
    import io

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or set(_CSV_FIELDS) - set(reader.fieldnames):
        sys.stderr.write(
            "[telemetry-warn] cache: header invalid, treating as empty\n"
        )
        return []
    for row in reader:
        entry = _row_to_entry(row)
        if entry is None:
            saw_malformed = True
            continue
        entries.append(entry)
    if saw_malformed:
        sys.stderr.write("[telemetry-warn] cache: skipped malformed rows\n")
    return entries


def _read_all(path: Path) -> list[CacheEntry]:
    """Read all valid entries from the cache CSV. Malformed rows are skipped."""
    if not path.exists():
        return []
    try:
        with path.open("r", newline="", encoding="utf-8") as f:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            except OSError:
                pass
            try:
                text = f.read()
            finally:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
    except OSError as e:
        sys.stderr.write(f"[telemetry-warn] cache: read failed: {e}\n")
        return []
    return _parse_rows_from_text(text)


def _serialize(entries: list[CacheEntry]) -> str:
    import io

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_FIELDS)
    writer.writeheader()
    for e in entries:
        writer.writerow(dataclasses.asdict(e))
    return buf.getvalue()


def _with_exclusive_lock(
    path: Path,
    mutate: Callable[[list[CacheEntry]], list[CacheEntry]],
) -> None:
    """Acquire an exclusive flock on the cache file, read+mutate+write atomically.

    Uses a sidecar lockfile (.lock) to keep the lock independent of the
    truncate/rewrite cycle on the data file.
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_fd = os.open(str(lock_path), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        # Read current state under the lock.
        current = _read_all(path)
        new = mutate(current)
        text = _serialize(new)
        # Atomic replace: write to temp file then rename.
        tmp = path.with_suffix(path.suffix + ".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, text.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(lock_fd)


def cache_lookup(sha: str, *, cache_path: Path | None = None) -> CacheEntry | None:
    """Return the matching cache entry or None.

    Treats any corruption/IO error as a cache miss. Logs warnings to stderr.
    """
    path = _resolve_path(cache_path)
    try:
        entries = _read_all(path)
    except Exception as exc:  # belt-and-braces; never raise
        sys.stderr.write(f"[telemetry-warn] cache: lookup failed: {exc}\n")
        return None
    for entry in entries:
        if entry.hash == sha:
            return entry
    return None


def cache_record(
    sha: str,
    verdict: str,
    risks_summary: str,
    command_preview: str,
    *,
    cache_path: Path | None = None,
) -> None:
    """Append/update a cache entry. Only safe verdicts are auto-recorded.

    If an entry with the same hash already exists, fire_count is incremented
    and last_seen / last_validated_at are refreshed.
    """
    if verdict != "safe":
        # Auto-fill rule: only safe verdicts go in the cache.
        return
    path = _resolve_path(cache_path)

    def mutate(entries: list[CacheEntry]) -> list[CacheEntry]:
        now = _now_iso()
        updated: list[CacheEntry] = []
        found = False
        for e in entries:
            if e.hash == sha:
                found = True
                updated.append(
                    CacheEntry(
                        hash=sha,
                        verdict=verdict,
                        risks_summary=risks_summary,
                        command_preview=command_preview,
                        fire_count=e.fire_count + 1,
                        last_seen=now,
                        last_validated_at=now,
                        cache_source=e.cache_source,
                    )
                )
            else:
                updated.append(e)
        if not found:
            updated.append(
                CacheEntry(
                    hash=sha,
                    verdict=verdict,
                    risks_summary=risks_summary,
                    command_preview=command_preview,
                    fire_count=1,
                    last_seen=now,
                    last_validated_at=now,
                    cache_source="auto",
                )
            )
        return updated

    try:
        _with_exclusive_lock(path, mutate)
    except OSError as e:
        sys.stderr.write(f"[telemetry-warn] cache: record failed: {e}\n")


def cache_age_days(sha: str, *, cache_path: Path | None = None) -> float | None:
    """Return the age in days of the entry's last_validated_at, or None."""
    entry = cache_lookup(sha, cache_path=cache_path)
    if entry is None:
        return None
    parsed = _parse_iso(entry.last_validated_at)
    if parsed is None:
        return None
    now = datetime.datetime.now(datetime.timezone.utc)
    delta = now - parsed
    return delta.total_seconds() / 86400.0


def cache_is_stale(age_days: float | None) -> bool:
    """True if age >= 90 days (or unknown). 89-day-old entries are still fresh."""
    if age_days is None:
        return True
    return age_days >= _REVALIDATION_DAYS


def cache_revalidate(
    sha: str, new_verdict: str, *, cache_path: Path | None = None
) -> None:
    """Refresh last_validated_at for sha; remove entry if verdict flipped from safe."""
    path = _resolve_path(cache_path)

    def mutate(entries: list[CacheEntry]) -> list[CacheEntry]:
        now = _now_iso()
        updated: list[CacheEntry] = []
        for e in entries:
            if e.hash == sha:
                if new_verdict != "safe":
                    continue
                updated.append(
                    CacheEntry(
                        hash=e.hash,
                        verdict="safe",
                        risks_summary=e.risks_summary,
                        command_preview=e.command_preview,
                        fire_count=e.fire_count,
                        last_seen=e.last_seen,
                        last_validated_at=now,
                        cache_source=e.cache_source,
                    )
                )
            else:
                updated.append(e)
        return updated

    try:
        _with_exclusive_lock(path, mutate)
    except OSError as e:
        sys.stderr.write(f"[telemetry-warn] cache: revalidate failed: {e}\n")
