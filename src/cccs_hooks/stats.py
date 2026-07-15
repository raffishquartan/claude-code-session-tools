"""Analytics CLI for the bash-security-review hook DB.

Usage: cccs-stats [--days N]

Prints a daily efficiency summary (from the cache_efficiency view) and an
all-time verdict breakdown from hook_invocations.
"""
from __future__ import annotations

import argparse
import sqlite3

from cccs_hooks.cache import _db_path
from cc_session_tools.lib.db import connect as _sqlite_connect


def _connect_readonly() -> sqlite3.Connection | None:
    try:
        return _sqlite_connect(_db_path(), readonly=True)
    except sqlite3.Error:
        return None


def _print_efficiency(conn: sqlite3.Connection, days: int) -> None:
    rows = conn.execute(
        "SELECT day, total, trivial, cached, claude_calls, "
        "heuristic_escalations, cache_hit_pct, avg_claude_ms "
        "FROM cache_efficiency "
        "WHERE day >= DATE('now', ?) "
        "ORDER BY day DESC",
        (f"-{days} days",),
    ).fetchall()
    print(f"\nHook invocations — last {days} days")
    print("=" * 74)
    if not rows:
        print("  (no data)")
        return
    header = f"{'day':<12} {'total':>6} {'trivial':>8} {'cached':>7} {'claude':>7} {'heur_esc':>9} {'hit%':>6} {'avg_ms':>8}"
    print(header)
    print("-" * 74)
    for r in rows:
        avg_ms = f"{int(r['avg_claude_ms'])}" if r["avg_claude_ms"] is not None else "-"
        hit_pct = f"{r['cache_hit_pct']:.1f}%" if r["cache_hit_pct"] is not None else "-"
        print(
            f"{r['day']:<12} {r['total']:>6} {r['trivial']:>8} {r['cached']:>7} "
            f"{r['claude_calls']:>7} {r['heuristic_escalations']:>9} {hit_pct:>6} {avg_ms:>8}"
        )


def _print_verdict_breakdown(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT verdict, COUNT(*) AS n "
        "FROM hook_invocations "
        "GROUP BY verdict "
        "ORDER BY n DESC",
    ).fetchall()
    print("\nVerdict breakdown (all time)")
    print("=" * 30)
    if not rows:
        print("  (no data)")
        return
    for r in rows:
        print(f"  {r['verdict']:<20} {r['n']:>6}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Show bash-security-review hook statistics")
    parser.add_argument("--days", type=int, default=30, help="Days of history to show (default: 30)")
    args = parser.parse_args(argv)

    conn = _connect_readonly()
    if conn is None:
        print("No hook DB found. Run the bash-security-review hook at least once to populate it.")
        return
    try:
        _print_efficiency(conn, args.days)
        _print_verdict_breakdown(conn)
        print()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
