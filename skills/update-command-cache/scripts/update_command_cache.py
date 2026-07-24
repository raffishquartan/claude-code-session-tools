#!/usr/bin/env python3
# Copyright (c) 2026 raffishquartan. All rights reserved.
# Licensed for personal use only.

"""Curate the bash-security-review command cache.

Reads telemetry.db's telemetry_events table (hook='bash-security-review'),
surfaces safe-verdict commands not yet in the cache, prompts for approval,
and records approved ones.

Usage:
    CCCS_FIRES_ACCESS=1 python3 update_command_cache.py [--list]
    python3 update_command_cache.py --remove <sha>
    python3 update_command_cache.py --flip <sha> <verdict>

The CCCS_FIRES_ACCESS=1 env var is required to read telemetry.db through the
bash-hard-deny hook's allowlist.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

# Make cccs_hooks / cc_session_tools importable when running from the skill dir.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from cccs_hooks.cache import (  # noqa: E402
    _connect,
    cache_lookup,
    cache_record,
)
from cc_session_tools.lib import telemetry_store  # noqa: E402

_DEFAULT_PREVIEW_LIMIT = 200


def read_telemetry_events(hooks_dir: Path | None = None) -> list[dict[str, object]]:
    conn = telemetry_store.connect(hooks_dir)
    try:
        rows = conn.execute(
            "SELECT * FROM telemetry_events WHERE hook = 'bash-security-review' ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _strip_sha_prefix(input_hash: str) -> str:
    return input_hash[len("sha256:") :] if input_hash.startswith("sha256:") else input_hash


def _delete_entry(sha: str) -> None:
    """Delete one cache row by exact_hash.

    The SQLite cache stores only 'safe' verdicts and auto-prunes on write, so an
    explicit remove (and a flip to a non-safe verdict) is just a row delete.
    """
    with _connect() as conn:
        conn.execute("DELETE FROM command_cache WHERE exact_hash=?", (sha,))
        conn.commit()


def collect_candidates(fires: list[dict[str, object]]) -> list[dict[str, object]]:
    """Return one row per (sha) for safe fires that escalated to claude.

    Each row carries: sha, verdict, fire_count, last_seen, sample_session_id.
    """
    counts: Counter[str] = Counter()
    last_seen: dict[str, str] = {}
    sample_session: dict[str, str] = {}
    for entry in fires:
        # read_telemetry_events() already filters to hook='bash-security-review'
        # in SQL, so no hook check here (validate once, trust afterwards).
        if entry.get("verdict") != "safe":
            continue
        if entry.get("cache") == "hit":
            continue  # already cached
        sha = _strip_sha_prefix(str(entry.get("input_hash", "")))
        if not sha:
            continue
        counts[sha] += 1
        ts = str(entry.get("ts", ""))
        if ts > last_seen.get(sha, ""):
            last_seen[sha] = ts
        sample_session.setdefault(sha, str(entry.get("session_id", "")))
    rows: list[dict[str, object]] = []
    for sha, count in counts.most_common():
        if cache_lookup(sha) is not None:
            continue
        rows.append(
            {
                "sha": sha,
                "fire_count": count,
                "last_seen": last_seen[sha],
                "sample_session": sample_session[sha],
            }
        )
    return rows


def cmd_list(args: argparse.Namespace) -> int:
    if os.environ.get("CCCS_FIRES_ACCESS") != "1":
        sys.stderr.write(
            "Refusing to read telemetry.db without CCCS_FIRES_ACCESS=1.\n"
            "Re-run as: CCCS_FIRES_ACCESS=1 python3 update_command_cache.py\n"
        )
        return 2
    rows = read_telemetry_events()
    candidates = collect_candidates(rows)
    if not candidates:
        print("No safe-verdict fires waiting to be promoted to the cache.")
        return 0
    print(f"Found {len(candidates)} candidate sha(s) waiting for promotion:\n")
    for row in candidates:
        print(
            f"  sha={row['sha'][:12]}...  fires={row['fire_count']}  "
            f"last_seen={row['last_seen']}  session={row['sample_session']}"
        )
    print(
        "\nNote: this script does not yet have the original commands in plain "
        "text - telemetry.db only stores hashes. The Claude Code skill consumer "
        "of this script is expected to walk this list, retrieve the originals "
        "from session transcripts, and call cache_record() for each approved "
        "entry."
    )
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    sha = args.sha
    entry = cache_lookup(sha)
    if entry is None:
        print(f"No cache entry for sha={sha}.")
        return 1
    print(f"Will remove: hash={sha} verdict={entry.verdict} preview={entry.command_preview}")
    if not args.yes:
        ans = input("Remove? [y/N] ").strip().lower()
        if ans != "y":
            print("Aborted.")
            return 1
    _delete_entry(sha)
    print("Removed.")
    return 0


def cmd_flip(args: argparse.Namespace) -> int:
    sha = args.sha
    new_verdict = args.verdict
    if new_verdict not in {"safe", "suspicious", "dangerous"}:
        sys.stderr.write("verdict must be one of safe, suspicious, dangerous\n")
        return 2
    entry = cache_lookup(sha)
    if entry is None:
        print(f"No cache entry for sha={sha}.")
        return 1
    print(
        f"Will flip: hash={sha} {entry.verdict} -> {new_verdict} "
        f"preview={entry.command_preview}"
    )
    if not args.yes:
        ans = input("Flip? [y/N] ").strip().lower()
        if ans != "y":
            print("Aborted.")
            return 1
    if new_verdict == "safe":
        cache_record(sha, "safe", entry.risks_summary, entry.command_preview)
        print("Refreshed last_validated_at.")
    else:
        _delete_entry(sha)
        print("Removed (verdict no longer safe).")
    return 0


def cmd_promote(args: argparse.Namespace) -> int:
    """Direct promote: hash + verdict + preview + risks. Used by skill consumers."""
    if args.verdict != "safe":
        sys.stderr.write("Only 'safe' verdicts may be promoted to the cache.\n")
        return 2
    cache_record(args.sha, "safe", args.risks, args.preview)
    print(f"Recorded {args.sha[:12]}... in cache.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd")
    list_p = sub.add_parser("list", help="List safe fires not yet cached")
    list_p.set_defaults(func=cmd_list)
    rm_p = sub.add_parser("remove", help="Remove a cache entry by sha")
    rm_p.add_argument("sha")
    rm_p.add_argument("-y", "--yes", action="store_true")
    rm_p.set_defaults(func=cmd_remove)
    flip_p = sub.add_parser("flip", help="Re-validate or evict a cache entry")
    flip_p.add_argument("sha")
    flip_p.add_argument("verdict", choices=("safe", "suspicious", "dangerous"))
    flip_p.add_argument("-y", "--yes", action="store_true")
    flip_p.set_defaults(func=cmd_flip)
    promote_p = sub.add_parser("promote", help="Record a single safe fire")
    promote_p.add_argument("sha")
    promote_p.add_argument("--verdict", default="safe")
    promote_p.add_argument("--risks", default="none")
    promote_p.add_argument("--preview", default="")
    promote_p.set_defaults(func=cmd_promote)
    args = p.parse_args(argv)
    if args.cmd is None:
        return cmd_list(args)
    func = args.func
    rc: int = func(args)
    return rc


if __name__ == "__main__":
    sys.exit(main())
