from __future__ import annotations

import json

import pytest

from cc_session_tools.lib.pdata import manifest, rename_group, repository, service


def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv("CCST_PDATA_BACKUP_DIR", str(tmp_path / "backups"))


def test_dry_run_reports_row_count_and_no_extension_table(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)
    service.add_record(project="demo", record_group="old-name", content="x", file_path=None, fields={})

    plan = rename_group.dry_run(project="demo", project_root=project_root, old="old-name", new="new-name")

    assert plan.row_count == 1
    assert plan.has_extension_table is False
    assert plan.manifest_entry_paths == []


def test_dry_run_rejects_unknown_old_group(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)

    with pytest.raises(ValueError, match="no such record_group"):
        rename_group.dry_run(project="demo", project_root=project_root, old="nope", new="new-name")


def test_dry_run_rejects_new_group_that_already_has_rows(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)
    service.add_record(project="demo", record_group="old-name", content="x", file_path=None, fields={})
    service.add_record(project="demo", record_group="new-name", content="y", file_path=None, fields={})

    with pytest.raises(ValueError, match="already exists"):
        rename_group.dry_run(project="demo", project_root=project_root, old="old-name", new="new-name")


def test_dry_run_rejects_same_old_and_new(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)
    service.add_record(project="demo", record_group="old-name", content="x", file_path=None, fields={})

    with pytest.raises(ValueError, match="are the same record_group"):
        rename_group.dry_run(project="demo", project_root=project_root, old="old-name", new="old-name")


def test_dry_run_finds_matching_manifest_entries(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)
    service.add_record(project="demo", record_group="old-name", content="x", file_path=None, fields={})
    m = manifest.Manifest(project="demo", entries=[
        manifest.ManifestEntry(
            path="receipts.csv", classification="db-owned", record_group="old-name",
            strategy="csv-rows",
        ),
        manifest.ManifestEntry(path="README.md", classification="folder-owned"),
    ])
    manifest.save(m, project_root / ".ccst-pdata-proposal.json")

    plan = rename_group.dry_run(project="demo", project_root=project_root, old="old-name", new="new-name")

    assert plan.manifest_entry_paths == ["receipts.csv"]


def test_write_renames_records_and_extension_table(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)
    record = service.add_record(
        project="demo", record_group="old-name", content="x", file_path=None, fields={},
    )
    service.schema_add_field(
        project="demo", record_group="old-name", field_name="amount", sql_type="TEXT",
        description="an amount field", default=None,
    )

    result = rename_group.write(project="demo", project_root=project_root, old="old-name", new="new-name")

    assert result.failure is None
    assert result.backup_path is not None
    assert result.backup_path.exists()

    conn = repository.connect("demo")
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM records WHERE record_group='old-name'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM records WHERE record_group='new-name'"
        ).fetchone()[0] == 1
        assert repository.extension_table_exists(conn, "old-name") is False
        assert repository.extension_table_exists(conn, "new-name") is True
        assert conn.execute(
            "SELECT COUNT(*) FROM record_group_fields WHERE record_group='old-name'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT field_name FROM record_group_fields WHERE record_group='new-name'"
        ).fetchone()[0] == "amount"
    finally:
        conn.close()

    updated = service.get_record(project="demo", record_id=record.id)
    assert updated is not None
    assert updated.record_group == "new-name"


def test_write_updates_matching_manifest_entries_only(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)
    service.add_record(project="demo", record_group="old-name", content="x", file_path=None, fields={})
    proposal_path = project_root / ".ccst-pdata-proposal.json"
    m = manifest.Manifest(project="demo", entries=[
        manifest.ManifestEntry(
            path="receipts.csv", classification="db-owned", record_group="old-name",
            strategy="csv-rows",
        ),
        manifest.ManifestEntry(
            path="other.csv", classification="db-owned", record_group="unrelated",
            strategy="csv-rows",
        ),
    ])
    manifest.save(m, proposal_path)

    result = rename_group.write(project="demo", project_root=project_root, old="old-name", new="new-name")

    assert result.failure is None
    reloaded = manifest.load(proposal_path)
    by_path = {e.path: e.record_group for e in reloaded.entries}
    assert by_path["receipts.csv"] == "new-name"
    assert by_path["other.csv"] == "unrelated"


def test_write_is_a_noop_on_disk_when_project_was_never_migrated(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)
    service.add_record(project="demo", record_group="old-name", content="x", file_path=None, fields={})

    result = rename_group.write(project="demo", project_root=project_root, old="old-name", new="new-name")

    assert result.failure is None
    assert not (project_root / ".ccst-pdata-proposal.json").exists()
