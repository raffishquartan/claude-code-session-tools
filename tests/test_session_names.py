"""Tests for the session-name resolver."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_code_usage import session_names as sn


def _write_live(dir_: Path, pid: str, sid: str, name: str | None) -> None:
    payload = {"pid": int(pid), "sessionId": sid}
    if name is not None:
        payload["name"] = name
    (dir_ / f"{pid}.json").write_text(json.dumps(payload))


def test_load_live_names_returns_id_to_name(tmp_path: Path) -> None:
    live = tmp_path / "sessions"
    live.mkdir()
    _write_live(live, "1001", "uuid-aaa", "20260509-foo")
    _write_live(live, "1002", "uuid-bbb", "20260509-bar")
    _write_live(live, "1003", "uuid-ccc", None)  # missing name -> excluded

    out = sn.load_live_names(live)

    assert out == {"uuid-aaa": "20260509-foo", "uuid-bbb": "20260509-bar"}


def test_load_live_names_missing_directory(tmp_path: Path) -> None:
    assert sn.load_live_names(tmp_path / "does-not-exist") == {}


def test_load_live_names_skips_corrupt_files(tmp_path: Path) -> None:
    live = tmp_path / "sessions"
    live.mkdir()
    _write_live(live, "1001", "uuid-aaa", "20260509-foo")
    (live / "broken.json").write_text("{not valid json")

    assert sn.load_live_names(live) == {"uuid-aaa": "20260509-foo"}


def test_persistent_cache_round_trip(tmp_path: Path) -> None:
    live = tmp_path / "sessions"
    live.mkdir()
    cache_path = tmp_path / "names.json"
    _write_live(live, "1001", "uuid-aaa", "20260509-foo")

    merged = sn.update_persistent_cache(cache_path, live)
    assert merged == {"uuid-aaa": "20260509-foo"}
    assert json.loads(cache_path.read_text()) == merged

    # Live record disappears (process exits), cache must still know the name.
    (live / "1001.json").unlink()
    merged2 = sn.update_persistent_cache(cache_path, live)
    assert merged2 == {"uuid-aaa": "20260509-foo"}


def test_persistent_cache_live_takes_precedence_for_renames(tmp_path: Path) -> None:
    live = tmp_path / "sessions"
    live.mkdir()
    cache_path = tmp_path / "names.json"
    cache_path.write_text(json.dumps({"uuid-aaa": "old-name"}))
    _write_live(live, "1001", "uuid-aaa", "new-name")

    merged = sn.update_persistent_cache(cache_path, live)
    assert merged["uuid-aaa"] == "new-name"


def test_fallback_name_uses_first_8_chars() -> None:
    assert sn.fallback_name("ddab284a-db4a-4223-b9cd-27ce2fc3313c") == "sess-ddab284a"


def test_format_session_modes() -> None:
    m = {"u-1": "20260509-foo"}
    assert sn.format_session("u-1", m, "name") == "20260509-foo"
    assert sn.format_session("u-1", m, "uuid") == "u-1"
    assert sn.format_session("u-1", m, "both") == "20260509-foo  (u-1)"
    # unknown UUID falls back
    assert sn.format_session("u-2", m, "name") == "sess-u-2"
    with pytest.raises(ValueError):
        sn.format_session("u-1", m, "bogus")


def test_resolve_filter_exact_uuid_passthrough() -> None:
    m = {"ddab284a-...": "20260509-foo"}
    # Exact UUID present in the map
    assert list(sn.resolve_filter("ddab284a-...", m)) == ["ddab284a-..."]


def test_resolve_filter_exact_name_match() -> None:
    m = {"u-1": "20260509-foo", "u-2": "20260509-bar"}
    assert list(sn.resolve_filter("20260509-foo", m)) == ["u-1"]


def test_resolve_filter_uuid_prefix() -> None:
    m = {"ddab284a-db4a": "n1", "ddab999z-zzz": "n2"}
    assert list(sn.resolve_filter("ddab284a", m)) == ["ddab284a-db4a"]


def test_resolve_filter_name_substring_case_insensitive() -> None:
    m = {"u-1": "20260509-Foo-Bar", "u-2": "20260509-baz"}
    assert sorted(sn.resolve_filter("foo", m)) == ["u-1"]


def test_resolve_filter_unknown_uuid_passes_through() -> None:
    # A 36-char hex-ish string we've never seen is presumed to be a UUID
    # the user knows about and we don't yet.
    m = {}
    needle = "00000000-0000-0000-0000-000000000000"
    assert list(sn.resolve_filter(needle, m)) == [needle]


# --- load_jsonl_titles tests ---

def _make_jsonl(dir_: Path, name: str, records: list[dict]) -> Path:
    p = dir_ / name
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return p


def test_load_jsonl_titles_returns_uuid_to_title(tmp_path: Path) -> None:
    _make_jsonl(tmp_path, "abc.jsonl", [
        {"type": "custom-title", "customTitle": "20260509-my-session", "sessionId": "uuid-abc"},
    ])
    result = sn.load_jsonl_titles(tmp_path)
    assert result == {"uuid-abc": "20260509-my-session"}


def test_load_jsonl_titles_skips_files_without_custom_title(tmp_path: Path) -> None:
    _make_jsonl(tmp_path, "no-title.jsonl", [
        {"type": "assistant", "sessionId": "uuid-xyz"},
    ])
    result = sn.load_jsonl_titles(tmp_path)
    assert result == {}


def test_load_jsonl_titles_uses_first_custom_title_per_file(tmp_path: Path) -> None:
    _make_jsonl(tmp_path, "abc.jsonl", [
        {"type": "custom-title", "customTitle": "first-title", "sessionId": "uuid-abc"},
        {"type": "custom-title", "customTitle": "second-title", "sessionId": "uuid-abc"},
    ])
    result = sn.load_jsonl_titles(tmp_path)
    assert result["uuid-abc"] == "first-title"


def test_load_jsonl_titles_searches_subdirectories(tmp_path: Path) -> None:
    sub = tmp_path / "proj-a"
    sub.mkdir()
    _make_jsonl(sub, "sess.jsonl", [
        {"type": "custom-title", "customTitle": "deep-session", "sessionId": "uuid-deep"},
    ])
    result = sn.load_jsonl_titles(tmp_path)
    assert result == {"uuid-deep": "deep-session"}


def test_load_jsonl_titles_missing_directory_returns_empty(tmp_path: Path) -> None:
    assert sn.load_jsonl_titles(tmp_path / "does-not-exist") == {}


# --- update_persistent_cache priority tests ---

def test_update_persistent_cache_jsonl_title_fills_unnamed_sessions(
    tmp_path: Path, monkeypatch
) -> None:
    """JSONL custom-title resolves sessions whose PID file has been pruned."""
    projects = tmp_path / "projects"
    projects.mkdir()
    _make_jsonl(projects, "sess.jsonl", [
        {"type": "custom-title", "customTitle": "20260509-recovered-name", "sessionId": "uuid-pruned"},
    ])
    cache_path = tmp_path / "names.json"
    live_dir = tmp_path / "sessions"
    live_dir.mkdir()  # empty - PID file gone

    merged = sn.update_persistent_cache(cache_path, live_dir, projects_dir=projects)
    assert merged.get("uuid-pruned") == "20260509-recovered-name"


def test_update_persistent_cache_pid_file_beats_jsonl_title(
    tmp_path: Path, monkeypatch
) -> None:
    """PID-file name takes priority over JSONL custom-title."""
    projects = tmp_path / "projects"
    projects.mkdir()
    _make_jsonl(projects, "sess.jsonl", [
        {"type": "custom-title", "customTitle": "jsonl-name", "sessionId": "uuid-1"},
    ])
    live = tmp_path / "sessions"
    live.mkdir()
    _write_live(live, "1001", "uuid-1", "pid-name")
    cache_path = tmp_path / "names.json"

    merged = sn.update_persistent_cache(cache_path, live, projects_dir=projects)
    assert merged["uuid-1"] == "pid-name"


def test_update_persistent_cache_jsonl_title_beats_old_cached_name(
    tmp_path: Path,
) -> None:
    """JSONL custom-title overwrites a stale cached name when PID file is gone."""
    projects = tmp_path / "projects"
    projects.mkdir()
    _make_jsonl(projects, "sess.jsonl", [
        {"type": "custom-title", "customTitle": "new-jsonl-name", "sessionId": "uuid-2"},
    ])
    cache_path = tmp_path / "names.json"
    cache_path.write_text(json.dumps({"uuid-2": "old-cached-name"}))
    live = tmp_path / "sessions"
    live.mkdir()  # empty

    merged = sn.update_persistent_cache(cache_path, live, projects_dir=projects)
    assert merged["uuid-2"] == "new-jsonl-name"


def test_update_persistent_cache_no_projects_dir_unchanged_behaviour(
    tmp_path: Path,
) -> None:
    """Omitting projects_dir falls back to old behaviour (no JSONL scan)."""
    cache_path = tmp_path / "names.json"
    live = tmp_path / "sessions"
    live.mkdir()
    _write_live(live, "1001", "uuid-ok", "live-name")

    merged = sn.update_persistent_cache(cache_path, live)
    assert merged == {"uuid-ok": "live-name"}
