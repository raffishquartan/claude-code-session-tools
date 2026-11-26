"""`ccst pdata search`: read-only text, regex, --where and date search across one or more record
groups of a project.

Validation lives here in two places only: SearchRequest.__post_init__ checks everything that
needs no store (conflicting or empty flags, regex syntax, date syntax), so a bad request fails
before any file is opened; _plan_groups checks names against each group's schema. Each group is
then searched with one SQL statement that filters, orders and limits; text and regex matching
run inside that statement through a Python function registered on the connection, so only
matching rows are fetched."""
from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from cc_session_tools.lib import db
from cc_session_tools.lib.pdata import naming, repository, service, store

_MATCH_FUNCTION = "pdata_search_match"
_ELLIPSIS = "..."
_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_SEARCHABLE_BASE_COLUMNS = ("content", "file_path")
_DATE_BASE_COLUMNS = ("created_at", "updated_at")
_KEPT_COLUMNS = ("id", "record_group")


def _parse_day(flag: str, raw: str) -> date:
    if _ISO_DAY.fullmatch(raw):
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    raise ValueError(f"{flag} must be an ISO date (yyyy-MM-dd), got {raw!r}")


@dataclass(frozen=True, slots=True)
class SearchRequest:
    """One search, as given on the command line. `fields` and `select` are None when the flag
    was not given; an empty tuple means the flag named nothing and is rejected."""

    project: str
    groups: tuple[str, ...]
    text: str | None = None
    regex: str | None = None
    case_sensitive: bool = False
    not_text: bool = False
    fields: tuple[str, ...] | None = None
    where: tuple[str, ...] = ()
    date_field: str | None = None
    since: str | None = None
    until: str | None = None
    select: tuple[str, ...] | None = None
    snippet: int | None = None
    sort: str | None = None
    desc: bool = False
    limit: int | None = None
    count_only: bool = False
    pattern: re.Pattern[str] | None = field(init=False, default=None)
    conditions: tuple[tuple[str, str, str], ...] = field(init=False, default=())
    since_day: date | None = field(init=False, default=None)
    until_day: date | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        store.validate_project_name(self.project)
        if not self.groups:
            raise ValueError("ccst pdata search needs at least one --group")
        for group in self.groups:
            naming.validate_record_group(group)
        object.__setattr__(self, "groups", tuple(dict.fromkeys(self.groups)))

        if self.text is not None and self.regex is not None:
            raise ValueError("give --text or --regex, not both")
        if self.text == "" or self.regex == "":
            raise ValueError("--text and --regex must not be empty")
        has_text = self.text is not None or self.regex is not None
        for flag, given in (
            ("--case-sensitive", self.case_sensitive), ("--not-text", self.not_text),
            ("--fields", self.fields is not None), ("--snippet", self.snippet is not None),
        ):
            if given and not has_text:
                raise ValueError(f"{flag} needs --text or --regex")
        if not has_text and not self.where and self.since is None and self.until is None:
            raise ValueError(
                "ccst pdata search requires a predicate: --text, --regex, --where, "
                "--since or --until"
            )
        if self.fields is not None and not self.fields:
            raise ValueError("--fields names no field")
        if self.select is not None and not self.select:
            raise ValueError("--select names no field")
        if self.limit is not None and self.limit < 1:
            raise ValueError(f"--limit must be a positive integer, got {self.limit}")
        if self.snippet is not None and self.snippet < 1:
            raise ValueError(f"--snippet must be a positive integer, got {self.snippet}")
        if self.snippet is not None and (self.not_text or self.count_only):
            raise ValueError("--snippet cannot be combined with --not-text or --count-only")
        if self.count_only and (
            self.select is not None or self.sort is not None or self.desc
            or self.limit is not None
        ):
            raise ValueError(
                "--count-only cannot be combined with --select, --sort, --desc or --limit"
            )
        if self.desc and self.sort is None:
            raise ValueError("--desc needs --sort")

        has_dates = self.since is not None or self.until is not None
        if has_dates and self.date_field is None:
            raise ValueError("--since/--until need --date-field NAME")
        if self.date_field is not None and not has_dates:
            raise ValueError("--date-field needs --since or --until")
        since_day = _parse_day("--since", self.since) if self.since is not None else None
        until_day = _parse_day("--until", self.until) if self.until is not None else None
        if since_day is not None and until_day is not None and since_day > until_day:
            raise ValueError(f"--since {self.since} is later than --until {self.until}")
        object.__setattr__(self, "since_day", since_day)
        object.__setattr__(self, "until_day", until_day)

        flags = 0 if self.case_sensitive else re.IGNORECASE
        if self.text is not None:
            object.__setattr__(self, "pattern", re.compile(re.escape(self.text), flags))
        elif self.regex is not None:
            try:
                object.__setattr__(self, "pattern", re.compile(self.regex, flags))
            except re.error as exc:
                raise ValueError(f"invalid --regex {self.regex!r}: {exc}") from exc
        object.__setattr__(
            self, "conditions", tuple(service.parse_where_clause(c) for c in self.where),
        )


@dataclass(frozen=True, slots=True)
class SearchResult:
    """`rows` in output order and shape; `columns` is the output header (known even when no row
    matched); `count` is the number of matching records (equal to len(rows) unless
    --count-only, which fetches no rows)."""

    rows: list[dict[str, object]]
    columns: list[str]
    count: int


@dataclass(frozen=True, slots=True)
class _GroupPlan:
    group: str
    ext_columns: tuple[str, ...]
    searched: tuple[str, ...]  # fields the text predicate applies to in this group
    skip: bool  # a positive text search with no searchable field here can match nothing


def _column_ref(name: str) -> str:
    return f'r."{name}"' if name in naming.BASE_RECORD_COLUMNS else f'e."{name}"'


def _unknown(flag: str, name: str, valid: list[str], group: str | None = None) -> ValueError:
    where = f" for group {group!r}" if group is not None else ""
    return ValueError(
        f"unknown {flag} field {name!r}{where}; valid fields: {', '.join(valid)}"
    )


def _ext_types(conn: sqlite3.Connection, group: str) -> dict[str, str]:
    return {
        str(c["name"]): str(c["type"]).upper()
        for c in repository.show_schema_columns(conn, group) if c["source"] == "extension"
    }


def _plan_groups(
    conn: sqlite3.Connection, request: SearchRequest,
) -> tuple[list[_GroupPlan], dict[str, dict[str, str]]]:
    existing = sorted(str(g["record_group"]) for g in repository.list_record_groups(conn))
    for group in request.groups:
        # A group with a schema but no records yet is empty, not unknown (as for `query`).
        if group not in existing and not repository.extension_table_exists(conn, group):
            raise ValueError(
                f"unknown group {group!r} in project {request.project!r}; existing groups: "
                f"{', '.join(existing) or '(none)'}"
            )
    types = {group: _ext_types(conn, group) for group in request.groups}

    def every_group(flag: str, name: str, allowed_base: tuple[str, ...]) -> None:
        for group in request.groups:
            valid = [*allowed_base, *types[group]]
            if name not in valid:
                raise _unknown(flag, name, valid, group)

    def some_group(flag: str, names: tuple[str, ...], allowed_base: tuple[str, ...]) -> None:
        valid = list(dict.fromkeys(
            [*allowed_base, *(n for group in request.groups for n in types[group])]
        ))
        for name in names:
            if name not in valid:
                raise _unknown(flag, name, valid)

    for field_name, _op, _value in request.conditions:
        every_group("--where", field_name, tuple(sorted(repository.BASE_QUERYABLE_COLUMNS)))
    if request.sort is not None:
        every_group("--sort", request.sort, naming.BASE_RECORD_COLUMNS)
    if request.date_field is not None:
        every_group("--date-field", request.date_field, _DATE_BASE_COLUMNS)
        for group in request.groups:
            sql_type = types[group].get(request.date_field, "INTEGER")
            if sql_type not in ("TEXT", "INTEGER", "REAL"):
                raise ValueError(
                    f"date field {request.date_field!r} in group {group!r} has type "
                    f"{sql_type or 'none'}; it must be TEXT, INTEGER or REAL"
                )
    if request.fields is not None:
        some_group("--fields", request.fields, _SEARCHABLE_BASE_COLUMNS)
    if request.select is not None:
        some_group("--select", request.select, naming.BASE_RECORD_COLUMNS)

    plans = []
    for group in request.groups:
        ext = types[group]
        if request.fields is not None:
            searched = tuple(
                f for f in request.fields if f in _SEARCHABLE_BASE_COLUMNS or f in ext
            )
        else:
            searched = (*_SEARCHABLE_BASE_COLUMNS, *(n for n, t in ext.items() if t == "TEXT"))
        skip = request.pattern is not None and not request.not_text and not searched
        plans.append(_GroupPlan(group, tuple(ext), searched, skip))
    return plans, types


def _local_midnight(flag: str, day: date, *, next_day: bool = False) -> int:
    """Epoch seconds of local midnight starting `day` (or the day after it), for comparing an
    epoch date field. Years at the edge of the calendar have no representable bound."""
    try:
        start = day + timedelta(days=1) if next_day else day
        return int(datetime.combine(start, time()).timestamp())
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError(
            f"{flag} {day.isoformat()} is outside the range an epoch date field can be "
            f"compared with"
        ) from exc


def _where_sql(
    request: SearchRequest, plan: _GroupPlan, types: dict[str, str],
) -> tuple[str, list[object]]:
    clauses = ["r.record_group=?", "r.deleted_at IS NULL"]
    params: list[object] = [plan.group]
    where_clauses, where_params = repository.where_sql(
        list(request.conditions), ext_columns=set(plan.ext_columns), record_group=plan.group,
    )
    clauses += where_clauses
    params += where_params
    if request.pattern is not None and plan.searched:
        call = f"{_MATCH_FUNCTION}({', '.join(_column_ref(f) for f in plan.searched)})"
        clauses.append(f"NOT {call}" if request.not_text else call)
    if request.date_field is not None:
        ref = _column_ref(request.date_field)
        if types.get(request.date_field) == "TEXT":
            if request.since_day is not None:
                clauses.append(f"substr({ref}, 1, 10) >= ?")
                params.append(request.since_day.isoformat())
            if request.until_day is not None:
                clauses.append(f"substr({ref}, 1, 10) <= ?")
                params.append(request.until_day.isoformat())
        else:
            if request.since_day is not None:
                clauses.append(f"{ref} >= ?")
                params.append(_local_midnight("--since", request.since_day))
            if request.until_day is not None:
                clauses.append(f"{ref} < ?")
                params.append(_local_midnight("--until", request.until_day, next_day=True))
    return " AND ".join(clauses), params


def _from_sql(plan: _GroupPlan) -> str:
    if not plan.ext_columns:
        return "FROM records r"
    table = naming.extension_table_name(plan.group)
    return f'FROM records r LEFT JOIN "{table}" e ON e.record_id = r.id'


def _sort_key(value: object) -> tuple[int, object]:
    """SQLite's cross-type order (NULL, numbers, text, blobs) so merged groups sort the same
    way each group's ORDER BY did."""
    if value is None:
        return (0, 0)
    if isinstance(value, (int, float)):
        return (1, value)
    if isinstance(value, str):
        return (2, value)
    return (3, value)


def _snippet(value: object, pattern: re.Pattern[str], width: int) -> object:
    if value is None:
        return None
    text = str(value)
    match = pattern.search(text)
    if match is None:
        return text[:width] + _ELLIPSIS if len(text) > width else text
    start = max(0, match.start() - width)
    end = min(len(text), match.end() + width)
    prefix = _ELLIPSIS if start > 0 else ""
    suffix = _ELLIPSIS if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"


def _output_columns(request: SearchRequest, plans: list[_GroupPlan]) -> list[str]:
    if request.select is not None:
        return list(dict.fromkeys([*_KEPT_COLUMNS, *request.select]))
    ext = dict.fromkeys(name for plan in plans for name in plan.ext_columns)
    return [*naming.BASE_RECORD_COLUMNS, *ext]


def _match_function(pattern: re.Pattern[str]) -> Callable[..., int]:
    def match(*values: object) -> int:
        return int(any(v is not None and pattern.search(str(v)) for v in values))
    return match


def run(request: SearchRequest) -> SearchResult:
    if not store.db_path(request.project).exists():
        raise ValueError(f"project {request.project!r} has no pdata store")
    # Read-only handle: repository.connect would run the schema DDL, a write.
    conn = db.connect(store.db_path(request.project), readonly=True)
    try:
        if request.pattern is not None:
            conn.create_function(
                _MATCH_FUNCTION, -1, _match_function(request.pattern), deterministic=True,
            )
        plans, types = _plan_groups(conn, request)
        columns = _output_columns(request, plans)
        if request.count_only:
            total = 0
            for plan in plans:
                if plan.skip:
                    continue
                where, params = _where_sql(request, plan, types[plan.group])
                total += conn.execute(
                    f"SELECT COUNT(*) {_from_sql(plan)} WHERE {where}", params,
                ).fetchone()[0]
            return SearchResult(rows=[], columns=columns, count=total)

        direction = " DESC" if request.desc else ""
        rows: list[dict[str, object]] = []
        for plan in plans:
            if plan.skip:
                continue
            if request.sort is None and request.limit is not None and len(rows) >= request.limit:
                break
            where, params = _where_sql(request, plan, types[plan.group])
            order = (
                f"{_column_ref(request.sort)}{direction}, r.id{direction}"
                if request.sort is not None else "r.created_at, r.id"
            )
            select = ", ".join(["r.*", *(f'e."{c}"' for c in plan.ext_columns)])
            sql = f"SELECT {select} {_from_sql(plan)} WHERE {where} ORDER BY {order}"
            if request.limit is not None:
                sql += " LIMIT ?"
                params.append(request.limit)
            for found in conn.execute(sql, params):
                row = dict(found)
                if request.pattern is not None and request.snippet is not None:
                    for name in plan.searched:
                        row[name] = _snippet(row[name], request.pattern, request.snippet)
                rows.append(row)
    finally:
        conn.close()

    if request.sort is not None:
        sort_field = request.sort
        rows.sort(
            key=lambda r: (_sort_key(r.get(sort_field)), str(r["id"])), reverse=request.desc,
        )
    if request.limit is not None:
        rows = rows[: request.limit]
    shaped = [{name: row.get(name) for name in columns} for row in rows]
    return SearchResult(rows=shaped, columns=columns, count=len(shaped))
