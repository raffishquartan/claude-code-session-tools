#!/usr/bin/env python3
"""Measure the persistent context footprint and write a ranked report.

Generated/maintained as part of the reduce-persistent-context skill.
Run from the skill directory so the sibling-module imports resolve:
    cd ~/.claude/skills/reduce-persistent-context
    python3 analyze_context.py --captured <path> --out <dir> \
        --project-root <path to the project being audited>
"""
import argparse
from datetime import date, timedelta
from pathlib import Path

import measure
import usage
import duplicates
import report

WINDOW_DAYS = 90


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--claude-home", default=str(Path.home() / ".claude"))
    ap.add_argument("--captured", required=True)
    ap.add_argument("--out", required=True)
    # No cwd-based default: Step 2 of SKILL.md always `cd`s into the skill
    # directory first so sibling imports resolve, so os.getcwd() at runtime
    # would be this skill's own directory, never the audited project. Callers
    # must pass the audited project's root explicitly; omitting it just skips
    # the project-CLAUDE.md row (same "absent file" behaviour as the global one).
    ap.add_argument("--project-root", default=None,
                    help="Root of the project being audited, for its CLAUDE.md")
    ap.add_argument("--since", default=None,
                    help="ISO date for the usage window; default 90 days ago")
    args = ap.parse_args()

    home = Path(args.claude_home)
    recs = [measure.measure_harness_baseline()]
    recs += measure.measure_claude_md(home)
    if args.project_root is not None:
        recs += measure.measure_project_claude_md(Path(args.project_root))
    recs += measure.measure_skill_descriptions(home / "skills")
    recs += measure.measure_captured(Path(args.captured))

    since = args.since
    if since is None:
        since = (date.today() - timedelta(days=WINDOW_DAYS)).isoformat()
    usage_map: dict[str, int] = {}
    for dim in ("mcp", "plugin", "tool"):
        usage_map.update(usage.query_usage(group_by=dim, since=since))

    dup_pairs = duplicates.find_duplicate_pairs(home / "skills")
    ranked = report.rank(recs, usage_map, dup_pairs)
    total = sum(r.tokens for r in recs)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "context-report.json").write_text(report.render_json(ranked, total))
    (out / "context-report.md").write_text(report.render_markdown(ranked, total))
    print(f"Wrote report for {total:,} persistent tokens to {out}")


if __name__ == "__main__":
    main()
