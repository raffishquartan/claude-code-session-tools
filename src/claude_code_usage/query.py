"""Multi-dimensional query layer over the parsed fact table.

`run_query(df, ...)` is the single entry point. It:

1. Applies row filters (`since`, `until`, `project`, `session`, `model`,
   `mcp`, `plugin`, `tool`).
2. If a tool / mcp / plugin filter or group-by is in play, explodes the
   message-level frame into one row per `tool_use` block with
   evenly-split attributed tokens.
3. Adds a `cost_usd` column.
4. Buckets `ts` into `day`, `week`, `month`, or `year` if requested.
5. Groups by `group_by` and aggregates:
   `total_tokens, input_tokens, output_tokens, cache_read,
    cache_creation_5m, cache_creation_1h, cost_usd, message_count,
    session_count, tool_call_count`.
"""

from __future__ import annotations

from typing import Iterable, Mapping

import pandas as pd

from . import attribution, pricing, session_names as _session_names


TOOL_DIMENSIONS = {"tool", "mcp", "plugin"}
TIME_DIMENSIONS = {"day", "week", "month", "year"}


def run_query(
    df: pd.DataFrame,
    since: str | None = None,
    until: str | None = None,
    project: str | None = None,
    session: str | None = None,
    model: str | None = None,
    mcp: str | None = None,
    plugin: str | None = None,
    tool: str | None = None,
    group_by: Iterable[str] | None = None,
    session_name_map: Mapping[str, str] | None = None,
    include_children: bool = False,
) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    group_by = list(group_by) if group_by else []
    work = df.copy()
    work["ts"] = pd.to_datetime(work["ts"], utc=True, format="ISO8601")

    if since is not None:
        work = work[work["ts"] >= pd.Timestamp(since, tz="UTC")]
    if until is not None:
        work = work[work["ts"] < pd.Timestamp(until, tz="UTC")]
    if project is not None:
        work = work[work["project_name"] == project]
    if session is not None:
        if session_name_map:
            wanted = list(_session_names.resolve_filter(session, dict(session_name_map)))
        else:
            wanted = [session]
        work = work[work["session_id"].isin(wanted)] if wanted else work.iloc[0:0]
    if model is not None:
        work = work[work["model"].str.contains(model, na=False)]

    needs_tool_explode = bool(
        mcp or plugin or tool or (set(group_by) & TOOL_DIMENSIONS)
    )

    if needs_tool_explode:
        work = _explode_by_tools(work)
        if mcp is not None:
            work = work[(work["kind"] == "mcp") & (work["identifier"] == mcp)]
        if plugin is not None:
            work = work[(work["kind"] == "plugin") & (work["identifier"] == plugin)]
        if tool is not None:
            work = work[work["tool"] == tool]
        # Group-by 'mcp' / 'plugin' implicitly filters to the matching kind
        # and groups by `identifier`.
        if "mcp" in group_by:
            work = work[work["kind"] == "mcp"]
        if "plugin" in group_by:
            work = work[work["kind"] == "plugin"]

    work = pricing.add_cost_column(work)
    work = _add_time_buckets(work, group_by)

    result = _aggregate(work, group_by) if group_by else _aggregate(work, [])
    if "session" in group_by:
        result = _add_session_name_column(result, session_name_map or {})
    if include_children and "session" in group_by:
        result = _fold_children(result, work)
    return result


def _explode_by_tools(df: pd.DataFrame) -> pd.DataFrame:
    """Explode message rows into one row per tool_use block.

    Output columns: all original message columns, plus `tool`, `kind`,
    `identifier`. Token columns are scaled by 1/N for each of the N tool
    uses in that message; messages with no tool uses contribute one row
    in the synthetic `<no-tool>` bucket with full tokens.
    """
    rows: list[dict] = []
    for record in df.to_dict("records"):
        raw = record.get("tool_calls")
        tools = list(raw) if raw is not None and len(raw) > 0 else []
        if not tools:
            new = dict(record)
            new["tool"] = attribution.NO_TOOL
            new["kind"] = "no-tool"
            new["identifier"] = attribution.NO_TOOL
            new["tool_call_count"] = 0
            rows.append(new)
            continue
        share = 1.0 / len(tools)
        for name in tools:
            kind, ident = attribution.classify_tool(name)
            new = dict(record)
            new["tool"] = name
            new["kind"] = kind
            new["identifier"] = ident
            new["tool_call_count"] = 1
            for col in (
                "input_tokens",
                "output_tokens",
                "cache_creation_5m",
                "cache_creation_1h",
                "cache_read",
            ):
                new[col] = record[col] * share
            rows.append(new)
    return pd.DataFrame(rows)


def _add_time_buckets(df: pd.DataFrame, group_by: list[str]) -> pd.DataFrame:
    if not (set(group_by) & TIME_DIMENSIONS):
        return df
    out = df.copy()
    if "day" in group_by:
        out["day"] = out["ts"].dt.strftime("%Y-%m-%d")
    if "week" in group_by:
        # ISO week label, e.g. "2026-W18"
        iso = out["ts"].dt.isocalendar()
        out["week"] = iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)
    if "month" in group_by:
        out["month"] = out["ts"].dt.strftime("%Y-%m")
    if "year" in group_by:
        out["year"] = out["ts"].dt.strftime("%Y")
    return out


_DIMENSION_ALIASES = {
    "project": "project_name",
    "session": "session_id",
    "mcp": "identifier",
    "plugin": "identifier",
}


def _resolve_dimensions(group_by: list[str]) -> list[str]:
    return [_DIMENSION_ALIASES.get(d, d) for d in group_by]


def _aggregate(df: pd.DataFrame, group_by: list[str]) -> pd.DataFrame:
    if "tool_call_count" not in df.columns:
        df = df.copy()
        if "tool_calls" in df.columns:
            # Non-exploded path: derive count from the raw list column.
            df["tool_call_count"] = df["tool_calls"].apply(
                lambda xs: len(xs) if xs is not None else 0
            )
        else:
            # Exploded path returned a column-less empty frame (e.g. stale
            # parquet cache missing tool_calls, or empty input after filters).
            df["tool_call_count"] = 0

    agg_spec = {
        "input_tokens": "sum",
        "output_tokens": "sum",
        "cache_creation_5m": "sum",
        "cache_creation_1h": "sum",
        "cache_read": "sum",
        "cost_usd": "sum",
        "session_id": pd.Series.nunique,
        "tool_call_count": "sum",
        "uuid": "count",
    }

    if not group_by:
        out = df.agg(
            {
                **{k: v for k, v in agg_spec.items() if k != "uuid"},
                "uuid": "count",
            }
        ).to_frame().T
    else:
        cols = _resolve_dimensions(group_by)
        # Drop any agg columns that conflict with the groupby keys.
        spec = {k: v for k, v in agg_spec.items() if k not in cols}
        out = df.groupby(cols, dropna=False).agg(spec).reset_index()
        # If session_id was dropped because we grouped on it, distinct
        # session count is just 1 per row.
        if "session_id" in cols and "session_id" not in out.columns:
            out["session_id"] = 1

    out = out.rename(columns={"uuid": "message_count", "session_id": "session_count"})
    out["total_tokens"] = (
        out["input_tokens"]
        + out["output_tokens"]
        + out["cache_creation_5m"]
        + out["cache_creation_1h"]
        + out["cache_read"]
    )
    front = (_resolve_dimensions(group_by) if group_by else []) + [
        "total_tokens",
        "input_tokens",
        "output_tokens",
        "cache_creation_5m",
        "cache_creation_1h",
        "cache_read",
        "cost_usd",
        "message_count",
        "session_count",
        "tool_call_count",
    ]
    return out[[c for c in front if c in out.columns]]


def _fold_children(result: pd.DataFrame, work: pd.DataFrame) -> pd.DataFrame:
    """Fold child session tokens/cost into their parent rows.

    Requires `work` to carry a `parent_session_id` column (added by
    `parent_inference.resolve_parents`). Child sessions (those whose
    `session_id` appears as any row's `parent_session_id`) are removed from
    `result`; their aggregate tokens and cost are added to the parent rows
    as `child_session_count`, `child_token_total`, `child_cost_usd`.
    """
    if "parent_session_id" not in work.columns or "session_count" not in result.columns:
        return result

    child_work = work[work["parent_session_id"].notna()].copy()

    out = result.copy()
    out["child_session_count"] = 0
    out["child_token_total"] = 0
    out["child_cost_usd"] = 0.0

    if child_work.empty:
        return out

    child_work["_row_total"] = (
        child_work["input_tokens"]
        + child_work["output_tokens"]
        + child_work["cache_creation_5m"]
        + child_work["cache_creation_1h"]
        + child_work["cache_read"]
    )
    child_agg = (
        child_work.groupby("parent_session_id")
        .agg(
            _child_sessions=("session_id", "nunique"),
            _child_tokens=("_row_total", "sum"),
            _child_cost=("cost_usd", "sum"),
        )
        .reset_index()
        .rename(columns={"parent_session_id": "_parent_id"})
    )

    child_uuids = set(child_work["session_id"].unique())
    out = out[~out["session_count"].isin(child_uuids)].copy()

    out = out.merge(child_agg, left_on="session_count", right_on="_parent_id", how="left")
    out["child_session_count"] = out["_child_sessions"].fillna(0).astype(int)
    out["child_token_total"] = out["_child_tokens"].fillna(0).astype(int)
    out["child_cost_usd"] = out["_child_cost"].fillna(0.0)
    return out.drop(columns=["_child_sessions", "_child_tokens", "_child_cost", "_parent_id"], errors="ignore")


def _add_session_name_column(df: pd.DataFrame, name_map: Mapping[str, str]) -> pd.DataFrame:
    """Attach a `session_name` column when `session_count` holds UUIDs.

    When `group_by` includes `session`, the aggregator renames the
    `session_id` group key to `session_count` (so its values are UUIDs,
    not counts - quirky but back-compat). We add the human-readable
    name as a sibling column and put it immediately before the UUID
    column so default `--session-format=name` output reads top-to-bottom.
    """
    if "session_count" not in df.columns or df.empty:
        return df
    out = df.copy()
    out["session_name"] = out["session_count"].map(
        lambda uid: name_map.get(uid) or _session_names.fallback_name(uid)
    )
    cols = list(out.columns)
    cols.remove("session_name")
    insert_at = cols.index("session_count")
    cols.insert(insert_at, "session_name")
    return out[cols]
