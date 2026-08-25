"""Record-group rename for `ccst pdata rename-group` (feature request from the `home` project's
migration-test session, message 20260822T114514Z-91ed).

A record_group rename touches three places that must move together in one transaction -
`records.record_group`, `record_group_fields.record_group`, and the `ext_<group>` table's own
name - plus a fourth, non-transactional place that is just as easy to forget: any matching entry
in `.ccst-pdata-proposal.json` (the classification manifest, spec §7.1). Skip that and
verify.check_row_count_parity cross-checks live row counts against the manifest's archived-file
entries by record_group; every renamed group then fails `ccst pdata verify` forever with a
"possible data loss" row-count-parity issue that isn't actually data loss - it's just comparing
the new name's row count against the old name's manifest entry.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from cc_session_tools.lib.pdata import backup, init_paths, manifest, naming, repository


@dataclass(frozen=True, slots=True)
class RenameGroupPlan:
    project: str
    project_root: Path
    old: str
    new: str
    row_count: int
    has_extension_table: bool
    manifest_entry_paths: list[str]


@dataclass(frozen=True, slots=True)
class RenameGroupFailure:
    reasons: list[str]


@dataclass(frozen=True, slots=True)
class RenameGroupResult:
    plan: RenameGroupPlan
    backup_path: Path | None
    failure: RenameGroupFailure | None


def _plan(*, project: str, project_root: Path, old: str, new: str) -> RenameGroupPlan:
    naming.validate_record_group(old)
    naming.validate_record_group(new)
    if old == new:
        raise ValueError(f"--from and --to are the same record_group: {old!r}")

    conn = repository.connect(project)
    try:
        old_row_count = conn.execute(
            "SELECT COUNT(*) FROM records WHERE record_group=?", (old,)
        ).fetchone()[0]
        old_has_ext = repository.extension_table_exists(conn, old)
        if old_row_count == 0 and not old_has_ext:
            raise ValueError(f"no such record_group {old!r} in project {project!r}")

        # "has rows" is checked including soft-deleted rows, and an ext table with zero rows
        # (a schema-only group) also counts - both would otherwise collide silently with the
        # ALTER TABLE RENAME TO / UPDATE below instead of raising a clear refusal up front.
        new_row_count = conn.execute(
            "SELECT COUNT(*) FROM records WHERE record_group=?", (new,)
        ).fetchone()[0]
        new_has_ext = repository.extension_table_exists(conn, new)
        if new_row_count > 0 or new_has_ext:
            raise ValueError(
                f"record_group {new!r} already exists in project {project!r} - "
                f"rename-group refuses to merge into an existing group"
            )
    finally:
        conn.close()

    proposal_path = project_root / init_paths.PROPOSAL_FILENAME
    manifest_entry_paths: list[str] = []
    if proposal_path.exists():
        m = manifest.load(proposal_path)
        manifest_entry_paths = [e.path for e in m.entries if e.record_group == old]

    return RenameGroupPlan(
        project=project, project_root=project_root, old=old, new=new,
        row_count=old_row_count, has_extension_table=old_has_ext,
        manifest_entry_paths=manifest_entry_paths,
    )


def dry_run(*, project: str, project_root: Path, old: str, new: str) -> RenameGroupPlan:
    return _plan(project=project, project_root=project_root, old=old, new=new)


def _rename_in_db(project: str, *, old: str, new: str) -> None:
    conn = repository.connect(project)
    try:
        with repository._immediate(conn):
            if repository.extension_table_exists(conn, old):
                old_table = naming.extension_table_name(old)
                new_table = naming.extension_table_name(new)
                conn.execute(f'ALTER TABLE "{old_table}" RENAME TO "{new_table}"')
            conn.execute("UPDATE records SET record_group=? WHERE record_group=?", (new, old))
            conn.execute(
                "UPDATE record_group_fields SET record_group=? WHERE record_group=?", (new, old)
            )
    finally:
        conn.close()


def _rename_in_manifest(project_root: Path, *, old: str, new: str) -> int:
    """Returns the number of entries updated. No-op (returns 0) if the project was never
    migrated - nothing to update against."""
    proposal_path = project_root / init_paths.PROPOSAL_FILENAME
    if not proposal_path.exists():
        return 0
    m = manifest.load(proposal_path)
    updated = 0
    for entry in m.entries:
        if entry.record_group == old:
            entry.record_group = new
            updated += 1
    if updated:
        manifest.save(m, proposal_path)
    return updated


def write(*, project: str, project_root: Path, old: str, new: str) -> RenameGroupResult:
    plan = _plan(project=project, project_root=project_root, old=old, new=new)

    # Safety net before any write, matching reorganize.write() / init_service.write()'s own
    # use of the same helper.
    try:
        backup_path = backup.create_backup(project=project, project_root=project_root)
    except backup.BackupError as exc:
        return RenameGroupResult(
            plan=plan, backup_path=None, failure=RenameGroupFailure(reasons=[str(exc)]),
        )

    try:
        _rename_in_db(project, old=old, new=new)
    except sqlite3.Error as exc:
        return RenameGroupResult(
            plan=plan, backup_path=backup_path, failure=RenameGroupFailure(reasons=[str(exc)]),
        )

    # The DB rename already committed by this point - a manifest-write failure here (a
    # permissions error, a malformed manifest another process wrote concurrently) must be
    # reported clearly rather than silently leaving the manifest pointing at the old name,
    # since that's exactly the "possible data loss" verify failure this command exists to
    # prevent.
    try:
        _rename_in_manifest(project_root, old=old, new=new)
    except (OSError, ValueError) as exc:
        return RenameGroupResult(
            plan=plan, backup_path=backup_path,
            failure=RenameGroupFailure(reasons=[
                f"records/record_group_fields/ext table renamed successfully, but updating "
                f"{init_paths.PROPOSAL_FILENAME} failed: {exc} - fix it by hand for these "
                f"paths: {plan.manifest_entry_paths}"
            ]),
        )

    return RenameGroupResult(plan=plan, backup_path=backup_path, failure=None)
