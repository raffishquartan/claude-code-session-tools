from __future__ import annotations

import pytest

from cc_session_tools.lib.pdata import repository, verify


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
