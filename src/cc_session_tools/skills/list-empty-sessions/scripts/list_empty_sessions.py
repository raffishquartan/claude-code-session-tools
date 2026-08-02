#!/usr/bin/env python3
"""List Claude Code sessions that contain no user-typed messages.

Thin wrapper around `ccs --emptiness only [--global]`. All discovery and
emptiness logic lives in cc_session_tools; this script reformats the output
and appends follow-up suggestions the user can copy-paste.

See ~/.claude/skills/list-empty-sessions/SKILL.md for usage notes.
"""
from __future__ import annotations

import argparse
import subprocess
import sys


def main() -> int:
    ap = argparse.ArgumentParser(
        description="List Claude Code sessions with no user-typed messages."
    )
    ap.add_argument(
        "--global",
        dest="do_global",
        action="store_true",
        help="Search all configured session roots, not just the current project.",
    )
    args = ap.parse_args()

    # Build the ccs command.
    cmd = ["ccs", "--emptiness", "only"]
    if args.do_global:
        cmd.append("--global")

    result = subprocess.run(cmd, capture_output=True, text=True)

    # stderr carries the search-count footer and any warnings — pass through.
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)

    scope_label = "global" if args.do_global else "local"
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]

    if not lines:
        print(f"No empty sessions found ({scope_label}).")
        return 0

    # In global mode ccs already groups output by project; re-print as-is.
    for line in lines:
        print(line)

    count = len(lines)
    print()
    print(f"{count} empty session{'s' if count != 1 else ''} found ({scope_label}).")
    print()

    # Build the basenames list (strip the " (~/...)" project suffix in global
    # mode so the follow-up commands only contain the raw basenames).
    basenames = [ln.split("  (")[0].strip() for ln in lines]

    print("Follow-up commands:")
    print()
    print("  Resume a session (pick one basename):")
    print(f"    ccr {basenames[0]}")
    print()
    print("  Delete all listed empties (review the dry-run plan, then add --execute):")
    delete_cmd = (
        "    python3 ~/.claude/skills/delete-sessions/scripts/delete_sessions.py \\\n"
        + "        "
        + " \\\n        ".join(basenames)
    )
    print(delete_cmd)
    print()
    print("  (Add --execute to the delete command once you have reviewed the plan.)")

    return 0 if result.returncode == 0 else result.returncode


if __name__ == "__main__":
    sys.exit(main())
