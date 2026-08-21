from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from cc_session_tools.lib.pdata import backup, reorganize, service


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


def test_write_moves_files_and_updates_matching_records(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(backup.BACKUP_DIR_ENV, str(tmp_path / "backups"))
    project_root = tmp_path / "projects" / "demo"
    corr = project_root / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-note.md").write_text("x")
    record = service.add_record(
        project="demo", record_group="letters", content="x",
        file_path="correspondence/2025.03.14-note.md", fields={}, created_at=1,
    )

    result = reorganize.write(
        project="demo", project_root=project_root, folder="correspondence",
        strategy="by-year",
    )

    assert result.failure is None
    assert not (corr / "2025.03.14-note.md").exists()
    assert (corr / "2025" / "2025.03.14-note.md").exists()
    updated = service.list_records(project="demo", record_group="letters")[0]
    assert updated.file_path == "correspondence/2025/2025.03.14-note.md"
    assert updated.version == record.version + 1
    assert result.backup_path.exists()


def test_write_uses_git_mv_when_project_root_is_a_git_repo(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(backup.BACKUP_DIR_ENV, str(tmp_path / "backups"))
    project_root = tmp_path / "projects" / "demo"
    corr = project_root / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-note.md").write_text("x")
    subprocess.run(["git", "init", "-q"], cwd=project_root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=project_root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t.com", "-c", "user.name=t",
                    "commit", "-q", "-m", "init"], cwd=project_root, check=True)

    reorganize.write(project="demo", project_root=project_root, folder="correspondence",
                      strategy="by-year")

    # write() only stages the rename via `git mv` - it never commits - so this checks the
    # staged rename directly rather than `git log --follow`, which walks committed history
    # only and would see nothing yet.
    status = subprocess.run(["git", "status", "--short"],
                            cwd=project_root, capture_output=True, text=True, check=True)
    assert "correspondence/2025.03.14-note.md" in status.stdout
    assert "correspondence/2025/2025.03.14-note.md" in status.stdout


def test_write_rolls_back_moved_files_on_record_update_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(backup.BACKUP_DIR_ENV, str(tmp_path / "backups"))
    project_root = tmp_path / "projects" / "demo"
    corr = project_root / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-note.md").write_text("x")
    service.add_record(
        project="demo", record_group="letters", content="x",
        file_path="correspondence/2025.03.14-note.md", fields={}, created_at=1,
    )

    # Force a version conflict: simplest way to simulate this without threads is to monkeypatch
    # service.update_record to raise once.
    calls = {"n": 0}
    def _flaky_update(**kwargs):
        calls["n"] += 1
        raise service.VersionConflictError(current={}, attempted={})
    monkeypatch.setattr(reorganize.service, "update_record", _flaky_update)

    result = reorganize.write(project="demo", project_root=project_root, folder="correspondence",
                              strategy="by-year")

    assert result.failure is not None
    assert calls["n"] == 1
    # Rolled back: the file is back at its original flat location, not left half-moved.
    assert (corr / "2025.03.14-note.md").exists()
    assert not (corr / "2025").exists()


def test_write_reverts_earlier_successful_record_update_when_a_later_one_fails(monkeypatch, tmp_path):
    """Regression test: a naive rollback that only undoes file moves (not already-applied
    update_record() calls) leaves an earlier-succeeded record's file_path pointing at the new
    location while its file gets moved back to the old one - a dangling reference. Both the
    file and the DB row must end up consistent with each other."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(backup.BACKUP_DIR_ENV, str(tmp_path / "backups"))
    project_root = tmp_path / "projects" / "demo"
    corr = project_root / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-a.md").write_text("a")
    (corr / "2025.03.15-b.md").write_text("b")
    service.add_record(
        project="demo", record_group="letters", content="a",
        file_path="correspondence/2025.03.14-a.md", fields={}, created_at=1,
    )
    service.add_record(
        project="demo", record_group="letters", content="b",
        file_path="correspondence/2025.03.15-b.md", fields={}, created_at=1,
    )

    real_update = service.update_record
    def _fail_for_b(**kwargs):
        if (kwargs.get("file_path") or "").endswith("b.md"):
            raise service.VersionConflictError(current={}, attempted={})
        return real_update(**kwargs)
    monkeypatch.setattr(reorganize.service, "update_record", _fail_for_b)

    result = reorganize.write(project="demo", project_root=project_root, folder="correspondence",
                              strategy="by-year")

    assert result.failure is not None
    # Both files back at their original flat location, and record "a"'s file_path reverted too.
    assert (corr / "2025.03.14-a.md").exists()
    assert (corr / "2025.03.15-b.md").exists()
    assert not (corr / "2025").exists()
    records = {r.content: r for r in service.list_records(project="demo", record_group="letters")}
    assert records["a"].file_path == "correspondence/2025.03.14-a.md"
    assert records["b"].file_path == "correspondence/2025.03.15-b.md"


def test_write_reverts_earlier_successful_record_update_when_a_later_one_fails_reverse_order(
    monkeypatch, tmp_path,
):
    """Same scenario as the test above with "b" inserted (and so processed) before "a" -
    plan.matched_records order follows DB insertion order, so this is a genuinely different
    processing order, not just a relabelling. write()'s rollback logic itself doesn't depend
    on which matched record comes first, but that needs its own test to actually be exercised,
    not just asserted in a comment on a test that always inserts in the same order."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(backup.BACKUP_DIR_ENV, str(tmp_path / "backups"))
    project_root = tmp_path / "projects" / "demo"
    corr = project_root / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-a.md").write_text("a")
    (corr / "2025.03.15-b.md").write_text("b")
    service.add_record(
        project="demo", record_group="letters", content="b",
        file_path="correspondence/2025.03.15-b.md", fields={}, created_at=1,
    )
    service.add_record(
        project="demo", record_group="letters", content="a",
        file_path="correspondence/2025.03.14-a.md", fields={}, created_at=1,
    )

    real_update = service.update_record
    def _fail_for_a(**kwargs):
        if (kwargs.get("file_path") or "").endswith("a.md"):
            raise service.VersionConflictError(current={}, attempted={})
        return real_update(**kwargs)
    monkeypatch.setattr(reorganize.service, "update_record", _fail_for_a)

    result = reorganize.write(project="demo", project_root=project_root, folder="correspondence",
                              strategy="by-year")

    assert result.failure is not None
    assert (corr / "2025.03.14-a.md").exists()
    assert (corr / "2025.03.15-b.md").exists()
    assert not (corr / "2025").exists()
    records = {r.content: r for r in service.list_records(project="demo", record_group="letters")}
    assert records["a"].file_path == "correspondence/2025.03.14-a.md"
    assert records["b"].file_path == "correspondence/2025.03.15-b.md"


def test_write_survives_a_failed_rollback_reversal_without_crashing(monkeypatch, tmp_path):
    """If update_record()'s own reversal call (used only during rollback) itself raises,
    write() must still return a ReorganizeResult, not propagate the exception uncaught -
    matching init_service._rollback()'s contract that a rollback failure is reported
    alongside the caller's own failure reasons, never raised. The record whose reversal
    failed must stay consistent with its (unreverted) new file_path rather than getting its
    file moved back while the DB still points at the new path."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(backup.BACKUP_DIR_ENV, str(tmp_path / "backups"))
    project_root = tmp_path / "projects" / "demo"
    corr = project_root / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-a.md").write_text("a")
    (corr / "2025.03.15-b.md").write_text("b")
    service.add_record(
        project="demo", record_group="letters", content="a",
        file_path="correspondence/2025.03.14-a.md", fields={}, created_at=1,
    )
    service.add_record(
        project="demo", record_group="letters", content="b",
        file_path="correspondence/2025.03.15-b.md", fields={}, created_at=1,
    )

    real_update = service.update_record
    def _selective_failure(**kwargs):
        file_path = kwargs.get("file_path") or ""
        if file_path.endswith("b.md"):
            raise service.VersionConflictError(current={}, attempted={})  # b's forward update
        if file_path == "correspondence/2025.03.14-a.md":
            raise service.VersionConflictError(current={}, attempted={})  # a's reversal
        return real_update(**kwargs)  # a's forward update
    monkeypatch.setattr(reorganize.service, "update_record", _selective_failure)

    result = reorganize.write(project="demo", project_root=project_root, folder="correspondence",
                              strategy="by-year")

    assert result.failure is not None
    assert any("rollback failed" in reason for reason in result.failure.reasons)
    records = {r.content: r for r in service.list_records(project="demo", record_group="letters")}
    assert records["a"].file_path == "correspondence/2025/2025.03.14-a.md"
    assert (corr / "2025" / "2025.03.14-a.md").exists()
    assert not (corr / "2025.03.14-a.md").exists()


def test_write_rolls_back_earlier_moves_when_a_later_move_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(backup.BACKUP_DIR_ENV, str(tmp_path / "backups"))
    project_root = tmp_path / "projects" / "demo"
    corr = project_root / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-a.md").write_text("a")
    (corr / "2026.01.02-b.md").write_text("b")

    real_move_file = reorganize._move_file
    def _fail_for_b(**kwargs):
        if kwargs["move"].old_relative.endswith("b.md"):
            raise OSError("simulated move failure")
        return real_move_file(**kwargs)
    monkeypatch.setattr(reorganize, "_move_file", _fail_for_b)

    result = reorganize.write(project="demo", project_root=project_root, folder="correspondence",
                              strategy="by-year")

    assert result.failure is not None
    # a's move (which succeeded) is rolled back; b's move never happened at all - both files
    # end up back at their original flat locations, and neither year subdirectory survives.
    assert (corr / "2025.03.14-a.md").exists()
    assert (corr / "2026.01.02-b.md").exists()
    assert not (corr / "2025").exists()
    assert not (corr / "2026").exists()


def test_move_file_cleans_up_newly_created_directory_when_the_move_itself_fails(monkeypatch, tmp_path):
    project_root = tmp_path / "projects" / "demo"
    corr = project_root / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-note.md").write_text("x")
    move = reorganize.Move(
        old_relative="correspondence/2025.03.14-note.md",
        new_relative="correspondence/2025/2025.03.14-note.md",
    )

    def _fail_rename(self, target):
        raise OSError("simulated rename failure")
    monkeypatch.setattr(Path, "rename", _fail_rename)

    with pytest.raises(OSError):
        reorganize._move_file(project_root=project_root, move=move, folder="correspondence",
                              use_git=False)

    # dest.parent (correspondence/2025/) was created by _move_file's own mkdir call, then the
    # move itself failed - it must not be left behind as debris (write()'s own rollback only
    # knows about *completed* moves, so nothing else would ever clean this up).
    assert not (corr / "2025").exists()
    assert (corr / "2025.03.14-note.md").exists()


def test_move_file_never_removes_a_directory_that_predates_the_call(monkeypatch, tmp_path):
    """Regression test: cleanup-on-failure must only remove directories this specific
    _move_file call created. An empty correspondence/2025/ that already existed before the
    call (e.g. left over from an earlier partial/crashed run, or present for any other reason)
    must survive a failed move exactly as it would have if _move_file had never run."""
    project_root = tmp_path / "projects" / "demo"
    corr = project_root / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-note.md").write_text("x")
    (corr / "2025").mkdir()  # pre-exists, empty, before _move_file is ever called
    move = reorganize.Move(
        old_relative="correspondence/2025.03.14-note.md",
        new_relative="correspondence/2025/2025.03.14-note.md",
    )

    def _fail_rename(self, target):
        raise OSError("simulated rename failure")
    monkeypatch.setattr(Path, "rename", _fail_rename)

    with pytest.raises(OSError):
        reorganize._move_file(project_root=project_root, move=move, folder="correspondence",
                              use_git=False)

    assert (corr / "2025").exists()  # pre-existing directory survives - it wasn't ours to remove
    assert (corr / "2025.03.14-note.md").exists()


def test_write_reports_structured_failure_when_backup_fails(monkeypatch, tmp_path):
    """Matches init_service.write()'s own contract for this exact call: a backup failure
    must become a ReorganizeResult(failure=...), not an uncaught BackupError - nothing has
    been moved yet at this point, so there's nothing to roll back."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(backup.BACKUP_DIR_ENV, str(tmp_path / "backups"))
    project_root = tmp_path / "projects" / "demo"
    corr = project_root / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-note.md").write_text("x")

    def _raise(*args, **kwargs):
        raise backup.BackupError("simulated backup failure")
    monkeypatch.setattr(reorganize.backup, "create_backup", _raise)

    result = reorganize.write(project="demo", project_root=project_root, folder="correspondence",
                              strategy="by-year")

    assert result.failure is not None
    assert result.backup_path is None
    assert (corr / "2025.03.14-note.md").exists()  # untouched - failure was before any move
