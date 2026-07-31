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
