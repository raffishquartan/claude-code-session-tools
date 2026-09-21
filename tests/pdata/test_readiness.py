"""`ccst pdata readiness-scan` core: deterministic CSV blocker scan (fictional fixtures only)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cc_session_tools.lib.pdata import classify, readiness


def _w(root: Path, rel: str, content: str | bytes) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        p.write_bytes(content.encode("utf-8"))
    else:
        p.write_bytes(content)
    return p


def _kinds(report: readiness.ReadinessReport, file: str | None = None) -> set[str]:
    return {f.kind for f in report.findings if file is None or f.file == file}


def _find(report: readiness.ReadinessReport, kind: str, file: str) -> readiness.Finding:
    return next(f for f in report.findings if f.kind == kind and f.file == file)


def test_finding_kinds_constant_lists_every_kind() -> None:
    assert set(readiness.FINDING_KINDS) == {
        "ragged-rows", "machine-paths", "null-strings", "duplicate-rows", "comment-rows",
        "repeated-header", "mixed-date-formats", "mixed-separators", "bad-header",
        "no-unique-column", "unreadable", "bom", "crlf",
    }


def test_inventory_counts_rows_and_columns(tmp_path: Path) -> None:
    _w(tmp_path, "a.csv", "id,name\n1,x\n2,y\n3,z\n")
    report = readiness.scan_project(tmp_path)
    (f,) = report.files
    assert (f.path, f.rows, f.columns, f.header) == ("a.csv", 3, 2, ("id", "name"))
    assert report.findings == ()


def test_clean_file_has_no_findings_and_lists_unique_columns(tmp_path: Path) -> None:
    _w(tmp_path, "a.csv", "id,name\n1,x\n2,y\n")
    (f,) = readiness.scan_project(tmp_path).files
    assert [u.name for u in f.unique_columns] == ["id", "name"]


def test_header_only_file_has_no_unique_columns_or_no_unique_column_finding(tmp_path: Path) -> None:
    _w(tmp_path, "a.csv", "id,name\n")
    report = readiness.scan_project(tmp_path)
    (f,) = report.files
    assert f.rows == 0 and f.unique_columns == ()
    assert "no-unique-column" not in _kinds(report)


def test_single_row_file_reports_no_unique_columns(tmp_path: Path) -> None:
    _w(tmp_path, "a.csv", "id,name\n1,x\n")
    report = readiness.scan_project(tmp_path)
    assert report.files[0].unique_columns == ()
    assert "no-unique-column" not in _kinds(report)


def test_no_unique_column_finding(tmp_path: Path) -> None:
    _w(tmp_path, "a.csv", "a,b\n1,x\n1,y\n2,x\n")
    assert "no-unique-column" in _kinds(readiness.scan_project(tmp_path))


def test_unique_column_labels(tmp_path: Path) -> None:
    long = "word " * 20
    _w(tmp_path, "a.csv", f"id,summary,code\n1,{long}one,a1\n2,{long}two,b2\n")
    (f,) = readiness.scan_project(tmp_path).files
    labels = {u.name: set(u.labels) for u in f.unique_columns}
    assert labels["id"] == {"integer-like"}
    assert labels["summary"] == {"free-text"}
    assert labels["code"] == set()


def test_ragged_rows_counts_and_examples(tmp_path: Path) -> None:
    _w(tmp_path, "a.csv", "a,b\n1,2\n3\n4,5,6\n7,8\n")
    f = _find(readiness.scan_project(tmp_path), "ragged-rows", "a.csv")
    assert f.count == 2 and f.rows == (2, 3)


def test_row_examples_capped_at_three(tmp_path: Path) -> None:
    _w(tmp_path, "a.csv", "a,b\n" + "x\n" * 6)
    f = _find(readiness.scan_project(tmp_path), "ragged-rows", "a.csv")
    assert f.count == 6 and f.rows == (1, 2, 3)


def test_bad_header_empty_and_duplicate(tmp_path: Path) -> None:
    _w(tmp_path, "a.csv", "a,,a\n1,2,3\n")
    assert "bad-header" in _kinds(readiness.scan_project(tmp_path))


def test_mixed_date_formats_positive_and_negative(tmp_path: Path) -> None:
    _w(tmp_path, "mixed.csv", "d\n2026-03-19\n19/03/2026\n2026-03-20\n")
    _w(tmp_path, "one.csv", "d\n2026-03-19\n2026-03-20\n")
    r = readiness.scan_project(tmp_path)
    f = _find(r, "mixed-date-formats", "mixed.csv")
    assert f.column == "d" and dict(f.detail) == {"iso-date": 2, "slash": 1}
    assert "mixed-date-formats" not in _kinds(r, "one.csv")


def test_yyyymmdd_only_counts_when_a_real_date(tmp_path: Path) -> None:
    _w(tmp_path, "a.csv", "n\n12345678\n99999999\n2026-03-19\n")
    assert "mixed-date-formats" not in _kinds(readiness.scan_project(tmp_path))
    _w(tmp_path, "b.csv", "d\n20260319\n2026-03-19\n")
    assert "mixed-date-formats" in _kinds(readiness.scan_project(tmp_path), "b.csv")


def test_mixed_separators_needs_both_pipe_and_semicolon(tmp_path: Path) -> None:
    _w(tmp_path, "a.csv", "tags\na|b\nc;d\n")
    _w(tmp_path, "b.csv", "tags\na|b\nc|d\n")
    r = readiness.scan_project(tmp_path)
    assert "mixed-separators" in _kinds(r, "a.csv")
    assert "mixed-separators" not in _kinds(r, "b.csv")


def test_machine_paths_reports_count_and_rows_but_no_cell_text(tmp_path: Path) -> None:
    _w(tmp_path, "a.csv", "p\n/Users/alice/notes.txt\nok\nC:\\Users\\bob\\x.txt\n/mnt/c/stuff\n")
    r = readiness.scan_project(tmp_path)
    f = _find(r, "machine-paths", "a.csv")
    assert f.column == "p" and f.count == 3 and f.rows == (1, 3, 4)
    blob = readiness.format_markdown(r) + readiness.format_json(r)
    assert "alice" not in blob and "bob" not in blob


def test_null_strings(tmp_path: Path) -> None:
    _w(tmp_path, "a.csv", "v\nNO DATA\nn/a\nreal\n\"\"\nTBD\n")
    f = _find(readiness.scan_project(tmp_path), "null-strings", "a.csv")
    assert f.count == 3 and dict(f.detail) == {"NO DATA": 1, "N/A": 1, "TBD": 1}


def test_duplicate_comment_and_repeated_header_rows(tmp_path: Path) -> None:
    _w(tmp_path, "a.csv", "a,b\n1,2\n1,2\n# banner,x\na,b\n3,4\n")
    r = readiness.scan_project(tmp_path)
    assert _find(r, "duplicate-rows", "a.csv").count == 1
    assert _find(r, "comment-rows", "a.csv").rows == (3,)
    assert _find(r, "repeated-header", "a.csv").rows == (4,)


def test_bom_and_crlf_detected_and_header_is_bom_free(tmp_path: Path) -> None:
    _w(tmp_path, "a.csv", b"\xef\xbb\xbfid,name\r\n1,x\r\n2,y\r\n")
    r = readiness.scan_project(tmp_path)
    assert {"bom", "crlf"} <= _kinds(r, "a.csv")
    assert r.files[0].header == ("id", "name")


def test_unreadable_reasons_and_scan_continues(tmp_path: Path) -> None:
    _w(tmp_path, "empty.csv", b"")
    _w(tmp_path, "latin.csv", b"a\n\xe9\n")
    _w(tmp_path, "quote.csv", 'a,b\n1,"never closed\n2,3\n')
    _w(tmp_path, "good.csv", "a,b\n1,2\n3,4\n")
    r = readiness.scan_project(tmp_path)
    reasons = {f.file: dict(f.detail)["reason"] for f in r.findings if f.kind == "unreadable"}
    assert reasons == {"empty.csv": "empty-file", "latin.csv": "not-utf8", "quote.csv": "csv-parse-error"}
    assert [f.path for f in r.files] == ["good.csv"]


def test_oversized_field_is_unreadable_field_too_large(tmp_path: Path) -> None:
    _w(tmp_path, "big.csv", "a\n" + "x" * 200_000 + "\n")
    f = _find(readiness.scan_project(tmp_path), "unreadable", "big.csv")
    assert dict(f.detail)["reason"] == "field-too-large"


def test_scanned_set_equals_classifier_csv_set(tmp_path: Path) -> None:
    for rel in ("top.csv", "sub/x.csv", ".archive/old.csv", ".git/a.csv", "cc-sessions/s/b.csv",
                ".pdata-migrated/c.csv"):
        _w(tmp_path, rel, "a,b\n1,2\n3,4\n")
    scanned = {f.path for f in readiness.scan_project(tmp_path).files}
    classified = {e.path for e in classify.walk_and_classify(tmp_path) if e.path.endswith(".csv")}
    assert scanned == classified == {"top.csv", "sub/x.csv", ".archive/old.csv"}


def test_directory_symlinks_are_not_followed(tmp_path: Path) -> None:
    real = tmp_path / "real"
    _w(real, "a.csv", "a\n1\n")
    (tmp_path / "link").symlink_to(real, target_is_directory=True)
    assert [f.path for f in readiness.scan_project(tmp_path).files] == ["real/a.csv"]


def test_path_prefix_restricts_scan(tmp_path: Path) -> None:
    _w(tmp_path, "correspondence/a.csv", "a\n1\n2\n")
    _w(tmp_path, "other/b.csv", "a\n1\n2\n")
    r = readiness.scan_project(tmp_path, path_prefix="correspondence/")
    assert [f.path for f in r.files] == ["correspondence/a.csv"]


def test_not_scanned_counts_by_kind(tmp_path: Path) -> None:
    _w(tmp_path, "a.json", "{}")
    _w(tmp_path, "b.json", "{}")
    _w(tmp_path, "n.md", "x")
    _w(tmp_path, "z.pdf", "x")
    _w(tmp_path, ".git/c.json", "{}")
    assert readiness.scan_project(tmp_path).not_scanned == {"json": 2, "markdown": 1, "other": 1}


def test_markdown_states_row_number_definition_and_zero_csv_case(tmp_path: Path) -> None:
    out = readiness.format_markdown(readiness.scan_project(tmp_path))
    assert "No CSV files found" in out
    _w(tmp_path, "a.csv", "a,b\n1,2\n3\n")
    out = readiness.format_markdown(readiness.scan_project(tmp_path))
    assert "data record" in out and "not a stable key" in out


def test_markdown_omits_header_names_and_findings_only_omits_inventory(tmp_path: Path) -> None:
    _w(tmp_path, "a.csv", "secretcolumn,b\nx,2\nx\n")
    r = readiness.scan_project(tmp_path)
    full = readiness.format_markdown(r)
    assert "secretcolumn" not in full and "a.csv: 2 rows, 2 columns" in full
    slim = readiness.format_markdown(r, findings_only=True)
    assert "ragged-rows" in slim and "2 rows, 2 columns" not in slim


def test_json_has_files_findings_and_headers(tmp_path: Path) -> None:
    _w(tmp_path, "a.csv", "id,name\n1,x\n2\n")
    doc = json.loads(readiness.format_json(readiness.scan_project(tmp_path)))
    assert doc["files"][0]["header"] == ["id", "name"]
    assert any(f["kind"] == "ragged-rows" for f in doc["findings"])
    assert set(doc) >= {"files", "findings", "not_scanned"}


def test_scan_is_read_only(tmp_path: Path) -> None:
    _w(tmp_path, "a.csv", "a,b\n1\n")
    before = sorted((p.name, p.stat().st_mtime_ns) for p in tmp_path.rglob("*"))
    readiness.scan_project(tmp_path)
    assert before == sorted((p.name, p.stat().st_mtime_ns) for p in tmp_path.rglob("*"))


@pytest.mark.parametrize("kind", readiness.FINDING_KINDS)
def test_every_kind_is_a_plain_string(kind: str) -> None:
    assert isinstance(kind, str) and kind
