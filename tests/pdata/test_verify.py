from __future__ import annotations

import pytest

from cc_session_tools.lib.pdata import init_paths, manifest, repository, service, verify
from cc_session_tools.lib.pdata.manifest import Manifest, ManifestEntry


def _write_proposal(project_root, entries):
    project_root.mkdir(parents=True, exist_ok=True)
    manifest.save(Manifest(project=project_root.name, entries=entries),
                  project_root / init_paths.PROPOSAL_FILENAME)


def test_ensure_verify_tables_creates_watermark_and_runs_tables(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        verify.ensure_verify_tables(conn)
        tables = {
            r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "pdata_verify_watermark" in tables
        assert "pdata_verify_runs" in tables
    finally:
        conn.close()


def test_ensure_verify_tables_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        verify.ensure_verify_tables(conn)
        verify.ensure_verify_tables(conn)  # must not raise
    finally:
        conn.close()


def test_verify_issue_and_summary_are_plain_dataclasses():
    issue = verify.VerifyIssue(
        check="file-path-resolution", severity="FAIL", record_group="filings",
        record_id=1, message="broken",
    )
    summary = verify.VerifySummary(
        project="demo", run_at=1000, full_scan=False, status="FAIL", issues=[issue],
    )
    assert summary.issues[0].message == "broken"


def test_row_count_parity_skips_project_with_no_migration_history(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    conn = repository.connect("demo")
    try:
        assert verify.check_row_count_parity(conn, "demo") == []
    finally:
        conn.close()


def test_row_count_parity_ok_when_counts_match(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    project_root = tmp_path / "projects" / "demo"
    entry = ManifestEntry(path="ideas.csv", classification="db-owned",
                          record_group="ideas", strategy="csv-rows")
    _write_proposal(project_root, [entry])
    archive_root = project_root / init_paths.MIGRATED_ARCHIVE_DIRNAME
    archive_root.mkdir(parents=True)
    (archive_root / "ideas.csv").write_text("idea\nfirst\nsecond\n")

    service.add_record(project="demo", record_group="ideas", content="first",
                       file_path=None, fields={}, created_at=1)
    service.add_record(project="demo", record_group="ideas", content="second",
                       file_path=None, fields={}, created_at=2)

    conn = repository.connect("demo")
    try:
        assert verify.check_row_count_parity(conn, "demo") == []
    finally:
        conn.close()


def test_row_count_parity_fails_when_rows_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    project_root = tmp_path / "projects" / "demo"
    entry = ManifestEntry(path="ideas.csv", classification="db-owned",
                          record_group="ideas", strategy="csv-rows")
    _write_proposal(project_root, [entry])
    archive_root = project_root / init_paths.MIGRATED_ARCHIVE_DIRNAME
    archive_root.mkdir(parents=True)
    (archive_root / "ideas.csv").write_text("idea\nfirst\nsecond\n")

    service.add_record(project="demo", record_group="ideas", content="first",
                       file_path=None, fields={}, created_at=1)
    # "second" was never inserted — simulates a silent write-loss bug.

    conn = repository.connect("demo")
    try:
        issues = verify.check_row_count_parity(conn, "demo")
        assert len(issues) == 1
        assert issues[0].severity == "FAIL"
        assert issues[0].check == "row-count-parity"
        assert issues[0].record_group == "ideas"
    finally:
        conn.close()


def test_row_count_parity_ok_when_more_rows_than_migrated(monkeypatch, tmp_path):
    """A record_group is allowed to grow after migration via ordinary
    ccst pdata add calls (plan Decision 2) — more rows than expected is
    healthy growth, not a defect."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    project_root = tmp_path / "projects" / "demo"
    entry = ManifestEntry(path="ideas.csv", classification="db-owned",
                          record_group="ideas", strategy="csv-rows")
    _write_proposal(project_root, [entry])
    archive_root = project_root / init_paths.MIGRATED_ARCHIVE_DIRNAME
    archive_root.mkdir(parents=True)
    (archive_root / "ideas.csv").write_text("idea\nfirst\n")

    service.add_record(project="demo", record_group="ideas", content="first",
                       file_path=None, fields={}, created_at=1)
    service.add_record(project="demo", record_group="ideas", content="added later",
                       file_path=None, fields={}, created_at=2)

    conn = repository.connect("demo")
    try:
        assert verify.check_row_count_parity(conn, "demo") == []
    finally:
        conn.close()


def test_row_count_parity_skips_entries_not_yet_cut_over(monkeypatch, tmp_path):
    """An entry classified db-owned in the proposal but with no archived
    counterpart yet (dry-run reviewed but --write hasn't cut it over, or it
    was a --rehearse-only run) has nothing to compare against."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    project_root = tmp_path / "projects" / "demo"
    entry = ManifestEntry(path="ideas.csv", classification="db-owned",
                          record_group="ideas", strategy="csv-rows")
    _write_proposal(project_root, [entry])
    # No .pdata-migrated/ideas.csv written — never cut over.

    conn = repository.connect("demo")
    try:
        assert verify.check_row_count_parity(conn, "demo") == []
    finally:
        conn.close()


def test_row_count_parity_sums_expected_across_entries_sharing_a_record_group(
    monkeypatch, tmp_path,
):
    """log.md + log.csv both mapping to record_group="log" is a real fixture shape
    the migration tests exercise. Parity must compare the *summed* expected row count
    across every entry that feeds the group against that group's actual count once —
    comparing the shared actual count against each entry's expected count independently
    would let a loss that stays above the smaller entry's own threshold pass silently."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    project_root = tmp_path / "projects" / "demo"
    entries = [
        ManifestEntry(path="log.md", classification="db-owned", record_group="log",
                      strategy="whole-file"),
        ManifestEntry(path="log.csv", classification="db-owned", record_group="log",
                      strategy="csv-rows"),
    ]
    _write_proposal(project_root, entries)
    archive_root = project_root / init_paths.MIGRATED_ARCHIVE_DIRNAME
    archive_root.mkdir(parents=True)
    (archive_root / "log.md").write_text("one whole-file row")  # whole-file -> 1 row
    (archive_root / "log.csv").write_text("entry\nfirst\nsecond\nthird\n")  # csv-rows -> 3 rows

    # Originally migrated total for record_group "log": 1 (log.md) + 3 (log.csv) = 4 rows.
    # Only 3 active rows survive below — that is >= log.md's own expected (1) and >= log.csv's
    # own expected (3) when checked independently, so a per-entry comparison would report this
    # project clean. The correct comparison is 3 actual < 4 summed-expected -> FAIL.
    for content in ("first", "second", "third"):
        service.add_record(project="demo", record_group="log", content=content,
                           file_path=None, fields={}, created_at=1)

    conn = repository.connect("demo")
    try:
        issues = verify.check_row_count_parity(conn, "demo")
        assert len(issues) == 1
        assert issues[0].record_group == "log"
    finally:
        conn.close()


def test_row_count_parity_not_tripped_by_a_legitimate_delete(monkeypatch, tmp_path):
    """A row that was part of the originally-migrated count can later be soft-deleted by an
    ordinary `ccst pdata delete` (spec §4.5) without ever failing parity again — a
    soft-deleted row still physically exists in the table (only deleted_at is set), so it must
    still count toward "not lost". Excluding deleted rows from the actual count makes every
    legitimate delete of an originally-migrated row an unfixable false-positive FAIL forever
    after, which contradicts Decision 2's grows-only-shrinks-on-real-loss model."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    project_root = tmp_path / "projects" / "demo"
    entry = ManifestEntry(path="ideas.csv", classification="db-owned",
                          record_group="ideas", strategy="csv-rows")
    _write_proposal(project_root, [entry])
    archive_root = project_root / init_paths.MIGRATED_ARCHIVE_DIRNAME
    archive_root.mkdir(parents=True)
    (archive_root / "ideas.csv").write_text("idea\nfirst\nsecond\n")

    record = service.add_record(project="demo", record_group="ideas", content="first",
                                file_path=None, fields={}, created_at=1)
    service.add_record(project="demo", record_group="ideas", content="second",
                       file_path=None, fields={}, created_at=2)
    service.delete_record(project="demo", record_id=record.id, expected_version=1)

    conn = repository.connect("demo")
    try:
        assert verify.check_row_count_parity(conn, "demo") == []
    finally:
        conn.close()


def test_file_path_resolution_ok_when_file_exists(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)
    (project_root / "a.pdf").write_bytes(b"%PDF-1.4")

    service.add_record(project="demo", record_group="filings", content="x",
                       file_path="a.pdf", fields={}, created_at=1)

    conn = repository.connect("demo")
    try:
        assert verify.check_file_path_resolution(conn, "demo", since=None) == []
    finally:
        conn.close()


def test_file_path_resolution_fails_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)
    # a.pdf deliberately not created

    service.add_record(project="demo", record_group="filings", content="x",
                       file_path="a.pdf", fields={}, created_at=1)

    conn = repository.connect("demo")
    try:
        issues = verify.check_file_path_resolution(conn, "demo", since=None)
        assert len(issues) == 1
        assert issues[0].severity == "FAIL"
        assert issues[0].check == "file-path-resolution"
    finally:
        conn.close()


def test_file_path_resolution_ignores_rows_with_no_file_path(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    service.add_record(project="demo", record_group="ccst-ideas", content="an idea",
                       file_path=None, fields={}, created_at=1)
    conn = repository.connect("demo")
    try:
        assert verify.check_file_path_resolution(conn, "demo", since=None) == []
    finally:
        conn.close()


def test_file_path_resolution_honors_since_cursor(monkeypatch, tmp_path):
    """A row updated before `since` is skipped even if its file is missing —
    incremental scope (plan Decision 5); --full passes since=None to check
    every row regardless of age."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)

    service.add_record(project="demo", record_group="filings", content="old",
                       file_path="missing-old.pdf", fields={}, created_at=100)
    service.add_record(project="demo", record_group="filings", content="new",
                       file_path="missing-new.pdf", fields={}, created_at=200)

    conn = repository.connect("demo")
    try:
        issues = verify.check_file_path_resolution(conn, "demo", since=150)
        assert len(issues) == 1
        assert "missing-new.pdf" in issues[0].message

        issues_full = verify.check_file_path_resolution(conn, "demo", since=None)
        assert len(issues_full) == 2
    finally:
        conn.close()
