from __future__ import annotations

import re

import pytest

from cc_session_tools.lib.pdata import reorganize, service


def test_dry_run_computes_by_year_moves_from_filename_dates(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    project_root = tmp_path / "projects" / "demo"
    corr = project_root / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-1030--A-to-B--email.md").write_text("x")
    (corr / "2026.01.02-0900--C-to-D--email.md").write_text("y")

    plan = reorganize.dry_run(
        project="demo", project_root=project_root, folder="correspondence",
        strategy="by-year",
    )

    moves = {m.old_relative: m.new_relative for m in plan.moves}
    assert moves["correspondence/2025.03.14-1030--A-to-B--email.md"] == \
        "correspondence/2025/2025.03.14-1030--A-to-B--email.md"
    assert moves["correspondence/2026.01.02-0900--C-to-D--email.md"] == \
        "correspondence/2026/2026.01.02-0900--C-to-D--email.md"
    assert plan.matched_records == []
    assert plan.external_references == []


def test_dry_run_falls_back_to_mtime_when_no_leading_date(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    project_root = tmp_path / "projects" / "demo"
    corr = project_root / "correspondence"
    corr.mkdir(parents=True)
    f = corr / "no-date-in-name.md"
    f.write_text("x")

    plan = reorganize.dry_run(
        project="demo", project_root=project_root, folder="correspondence",
        strategy="by-year",
    )

    # mtime-derived year - just assert it landed under *some* four-digit year folder,
    # not a specific one (avoids a flaky test pinned to "this year").
    (move,) = plan.moves
    assert re.fullmatch(r"correspondence/\d{4}/no-date-in-name\.md", move.new_relative)


def test_dry_run_finds_matching_pdata_records(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    project_root = tmp_path / "projects" / "demo"
    corr = project_root / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-note.md").write_text("x")
    service.add_record(
        project="demo", record_group="letters", content="x",
        file_path="correspondence/2025.03.14-note.md", fields={}, created_at=1,
    )

    plan = reorganize.dry_run(
        project="demo", project_root=project_root, folder="correspondence",
        strategy="by-year",
    )

    assert len(plan.matched_records) == 1
    matched = plan.matched_records[0]
    assert matched.new_file_path == "correspondence/2025/2025.03.14-note.md"


def test_dry_run_reports_external_references_without_moving_anything(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    project_root = tmp_path / "projects" / "demo"
    corr = project_root / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-note.md").write_text("x")
    (project_root / "CLAUDE.md").write_text(
        "See correspondence/2025.03.14-note.md for the full record.\n"
    )

    plan = reorganize.dry_run(
        project="demo", project_root=project_root, folder="correspondence",
        strategy="by-year",
    )

    assert len(plan.external_references) == 1
    ref = plan.external_references[0]
    assert ref.file == project_root / "CLAUDE.md"
    assert "correspondence/2025.03.14-note.md" in ref.line_text
    # Nothing moved - dry_run never touches the filesystem or the DB.
    assert (corr / "2025.03.14-note.md").exists()


def test_dry_run_computes_by_year_month_moves_from_filename_dates(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    project_root = tmp_path / "projects" / "demo"
    corr = project_root / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-1030--A-to-B--email.md").write_text("x")

    plan = reorganize.dry_run(
        project="demo", project_root=project_root, folder="correspondence",
        strategy="by-year-month",
    )

    (move,) = plan.moves
    assert move.new_relative == "correspondence/2025/03/2025.03.14-1030--A-to-B--email.md"


def test_dry_run_by_year_month_falls_back_to_mtime_when_no_leading_date(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    project_root = tmp_path / "projects" / "demo"
    corr = project_root / "correspondence"
    corr.mkdir(parents=True)
    (corr / "no-date-in-name.md").write_text("x")

    plan = reorganize.dry_run(
        project="demo", project_root=project_root, folder="correspondence",
        strategy="by-year-month",
    )

    (move,) = plan.moves
    assert re.fullmatch(r"correspondence/\d{4}/\d{2}/no-date-in-name\.md", move.new_relative)


def test_dry_run_excludes_ccst_bookkeeping_files_from_external_references(monkeypatch, tmp_path):
    """A project that has already run `ccst pdata init` has a .ccst-pdata-proposal.json (and
    possibly a ccst-pdata-init-write.log) at its root, both of which literally contain the
    file_path of every classified entry - including files under whatever folder is being
    reorganized. These must not be reported as external references needing manual review."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    project_root = tmp_path / "projects" / "demo"
    corr = project_root / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-note.md").write_text("x")
    (project_root / ".ccst-pdata-proposal.json").write_text(
        '{"entries": [{"path": "correspondence/2025.03.14-note.md"}]}\n'
    )
    (project_root / "ccst-pdata-init-write.log").write_text(
        "importing correspondence/2025.03.14-note.md -> group=correspondence...\n"
    )

    plan = reorganize.dry_run(
        project="demo", project_root=project_root, folder="correspondence",
        strategy="by-year",
    )

    assert plan.external_references == []


def test_dry_run_rejects_unknown_strategy(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    project_root = tmp_path / "projects" / "demo"
    (project_root / "correspondence").mkdir(parents=True)

    with pytest.raises(ValueError, match="strategy"):
        reorganize.dry_run(
            project="demo", project_root=project_root, folder="correspondence",
            strategy="by-topic",
        )


def test_dry_run_rejects_missing_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)

    with pytest.raises(FileNotFoundError, match="correspondence"):
        reorganize.dry_run(
            project="demo", project_root=project_root, folder="correspondence",
            strategy="by-year",
        )


def test_dry_run_rejects_absolute_folder_path(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)

    with pytest.raises(ValueError, match="absolute path"):
        reorganize.dry_run(
            project="demo", project_root=project_root, folder="/etc",
            strategy="by-year",
        )


def test_dry_run_rejects_folder_with_parent_traversal(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)

    with pytest.raises(ValueError, match="path-traversal"):
        reorganize.dry_run(
            project="demo", project_root=project_root, folder="../../etc",
            strategy="by-year",
        )
