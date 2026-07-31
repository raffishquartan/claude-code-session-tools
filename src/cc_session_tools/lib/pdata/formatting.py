"""Output rendering for ccst pdata list/query/get: table, json, csv — plus a human-readable
current-vs-attempted conflict diff for update (spec §6.2)."""
from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping

_FORMATS = ("table", "json", "csv")


def render(rows: list[dict[str, object]], *, fmt: str) -> str:
    if fmt not in _FORMATS:
        raise ValueError(f"invalid format {fmt!r}: must be one of {', '.join(_FORMATS)}")
    if fmt == "json":
        return json.dumps(rows)
    if not rows:
        return "No rows." if fmt == "table" else ""
    if fmt == "csv":
        return _render_csv(rows)
    return _render_table(rows)


def _render_csv(rows: list[dict[str, object]]) -> str:
    fieldnames = list(rows[0].keys())
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: ("" if v is None else v) for k, v in row.items()})
    return buf.getvalue()


def _render_table(rows: list[dict[str, object]]) -> str:
    fieldnames = list(rows[0].keys())
    str_rows = [
        {k: ("" if row.get(k) is None else str(row.get(k))) for k in fieldnames}
        for row in rows
    ]
    widths = {k: max(len(k), max(len(r[k]) for r in str_rows)) for k in fieldnames}
    header = "  ".join(k.ljust(widths[k]) for k in fieldnames)
    sep = "  ".join("-" * widths[k] for k in fieldnames)
    lines = [header, sep]
    lines += ["  ".join(r[k].ljust(widths[k]) for k in fieldnames) for r in str_rows]
    return "\n".join(lines)


def render_conflict_diff(
    current: Mapping[str, object], attempted: Mapping[str, object], *, fmt: str,
) -> str:
    """current-vs-attempted diff for an update()/delete() version conflict (spec §6.2)."""
    if fmt == "json":
        return json.dumps({"current": current, "attempted": attempted})
    lines = [f"version conflict on record {current.get('id')}:"]
    all_keys = sorted(set(current) | set(attempted))
    for key in all_keys:
        cur_val = current.get(key)
        att_val = attempted.get(key)
        if key in attempted and cur_val != att_val:
            lines.append(f"  {key}: current={cur_val!r} attempted={att_val!r}")
    return "\n".join(lines)
