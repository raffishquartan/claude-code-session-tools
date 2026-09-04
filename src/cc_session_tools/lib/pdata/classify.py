"""Heuristic file-tree classifier for `ccst pdata init` (spec §7.1 step 1, §3).

Deliberately conservative for free text: only CSV and JSON get an automatic
db-owned proposal, because their internal structure is genuinely machine-inferable
without judgement. A markdown/text file's shape (an append-only log vs. a
versioned plan doc vs. a stacked-snapshot journal — spec §4.3) cannot be told
apart by a generic scan; guessing wrong here would silently bake a per-project
judgement call into shared tooling, which this plan must not do (see the
per-project inventory doc, not this module, for those calls). Every markdown/
text/unknown-extension file defaults to folder-owned; a human (via the
pm-project-init skill) reviews the printed report and flips individual entries
to db-owned with an explicit record_group/strategy.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from cc_session_tools.lib.pdata import naming
from cc_session_tools.lib.pdata.init_paths import (
    EXCLUDED_DIR_NAMES,
    LEGACY_PROPOSAL_FILENAME,
    PROPOSAL_FILENAME,
)
from cc_session_tools.lib.pdata.manifest import FieldSpec, ManifestEntry
from cc_session_tools.lib.pdata.write_log import LOG_FILENAME as WRITE_LOG_FILENAME

_BINARY_EXTENSIONS = frozenset({
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".docx", ".xlsx", ".pptx",
    ".zip", ".mp3", ".mp4", ".heic", ".mov",
})


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return re.sub(r"-+", "-", slug) or "group"


def _slugify_field_name(header: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", header.lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)
    if not slug or not slug[0].isalpha():
        slug = f"f_{slug}" if slug else "field"
    return slug


def _safe_field_name(candidate: str, seen: set[str]) -> str:
    """Guarantees the returned name both passes naming.validate_field_name (rejecting
    a collision with a reserved base column like `id`/`version`/`created_at`/
    `updated_at`/`record_group`/`deleted_at`, or the ext table's own `record_id`) and
    doesn't repeat a name already used elsewhere in this same entry. Without this, a
    plausible real header (e.g. a CSV column literally named `version`) would pass
    classification silently and only fail inside schema_add_field at --write time,
    aborting and soft-deleting the whole run — well past the human-review step the
    spec's dry-run report exists for. Terminates because each branch strictly
    lengthens the candidate with a suffix that is itself always a valid, non-reserved
    identifier fragment."""
    name = candidate
    while True:
        try:
            naming.validate_field_name(name)
        except ValueError:
            name = f"{name}_field"
            continue
        if name in seen:
            name = f"{name}_2"
            continue
        return name


def _default_record_group(path: Path) -> str:
    group = _slugify(path.stem)
    naming.validate_record_group(group)
    return group


def _classify_csv(rel_path: str, abs_path: Path) -> ManifestEntry:
    with abs_path.open(newline="", encoding="utf-8") as f:
        header = next(csv.reader(f), [])
    content_column = next((h for h in header if h.strip().lower() == "content"), None)
    file_path_column = next(
        (h for h in header if h.strip().lower() in ("file_path", "path")), None
    )
    seen: set[str] = set()
    fields: list[FieldSpec] = []
    for h in header:
        if h in (content_column, file_path_column):
            continue
        name = _safe_field_name(_slugify_field_name(h), seen)
        seen.add(name)
        fields.append(FieldSpec(name=name, sql_type="TEXT", column=h))
    return ManifestEntry(
        path=rel_path, classification="db-owned", strategy="csv-rows",
        record_group=_default_record_group(abs_path),
        content_column=content_column, file_path_column=file_path_column, fields=fields,
    )


def _infer_sql_type(value: object) -> str:
    if isinstance(value, bool):
        return "INTEGER"
    if isinstance(value, int):
        return "INTEGER"
    if isinstance(value, float):
        return "REAL"
    return "TEXT"


def _classify_json(rel_path: str, abs_path: Path) -> ManifestEntry:
    data: Any
    try:
        data = json.loads(abs_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ManifestEntry(path=rel_path, classification="folder-owned")

    seen: set[str] = set()
    fields: list[FieldSpec] = []
    if isinstance(data, list) and data and all(isinstance(el, dict) for el in data):
        sample = data[0]
        for k, v in sample.items():
            name = _safe_field_name(_slugify_field_name(k), seen)
            seen.add(name)
            fields.append(FieldSpec(name=name, sql_type=_infer_sql_type(v), column=k))
        return ManifestEntry(
            path=rel_path, classification="db-owned", strategy="json-array-rows",
            record_group=_default_record_group(abs_path), fields=fields,
        )
    if isinstance(data, dict):
        for k, v in data.items():
            name = _safe_field_name(_slugify_field_name(k), seen)
            seen.add(name)
            fields.append(FieldSpec(name=name, sql_type=_infer_sql_type(v), column=k))
        return ManifestEntry(
            path=rel_path, classification="db-owned", strategy="json-singleton",
            record_group=_default_record_group(abs_path), fields=fields,
        )
    return ManifestEntry(path=rel_path, classification="folder-owned")


def classify_path(rel_path: str, abs_path: Path) -> ManifestEntry:
    suffix = abs_path.suffix.lower()
    if suffix in _BINARY_EXTENSIONS:
        return ManifestEntry(path=rel_path, classification="folder-owned")
    if suffix == ".csv":
        return _classify_csv(rel_path, abs_path)
    if suffix == ".json":
        return _classify_json(rel_path, abs_path)
    return ManifestEntry(path=rel_path, classification="folder-owned")


def _disambiguate_record_groups(
    entries: list[ManifestEntry], *, existing_record_groups: frozenset[str] = frozenset(),
) -> None:
    """_default_record_group derives its proposal from path.stem alone, so two
    files sharing a basename in different subdirectories (e.g. a/notes.csv and
    b/notes.csv) would otherwise both auto-propose the same record_group —
    silently merging their unrelated rows into one shared group at --write time
    with no error or warning. Detect every such collision across the whole
    manifest and disambiguate each colliding entry by prefixing its parent
    directory's slug, so the dry-run report already shows the group name that
    will actually be used.

    `existing_record_groups` (the project's already-live record_groups, per
    service.schema_list — populated by an earlier ccst pdata init run, by Plan
    A's own service.add_record usage, or by an unrelated mechanism like Plan C's
    session-output groups) is folded into the same collision check: a freshly
    proposed group colliding with an existing live one is disambiguated exactly
    like an in-pass collision, even when only one new file in this pass proposes
    that name. Without this, a first classification pass against a project with
    prior pdata activity (or a forced reclassification after deleting the
    proposal file) could silently propose merging new content into an
    already-populated, possibly system-managed record_group with zero warning in
    the dry-run report."""
    by_group: dict[str, list[ManifestEntry]] = {}
    for entry in entries:
        if entry.classification == "db-owned":
            by_group.setdefault(entry.db_group(), []).append(entry)
    for group, colliding in by_group.items():
        if len(colliding) < 2 and group not in existing_record_groups:
            continue
        seen: set[str] = set(existing_record_groups)
        for entry in colliding:
            parent = Path(entry.path).parent
            prefix = _slugify(str(parent)) if str(parent) != "." else "root"
            candidate = f"{prefix}-{group}"
            while candidate in seen:
                candidate = f"{candidate}-2"
            seen.add(candidate)
            entry.record_group = candidate


def walk_and_classify(
    project_root: Path, *, existing_record_groups: frozenset[str] = frozenset(),
) -> list[ManifestEntry]:
    """Walk project_root, classifying every file not inside an excluded directory
    and not the proposal file or --write's own log file. Returns entries sorted by
    relative path for deterministic report output. `existing_record_groups` — see
    _disambiguate_record_groups — lets a caller (manifest.load_or_create) pass in
    the project's already-live record_groups so a fresh classification pass never
    silently proposes merging into one of them."""
    entries: list[ManifestEntry] = []
    for abs_path in sorted(project_root.rglob("*")):
        if not abs_path.is_file():
            continue
        rel = abs_path.relative_to(project_root)
        if any(part in EXCLUDED_DIR_NAMES for part in rel.parts[:-1]):
            continue
        if abs_path.name in (PROPOSAL_FILENAME, LEGACY_PROPOSAL_FILENAME, WRITE_LOG_FILENAME):
            continue
        entries.append(classify_path(str(rel), abs_path))
    _disambiguate_record_groups(entries, existing_record_groups=existing_record_groups)
    return entries
