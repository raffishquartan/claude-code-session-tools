from __future__ import annotations

import os

import pytest

from cc_session_tools.lib import roots
from cc_session_tools.lib.pdata import session_output


def _make_project(base, name, *, with_sessions: bool) -> None:
    proj = base / name
    proj.mkdir(parents=True)
    if with_sessions:
        (proj / "cc-sessions").mkdir()


def test_discover_projects_with_sessions_filters_to_cc_sessions_dirs(monkeypatch, tmp_path):
    proj_root = tmp_path / "cc"
    proj_root.mkdir()
    _make_project(proj_root, "has-sessions", with_sessions=True)
    _make_project(proj_root, "no-sessions", with_sessions=False)
    monkeypatch.setenv(roots.PROJ_ROOT_ENV, str(proj_root))
    monkeypatch.delenv(roots.REPO_ROOT_ENV, raising=False)

    found = session_output.discover_projects_with_sessions()

    names = [name for name, _ in found]
    assert names == ["has-sessions"]


def test_discover_projects_with_sessions_ignores_repo_root(monkeypatch, tmp_path):
    repo_root = tmp_path / "repos"
    proj_root = tmp_path / "cc"
    repo_root.mkdir()
    proj_root.mkdir()
    # A dev repo with its own cc-sessions/ history (Claude Code was run directly inside it) must
    # never be treated as a project, even though REPO_ROOT_ENV is configured and valid.
    _make_project(repo_root, "repo-only", with_sessions=True)
    _make_project(proj_root, "real-project", with_sessions=True)
    monkeypatch.setenv(roots.REPO_ROOT_ENV, str(repo_root))
    monkeypatch.setenv(roots.PROJ_ROOT_ENV, str(proj_root))

    found = session_output.discover_projects_with_sessions()

    names = [name for name, _ in found]
    assert names == ["real-project"]


def test_discover_projects_with_sessions_raises_when_proj_root_not_configured(
    monkeypatch, tmp_path
):
    # REPO_ROOT_ENV being configured must not satisfy this function's requirement - it only
    # ever looks at PROJ_ROOT_ENV.
    repo_root = tmp_path / "repos"
    repo_root.mkdir()
    monkeypatch.setenv(roots.REPO_ROOT_ENV, str(repo_root))
    monkeypatch.delenv(roots.PROJ_ROOT_ENV, raising=False)
    with pytest.raises(roots.RootsConfigError):
        session_output.discover_projects_with_sessions()


def test_find_project_root_returns_none_for_unknown_project(monkeypatch, tmp_path):
    proj_root = tmp_path / "cc"
    proj_root.mkdir()
    _make_project(proj_root, "known", with_sessions=True)
    monkeypatch.setenv(roots.PROJ_ROOT_ENV, str(proj_root))
    monkeypatch.delenv(roots.REPO_ROOT_ENV, raising=False)

    assert session_output.find_project_root("known") == proj_root / "known"
    assert session_output.find_project_root("unknown") is None


def _touch(path, mtime: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("content")
    os.utime(path, (mtime, mtime))


def test_find_new_out_files_only_returns_files_newer_than_watermark(tmp_path):
    project_root = tmp_path / "proj"
    old_file = project_root / "cc-sessions" / "20260701-a" / "out" / "old.md"
    new_file = project_root / "cc-sessions" / "20260710-b" / "out" / "new.md"
    _touch(old_file, 1000)
    _touch(new_file, 2000)

    found = session_output.find_new_out_files(project_root, since_mtime=1500)

    paths = [p for p, _ in found]
    assert new_file in paths
    assert old_file not in paths


def test_find_new_out_files_ignores_non_out_dirs_and_missing_cc_sessions(tmp_path):
    project_root = tmp_path / "proj"
    working_file = project_root / "cc-sessions" / "20260701-a" / "working" / "WORKLOG.md"
    _touch(working_file, 5000)

    found = session_output.find_new_out_files(project_root, since_mtime=0)

    assert found == []


def test_find_new_out_files_returns_empty_list_for_project_with_no_cc_sessions(tmp_path):
    project_root = tmp_path / "proj"
    project_root.mkdir()
    assert session_output.find_new_out_files(project_root, since_mtime=0) == []


def test_session_tag_from_relpath_extracts_the_session_directory_name():
    rel = "cc-sessions/20260710-foo-bar/out/report.md"
    assert session_output.session_tag_from_relpath(rel) == "20260710-foo-bar"


@pytest.mark.parametrize(
    "bad_rel",
    ["out/report.md", "cc-sessions/report.md", "working/foo/out/x.md"],
)
def test_session_tag_from_relpath_rejects_malformed_paths(bad_rel):
    with pytest.raises(ValueError, match="cc-sessions"):
        session_output.session_tag_from_relpath(bad_rel)


def test_ensure_session_output_schema_creates_session_tag_column(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    from cc_session_tools.lib.pdata import repository

    session_output.ensure_session_output_schema("testproj")

    conn = repository.connect("testproj")
    try:
        cols = repository.list_extension_columns(conn, session_output.SESSION_OUTPUT_GROUP)
        assert "session_tag" in cols
    finally:
        conn.close()


def test_ensure_session_output_schema_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    session_output.ensure_session_output_schema("testproj")
    session_output.ensure_session_output_schema("testproj")  # must not raise


def test_ensure_session_output_schema_creates_file_path_index(monkeypatch, tmp_path):
    # _is_already_registered's dedupe check filters on file_path within the session-output
    # record_group. Plan A's base schema only indexes record_group and updated_at (not
    # file_path) — without a targeted index here, that check is an unindexed scan across every
    # session-output row ever written for this project, which by design accumulates forever,
    # directly contradicting spec Goal G5 ("cost never scales with accumulated history"). This
    # index is scoped to this one record_group via a partial index
    # (WHERE record_group = 'session-output'), so it stays a session-output-only concern and
    # does not touch Plan A's own shared index list.
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    from cc_session_tools.lib.pdata import repository

    session_output.ensure_session_output_schema("testproj")

    conn = repository.connect("testproj")
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' "
            "AND name = 'idx_records_session_output_file_path'"
        ).fetchall()
        assert len(rows) == 1
    finally:
        conn.close()


def test_get_watermark_defaults_to_zero_for_new_project(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    assert session_output.get_watermark("testproj") == 0


def test_set_watermark_then_get_watermark_round_trips(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    session_output.set_watermark("testproj", 1234)
    assert session_output.get_watermark("testproj") == 1234

    session_output.set_watermark("testproj", 5678)  # update path, not just create
    assert session_output.get_watermark("testproj") == 5678


def test_set_watermark_retries_once_on_version_conflict(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    from cc_session_tools.lib.pdata import service

    session_output.set_watermark("testproj", 100)  # creates the row

    real_update_record = service.update_record
    calls = {"n": 0}

    def flaky_update_record(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            record = session_output._get_watermark_record("testproj")
            raise service.VersionConflictError(
                current={"id": record.id, "version": record.version + 1},
                attempted=kwargs,
            )
        return real_update_record(**kwargs)

    monkeypatch.setattr(service, "update_record", flaky_update_record)

    session_output.set_watermark("testproj", 200)  # must not raise

    assert calls["n"] == 2
    assert session_output.get_watermark("testproj") == 200


def test_set_watermark_propagates_conflict_that_persists_after_retry(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    from cc_session_tools.lib.pdata import service

    session_output.set_watermark("testproj", 100)

    def always_conflicts(**kwargs):
        record = session_output._get_watermark_record("testproj")
        raise service.VersionConflictError(
            current={"id": record.id, "version": record.version + 1},
            attempted=kwargs,
        )

    monkeypatch.setattr(service, "update_record", always_conflicts)

    with pytest.raises(service.VersionConflictError):
        session_output.set_watermark("testproj", 200)


def test_reconcile_project_registers_new_files_and_advances_watermark(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    project_root = tmp_path / "proj"
    out_file = project_root / "cc-sessions" / "20260710-foo" / "out" / "report.md"
    _touch(out_file, 2000)

    result = session_output.reconcile_project("testproj", project_root)

    assert result.scanned == 1
    assert result.registered == 1
    assert result.watermark == 2000
    assert session_output.get_watermark("testproj") == 2000

    from cc_session_tools.lib.pdata import service

    rows = service.list_records(project="testproj", record_group=session_output.SESSION_OUTPUT_GROUP)
    assert len(rows) == 1
    assert rows[0].file_path == "cc-sessions/20260710-foo/out/report.md"
    assert rows[0].content == "report.md"
    assert rows[0].fields["session_tag"] == "20260710-foo"


def test_reconcile_project_skips_already_registered_files(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    project_root = tmp_path / "proj"
    out_file = project_root / "cc-sessions" / "20260710-foo" / "out" / "report.md"
    _touch(out_file, 2000)

    session_output.reconcile_project("testproj", project_root)
    # A second run with no new files must not re-insert or error.
    result = session_output.reconcile_project("testproj", project_root)

    assert result.registered == 0

    from cc_session_tools.lib.pdata import service

    rows = service.list_records(project="testproj", record_group=session_output.SESSION_OUTPUT_GROUP)
    assert len(rows) == 1


def test_reconcile_project_dry_run_does_not_write(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    project_root = tmp_path / "proj"
    out_file = project_root / "cc-sessions" / "20260710-foo" / "out" / "report.md"
    _touch(out_file, 2000)

    result = session_output.reconcile_project("testproj", project_root, dry_run=True)

    assert result.registered == 1  # reports what WOULD be registered
    assert session_output.get_watermark("testproj") == 0  # but writes nothing

    from cc_session_tools.lib.pdata import service

    rows = service.list_records(project="testproj", record_group=session_output.SESSION_OUTPUT_GROUP)
    assert rows == []


def test_reconcile_project_dry_run_on_new_project_creates_no_db_file(monkeypatch, tmp_path):
    # A dry-run that only reads should not have the side effect of creating the project's .db
    # file — repository.connect() creates it and its base schema on every call (no readonly
    # path), so this specifically exercises the case where _is_already_registered and
    # get_watermark's own store.db_path().exists() short-circuits are the only thing preventing
    # that connect() call from ever happening.
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    from cc_session_tools.lib.pdata import store

    project_root = tmp_path / "proj"
    out_file = project_root / "cc-sessions" / "20260710-foo" / "out" / "report.md"
    _touch(out_file, 2000)

    session_output.reconcile_project("newproj", project_root, dry_run=True)

    assert not store.db_path("newproj").exists()


def test_reconcile_project_handles_empty_project(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    project_root = tmp_path / "empty-proj"
    project_root.mkdir()

    result = session_output.reconcile_project("testproj", project_root)

    assert result.scanned == 0
    assert result.registered == 0
