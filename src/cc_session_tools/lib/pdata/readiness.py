"""Deterministic pdata-readiness scan of a project's CSV files (`ccst pdata readiness-scan`).

Reports the mechanical blockers a migration audit would otherwise find by reading: ragged rows,
mixed date formats, mixed separators, machine-specific paths, fake nulls, duplicates, BOM/CRLF and
unreadable files, plus a per-file inventory and unique-column facts. Read-only: the scan never
writes. Findings carry counts and 1-based data-record numbers (header excluded), never cell text;
`unreadable` reasons are a fixed vocabulary, so file content cannot reach a report.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from cc_session_tools.lib.pdata.init_paths import EXCLUDED_DIR_NAMES

FINDING_KINDS: tuple[str, ...] = (
    "ragged-rows", "machine-paths", "null-strings", "duplicate-rows", "comment-rows",
    "repeated-header", "mixed-date-formats", "mixed-separators", "bad-header",
    "no-unique-column", "unreadable", "bom", "crlf",
)

MAX_EXAMPLE_ROWS = 3
FREE_TEXT_MEAN_LENGTH = 64
NULL_STRINGS = frozenset({"NO DATA", "N/A", "NA", "NULL", "NONE", "TBD", "UNKNOWN", "-", "--", "?"})
_MACHINE_PATH = re.compile(r"/Users/[^/\s]+|/home/[^/\s]+|/mnt/[a-z]/|[A-Za-z]:\\Users\\")
_DATE_FORMATS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("iso-datetime", re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")),
    ("iso-date", re.compile(r"^\d{4}-\d{2}-\d{2}$")),
    ("slash", re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}$")),
    ("d-month-yyyy", re.compile(r"^\d{1,2} [A-Za-z]{3,9} \d{4}$")),
    ("month-d-yyyy", re.compile(r"^[A-Za-z]{3,9} \d{1,2},? \d{4}$")),
)
_BOM = b"\xef\xbb\xbf"


@dataclass(frozen=True)
class Finding:
    kind: str
    file: str
    column: str | None = None
    count: int | None = None
    rows: tuple[int, ...] = ()
    detail: tuple[tuple[str, int | str], ...] = ()


@dataclass(frozen=True)
class UniqueColumn:
    name: str
    labels: tuple[str, ...]


@dataclass(frozen=True)
class FileReport:
    path: str
    rows: int
    columns: int
    header: tuple[str, ...]
    unique_columns: tuple[UniqueColumn, ...]


@dataclass(frozen=True)
class ReadinessReport:
    files: tuple[FileReport, ...]
    findings: tuple[Finding, ...]
    not_scanned: dict[str, int]


class _Unreadable(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _is_real_yyyymmdd(value: str) -> bool:
    if not re.fullmatch(r"\d{8}", value):
        return False
    try:
        parsed = datetime.strptime(value, "%Y%m%d")
    except ValueError:
        return False
    return 1900 <= parsed.year <= 2100


def _date_format(value: str) -> str | None:
    for name, pattern in _DATE_FORMATS:
        if pattern.match(value):
            return name
    return "yyyymmdd" if _is_real_yyyymmdd(value) else None


def _walk(root: Path) -> list[Path]:
    """Every file under root outside EXCLUDED_DIR_NAMES, without following directory symlinks -
    the same exclusion set `ccst pdata init`'s classifier uses, so coverage matches migration."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED_DIR_NAMES)
        found.extend(Path(dirpath) / name for name in sorted(filenames))
    return found


def _parse(raw: bytes) -> tuple[list[list[str]], bool, bool]:
    if not raw:
        raise _Unreadable("empty-file")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise _Unreadable("not-utf8") from None
    if not text.strip():
        raise _Unreadable("empty-file")
    try:
        records = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as exc:
        raise _Unreadable(
            "field-too-large" if "field larger" in str(exc) else "csv-parse-error"
        ) from None
    return records, raw.startswith(_BOM), b"\r\n" in raw


def _scan_file(rel: str, raw: bytes) -> tuple[FileReport | None, list[Finding]]:
    try:
        records, has_bom, has_crlf = _parse(raw)
    except _Unreadable as exc:
        return None, [Finding("unreadable", rel, detail=(("reason", exc.reason),))]

    findings: list[Finding] = []
    if has_bom:
        findings.append(Finding("bom", rel))
    if has_crlf:
        findings.append(Finding("crlf", rel))

    header = records[0]
    ncols = len(header)
    if any(not h.strip() for h in header) or len(set(header)) != ncols:
        findings.append(Finding("bad-header", rel))

    data = [r for r in records[1:] if r]
    nonempty = [0] * ncols
    lengths = [0] * ncols
    distinct: list[set[str]] = [set() for _ in range(ncols)]
    all_int = [True] * ncols
    dates: list[dict[str, int]] = [{} for _ in range(ncols)]
    seps: list[dict[str, int]] = [{} for _ in range(ncols)]
    nulls: list[dict[str, int]] = [{} for _ in range(ncols)]
    null_rows: list[list[int]] = [[] for _ in range(ncols)]
    path_rows: list[list[int]] = [[] for _ in range(ncols)]
    ragged: list[int] = []
    dup_rows: list[int] = []
    comment_rows: list[int] = []
    header_rows: list[int] = []
    seen_rows: set[tuple[str, ...]] = set()

    for number, row in enumerate(data, start=1):
        if row == header:
            header_rows.append(number)
        if row[0].startswith("#"):
            comment_rows.append(number)
        if len(row) != ncols:
            ragged.append(number)
        key = tuple(row)
        if key in seen_rows:
            dup_rows.append(number)
        seen_rows.add(key)
        for i, value in enumerate(row[:ncols]):
            if not value.strip():
                continue
            nonempty[i] += 1
            lengths[i] += len(value)
            distinct[i].add(value)
            if not re.fullmatch(r"-?\d+", value.strip()):
                all_int[i] = False
            fmt = _date_format(value.strip())
            if fmt:
                dates[i][fmt] = dates[i].get(fmt, 0) + 1
            for sep in ("|", ";"):
                if sep in value:
                    seps[i][sep] = seps[i].get(sep, 0) + 1
            token = value.strip().upper()
            if token in NULL_STRINGS:
                nulls[i][token] = nulls[i].get(token, 0) + 1
                null_rows[i].append(number)
            if _MACHINE_PATH.search(value):
                path_rows[i].append(number)

    def row_finding(kind: str, rows: list[int], column: str | None = None,
                    detail: tuple[tuple[str, int | str], ...] = ()) -> Finding:
        return Finding(kind, rel, column, len(rows), tuple(rows[:MAX_EXAMPLE_ROWS]), detail)

    if ragged:
        findings.append(row_finding("ragged-rows", ragged))
    if dup_rows:
        findings.append(row_finding("duplicate-rows", dup_rows))
    if comment_rows:
        findings.append(row_finding("comment-rows", comment_rows))
    if header_rows:
        findings.append(row_finding("repeated-header", header_rows))
    for i, name in enumerate(header):
        if len(dates[i]) >= 2:
            findings.append(Finding("mixed-date-formats", rel, name,
                                    detail=tuple(sorted(dates[i].items()))))
        if len(seps[i]) == 2:
            findings.append(Finding("mixed-separators", rel, name,
                                    detail=tuple(sorted(seps[i].items()))))
        if path_rows[i]:
            findings.append(row_finding("machine-paths", path_rows[i], name))
        if null_rows[i]:
            findings.append(row_finding("null-strings", null_rows[i], name,
                                        tuple(sorted(nulls[i].items()))))

    unique: list[UniqueColumn] = []
    if len(data) >= 2:
        for i, name in enumerate(header):
            if nonempty[i] == len(data) and len(distinct[i]) == len(data):
                labels: list[str] = []
                if all_int[i]:
                    labels.append("integer-like")
                if lengths[i] / len(data) > FREE_TEXT_MEAN_LENGTH:
                    labels.append("free-text")
                unique.append(UniqueColumn(name, tuple(labels)))
        if not unique:
            findings.append(Finding("no-unique-column", rel))

    return FileReport(rel, len(data), ncols, tuple(header), tuple(unique)), findings


def scan_project(root: Path, *, path_prefix: str | None = None) -> ReadinessReport:
    files: list[FileReport] = []
    findings: list[Finding] = []
    not_scanned = {"json": 0, "markdown": 0, "other": 0}
    for abs_path in _walk(root):
        rel = abs_path.relative_to(root).as_posix()
        if path_prefix and not rel.startswith(path_prefix):
            continue
        suffix = abs_path.suffix.lower()
        if suffix != ".csv":
            bucket = {".json": "json", ".md": "markdown", ".markdown": "markdown"}.get(suffix, "other")
            not_scanned[bucket] += 1
            continue
        report, file_findings = _scan_file(rel, abs_path.read_bytes())
        if report is not None:
            files.append(report)
        findings.extend(file_findings)
    findings.sort(key=lambda f: (f.file, FINDING_KINDS.index(f.kind), f.column or ""))
    return ReadinessReport(tuple(files), tuple(findings), not_scanned)


def _finding_line(f: Finding) -> str:
    parts = [f"- {f.kind}: {f.file}"]
    if f.column is not None:
        parts.append(f"column {f.column!r}")
    if f.count is not None:
        parts.append(f"count={f.count}")
    if f.rows:
        parts.append("rows=" + ",".join(str(r) for r in f.rows))
    if f.detail:
        parts.append(", ".join(f"{k}={v}" for k, v in f.detail))
    return "  ".join(parts)


def format_markdown(report: ReadinessReport, *, findings_only: bool = False) -> str:
    ns = report.not_scanned
    lines = [
        "# pdata readiness scan",
        "",
        "Row numbers are 1-based data records (header excluded), not line numbers: a quoted "
        "multiline cell makes them differ.",
        f"Scanned {len(report.files)} CSV file(s); not scanned: {ns['json']} JSON, "
        f"{ns['markdown']} Markdown, {ns['other']} other.",
    ]
    if not report.files and not any(f.kind == "unreadable" for f in report.findings):
        lines += ["", "No CSV files found."]
    lines += ["", "## Findings"]
    lines += [_finding_line(f) for f in report.findings] or ["No findings."]
    if not findings_only and report.files:
        lines += [
            "",
            "## Inventory",
            "A unique column is a fact, not a recommended key: it is not a stable key until a "
            "human has confirmed its values are not re-numbered per session or machine.",
        ]
        for f in report.files:
            unique = ", ".join(
                u.name + (f" ({'/'.join(u.labels)})" if u.labels else "") for u in f.unique_columns
            )
            lines.append(
                f"- {f.path}: {f.rows} rows, {f.columns} columns"
                + (f"; unique: {unique}" if unique else "")
            )
    return "\n".join(lines)


def format_json(report: ReadinessReport) -> str:
    return json.dumps(
        {
            "files": [
                {
                    "path": f.path, "rows": f.rows, "columns": f.columns,
                    "header": list(f.header),
                    "unique_columns": [{"name": u.name, "labels": list(u.labels)}
                                       for u in f.unique_columns],
                }
                for f in report.files
            ],
            "findings": [
                {
                    "kind": f.kind, "file": f.file, "column": f.column, "count": f.count,
                    "rows": list(f.rows), "detail": dict(f.detail),
                }
                for f in report.findings
            ],
            "not_scanned": report.not_scanned,
        },
        indent=2,
    )
