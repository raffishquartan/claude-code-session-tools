#!/usr/bin/env python3
"""Curate the bash-security-review command cache.

Reads telemetry.db's telemetry_events table (hook='bash-security-review'),
surfaces safe-verdict commands not yet in the cache with a truncated preview of
each command recovered from its session transcripts (the same recovery as
`ccst telemetry recover`), and records approved ones.

Usage:
    CCST_FIRES_ACCESS=1 python3 update_command_cache.py [list] [--no-preview]
    python3 update_command_cache.py remove <sha> [--yes]
    python3 update_command_cache.py flip <sha> <safe|suspicious|dangerous> [--yes]
    python3 update_command_cache.py promote <sha> [--verdict safe] [--risks ...] [--preview ...]

Must be run through an interpreter that has cc-session-tools installed (`uv run
python3 <path>` in a repo checkout, or the venv `uv tool install` creates) -
`hooks` and `cc_session_tools.lib` are ordinary top-level installed packages,
not importable from a bare system `python3` that never had the package
installed.

`list` refuses to read telemetry.db or transcripts unless CCST_FIRES_ACCESS=1
is set, a convention shared with the bash-hard-deny hook's telemetry-read rule.
"""
from __future__ import annotations

import argparse
import os
import shlex
import sys
from collections import Counter
from pathlib import Path

from hooks.cache import (
    _connect,
    cache_lookup,
    cache_record,
)
from cc_session_tools.lib import command_recovery, telemetry_store


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


def _rerun_command() -> str:
    """The exact invocation that re-runs `list` through this interpreter and script."""
    script = Path(__file__).resolve()
    return f"CCST_FIRES_ACCESS=1 {shlex.quote(sys.executable)} {shlex.quote(str(script))} list"


def _preview(rec: command_recovery.Recovery) -> str:
    if rec.status is command_recovery.RecoveryStatus.COMMAND:
        return "command: " + command_recovery.format_command(
            rec.command, command_recovery.DEFAULT_MAX_CHARS
        )
    return f"status={rec.status.value}"


def cmd_list(args: argparse.Namespace) -> int:
    if os.environ.get("CCST_FIRES_ACCESS") != "1":
        sys.stderr.write(
            "Refusing to read telemetry.db without CCST_FIRES_ACCESS=1.\n"
            f"Re-run as: {_rerun_command()}\n"
        )
        return 2
    rows = read_telemetry_events()
    candidates = collect_candidates(rows)
    if not candidates:
        print("No safe-verdict fires waiting to be promoted to the cache.")
        return 0
    previews: dict[str, str] = {}
    if not args.no_preview:
        shas = [str(row["sha"]) for row in candidates]
        previews = {
            rec.arg: _preview(rec) for rec in command_recovery.recover(shas)
        }
    print(f"Found {len(candidates)} candidate sha(s) waiting for promotion:\n")
    for row in candidates:
        line = (
            f"  sha={str(row['sha'])[:12]}...  fires={row['fire_count']}  "
            f"last_seen={row['last_seen']}  session={row['sample_session']}"
        )
        if not args.no_preview:
            line += f"  {previews[str(row['sha'])]}"
        print(line)
    print(
        "\nPreviews are truncated for triage. Before promoting, show the full command "
        "with `ccst telemetry recover --max-chars 0 <sha>` and pass it to "
        "`promote <full sha> --preview ...`; a status other than `command` means the "
        "plaintext could not be confirmed, so do not promote that candidate on a guess."
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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    # Defaults for the no-subcommand path, which runs `list` with previews.
    p.set_defaults(func=cmd_list, no_preview=False)
    sub = p.add_subparsers(dest="cmd")
    list_p = sub.add_parser("list", help="List safe fires not yet cached, with command previews")
    list_p.add_argument(
        "--no-preview", action="store_true",
        help="Skip recovering command previews (opens no transcript)",
    )
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
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    func = args.func
    rc: int = func(args)
    return rc


if __name__ == "__main__":
    sys.exit(main())
