"""Classification manifest for `ccst pdata init` (spec §7.1 steps 1-2).

A Manifest is the single artifact carrying a project's per-file classification
between invocations. The first dry run creates it fresh from
classify.walk_and_classify(); every later dry run returns the file completely
unchanged so a human's overrides (record_group renames, folder-owned overrides,
field-type corrections) are never silently clobbered. `--write` always operates
on whatever is currently on disk.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_VALID_CLASSIFICATIONS = {"folder-owned", "db-owned"}
_VALID_STRATEGIES = {
    "whole-file", "delimited-sections", "csv-rows", "json-array-rows", "json-singleton",
}


@dataclass
class FieldSpec:
    name: str
    sql_type: str
    column: str | None = None
    description: str | None = None
    default: object | None = None


@dataclass
class ManifestEntry:
    path: str
    classification: str
    reviewed: bool = False
    record_group: str | None = None
    strategy: str | None = None
    delimiter: str | None = None
    content_column: str | None = None
    file_path_column: str | None = None
    fields: list[FieldSpec] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.classification not in _VALID_CLASSIFICATIONS:
            raise ValueError(
                f"invalid classification {self.classification!r} for {self.path!r}"
            )
        if self.classification == "db-owned":
            if not self.record_group:
                raise ValueError(f"db-owned entry {self.path!r} needs a record_group")
            if self.strategy not in _VALID_STRATEGIES:
                raise ValueError(f"invalid strategy {self.strategy!r} for {self.path!r}")

    def db_group(self) -> str:
        """The validated non-None record_group (guaranteed by __post_init__ for every
        db-owned entry) — narrows the type once here instead of an assert/ignore at
        every call site."""
        assert self.record_group is not None, "db_group() called on a non-db-owned entry"
        return self.record_group

    def db_strategy(self) -> str:
        assert self.strategy is not None, "db_strategy() called on a non-db-owned entry"
        return self.strategy


@dataclass
class Manifest:
    project: str
    entries: list[ManifestEntry]

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for entry in self.entries:
            if entry.path in seen:
                raise ValueError(f"duplicate entry path in manifest: {entry.path!r}")
            seen.add(entry.path)


def save(m: Manifest, path: Path) -> None:
    data = {"project": m.project, "entries": [asdict(e) for e in m.entries]}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load(path: Path) -> Manifest:
    """Raises ValueError (never a raw KeyError/TypeError/AttributeError) for any
    malformed proposal file — a missing "project"/"entries" key, an "entries" that
    isn't a list, an entry that isn't an object, or an entry with an unexpected
    field shape. This is a real hand-edit surface (pm-project-init Step 4
    instructs editing this file directly), so a shape error here must reach
    _cmd_pdata_init's `except (FileNotFoundError, ValueError)` and dry_run's
    `except ValueError` and produce the documented exit-2 validation error instead
    of an uncaught traceback."""
    data: Any = json.loads(path.read_text(encoding="utf-8"))
    try:
        entries: list[ManifestEntry] = []
        for raw_entry in data["entries"]:
            raw_fields = raw_entry.get("fields", [])
            field_specs = [FieldSpec(**rf) for rf in raw_fields]
            entry_kwargs = {k: v for k, v in raw_entry.items() if k != "fields"}
            entries.append(ManifestEntry(**entry_kwargs, fields=field_specs))
        return Manifest(project=data["project"], entries=entries)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"malformed manifest at {path}: {exc}") from exc


def load_or_create(
    project_root: Path, project: str, proposal_path: Path,
    *, existing_record_groups: frozenset[str] = frozenset(),
) -> Manifest:
    """Never overwrites an existing proposal file (spec §7.1 step 2 — a human's
    overrides must survive a re-run of the dry-run pass). First call for a project
    creates it fresh from classify.walk_and_classify(); every later call returns the
    file exactly as it is on disk. Delete the file to force a fresh classification
    pass. `existing_record_groups` — the project's already-live record_groups —
    is threaded through to the classifier so a fresh pass (first-ever run against
    a project with prior pdata activity, or a forced reclassification) never
    silently proposes merging a new file into an already-populated group; see
    classify._disambiguate_record_groups."""
    from cc_session_tools.lib.pdata import classify  # local import: breaks the
    # classify<->manifest cycle (classify.py imports these dataclasses at module level)

    if proposal_path.exists():
        return load(proposal_path)
    entries = classify.walk_and_classify(
        project_root, existing_record_groups=existing_record_groups
    )
    m = Manifest(project=project, entries=entries)
    save(m, proposal_path)
    return m
