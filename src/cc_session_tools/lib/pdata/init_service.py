"""Orchestration for `ccst pdata init` (spec §7): dry-run classification (steps
0-2) and the write/verify/backup/cutover phase. Every DB write goes through Plan
A's service.py — this module owns no SQL of its own.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cc_session_tools.lib.pdata import init_paths, manifest, repository, service
from cc_session_tools.lib.pdata.manifest import Manifest


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
