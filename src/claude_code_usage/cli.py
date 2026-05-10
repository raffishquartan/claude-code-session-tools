"""Command-line interface.

Usage:

    claude-code-usage query [filters] [--group-by ...] [--format markdown|csv|json]
    claude-code-usage report [--since ...] [--until ...] [--output PATH]
    claude-code-usage warm-cache
    claude-code-usage reconcile

All sub-commands accept `--projects-dir` (default `~/.claude/projects`)
and `--cache-dir` (default in the user cache dir).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import platformdirs

from . import (
    __version__,
    cache,
    ccusage_wrapper as cw,
    parent_inference,
    query,
    report,
    session_names as _session_names,
)


DEFAULT_PROJECTS = Path.home() / ".claude" / "projects"
DEFAULT_CACHE = (
    Path(platformdirs.user_cache_dir("claude-code-usage", "raffishquartan"))
    / "parquet"
)


def _common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--projects-dir",
        default=str(DEFAULT_PROJECTS),
        help=f"Path to ~/.claude/projects (default: {DEFAULT_PROJECTS})",
    )
    p.add_argument(
        "--cache-dir",
        default=str(DEFAULT_CACHE),
        help=f"Path to the parquet cache (default: {DEFAULT_CACHE})",
    )


def _filter_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--since")
    p.add_argument("--until")
    p.add_argument("--project")
    p.add_argument("--session")
    p.add_argument("--model")
    p.add_argument("--mcp")
    p.add_argument("--plugin")
    p.add_argument("--tool")


def _make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="claude-code-usage")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("query", help="Run a multi-dimensional query.")
    _common_args(q)
    _filter_args(q)
    q.add_argument(
        "--group-by",
        default="",
        help="Comma-separated dimensions: project,session,model,mcp,plugin,tool,day,week,month,year",
    )
    q.add_argument(
        "--format", choices=["markdown", "csv", "json"], default="markdown"
    )
    q.add_argument("--top", type=int, default=20)
    q.add_argument(
        "--sort", default="cost_usd",
        help="Column to sort by (default: cost_usd)",
    )
    q.add_argument(
        "--include-children",
        action="store_true",
        default=False,
        help=(
            "When grouping by session, fold child session tokens/cost into the "
            "parent row and add child_session_count, child_token_total, child_cost_usd "
            "columns. Child session rows are removed from the output."
        ),
    )
    excl = q.add_mutually_exclusive_group()
    excl.add_argument(
        "--exclude-hooks",
        action="store_true",
        default=False,
        help=(
            "Exclude hook-security-review sessions (bash-security-review.sh fires) "
            "from all results. Useful for per-session cost breakdowns without the "
            "~$1.60 hook sessions distorting the totals."
        ),
    )
    excl.add_argument(
        "--include-hooks",
        action="store_true",
        default=False,
        help="Include hook sessions (default behaviour; alias for not passing --exclude-hooks).",
    )
    q.add_argument(
        "--session-format",
        choices=["name", "uuid", "both"],
        default="name",
        help=(
            "How to render the session column when grouping by session. "
            "name (default) shows the display name set via `claude -n` or "
            "ccd; uuid shows the full UUID; both shows name and UUID. "
            "Unknown sessions fall back to `sess-<uuid8>`."
        ),
    )

    r = sub.add_parser("report", help="Render a full multi-section markdown report.")
    _common_args(r)
    r.add_argument("--since")
    r.add_argument("--until")
    r.add_argument(
        "--output", default="-",
        help="Output file path (default: stdout)",
    )
    r.add_argument("--top", type=int, default=20)

    ch = sub.add_parser(
        "children",
        help="List child sessions (hook/subagent) of a given parent session.",
    )
    _common_args(ch)
    ch.add_argument(
        "parent_session",
        help="Parent session UUID or display-name substring.",
    )
    ch.add_argument(
        "--format", choices=["markdown", "csv", "json"], default="markdown"
    )
    ch.add_argument("--top", type=int, default=20)
    ch.add_argument("--sort", default="cost_usd")

    w = sub.add_parser("warm-cache", help="Populate / refresh the parquet cache.")
    _common_args(w)

    rec = sub.add_parser(
        "reconcile",
        help="Compare our totals against ccusage's authoritative figures.",
    )
    _common_args(rec)
    rec.add_argument("--since")
    rec.add_argument("--until")

    return p


def _load_df(args) -> "pd.DataFrame":
    c = cache.Cache(args.cache_dir)
    return c.load_or_parse(args.projects_dir)


def _format_df(df, fmt: str) -> str:
    if fmt == "csv":
        return df.to_csv(index=False)
    if fmt == "json":
        return df.to_json(orient="records", indent=2)
    return report._to_markdown_table(df)


def _load_df_with_parents(args) -> "tuple[pd.DataFrame, dict[str, str]]":
    """Load the fact table and enrich it with parent_session_id."""
    import pandas as pd  # local import keeps top-level clean
    df = _load_df(args)
    name_map = _session_names.update_persistent_cache(
        Path(args.cache_dir) / "session_names.json"
    )
    df = parent_inference.resolve_parents(df, name_map)
    return df, name_map


def _cmd_query(args) -> int:
    df, name_map = _load_df_with_parents(args)
    if getattr(args, "exclude_hooks", False) and "initiation_type" in df.columns:
        df = df[df["initiation_type"] != "hook-security-review"]
    group_by = [s.strip() for s in args.group_by.split(",") if s.strip()]
    result = query.run_query(
        df,
        since=args.since,
        until=args.until,
        project=args.project,
        session=args.session,
        model=args.model,
        mcp=args.mcp,
        plugin=args.plugin,
        tool=args.tool,
        group_by=group_by,
        session_name_map=name_map,
        include_children=args.include_children,
    )
    if not result.empty and args.sort in result.columns:
        result = result.sort_values(args.sort, ascending=False)
    if args.top and len(result) > args.top:
        result = result.head(args.top)
    result = _apply_session_format(result, args.session_format)
    sys.stdout.write(_format_df(result, args.format))
    if not _format_df(result, args.format).endswith("\n"):
        sys.stdout.write("\n")
    return 0


def _apply_session_format(df, fmt: str):
    """Show / hide / merge `session_name` and `session_count` per --session-format."""
    if "session_name" not in df.columns or "session_count" not in df.columns:
        return df
    out = df.copy()
    if fmt == "uuid":
        return out.drop(columns=["session_name"])
    if fmt == "name":
        out = out.drop(columns=["session_count"])
        return out.rename(columns={"session_name": "session"})
    if fmt == "both":
        out = out.rename(columns={"session_count": "session_id"})
        return out
    return out


def _cmd_children(args) -> int:
    df, name_map = _load_df_with_parents(args)
    if df.empty or "parent_session_id" not in df.columns:
        sys.stdout.write("No sessions found.\n")
        return 0

    parent_session = args.parent_session
    # Resolve the parent UUID (accept UUID or name substring)
    wanted = list(_session_names.resolve_filter(parent_session, dict(name_map)))
    if not wanted:
        wanted = [parent_session]

    child_rows = df[df["parent_session_id"].isin(wanted)]
    if child_rows.empty:
        sys.stdout.write("No child sessions found for the given parent.\n")
        return 0

    result = query.run_query(
        child_rows,
        group_by=["session"],
        session_name_map=name_map,
    )
    if not result.empty and args.sort in result.columns:
        result = result.sort_values(args.sort, ascending=False)
    if args.top and len(result) > args.top:
        result = result.head(args.top)
    result = _apply_session_format(result, "name")
    sys.stdout.write(_format_df(result, args.format))
    if not _format_df(result, args.format).endswith("\n"):
        sys.stdout.write("\n")
    return 0


def _cmd_report(args) -> int:
    df = _load_df(args)
    md = report.render_report(df, since=args.since, until=args.until, top_n=args.top)
    if args.output == "-":
        sys.stdout.write(md)
    else:
        Path(args.output).write_text(md)
        sys.stdout.write(f"Wrote report to {args.output}\n")
    return 0


def _cmd_warm_cache(args) -> int:
    c = cache.Cache(args.cache_dir)
    df = c.load_or_parse(args.projects_dir)
    _session_names.update_persistent_cache(
        Path(args.cache_dir) / "session_names.json",
        projects_dir=args.projects_dir,
    )
    sys.stdout.write(f"Cache warmed: {len(df):,} rows\n")
    return 0


def _cmd_reconcile(args) -> int:
    df = _load_df(args)
    headline = query.run_query(df, since=args.since, until=args.until)
    if headline.empty:
        sys.stdout.write("No usage in this range.\n")
        return 0
    h = headline.iloc[0]
    if not cw.is_available():
        sys.stdout.write(
            "ccusage CLI not installed. Install with: bun add -g ccusage\n"
        )
        return 1
    ccu = cw.run_daily(since=args.since, until=args.until, offline=True)
    ours = {
        "input_tokens": int(h.input_tokens),
        "output_tokens": int(h.output_tokens),
        "cache_read_tokens": int(h.cache_read),
        "cache_creation_tokens": int(h.cache_creation_5m + h.cache_creation_1h),
        "total_tokens": int(h.total_tokens),
    }
    theirs = {
        "input_tokens": ccu.totals.input_tokens,
        "output_tokens": ccu.totals.output_tokens,
        "cache_read_tokens": ccu.totals.cache_read_tokens,
        "cache_creation_tokens": ccu.totals.cache_creation_tokens,
        "total_tokens": ccu.totals.total_tokens,
    }
    diff = cw.reconcile_totals(ours, theirs, tolerance=0.005)
    sys.stdout.write(f"Our cost (estimate): ${h.cost_usd:,.2f}\n")
    sys.stdout.write(f"ccusage cost (canonical): ${ccu.totals.total_cost:,.2f}\n")
    sys.stdout.write(f"Token reconciliation max diff: {diff.max_relative_diff*100:.4f}% ")
    sys.stdout.write("PASS\n" if diff.passed else "FAIL\n")
    if not diff.passed:
        sys.stdout.write(f"Failed fields: {', '.join(diff.failed_fields)}\n")
    return 0 if diff.passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = _make_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    handlers = {
        "query": _cmd_query,
        "children": _cmd_children,
        "report": _cmd_report,
        "warm-cache": _cmd_warm_cache,
        "reconcile": _cmd_reconcile,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
