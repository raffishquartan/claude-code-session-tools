"""Orchestration for `ccst pdata init` (spec §7): dry-run classification (steps
0-2) and the write/verify/backup/cutover phase. Every DB write goes through Plan
A's service.py — this module owns no SQL of its own.
"""
from __future__ import annotations

import csv
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from cc_session_tools.lib import db
from cc_session_tools.lib.pdata import (
    backup,
    cutover,
    dump,
    init_paths,
    manifest,
    rehydrate,
    repository,
    service,
    store,
)
from cc_session_tools.lib.pdata.importers import ImportRow, count_source_rows, import_entry
from cc_session_tools.lib.pdata.manifest import Manifest, ManifestEntry


@dataclass
class DryRunResult:
    manifest: Manifest
    report: str
    proposal_path: Path


def dry_run(*, project: str, rehearse: Path | None = None) -> DryRunResult:
    project_root = init_paths.resolve_project_root(project, rehearse=rehearse)
    with init_paths.project_db_dir_override(rehearse):
        # repository.connect() runs the base-schema DDL (CREATE TABLE IF NOT
        # EXISTS) on every call — this is what "safe to run against an empty
        # folder... also how a genuinely new project gets its .db" (spec §5) means.
        repository.connect(project).close()
        # The project's already-live record_groups (from a prior ccst pdata init
        # run, from Plan A's service.add_record used directly, or from an
        # unrelated mechanism like Plan C's session-output groups) — threaded
        # through to the classifier so a first-ever/forced-reclassification pass
        # never silently proposes merging a new file into one of them (see
        # classify._disambiguate_record_groups).
        existing_record_groups = frozenset(
            str(group["record_group"]) for group in service.schema_list(project=project)
        )
    proposal_path = project_root / init_paths.PROPOSAL_FILENAME
    m = manifest.load_or_create(
        project_root, project, proposal_path,
        existing_record_groups=existing_record_groups,
    )
    return DryRunResult(manifest=m, report=_render_report(m), proposal_path=proposal_path)


def _render_report(m: Manifest) -> str:
    if not m.entries:
        return f"ccst pdata init — {m.project}: no files found, empty base schema created."
    lines = [f"ccst pdata init — {m.project}: {len(m.entries)} file(s) classified"]
    for e in m.entries:
        if e.classification == "folder-owned":
            lines.append(f"  [folder-owned] {e.path}")
        else:
            field_names = [f.name for f in e.fields]
            lines.append(
                f"  [db-owned]     {e.path} -> group={e.record_group} "
                f"strategy={e.strategy} fields={field_names}"
            )
    lines.append(
        "Review/override entries in the proposal file listed below before running --write."
    )
    return "\n".join(lines)


@dataclass
class WriteFailure:
    reasons: list[str]


@dataclass
class WriteResult:
    created_record_ids: list[int]
    entries_written: list[str]
    backup_path: Path | None
    failure: WriteFailure | None
    report: str = ""
    # True when write() adopted an existing sync dump instead of classifying/importing this
    # machine's current flat files (spec "ccst pdata init on a second machine (adopt-from-dump)").
    adopted_from_dump: bool = False


def _validate_no_conflicting_field_types(m: Manifest) -> None:
    """Two manifest entries can legitimately feed the same record_group (nothing
    in this module's manifest/write() design forbids it) — but Plan A's
    schema_add_field/add_extension_column silently no-ops when a field name
    already has a column, so two entries proposing the same field name with a
    *different* sql_type would otherwise have the second entry's type silently
    dropped, with no error, no warning, and no mention in the diff report, and its
    rows coerced/stored under the first entry's column type. Catch the conflict
    here, before any DDL or row import runs (a validation error, exit 2 — not a
    verification failure with partially-inserted rows to roll back), so an
    incompatible pair of entries is rejected up front instead of silently
    corrupting one side's data."""
    seen: dict[tuple[str, str], str] = {}
    for entry in m.entries:
        if entry.classification != "db-owned":
            continue
        for spec in entry.fields:
            key = (entry.db_group(), spec.name)
            prior_type = seen.get(key)
            if prior_type is not None and prior_type != spec.sql_type:
                raise ValueError(
                    f"conflicting sql_type for field {spec.name!r} in record_group "
                    f"{entry.db_group()!r}: {prior_type!r} (from an earlier entry) "
                    f"vs {spec.sql_type!r} (from {entry.path!r}) — align both "
                    f"entries' field sql_type in the proposal before running --write"
                )
            seen[key] = spec.sql_type


def _emit(on_progress: Callable[[str], None] | None, message: str) -> None:
    if on_progress is not None:
        on_progress(message)


def _rollback(*, project: str, created_ids: list[int]) -> list[str]:
    """Soft-delete every id in created_ids (spec §4.5) — no hard delete, full auditability.
    Every id here was inserted earlier in this single-threaded run, so its version is always
    1. Each delete_record call is wrapped individually: service.RecordNotFoundError/
    VersionConflictError are plain Exception subclasses (not ValueError/OSError), so an
    unwrapped raise here would abort the loop mid-way and leave some just-inserted rows
    soft-deleted and others still live. Any rollback failure is returned alongside the
    caller's own failure reasons rather than raised, since the caller still needs a
    WriteResult back, not a crash."""
    rollback_failures: list[str] = []
    for record_id in created_ids:
        try:
            service.delete_record(project=project, record_id=record_id, expected_version=1)
        except (service.RecordNotFoundError, service.VersionConflictError) as exc:
            rollback_failures.append(f"record {record_id}: rollback failed: {exc}")
    return rollback_failures


def _check_db_not_locked(project: str) -> None:
    """Pre-flight concurrency guard (inter-session message 20260819T114156Z-3ec7,
    gap #2): refuse to start --write if another connection already holds a write
    lock on the project's .db right now — e.g. a concurrent --write, or
    pdata-verify-all's scheduled cadence firing against the same project — rather
    than letting the first schema_add_field/add_record call inside the with-block
    below block on db.py's passive 5s busy_timeout and eventually fail with
    rows already mid-migration. Called as the very first thing inside write()'s
    project_db_dir_override/backup_dir_override with-block so store.db_path()
    resolves the rehearsal-sandboxed path when rehearsing, exactly like every
    other db access in this function.

    No-op if the project has no .db yet (a brand-new project's first --write) —
    nothing to guard, and connecting would create one just to probe it.

    The probe is a throwaway BEGIN IMMEDIATE / ROLLBACK on its own short-lived
    connection, not a lock held across the rest of write() — this does not
    protect against a second --write starting a few seconds *after* this check
    passes (a TOCTOU race that is out of scope; the existing passive
    busy_timeout is still the backstop for that narrower case). Its job is only
    to catch the common case of something *already* actively using the .db,
    immediately and with a clear message, instead of a raw sqlite3 error
    surfacing minutes into the migration.

    busy_timeout is overridden to 0 on this connection only — db.connect()'s
    shared 5000ms default is exactly the passive wait this probe exists to
    avoid; a probe that waits out the same timeout the mutation path already
    relies on would defeat the point of checking up front. isolation_level is
    set to None for the same reason repository.connect() sets it: so this
    caller-issued BEGIN IMMEDIATE isn't wrapped in a second, implicit
    transaction by sqlite3's own legacy transaction handling."""
    db_path = store.db_path(project)
    if not db_path.exists():
        return
    conn = db.connect(db_path)
    conn.isolation_level = None
    try:
        conn.execute("PRAGMA busy_timeout=0")
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            raise ValueError(
                f"cannot start 'ccst pdata init --write' for project {project!r}: "
                f"another process appears to be using {db_path} right now ({exc}) "
                f"— wait for it to finish, or confirm it isn't stuck, then re-run --write"
            ) from exc
        else:
            conn.execute("ROLLBACK")
    finally:
        conn.close()


def _format_published_at(latest_path: Path) -> str:
    return datetime.fromtimestamp(latest_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")


def _adopt_from_dump(
    *, project: str, project_root: Path, on_progress: Callable[[str], None] | None,
) -> WriteResult | None:
    """Spec "ccst pdata init on a second machine (adopt-from-dump)": a project already migrated
    to pdata on another machine publishes a dump into <project_root>/.pdata-db-dump/latest.sql —
    a machine with no local .db for this project yet must adopt that dump directly (same
    mechanism as the ongoing rehydrate trigger) rather than classifying/importing this machine's
    current flat files as if this were a first-ever migration, which would produce a DB
    disconnected from the dump's vector-clock history and likely duplicate content already
    captured elsewhere.

    Returns the early WriteResult to hand straight back from write() when adoption ran; None when
    there is nothing to adopt (no dump has ever been published for this project — the ordinary
    classify/import flow proceeds exactly as before this feature existed) or when this machine
    already has its own local .db for the project (already adopted/migrated here previously —
    ccst pdata rehydrate, not ccst pdata init, is the ongoing sync path for that case).

    Checked via the dump file's own existence, not DumpInfo.checksum_valid alone: read_latest()
    returns checksum_valid=False both when no dump was ever published and when one exists but is
    corrupt, and those two cases must not be treated the same — the first is the ordinary
    first-ever migration, the second is a real problem that must be surfaced, never silently
    papered over by falling through to classification."""
    latest_path = project_root / ".pdata-db-dump" / "latest.sql"
    if not latest_path.exists():
        return None
    info = dump.read_latest(project_root)
    if not info.checksum_valid:
        raise ValueError(
            f"ccst pdata init --project {project}: the existing sync dump at {latest_path} "
            "failed its checksum check — refusing to silently fall through to a fresh "
            "classification/import, which would create a second, disconnected history for this "
            "project. Publish a fresh dump from a known-good machine (ccst pdata dump --force) "
            "or reconcile manually (ccst pdata resolve) once those commands exist."
        )
    if store.db_path(project).exists():
        return None
    published_at = _format_published_at(latest_path)
    message = (
        f"Adopting existing pdata from sync dump (published by {info.machine_id}, "
        f"at {published_at}) - skipping file classification/import"
    )
    _emit(on_progress, message)
    rehydrate.rehydrate(project, force=True)
    return WriteResult(
        created_record_ids=[], entries_written=[], backup_path=None, failure=None,
        report=f"ccst pdata init — {project}: {message}.",
        adopted_from_dump=True,
    )


def write(
    *, project: str, rehearse: Path | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> WriteResult:
    project_root = init_paths.resolve_project_root(project, rehearse=rehearse)

    # Both rehearsal-isolation seams are entered for the whole function, not just the
    # write/verify/backup phase below: the adopt-from-dump check must run before the
    # proposal-file requirement (a second machine adopting from a dump has never run dry_run
    # here, so no proposal exists), and its store.db_path()/repository.connect() calls must
    # resolve against the rehearsal-sandboxed .db when rehearsing — project_db_dir_override
    # redirects the .db (Plan A's CCST_PROJECT_DB_DIR seam), backup_dir_override redirects where
    # backup.create_backup() below writes its tar.gz (this module's own CCST_PDATA_BACKUP_DIR
    # seam). Without the second seam, a rehearsed --write would still deposit a real
    # <project>-<YYYYMMDD-HHMMSS>.tar.gz into the production backup directory —
    # indistinguishable by filename from a genuine migration's backup. Both are no-ops when
    # rehearse is None.
    with (
        init_paths.project_db_dir_override(rehearse),
        init_paths.backup_dir_override(rehearse),
    ):
        adopted = _adopt_from_dump(
            project=project, project_root=project_root, on_progress=on_progress,
        )
        if adopted is not None:
            return adopted

        proposal_path = project_root / init_paths.PROPOSAL_FILENAME
        if not proposal_path.exists():
            raise FileNotFoundError(
                f"no classification proposal found at {proposal_path} — run "
                f"'ccst pdata init --project {project}' (add --rehearse if rehearsing) first"
            )
        m = manifest.load(proposal_path)
        _validate_no_conflicting_field_types(m)

        created_ids: list[int] = []
        reasons: list[str] = []
        written_entries: list[ManifestEntry] = []
        # (record_id, ImportRow) pairs per entry — kept (not just the id) so _verify can
        # spot-check DB content against what was actually imported, and so the
        # human-readable diff report (spec §7.1 step 4) has real content to show.
        entry_rows: dict[str, list[tuple[int, ImportRow]]] = {}

        _check_db_not_locked(project)

        db_owned = [e for e in m.entries if e.classification == "db-owned"]
        _emit(on_progress, f"Importing {len(db_owned)} file(s)...")
        for entry in m.entries:
            if entry.classification != "db-owned":
                continue
            try:
                _emit(on_progress, f"  importing {entry.path} -> group={entry.db_group()}...")
                for spec in entry.fields:
                    service.schema_add_field(
                        project=project, record_group=entry.db_group(),
                        field_name=spec.name, sql_type=spec.sql_type,
                        description=spec.description, default=spec.default,
                    )
                rows_for_entry: list[tuple[int, ImportRow]] = []
                for row in import_entry(project_root, entry):
                    record = service.add_record(
                        project=project, record_group=entry.db_group(),
                        content=row.content, file_path=row.file_path,
                        fields=row.fields, created_at=row.created_at,
                    )
                    created_ids.append(record.id)
                    rows_for_entry.append((record.id, row))
                entry_rows[entry.path] = rows_for_entry
                _emit(on_progress, f"    {len(rows_for_entry)} row(s) imported")
                written_entries.append(entry)
            except (ValueError, OSError, csv.Error) as exc:
                reasons.append(f"{entry.path}: {exc}")

        _emit(on_progress, "Verifying imported rows...")
        reasons.extend(
            _verify(project=project, project_root=project_root,
                    written_entries=written_entries, entry_rows=entry_rows)
        )

        if reasons:
            _emit(on_progress, "Verification failed — rolling back inserted rows...")
            return WriteResult(
                created_record_ids=[], entries_written=[], backup_path=None,
                failure=WriteFailure(
                    reasons=reasons + _rollback(project=project, created_ids=created_ids)
                ),
            )

        # Still inside both overrides: a rehearsed run's backup must land in the
        # rehearsal sandbox (backup_dir_override), never in the real backup dir.
        _emit(on_progress, "Backing up project and database before cutover...")
        try:
            backup_path = backup.create_backup(
                project=project, project_root=project_root, on_progress=on_progress,
            )
            _emit(on_progress, f"Backup written: {backup_path}")
        except backup.BackupError as exc:
            _emit(on_progress, "Backup failed — rolling back inserted rows...")
            return WriteResult(
                created_record_ids=[], entries_written=[], backup_path=None,
                failure=WriteFailure(
                    reasons=[str(exc)] + _rollback(project=project, created_ids=created_ids)
                ),
            )

    _emit(on_progress, f"Cutting over {len(written_entries)} file(s)...")
    cutover.archive_entries(project_root=project_root, entries=written_entries)
    return WriteResult(
        created_record_ids=created_ids,
        entries_written=[e.path for e in written_entries],
        backup_path=backup_path, failure=None,
        report=_render_diff_report(written_entries=written_entries, entry_rows=entry_rows),
    )


def _verify(
    *, project: str, project_root: Path, written_entries: list[ManifestEntry],
    entry_rows: dict[str, list[tuple[int, ImportRow]]],
) -> list[str]:
    """Spec §7.1 step 4: entry-count parity (DB rows vs. an independent re-count of
    the source file), a content spot-check (DB content vs. what was actually passed
    to add_record), and file_path resolution — all three must hold for every
    newly-inserted row before backup/cutover proceeds."""
    reasons: list[str] = []
    for entry in written_entries:
        rows = entry_rows[entry.path]
        expected_count = count_source_rows(project_root, entry)
        if len(rows) != expected_count:
            reasons.append(
                f"{entry.path}: imported {len(rows)} row(s) but re-counting the source "
                f"gives {expected_count} — entry-count parity check failed"
            )
        for record_id, import_row in rows:
            record = service.get_record(project=project, record_id=record_id)
            assert record is not None, (
                f"record {record_id} inserted this run but missing during verification"
            )
            if record.content != import_row.content:
                reasons.append(
                    f"{entry.path}: record {record_id} content does not match what "
                    f"was imported — content spot-check failed"
                )
            if record.file_path:
                target = project_root / record.file_path
                if not target.exists():
                    reasons.append(
                        f"{entry.path}: record {record_id} file_path "
                        f"{record.file_path!r} does not resolve under {project_root}"
                    )
    return reasons


def _render_diff_report(
    *, written_entries: list[ManifestEntry],
    entry_rows: dict[str, list[tuple[int, ImportRow]]],
) -> str:
    """Spec §7.1 step 4's human-readable diff report — old content vs. what landed
    in the DB — for review. Printed by the CLI as part of a successful `--write`'s
    output, immediately after verification passed and before the process would
    otherwise exit; there is no separate interactive confirmation gate ahead of
    backup/cutover (`--write` stays a single atomic operation), so this report is
    the review artifact for that already-completed run, not a pre-cutover prompt —
    the human review gate the spec protects against a wrong classification lives at
    step 2 (proposal review) and step 0 (--rehearse), both of which run before
    --write is ever invoked."""
    lines = ["ccst pdata init --write — verification diff report:"]
    for entry in written_entries:
        rows = entry_rows[entry.path]
        lines.append(f"  {entry.path}: {len(rows)} row(s) -> group={entry.db_group()}")
        for record_id, row in rows[:3]:
            preview = row.content[:80].replace("\n", " ")
            lines.append(f"    id={record_id} content={preview!r}")
        if len(rows) > 3:
            lines.append(f"    ... and {len(rows) - 3} more row(s)")
    return "\n".join(lines)
