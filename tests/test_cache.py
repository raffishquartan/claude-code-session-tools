"""Tests for the mtime-keyed parquet cache."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from claude_code_usage import cache as cache_mod


def _good_record(uuid: str = "u-1") -> dict:
    return {
        "type": "assistant",
        "uuid": uuid,
        "sessionId": "s-1",
        "timestamp": "2026-05-09T10:36:44.339Z",
        "cwd": "/x/oneshot",
        "message": {
            "id": f"msg_{uuid}",
            "model": "claude-opus-4-7",
            "type": "message",
            "role": "assistant",
            "content": [],
            "usage": {"input_tokens": 6, "output_tokens": 68},
        },
    }


def _write_session(dir_: Path, name: str, records: list[dict]) -> Path:
    p = dir_ / name
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return p


def test_first_run_parses_everything_and_writes_manifest(tmp_path: Path) -> None:
    src = tmp_path / "projects"
    src.mkdir()
    _write_session(src, "a.jsonl", [_good_record("u-1")])
    _write_session(src, "b.jsonl", [_good_record("u-2"), _good_record("u-3")])
    cache = cache_mod.Cache(tmp_path / "cache")
    df = cache.load_or_parse(src)
    assert len(df) == 3
    assert (tmp_path / "cache" / "manifest.json").exists()


def test_unchanged_file_is_not_reparsed(
    tmp_path: Path, monkeypatch
) -> None:
    src = tmp_path / "projects"
    src.mkdir()
    _write_session(src, "a.jsonl", [_good_record("u-1")])
    cache = cache_mod.Cache(tmp_path / "cache")
    cache.load_or_parse(src)

    # Re-instantiate cache to drop in-memory state, then count parse_file calls.
    calls: list[Path] = []
    real_parse_file = cache_mod.parser.parse_file

    def spy(p):
        calls.append(Path(p))
        return real_parse_file(p)

    monkeypatch.setattr(cache_mod.parser, "parse_file", spy)
    cache2 = cache_mod.Cache(tmp_path / "cache")
    df = cache2.load_or_parse(src)
    assert len(df) == 1
    assert calls == []  # cache hit; no re-parse


def test_changed_mtime_triggers_reparse(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "projects"
    src.mkdir()
    p = _write_session(src, "a.jsonl", [_good_record("u-1")])
    cache = cache_mod.Cache(tmp_path / "cache")
    cache.load_or_parse(src)

    p.write_text("\n".join(json.dumps(r) for r in [_good_record("u-1"), _good_record("u-2")]) + "\n")
    new_mtime = time.time() + 5
    os.utime(p, (new_mtime, new_mtime))

    calls: list[Path] = []
    real_parse_file = cache_mod.parser.parse_file
    def spy(pp):
        calls.append(Path(pp))
        return real_parse_file(pp)
    monkeypatch.setattr(cache_mod.parser, "parse_file", spy)

    cache2 = cache_mod.Cache(tmp_path / "cache")
    df = cache2.load_or_parse(src)
    assert len(df) == 2
    assert calls == [p]


def test_loaded_df_has_session_type_column(tmp_path: Path) -> None:
    src = tmp_path / "projects"
    src.mkdir()
    _write_session(src, "a.jsonl", [_good_record("u-1")])
    cache = cache_mod.Cache(tmp_path / "cache")
    df = cache.load_or_parse(src)
    assert "session_type" in df.columns


def test_loaded_df_has_hook_parent_name_column(tmp_path: Path) -> None:
    src = tmp_path / "projects"
    src.mkdir()
    _write_session(src, "a.jsonl", [_good_record("u-1")])
    cache = cache_mod.Cache(tmp_path / "cache")
    df = cache.load_or_parse(src)
    assert "hook_parent_name" in df.columns


def test_old_manifest_version_triggers_full_rebuild(tmp_path: Path, monkeypatch) -> None:
    import json as _json
    src = tmp_path / "projects"
    src.mkdir()
    _write_session(src, "a.jsonl", [_good_record("u-1")])
    cache = cache_mod.Cache(tmp_path / "cache")
    cache.load_or_parse(src)

    # Overwrite manifest with version=1 (old format)
    manifest_path = tmp_path / "cache" / "manifest.json"
    data = _json.loads(manifest_path.read_text())
    data["version"] = 1
    manifest_path.write_text(_json.dumps(data))

    calls: list[Path] = []
    real_parse_file = cache_mod.parser.parse_file
    def spy(p):
        calls.append(Path(p))
        return real_parse_file(p)
    monkeypatch.setattr(cache_mod.parser, "parse_file", spy)

    cache2 = cache_mod.Cache(tmp_path / "cache")
    df = cache2.load_or_parse(src)
    assert len(df) == 1
    assert calls == [src / "a.jsonl"]  # forced full reparse


def test_deleted_source_file_drops_shard(tmp_path: Path) -> None:
    src = tmp_path / "projects"
    src.mkdir()
    p = _write_session(src, "a.jsonl", [_good_record("u-1")])
    cache = cache_mod.Cache(tmp_path / "cache")
    cache.load_or_parse(src)

    def per_source_shards():
        return [
            x for x in (tmp_path / "cache").glob("*.parquet")
            if x.name != "combined.parquet"
        ]

    assert len(per_source_shards()) == 1

    p.unlink()
    cache2 = cache_mod.Cache(tmp_path / "cache")
    df = cache2.load_or_parse(src)
    assert df.empty
    assert per_source_shards() == []
