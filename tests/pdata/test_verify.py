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


def test_row_count_parity_skips_project_using_pdata_add_directly(monkeypatch, tmp_path):
    """A project that only ever used `pdata add`/service.add_record directly - never
    `ccst pdata init` - has populated record groups but no manifest and no .pdata-migrated/
    archive. This must NOT be flagged as 'migrated, manifest missing' - it never went through
    the classify-and-migrate flow at all, so there's nothing for a manifest to describe."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    service.add_record(
        project="demo", record_group="notes", content="x", file_path=None, fields={},
    )
    conn = repository.connect("demo")
    try:
        assert verify.check_row_count_parity(conn, "demo") == []
    finally:
        conn.close()


def test_row_count_parity_fails_when_manifest_missing_but_archive_has_content(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    project_root = tmp_path / "projects" / "demo"
    archived = project_root / init_paths.MIGRATED_ARCHIVE_DIRNAME / "notes"
    archived.mkdir(parents=True)
    (archived / "notes.md").write_text("archived original")
    conn = repository.connect("demo")
    try:
        issues = verify.check_row_count_parity(conn, "demo")
    finally:
        conn.close()
    assert len(issues) == 1
    assert issues[0].check == "manifest-missing"
    assert issues[0].severity == "FAIL"
    assert "manifest now missing" in issues[0].message
    assert "ccst pdata schema" in issues[0].message


def test_row_count_parity_resolves_legacy_manifest_name(monkeypatch, tmp_path):
    """A project with only the pre-rename manifest filename must be treated exactly like one
    with the new name - no manifest-missing issue, normal parity-check behavior."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)
    manifest.save(
        Manifest(project="demo", entries=[]),
        project_root / init_paths.LEGACY_PROPOSAL_FILENAME,
    )
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


def test_double_update_check_no_issue_on_first_sighting(monkeypatch, tmp_path):
    """A row verify has never seen before has nothing to compare against —
    it is recorded as a fresh watermark, not flagged."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    record = service.add_record(project="demo", record_group="key-events", content="x",
                                file_path=None, fields={}, created_at=1000)
    conn = repository.connect("demo")
    try:
        verify.ensure_verify_tables(conn)
        with repository._immediate(conn):
            issues = verify.check_suspicious_double_updates(conn, "demo", since=None)
        assert issues == []
        watermark = conn.execute(
            "SELECT * FROM pdata_verify_watermark WHERE record_id=?", (record.id,),
        ).fetchone()
        assert watermark["last_seen_version"] == 1
    finally:
        conn.close()


def test_double_update_check_flags_two_updates_within_window(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    record = service.add_record(project="demo", record_group="key-events", content="x",
                                file_path=None, fields={}, created_at=1000)
    conn = repository.connect("demo")
    try:
        verify.ensure_verify_tables(conn)
        with repository._immediate(conn):
            verify.check_suspicious_double_updates(conn, "demo", since=None)  # first sighting

        service.update_record(project="demo", record_id=record.id, expected_version=1,
                              content="v2", file_path=None, fields={}, updated_at=1010)
        service.update_record(project="demo", record_id=record.id, expected_version=2,
                              content="v3", file_path=None, fields={}, updated_at=1020)

        conn2 = repository.connect("demo")
        try:
            with repository._immediate(conn2):
                issues = verify.check_suspicious_double_updates(conn2, "demo", since=None)
            assert len(issues) == 1
            assert issues[0].severity == "WARN"
            assert issues[0].check == "suspicious-double-update"
            assert issues[0].record_id == record.id
        finally:
            conn2.close()
    finally:
        conn.close()


def test_double_update_check_ignores_single_update(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    record = service.add_record(project="demo", record_group="key-events", content="x",
                                file_path=None, fields={}, created_at=1000)
    conn = repository.connect("demo")
    try:
        verify.ensure_verify_tables(conn)
        with repository._immediate(conn):
            verify.check_suspicious_double_updates(conn, "demo", since=None)
    finally:
        conn.close()

    service.update_record(project="demo", record_id=record.id, expected_version=1,
                          content="v2", file_path=None, fields={}, updated_at=1010)

    conn2 = repository.connect("demo")
    try:
        with repository._immediate(conn2):
            issues = verify.check_suspicious_double_updates(conn2, "demo", since=None)
        assert issues == []
    finally:
        conn2.close()


def test_double_update_check_ignores_updates_outside_window(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    record = service.add_record(project="demo", record_group="key-events", content="x",
                                file_path=None, fields={}, created_at=1000)
    conn = repository.connect("demo")
    try:
        verify.ensure_verify_tables(conn)
        with repository._immediate(conn):
            verify.check_suspicious_double_updates(conn, "demo", since=None)
    finally:
        conn.close()

    far_future = 1000 + verify._DOUBLE_UPDATE_WINDOW_SECONDS + 1000
    service.update_record(project="demo", record_id=record.id, expected_version=1,
                          content="v2", file_path=None, fields={}, updated_at=1010)
    service.update_record(project="demo", record_id=record.id, expected_version=2,
                          content="v3", file_path=None, fields={}, updated_at=far_future)

    conn2 = repository.connect("demo")
    try:
        with repository._immediate(conn2):
            issues = verify.check_suspicious_double_updates(conn2, "demo", since=None)
        assert issues == []  # version advanced by 2, but not within the window
    finally:
        conn2.close()


def test_run_verify_persists_ok_summary_with_no_issues(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    service.add_record(project="demo", record_group="ccst-ideas", content="an idea",
                       file_path=None, fields={}, created_at=1000)
    summary = verify.run_verify(project="demo", full=True)
    assert summary.status == "OK"
    assert summary.issues == []
    assert summary.project == "demo"
    assert summary.full_scan is True


def test_run_verify_persists_worst_status_across_checks(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    service.add_record(project="demo", record_group="filings", content="x",
                       file_path="missing.pdf", fields={}, created_at=1000)
    summary = verify.run_verify(project="demo", full=True)
    assert summary.status == "FAIL"  # file-path-resolution is FAIL-severity
    assert len(summary.issues) == 1


def test_run_verify_second_call_reads_persisted_last_run(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    service.add_record(project="demo", record_group="notes", content="x",
                       file_path=None, fields={}, created_at=1)
    verify.run_verify(project="demo", full=True)
    last = verify.last_run("demo")
    assert last is not None
    assert last.status == "OK"


def test_run_verify_raises_for_project_with_no_existing_store(monkeypatch, tmp_path):
    """run_verify must never fabricate a brand-new, empty store (via repository.connect()'s
    own CREATE TABLE IF NOT EXISTS side effect) for a project name that has never had one —
    that would make `ccst pdata verify --project <typo>` silently report "clean" instead of
    surfacing the mistake. Matches discover_projects()'s own "only .dbs that already exist"
    standard (plan Decision 7) applied to a single named project too."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    with pytest.raises(ValueError, match="no data store"):
        verify.run_verify(project="never-touched-project", full=True)
    assert not (tmp_path / "never-touched-project.db").exists()


def test_last_run_returns_none_when_never_run(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    assert verify.last_run("never-verified-project") is None


def test_last_run_returns_none_when_db_does_not_exist(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "does-not-exist"))
    assert verify.last_run("demo") is None


def test_discover_projects_lists_dbs_sorted(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    service.add_record(project="zeta", record_group="notes", content="x",
                       file_path=None, fields={}, created_at=1)
    service.add_record(project="alpha", record_group="notes", content="x",
                       file_path=None, fields={}, created_at=1)
    assert verify.discover_projects() == ["alpha", "zeta"]


def test_discover_projects_empty_when_dir_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "does-not-exist"))
    assert verify.discover_projects() == []


def test_run_verify_prunes_old_runs_beyond_retention(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    service.add_record(project="demo", record_group="notes", content="x",
                       file_path=None, fields={}, created_at=1)
    for _ in range(verify._MAX_RETAINED_RUNS + 5):
        verify.run_verify(project="demo", full=True)
    conn = repository.connect("demo")
    try:
        count = conn.execute("SELECT COUNT(*) AS c FROM pdata_verify_runs").fetchone()["c"]
        assert count == verify._MAX_RETAINED_RUNS
    finally:
        conn.close()
