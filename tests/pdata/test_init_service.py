from __future__ import annotations

from cc_session_tools.lib.pdata import init_paths, init_service


def test_dry_run_empty_project_reports_no_files_and_creates_db(monkeypatch, tmp_path):
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))

    result = init_service.dry_run(project="biz")

    assert "no files found" in result.report
    assert result.manifest.entries == []
    assert (tmp_path / "dbs" / "biz.db").exists()


def test_dry_run_reports_classified_entries(monkeypatch, tmp_path):
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "CLAUDE.md").write_text("# demo\n")
    (project_dir / "ideas.csv").write_text("idea\nfirst\n")

    result = init_service.dry_run(project="demo")

    assert "[folder-owned] CLAUDE.md" in result.report
    assert "[db-owned]     ideas.csv -> group=ideas strategy=csv-rows" in result.report
    assert result.proposal_path == project_dir / init_paths.PROPOSAL_FILENAME
    assert result.proposal_path.exists()


def test_dry_run_second_call_preserves_hand_edited_proposal(monkeypatch, tmp_path):
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "notes.md").write_text("## one\nbody\n")

    from cc_session_tools.lib.pdata import manifest

    first = init_service.dry_run(project="demo")
    edited = manifest.load(first.proposal_path)
    edited.entries[0].classification = "db-owned"
    edited.entries[0].record_group = "notes"
    edited.entries[0].strategy = "delimited-sections"
    edited.entries[0].reviewed = True
    manifest.save(edited, first.proposal_path)

    second = init_service.dry_run(project="demo")
    assert second.manifest.entries[0].classification == "db-owned"
    assert second.manifest.entries[0].reviewed is True


def test_dry_run_disambiguates_against_existing_live_record_group(monkeypatch, tmp_path):
    """A record_group can already have live rows — from an earlier ccst pdata init
    run, or from an unrelated mechanism entirely (e.g. Plan A's service.add_record
    used directly, or a different plan's own writes) — even before this project's
    first classification pass for a *new* file proposing that same name. The
    fresh pass must never silently propose merging into it."""
    from cc_session_tools.lib.pdata import service

    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "notes.csv").write_text("x\n1\n")

    service.add_record(
        project="demo", record_group="notes", content="pre-existing",
        file_path=None, fields={},
    )

    result = init_service.dry_run(project="demo")

    assert result.manifest.entries[0].record_group != "notes"


def test_write_without_prior_dry_run_raises(monkeypatch, tmp_path):
    import pytest

    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    (tmp_path / "projects" / "demo").mkdir(parents=True)

    with pytest.raises(FileNotFoundError, match="proposal"):
        init_service.write(project="demo")


def test_write_imports_csv_rows_and_cuts_over(monkeypatch, tmp_path):
    from cc_session_tools.lib.pdata import service

    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv("CCST_PDATA_BACKUP_DIR", str(tmp_path / "backups"))
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "ideas.csv").write_text("idea\nfirst\nsecond\n")

    init_service.dry_run(project="demo")
    result = init_service.write(project="demo")

    assert result.failure is None
    assert len(result.created_record_ids) == 2
    assert result.entries_written == ["ideas.csv"]
    assert result.backup_path is not None and result.backup_path.exists()
    assert not (project_dir / "ideas.csv").exists()
    assert (project_dir / init_paths.MIGRATED_ARCHIVE_DIRNAME / "ideas.csv").exists()
    assert "ideas.csv: 2 row(s)" in result.report

    records = service.list_records(project="demo", record_group="ideas")
    assert {r.content for r in records} == {'{"idea": "first"}', '{"idea": "second"}'}


def test_write_aborts_and_soft_deletes_on_absolute_file_path(monkeypatch, tmp_path):
    from cc_session_tools.lib.pdata import manifest, service

    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv("CCST_PDATA_BACKUP_DIR", str(tmp_path / "backups"))
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "docs.csv").write_text("doc_path,note\n/etc/passwd,bad\n")

    dry = init_service.dry_run(project="demo")
    edited = manifest.load(dry.proposal_path)
    edited.entries[0].file_path_column = "doc_path"
    manifest.save(edited, dry.proposal_path)

    result = init_service.write(project="demo")

    assert result.failure is not None
    assert result.entries_written == []
    assert result.backup_path is None
    # nothing cut over, original file untouched
    assert (project_dir / "docs.csv").exists()
    assert not (project_dir / init_paths.MIGRATED_ARCHIVE_DIRNAME).exists()
    # no live rows left over from the aborted attempt
    assert service.list_records(project="demo", record_group="docs") == []


def test_write_verification_catches_unresolved_file_path_and_soft_deletes(monkeypatch, tmp_path):
    from cc_session_tools.lib.pdata import manifest, service

    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv("CCST_PDATA_BACKUP_DIR", str(tmp_path / "backups"))
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "docs.csv").write_text("doc_path,note\nmissing/does-not-exist.pdf,bad\n")

    dry = init_service.dry_run(project="demo")
    edited = manifest.load(dry.proposal_path)
    edited.entries[0].file_path_column = "doc_path"
    manifest.save(edited, dry.proposal_path)

    result = init_service.write(project="demo")

    assert result.failure is not None
    assert any("does not resolve" in reason for reason in result.failure.reasons)
    assert not (project_dir / init_paths.MIGRATED_ARCHIVE_DIRNAME).exists()
    assert service.list_records(
        project="demo", record_group="docs", include_deleted=True
    )[0].deleted_at is not None


def test_write_rehearse_leaves_real_project_db_and_backup_dir_untouched(monkeypatch, tmp_path):
    """Covers all three of --rehearse's isolation seams: project files, the .db
    (via CCST_PROJECT_DB_DIR), and the backup tarball (via CCST_PDATA_BACKUP_DIR).
    A real production backup dir is set here specifically so a bug that skips the
    backup-dir redirection would deposit a real tar.gz into it and fail this
    test, rather than the test silently writing into whatever default
    paths.data_home() resolves to on the machine running it."""
    import shutil

    from cc_session_tools.lib.pdata import service

    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    real_backup_dir = tmp_path / "real-backups"
    monkeypatch.setenv("CCST_PDATA_BACKUP_DIR", str(real_backup_dir))
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "ideas.csv").write_text("idea\nfirst\n")
    rehearsal_dir = tmp_path / "rehearsal-demo"
    shutil.copytree(project_dir, rehearsal_dir)

    init_service.dry_run(project="demo", rehearse=rehearsal_dir)
    result = init_service.write(project="demo", rehearse=rehearsal_dir)

    assert result.failure is None
    # real project folder still has its original file...
    assert (project_dir / "ideas.csv").exists()
    # ...and the real (non-rehearsal) db has no rows from the rehearsal run...
    assert service.list_records(project="demo", record_group="ideas") == []
    # ...and the rehearsal's backup tarball landed inside the rehearsal sandbox,
    # never in the real CCST_PDATA_BACKUP_DIR a genuine migration would use.
    assert result.backup_path is not None
    assert rehearsal_dir in result.backup_path.parents
    assert not real_backup_dir.exists() or not any(real_backup_dir.iterdir())


def test_write_aborts_and_soft_deletes_on_oversized_csv_field(monkeypatch, tmp_path):
    """csv.Error (e.g. a field exceeding csv.field_size_limit()) must be caught by the
    same abort-and-soft-delete path as ValueError/OSError, not crash write() with an
    unhandled exception mid-run."""
    import csv

    from cc_session_tools.lib.pdata import service

    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv("CCST_PDATA_BACKUP_DIR", str(tmp_path / "backups"))
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "ideas.csv").write_text("idea\nfirst\n" + ("x" * 200_000) + "\n")

    old_limit = csv.field_size_limit()
    csv.field_size_limit(100_000)
    try:
        init_service.dry_run(project="demo")
        result = init_service.write(project="demo")
    finally:
        csv.field_size_limit(old_limit)

    assert result.failure is not None
    assert service.list_records(project="demo", record_group="ideas") == []


def test_write_reports_rollback_failure_without_crashing(monkeypatch, tmp_path):
    """A RecordNotFoundError/VersionConflictError raised mid-rollback (both plain
    Exception subclasses, not ValueError/OSError) must be collected into the
    returned WriteFailure, not propagate and abort the rollback loop partway.

    This must reuse a fixture where at least one row is actually inserted before
    the run fails, or created_ids stays empty and the rollback loop (and this
    test's mocked delete_record) never runs at all. An absolute/'..'
    file_path_column value is the wrong fixture for that reason: add_record's
    _validate_relative_file_path() raises before repository.connect() is ever
    called, so no row lands and created_ids is empty. The unresolved-but-relative
    file_path fixture below inserts the row successfully and only fails later, in
    _verify() — which is what gives rollback something to actually do."""
    from cc_session_tools.lib.pdata import manifest, service

    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv("CCST_PDATA_BACKUP_DIR", str(tmp_path / "backups"))
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "docs.csv").write_text(
        "doc_path,note\nmissing/does-not-exist.pdf,bad\n"
    )

    dry = init_service.dry_run(project="demo")
    edited = manifest.load(dry.proposal_path)
    edited.entries[0].file_path_column = "doc_path"
    manifest.save(edited, dry.proposal_path)

    def _flaky_delete_record(**kwargs):
        raise service.RecordNotFoundError(kwargs["record_id"])

    monkeypatch.setattr(service, "delete_record", _flaky_delete_record)
    result = init_service.write(project="demo")

    assert result.failure is not None
    assert any("does not resolve" in reason for reason in result.failure.reasons)
    assert any("rollback failed" in reason for reason in result.failure.reasons)


def test_write_rolls_back_and_reports_failure_when_backup_raises(monkeypatch, tmp_path):
    from cc_session_tools.lib.pdata import backup, service

    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv("CCST_PDATA_BACKUP_DIR", str(tmp_path / "backups"))
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "ideas.csv").write_text("idea\nfirst\nsecond\n")

    init_service.dry_run(project="demo")

    def _always_fails(**kwargs):
        raise backup.BackupError("simulated backup failure")

    monkeypatch.setattr(backup, "create_backup", _always_fails)

    result = init_service.write(project="demo")

    assert result.failure is not None
    assert any("simulated backup failure" in reason for reason in result.failure.reasons)
    assert service.list_records(project="demo", record_group="ideas") == []
    # Nothing was cut over — source file untouched, no .pdata-migrated dir.
    assert (project_dir / "ideas.csv").exists()
    assert not (project_dir / init_paths.MIGRATED_ARCHIVE_DIRNAME).exists()


def test_write_rejects_conflicting_field_sql_types_across_entries(monkeypatch, tmp_path):
    """Two manifest entries feeding the same record_group with the same field name
    but a different sql_type must be rejected before any DDL/import runs — Plan
    A's schema_add_field silently no-ops on an already-existing column, which
    would otherwise drop the second entry's type with no error or warning."""
    import pytest

    from cc_session_tools.lib.pdata import manifest

    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "a.csv").write_text("count\n1\n")
    (project_dir / "b.csv").write_text("count\nx\n")

    dry = init_service.dry_run(project="demo")
    edited = manifest.load(dry.proposal_path)
    for entry in edited.entries:
        entry.record_group = "shared"
    edited.entries[0].fields[0].sql_type = "INTEGER"
    edited.entries[1].fields[0].sql_type = "TEXT"
    manifest.save(edited, dry.proposal_path)

    with pytest.raises(ValueError, match="conflicting sql_type"):
        init_service.write(project="demo")


def test_write_aborts_and_soft_deletes_on_manifest_strategy_shape_mismatch(monkeypatch, tmp_path):
    """A hand-edited manifest entry (pm-project-init Step 4) can assign a strategy
    that doesn't match the file's actual JSON shape — importers.py raises
    ValueError for this (see test_importers.py), and that ValueError must hit the
    same abort-and-soft-delete path as any other per-entry failure, not crash
    write() with an unhandled AttributeError/TypeError."""
    from cc_session_tools.lib.pdata import manifest, service

    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv("CCST_PDATA_BACKUP_DIR", str(tmp_path / "backups"))
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "chars.json").write_text('{"name": "solo"}')  # a JSON object

    dry = init_service.dry_run(project="demo")
    edited = manifest.load(dry.proposal_path)
    # force a strategy that doesn't match this file's actual (object) shape
    edited.entries[0].strategy = "json-array-rows"
    manifest.save(edited, dry.proposal_path)

    result = init_service.write(project="demo")

    assert result.failure is not None
    assert any("json-array-rows" in reason for reason in result.failure.reasons)
    assert service.list_records(project="demo", record_group="chars") == []
