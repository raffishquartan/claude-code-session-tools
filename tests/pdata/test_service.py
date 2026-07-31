from __future__ import annotations

import pytest

from cc_session_tools.lib.pdata import service


def test_add_record_content_only(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    record = service.add_record(
        project="testproj", record_group="ccst-ideas", content="an idea",
        file_path=None, fields={}, created_at=1000,
    )
    assert record.id == 1
    assert record.record_group == "ccst-ideas"
    assert record.content == "an idea"
    assert record.fields == {}


def test_add_record_rejects_invalid_record_group(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="record_group"):
        service.add_record(
            project="testproj", record_group="Not Valid", content="x",
            file_path=None, fields={}, created_at=1000,
        )


def test_add_record_rejects_absolute_file_path(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="relative"):
        service.add_record(
            project="testproj", record_group="filings", content="x",
            file_path="/etc/passwd", fields={}, created_at=1000,
        )


def test_add_record_rejects_path_traversal_file_path(monkeypatch, tmp_path):
    """Regression test: a relative-but-escaping --file (no leading '/') must be rejected too,
    since Plan B later resolves file_path against the project root."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="\\.\\."):
        service.add_record(
            project="testproj", record_group="filings", content="x",
            file_path="../../etc/passwd", fields={}, created_at=1000,
        )
