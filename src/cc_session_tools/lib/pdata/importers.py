"""Import strategies for `ccst pdata init`'s write phase (spec §7.1 step 3).

Each strategy reads one source file and yields ImportRow tuples — exactly the
(content, file_path, fields, created_at) shape service.add_record already
accepts (Plan A, spec §5). Field values are always passed through as str,
matching how `ccst pdata add --field k=v` already only ever sends strings from
the CLI boundary — SQLite's column-affinity rules convert a well-formed numeric
string into the extension column's real INTEGER/REAL storage class on insert,
so no importer-side type coercion is needed (plan Decision 7).
"""
from __future__ import annotations

import csv
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cc_session_tools.lib.pdata.manifest import ManifestEntry

_DEFAULT_SECTION_DELIMITER = r"(?m)^## .*$"


@dataclass
class ImportRow:
    content: str
    file_path: str | None
    fields: dict[str, str]
    created_at: int


def _mtime(path: Path) -> int:
    return int(path.stat().st_mtime)


def _stringify(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def import_whole_file(project_root: Path, entry: ManifestEntry) -> list[ImportRow]:
    path = project_root / entry.path
    return [ImportRow(content=path.read_text(encoding="utf-8"), file_path=None,
                      fields={}, created_at=_mtime(path))]


def import_delimited_sections(project_root: Path, entry: ManifestEntry) -> list[ImportRow]:
    path = project_root / entry.path
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(entry.delimiter or _DEFAULT_SECTION_DELIMITER)
    starts = [m.start() for m in pattern.finditer(text)]
    created_at = _mtime(path)
    if not starts:
        return [ImportRow(content=text, file_path=None, fields={}, created_at=created_at)]
    starts.append(len(text))
    return [
        ImportRow(content=text[starts[i]:starts[i + 1]].strip(), file_path=None,
                  fields={}, created_at=created_at)
        for i in range(len(starts) - 1)
    ]


def import_csv_rows(project_root: Path, entry: ManifestEntry) -> list[ImportRow]:
    path = project_root / entry.path
    created_at = _mtime(path)
    rows: list[ImportRow] = []
    with path.open(newline="", encoding="utf-8") as f:
        for raw_row in csv.DictReader(f):
            fields = {
                spec.name: _stringify(raw_row.get(spec.column or spec.name))
                for spec in entry.fields
            }
            if entry.content_column:
                content = raw_row.get(entry.content_column) or ""
            else:
                content = json.dumps(raw_row, ensure_ascii=False)
            file_path = raw_row.get(entry.file_path_column) if entry.file_path_column else None
            rows.append(ImportRow(content=content, file_path=file_path or None,
                                  fields=fields, created_at=created_at))
    return rows


def import_json_array_rows(project_root: Path, entry: ManifestEntry) -> list[ImportRow]:
    path = project_root / entry.path
    created_at = _mtime(path)
    data: Any = json.loads(path.read_text(encoding="utf-8"))
    # A hand-edited manifest entry (pm-project-init Step 4 instructs editing this
    # file directly) can assign 'json-array-rows' to a file whose actual JSON
    # shape isn't an array of objects. Validate the shape here and raise
    # ValueError — the exact exception type write()'s per-entry
    # `except (ValueError, OSError, csv.Error)` already catches — rather than
    # letting a bare list/dict access raise AttributeError/TypeError, which would
    # crash the CLI with a raw traceback and skip the soft-delete rollback for
    # every id already inserted earlier in the same run.
    if not isinstance(data, list):
        raise ValueError(
            f"{entry.path}: strategy 'json-array-rows' requires a JSON array, "
            f"got {type(data).__name__}"
        )
    rows: list[ImportRow] = []
    for index, element in enumerate(data):
        if not isinstance(element, dict):
            raise ValueError(
                f"{entry.path}: strategy 'json-array-rows' requires an array of "
                f"objects, but element {index} is a {type(element).__name__}"
            )
        fields = {
            spec.name: _stringify(element.get(spec.column or spec.name))
            for spec in entry.fields
        }
        rows.append(ImportRow(content=json.dumps(element, ensure_ascii=False),
                              file_path=None, fields=fields, created_at=created_at))
    return rows


def import_json_singleton(project_root: Path, entry: ManifestEntry) -> list[ImportRow]:
    path = project_root / entry.path
    created_at = _mtime(path)
    data: Any = json.loads(path.read_text(encoding="utf-8"))
    # Same rationale as import_json_array_rows above: a hand-edited entry can
    # assign 'json-singleton' to a file whose actual top-level JSON value isn't
    # an object (e.g. an array or scalar) — validate up front and raise
    # ValueError instead of letting `.get()` raise AttributeError.
    if not isinstance(data, dict):
        raise ValueError(
            f"{entry.path}: strategy 'json-singleton' requires a JSON object, "
            f"got {type(data).__name__}"
        )
    fields = {
        spec.name: _stringify(data.get(spec.column or spec.name))
        for spec in entry.fields
    }
    return [ImportRow(content=json.dumps(data, ensure_ascii=False), file_path=None,
                      fields=fields, created_at=created_at)]


STRATEGY_IMPORTERS: dict[str, Callable[[Path, ManifestEntry], list[ImportRow]]] = {
    "whole-file": import_whole_file,
    "delimited-sections": import_delimited_sections,
    "csv-rows": import_csv_rows,
    "json-array-rows": import_json_array_rows,
    "json-singleton": import_json_singleton,
}


def import_entry(project_root: Path, entry: ManifestEntry) -> list[ImportRow]:
    importer = STRATEGY_IMPORTERS[entry.db_strategy()]
    return importer(project_root, entry)


def count_source_rows(project_root: Path, entry: ManifestEntry) -> int:
    """Independently re-derives how many rows the source file *should* produce, for
    init_service's entry-count parity check (spec §7.1 step 4) — a comparison target
    computed fresh from the file, not a re-read of import_entry's own accumulated
    result, so it can catch a run where fewer rows landed in the DB than the source
    actually contains."""
    strategy = entry.db_strategy()
    path = project_root / entry.path
    if strategy in ("whole-file", "json-singleton"):
        return 1
    if strategy == "delimited-sections":
        text = path.read_text(encoding="utf-8")
        pattern = re.compile(entry.delimiter or _DEFAULT_SECTION_DELIMITER)
        starts = [m.start() for m in pattern.finditer(text)]
        return len(starts) if starts else 1
    if strategy == "csv-rows":
        with path.open(newline="", encoding="utf-8") as f:
            return sum(1 for _ in csv.DictReader(f))
    if strategy == "json-array-rows":
        data: Any = json.loads(path.read_text(encoding="utf-8"))
        return len(data)
    raise ValueError(f"unknown import strategy {strategy!r}")
