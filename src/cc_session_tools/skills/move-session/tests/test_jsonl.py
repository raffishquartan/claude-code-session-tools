"""Tests for jsonl manipulation: rewrite, verify, tombstone, cleanup script."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


class TestRewriteJsonlPaths:
    def test_replaces_strings_and_returns_counts(self, ms, tmp_path):
        p = tmp_path / "x.jsonl"
        p.write_text("foo bar foo baz\nfoo\n")
        counts = ms.rewrite_jsonl_paths(p, [("foo", "FOO"), ("baz", "BAZ")])
        assert counts == {"foo": 3, "baz": 1}
        assert p.read_text() == "FOO bar FOO BAZ\nFOO\n"

    def test_zero_count_when_pattern_absent(self, ms, tmp_path):
        p = tmp_path / "x.jsonl"
        p.write_text("nothing matches\n")
        counts = ms.rewrite_jsonl_paths(p, [("xyz", "ABC")])
        assert counts == {"xyz": 0}
        assert p.read_text() == "nothing matches\n"


class TestVerifyDstJsonl:
    def _make_pair(self, tmp_path, src_cwd, dst_cwd, src_lines):
        src = tmp_path / "src.jsonl"
        dst = tmp_path / "dst.jsonl"
        src_text = "".join(
            json.dumps({"cwd": src_cwd, "i": i}) + "\n" for i in range(src_lines)
        )
        dst_text = src_text.replace(src_cwd, dst_cwd)
        src.write_text(src_text)
        dst.write_text(dst_text)
        return src, dst

    def test_strict_passes_on_clean_rewrite(self, ms, tmp_path):
        src, dst = self._make_pair(tmp_path, "/old/cwd", "/new/cwd", 5)
        v = ms.verify_dst_jsonl(src, dst, "/new/cwd", "/old/cwd", "-old-cwd",
                                strict=True, dst_session_dir=tmp_path / "dst-session")
        assert v["ok"] is True
        assert v["src_lines"] == v["dst_lines"] == 5
        assert v["remaining_long"] == 0

    def test_strict_fails_when_src_string_lingers(self, ms, tmp_path):
        src, dst = self._make_pair(tmp_path, "/old/cwd", "/new/cwd", 3)
        # Sneak old path back into dst.
        dst.write_text(dst.read_text() + json.dumps({"cwd": "/old/cwd"}) + "\n")
        v = ms.verify_dst_jsonl(src, dst, "/new/cwd", "/old/cwd", "-old-cwd",
                                strict=True, dst_session_dir=tmp_path / "dst-session")
        assert v["ok"] is False
        assert v["remaining_long"] >= 1

    def test_strict_fails_on_line_count_mismatch(self, ms, tmp_path):
        src, dst = self._make_pair(tmp_path, "/old/cwd", "/new/cwd", 5)
        # Truncate dst.
        dst.write_text("\n".join(dst.read_text().splitlines()[:3]) + "\n")
        v = ms.verify_dst_jsonl(src, dst, "/new/cwd", "/old/cwd", "-old-cwd",
                                strict=True, dst_session_dir=tmp_path / "dst-session")
        assert v["ok"] is False

    def test_non_strict_allows_destination_growth(self, ms, tmp_path):
        """After resume, dst gets extra lines. Non-strict should still pass."""
        src, dst = self._make_pair(tmp_path, "/old/cwd", "/new/cwd", 3)
        # Append more legitimate dst-cwd lines.
        with dst.open("a") as fh:
            fh.write(json.dumps({"cwd": "/new/cwd", "extra": True}) + "\n")
        v = ms.verify_dst_jsonl(src, dst, "/new/cwd", "/old/cwd", "-old-cwd",
                                strict=False, dst_session_dir=tmp_path / "dst-session")
        assert v["ok"] is True
        assert v["dst_lines"] > v["src_lines"]


LAST_RECORD = {
    "uuid": "parent-id",
    "cwd": "/old/cwd",
    "sessionId": "sess-1",
    "version": "2.1.126",
    "gitBranch": "",
}


class TestTombstoneLinesForDestination:
    """Only the tombstones appended for the destination being verified, and
    any appended after them, are excluded from the source's length."""

    def _src(self, tmp_path: Path) -> Path:
        src = tmp_path / "src.jsonl"
        src.write_text("".join(
            json.dumps({"cwd": "/old/cwd", "i": i}) + "\n" for i in range(3)))
        return src

    def _session(self, tmp_path: Path, name: str) -> Path:
        return tmp_path / name / "cc-sessions" / "20260503-x"

    def _tombstone(self, ms, src: Path, tmp_path: Path, dst: Path) -> None:
        ms.append_tombstone(src, LAST_RECORD, tmp_path / "src-session", dst)

    def test_no_tombstone_excludes_nothing(self, ms, tmp_path):
        src = self._src(tmp_path)
        assert ms.tombstone_lines_for_destination(
            src, self._session(tmp_path, "b")) == 0

    def test_tombstone_for_destination_is_excluded(self, ms, tmp_path):
        src = self._src(tmp_path)
        b = self._session(tmp_path, "b")
        self._tombstone(ms, src, tmp_path, b)
        assert ms.tombstone_lines_for_destination(src, b) == 2

    def test_tombstone_for_another_destination_is_not_excluded(self, ms, tmp_path):
        src = self._src(tmp_path)
        self._tombstone(ms, src, tmp_path, self._session(tmp_path, "b"))
        assert ms.tombstone_lines_for_destination(
            src, self._session(tmp_path, "c")) == 0

    def test_destination_that_prefixes_another_does_not_match(self, ms, tmp_path):
        src = self._src(tmp_path)
        b = self._session(tmp_path, "b")
        self._tombstone(ms, src, tmp_path, b.with_name(b.name + "-later"))
        assert ms.tombstone_lines_for_destination(src, b) == 0

    def test_moved_twice(self, ms, tmp_path):
        src = self._src(tmp_path)
        b = self._session(tmp_path, "b")
        c = self._session(tmp_path, "c")
        self._tombstone(ms, src, tmp_path, b)
        self._tombstone(ms, src, tmp_path, c)
        assert ms.tombstone_lines_for_destination(src, b) == 4
        assert ms.tombstone_lines_for_destination(src, c) == 2

    def test_other_lines_after_the_tombstone_stay_counted(self, ms, tmp_path):
        src = self._src(tmp_path)
        b = self._session(tmp_path, "b")
        self._tombstone(ms, src, tmp_path, b)
        with src.open("a") as fh:
            fh.write(json.dumps({"type": "user", "uuid": "x"}) + "\n")
        assert ms.tombstone_lines_for_destination(src, b) == 2


class TestVerifyDstJsonlTombstone:
    def _pair_with_tombstone(self, ms, tmp_path: Path) -> tuple[Path, Path, Path]:
        src = tmp_path / "src.jsonl"
        dst = tmp_path / "dst.jsonl"
        src_text = "".join(
            json.dumps({"cwd": "/old/cwd", "i": i}) + "\n" for i in range(3))
        src.write_text(src_text)
        dst.write_text(src_text.replace("/old/cwd", "/new/cwd"))
        dst_session = tmp_path / "new" / "cc-sessions" / "20260503-x"
        ms.append_tombstone(src, LAST_RECORD, tmp_path / "src-session", dst_session)
        return src, dst, dst_session

    def test_non_strict_excludes_the_destination_tombstone(self, ms, tmp_path):
        src, dst, dst_session = self._pair_with_tombstone(ms, tmp_path)
        v = ms.verify_dst_jsonl(src, dst, "/new/cwd", "/old/cwd", "-old-cwd",
                                strict=False, dst_session_dir=dst_session)
        assert v["ok"] is True
        assert v["src_excluded_lines"] == 2

    def test_strict_does_not_exclude_the_tombstone(self, ms, tmp_path):
        src, dst, dst_session = self._pair_with_tombstone(ms, tmp_path)
        v = ms.verify_dst_jsonl(src, dst, "/new/cwd", "/old/cwd", "-old-cwd",
                                strict=True, dst_session_dir=dst_session)
        assert v["ok"] is False
        assert v["src_excluded_lines"] == 0


class TestMakeTombstoneRecords:
    def test_raises_value_error_on_none_last_record(self, ms, tmp_path):
        """Item 5.4.3 guard: make_tombstone_records must refuse None rather
        than NPEing inside .get(). Defence in depth: main() refuses earlier,
        but this guard catches future callers that forget."""
        with pytest.raises(ValueError, match="last_record is None"):
            ms.make_tombstone_records(None, tmp_path / "src", tmp_path / "dst")

    def test_records_have_proper_chain_and_metadata(self, ms, tmp_path):
        last = {
            "uuid": "parent-id",
            "cwd": "/src/cwd",
            "sessionId": "sess-1",
            "version": "2.1.126",
            "gitBranch": "main",
        }
        src_dir = tmp_path / "src"
        dst_dir = tmp_path / "projects-root" / "dst" / "cc-sessions" / "20260503-new"
        dst_dir.mkdir(parents=True)
        records = ms.make_tombstone_records(last, src_dir, dst_dir)
        assert len(records) == 2
        user, asst = records
        assert user["type"] == "user"
        assert asst["type"] == "assistant"
        assert user["parentUuid"] == "parent-id"
        assert asst["parentUuid"] == user["uuid"]
        # source metadata preserved
        for r in records:
            assert r["cwd"] == "/src/cwd"
            assert r["sessionId"] == "sess-1"
            assert r["version"] == "2.1.126"
        # both must JSON-serialise cleanly
        for r in records:
            json.dumps(r)


class TestWriteCleanupScript:
    def test_includes_rm_for_both_targets_when_cwd_changed(self, ms, tmp_path):
        src_dir = tmp_path / "old" / "cc-sessions" / "20260503-old"
        src_dir.mkdir(parents=True)
        src_jsonl = tmp_path / "old.jsonl"
        src_jsonl.write_text("{}")
        out = ms.write_cleanup_script(
            src_session_dir=src_dir,
            src_jsonl=src_jsonl,
            src_tag="20260503-old",
            dst_cwd_abs="/new/cwd",
            session_uuid="abc-123",
        )
        text = out.read_text()
        assert str(src_dir) in text
        assert str(src_jsonl) in text
        assert "rm -rf" in text
        assert "rm -f" in text
        assert "set -euo pipefail" in text
        assert out.stat().st_mode & 0o111  # executable

    def test_omits_jsonl_rm_for_rename_only(self, ms, tmp_path):
        src_dir = tmp_path / "proj" / "cc-sessions" / "20260503-old"
        src_dir.mkdir(parents=True)
        out = ms.write_cleanup_script(
            src_session_dir=src_dir,
            src_jsonl=None,  # rename-only signal
            src_tag="20260503-old",
            dst_cwd_abs="/proj",
            session_uuid="abc-123",
        )
        text = out.read_text()
        assert str(src_dir) in text
        assert "rm -rf" in text
        assert "rm -f" not in text
        assert "rename-only" in text  # explanatory comment block

    def test_writes_to_tmp_with_unique_timestamp(self, ms, tmp_path):
        src_dir = tmp_path / "p" / "cc-sessions" / "20260503-x"
        src_dir.mkdir(parents=True)
        out = ms.write_cleanup_script(
            src_session_dir=src_dir,
            src_jsonl=None,
            src_tag="20260503-x",
            dst_cwd_abs="/p",
            session_uuid="u",
        )
        assert str(out).startswith("/tmp/move-session-cleanup-20260503-x-")
        assert out.suffix == ".sh"
