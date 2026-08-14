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
                                strict=True)
        assert v["ok"] is True
        assert v["src_lines"] == v["dst_lines"] == 5
        assert v["remaining_long"] == 0

    def test_strict_fails_when_src_string_lingers(self, ms, tmp_path):
        src, dst = self._make_pair(tmp_path, "/old/cwd", "/new/cwd", 3)
        # Sneak old path back into dst.
        dst.write_text(dst.read_text() + json.dumps({"cwd": "/old/cwd"}) + "\n")
        v = ms.verify_dst_jsonl(src, dst, "/new/cwd", "/old/cwd", "-old-cwd",
                                strict=True)
        assert v["ok"] is False
        assert v["remaining_long"] >= 1

    def test_strict_fails_on_line_count_mismatch(self, ms, tmp_path):
        src, dst = self._make_pair(tmp_path, "/old/cwd", "/new/cwd", 5)
        # Truncate dst.
        dst.write_text("\n".join(dst.read_text().splitlines()[:3]) + "\n")
        v = ms.verify_dst_jsonl(src, dst, "/new/cwd", "/old/cwd", "-old-cwd",
                                strict=True)
        assert v["ok"] is False

    def test_non_strict_allows_destination_growth(self, ms, tmp_path):
        """After resume, dst gets extra lines. Non-strict should still pass."""
        src, dst = self._make_pair(tmp_path, "/old/cwd", "/new/cwd", 3)
        # Append more legitimate dst-cwd lines.
        with dst.open("a") as fh:
            fh.write(json.dumps({"cwd": "/new/cwd", "extra": True}) + "\n")
        v = ms.verify_dst_jsonl(src, dst, "/new/cwd", "/old/cwd", "-old-cwd",
                                strict=False)
        assert v["ok"] is True
        assert v["dst_lines"] > v["src_lines"]


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
