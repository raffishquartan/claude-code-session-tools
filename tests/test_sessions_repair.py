"""Tests for sessions_repair — resolving/fixing sessions.db rows with a
non-absolute project_dir."""
from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib import sessions_db, sessions_repair


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "sessions.db"


def test_find_non_absolute_rows_returns_only_bad_rows(db_path):
    sessions_db.ensure_session_row(Path("/repos/good"), "20260101-good", path=db_path)
    conn = sessions_db.connect(path=db_path)
    conn.execute(
        "INSERT INTO sessions (project_dir, basename, start_date, discovered_at) "
        "VALUES ('.', '20260101-bad', '20260101', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    bad = sessions_repair.find_non_absolute_rows(path=db_path)
    assert [r.basename for r in bad] == ["20260101-bad"]


def test_repair_dry_run_does_not_modify_db(tmp_path, db_path):
    root = tmp_path / "repos"
    proj = root / "myproj"
    (proj / "cc-sessions" / "20260101-bad").mkdir(parents=True)
    conn = sessions_db.connect(path=db_path)
    conn.execute(
        "INSERT INTO sessions (project_dir, basename, start_date, discovered_at) "
        "VALUES ('.', '20260101-bad', '20260101', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    report = sessions_repair.repair([root], path=db_path, dry_run=True)

    assert report.repaired == [("20260101-bad", proj)]
    rows = sessions_db.list_sessions(path=db_path)
    assert rows[0].project_dir == Path(".")  # unchanged


def test_repair_execute_updates_project_dir_and_preserves_timestamps(tmp_path, db_path):
    root = tmp_path / "repos"
    proj = root / "myproj"
    (proj / "cc-sessions" / "20260101-bad").mkdir(parents=True)
    conn = sessions_db.connect(path=db_path)
    conn.execute(
        "INSERT INTO sessions (project_dir, basename, start_date, discovered_at, last_active) "
        "VALUES ('.', '20260101-bad', '20260101', '2026-01-01T00:00:00Z', 12345.0)"
    )
    conn.commit()
    conn.close()

    report = sessions_repair.repair([root], path=db_path, dry_run=False)

    assert report.repaired == [("20260101-bad", proj)]
    rows = sessions_db.list_sessions(path=db_path)
    assert rows[0].project_dir == proj
    assert rows[0].last_active == 12345.0  # preserved, not reset


def test_repair_reports_unresolved_when_no_on_disk_match(tmp_path, db_path):
    root = tmp_path / "repos"
    root.mkdir()
    conn = sessions_db.connect(path=db_path)
    conn.execute(
        "INSERT INTO sessions (project_dir, basename, start_date, discovered_at) "
        "VALUES ('.', '20260101-orphan', '20260101', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    report = sessions_repair.repair([root], path=db_path, dry_run=False)

    assert report.repaired == []
    assert report.unresolved == ["20260101-orphan"]


def test_repair_reports_ambiguous_when_multiple_on_disk_matches(tmp_path, db_path):
    root = tmp_path / "repos"
    (root / "proj-a" / "cc-sessions" / "20260101-dup").mkdir(parents=True)
    (root / "proj-b" / "cc-sessions" / "20260101-dup").mkdir(parents=True)
    conn = sessions_db.connect(path=db_path)
    conn.execute(
        "INSERT INTO sessions (project_dir, basename, start_date, discovered_at) "
        "VALUES ('.', '20260101-dup', '20260101', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    report = sessions_repair.repair([root], path=db_path, dry_run=False)

    assert report.repaired == []
    assert len(report.ambiguous["20260101-dup"]) == 2


def test_repair_no_bad_rows_returns_empty_report(db_path):
    sessions_db.ensure_session_row(Path("/repos/good"), "20260101-good", path=db_path)
    report = sessions_repair.repair([Path("/repos")], path=db_path, dry_run=False)
    assert report.repaired == []
    assert report.unresolved == []
    assert report.ambiguous == {}


def test_repair_reports_conflict_for_two_bad_rows_resolving_to_same_target_in_one_batch(
    tmp_path, db_path
):
    """Two non-absolute rows sharing a basename (distinguished only by their bad
    project_dir value, e.g. '.' vs '..') that both resolve to the same single
    on-disk match must not both land in `applyable` — the second UPDATE would hit
    the row the first UPDATE just created, which is a conflict against the batch's
    own claimed targets, not just the pre-batch DB snapshot. Must be reported, not
    raise sqlite3.IntegrityError and abort the whole repair() call."""
    root = tmp_path / "repos"
    proj = root / "myproj"
    (proj / "cc-sessions" / "20260101-dup").mkdir(parents=True)
    conn = sessions_db.connect(path=db_path)
    conn.execute(
        "INSERT INTO sessions (project_dir, basename, start_date, discovered_at) "
        "VALUES ('.', '20260101-dup', '20260101', '2026-01-01T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO sessions (project_dir, basename, start_date, discovered_at) "
        "VALUES ('..', '20260101-dup', '20260101', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    report = sessions_repair.repair([root], path=db_path, dry_run=False)

    assert report.repaired == []
    assert report.conflicts == ["20260101-dup", "20260101-dup"]


def test_repair_applies_clean_row_alongside_a_conflicting_pair_in_the_same_batch(
    tmp_path, db_path
):
    """A batch can contain both a colliding pair (two bad rows resolving to the same
    target) AND an unrelated, cleanly-resolvable row. The clean row must still be
    applied normally — the conflict handling for the colliding pair must not sweep
    in unrelated rows just because they were processed in the same repair() call."""
    root = tmp_path / "repos"
    dup_proj = root / "dup-proj"
    (dup_proj / "cc-sessions" / "20260101-dup").mkdir(parents=True)
    clean_proj = root / "clean-proj"
    (clean_proj / "cc-sessions" / "20260101-clean").mkdir(parents=True)

    conn = sessions_db.connect(path=db_path)
    conn.execute(
        "INSERT INTO sessions (project_dir, basename, start_date, discovered_at) "
        "VALUES ('.', '20260101-dup', '20260101', '2026-01-01T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO sessions (project_dir, basename, start_date, discovered_at) "
        "VALUES ('..', '20260101-dup', '20260101', '2026-01-01T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO sessions (project_dir, basename, start_date, discovered_at) "
        "VALUES ('.', '20260101-clean', '20260101', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    report = sessions_repair.repair([root], path=db_path, dry_run=False)

    assert report.repaired == [("20260101-clean", clean_proj)]
    assert report.conflicts == ["20260101-dup", "20260101-dup"]

    rows = {r.basename: r for r in sessions_db.list_sessions(path=db_path)}
    assert rows["20260101-clean"].project_dir == clean_proj  # updated
    dup_dirs = {r.project_dir for r in sessions_db.list_sessions(path=db_path) if r.basename == "20260101-dup"}
    assert dup_dirs == {Path("."), Path("..")}  # both untouched


def test_repair_reports_conflict_instead_of_crashing_when_target_row_already_exists(
    tmp_path, db_path
):
    """A correct (proj, basename) row can already exist alongside a corrupted ('.', basename)
    row for the SAME basename — e.g. ccd.py's ensure_session_row() wrote the correct row at
    session-creation time, independently of whatever later corrupted a duplicate/stale row for
    the same basename. Resolving the bad row to `proj` and UPDATE-ing it would then violate the
    (project_dir, basename) PRIMARY KEY. This must be reported as a conflict, not raise."""
    root = tmp_path / "repos"
    proj = root / "myproj"
    (proj / "cc-sessions" / "20260101-dup").mkdir(parents=True)
    sessions_db.ensure_session_row(proj, "20260101-dup", path=db_path)  # the correct row
    conn = sessions_db.connect(path=db_path)
    conn.execute(
        "INSERT INTO sessions (project_dir, basename, start_date, discovered_at) "
        "VALUES ('.', '20260101-dup', '20260101', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    report = sessions_repair.repair([root], path=db_path, dry_run=False)

    assert report.repaired == []
    assert report.conflicts == ["20260101-dup"]
    rows = sessions_db.list_sessions(path=db_path)
    assert len(rows) == 2  # both rows still present, untouched
