"""Multi-section markdown report covering all dimensions for a date range."""

from __future__ import annotations

import io

import pandas as pd

from . import ccusage_wrapper as cw
from . import query


def render_report(
    df: pd.DataFrame,
    since: str | None = None,
    until: str | None = None,
    top_n: int = 20,
) -> str:
    """Return a multi-section Markdown report for the given range."""
    out = io.StringIO()
    out.write("# Claude Code usage report\n\n")
    period = _format_period(since, until)
    out.write(f"_{period}_\n\n")

    # Headline totals
    headline = query.run_query(df, since=since, until=until)
    out.write("## Headline\n\n")
    if headline.empty:
        out.write("_No usage in this range._\n\n")
        return out.getvalue()
    h = headline.iloc[0]
    out.write(f"- **Total tokens:** {int(h.total_tokens):,}\n")
    out.write(f"- **Total cost (our estimate):** ${h.cost_usd:,.2f}\n")
    out.write(f"- **Messages:** {int(h.message_count):,}\n")
    out.write(f"- **Sessions:** {int(h.session_count):,}\n")
    out.write(f"- **Tool invocations:** {int(h.tool_call_count):,}\n\n")

    # ccusage reconciliation block
    out.write(_render_ccusage_block(headline.iloc[0], since, until))

    # Section helper
    def section(title: str, group_by: list[str], top: int = top_n,
                sort_by: str = "cost_usd"):
        out.write(f"## {title}\n\n")
        result = query.run_query(df, since=since, until=until, group_by=group_by)
        if result.empty:
            out.write("_No data._\n\n")
            return
        result = result.sort_values(sort_by, ascending=False).head(top)
        out.write(_to_markdown_table(result))
        out.write("\n\n")

    section("By month", ["month"], top=24, sort_by="month")
    section("By project", ["project"])
    section("By model", ["model"])
    section("By MCP server", ["mcp"])
    section("By plugin", ["plugin"])
    section("By tool", ["tool"])
    section("By session", ["session"])

    return out.getvalue()


def _format_period(since: str | None, until: str | None) -> str:
    if since and until:
        return f"Period: {since} -> {until}"
    if since:
        return f"Period: from {since}"
    if until:
        return f"Period: until {until}"
    return "Period: all time"


def _render_ccusage_block(
    headline: pd.Series, since: str | None, until: str | None
) -> str:
    if not cw.is_available():
        return (
            "_ccusage CLI not installed - skipping authoritative-cost "
            "reconciliation. `bun add -g ccusage` to enable._\n\n"
        )
    try:
        ccu = cw.run_daily(since=since, until=until, offline=True)
    except Exception as exc:
        return f"_ccusage call failed: {exc}_\n\n"
    ours = {
        "input_tokens": int(headline.input_tokens),
        "output_tokens": int(headline.output_tokens),
        "cache_read_tokens": int(headline.cache_read),
        "cache_creation_tokens": int(
            headline.cache_creation_5m + headline.cache_creation_1h
        ),
        "total_tokens": int(headline.total_tokens),
    }
    theirs = {
        "input_tokens": ccu.totals.input_tokens,
        "output_tokens": ccu.totals.output_tokens,
        "cache_read_tokens": ccu.totals.cache_read_tokens,
        "cache_creation_tokens": ccu.totals.cache_creation_tokens,
        "total_tokens": ccu.totals.total_tokens,
    }
    diff = cw.reconcile_totals(ours, theirs, tolerance=0.005)
    out = io.StringIO()
    out.write("### ccusage reconciliation\n\n")
    out.write(f"- **ccusage total cost (canonical):** ${ccu.totals.total_cost:,.2f}\n")
    out.write(f"- Token reconciliation: max diff {diff.max_relative_diff*100:.4f}%; ")
    out.write(f"{'PASS' if diff.passed else 'FAIL'} (tolerance 0.5%)\n")
    if not diff.passed:
        out.write(f"- Mismatched fields: {', '.join(diff.failed_fields)}\n")
    out.write("\n")
    return out.getvalue()


def _to_markdown_table(df: pd.DataFrame) -> str:
    """Render a DataFrame as a GitHub-flavoured Markdown table."""
    if df.empty:
        return "_No rows._\n"
    cols = list(df.columns)
    lines = []
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
    for _, row in df.iterrows():
        cells = []
        for c in cols:
            v = row[c]
            if isinstance(v, float):
                if c == "cost_usd":
                    cells.append(f"${v:,.2f}")
                else:
                    cells.append(f"{v:,.0f}")
            elif isinstance(v, (int,)):
                cells.append(f"{v:,}")
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"
