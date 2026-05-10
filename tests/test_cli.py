"""Smoke tests for the CLI entrypoint."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from claude_code_usage import cli


def _good_record(
    uuid: str, model: str = "claude-opus-4-7", session_id: str = "s-1"
) -> dict:
    return {
        "type": "assistant",
        "uuid": uuid,
        "sessionId": session_id,
        "timestamp": "2026-04-15T10:00:00Z",
        "cwd": "/x/oneshot",
        "message": {
            "id": f"msg_{uuid}",
            "model": model,
            "type": "message",
            "role": "assistant",
            "content": [],
            "usage": {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
        },
    }


def _populate(tmp_path: Path) -> tuple[Path, Path]:
    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "session.jsonl").write_text(
        "\n".join(json.dumps(_good_record(f"u{i}")) for i in range(3)) + "\n"
    )
    cache_dir = tmp_path / "cache"
    return projects, cache_dir


def _populate_with_session_names(tmp_path: Path) -> tuple[Path, Path]:
    """Two sessions: one named (via cache), one unnamed -> fallback path."""
    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "a.jsonl").write_text(
        "\n".join(
            json.dumps(_good_record(f"u{i}", session_id="sid-named-1"))
            for i in range(2)
        )
        + "\n"
    )
    (projects / "b.jsonl").write_text(
        "\n".join(
            json.dumps(_good_record(f"v{i}", session_id="sid-unnamed-2"))
            for i in range(2)
        )
        + "\n"
    )
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "session_names.json").write_text(
        json.dumps({"sid-named-1": "20260509-test-named-session"})
    )
    return projects, cache_dir


def test_cli_query_prints_markdown(tmp_path, capsys, monkeypatch) -> None:
    projects, cache_dir = _populate(tmp_path)
    rc = cli.main(
        [
            "query",
            "--projects-dir", str(projects),
            "--cache-dir", str(cache_dir),
            "--group-by", "project",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "oneshot" in out
    assert "cost_usd" in out


def test_cli_query_csv_format(tmp_path, capsys) -> None:
    projects, cache_dir = _populate(tmp_path)
    rc = cli.main(
        [
            "query",
            "--projects-dir", str(projects),
            "--cache-dir", str(cache_dir),
            "--format", "csv",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "total_tokens" in out


def test_cli_version_flag_prints_and_exits(capsys) -> None:
    from claude_code_usage import __version__ as expected
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert expected in out
    assert "claude-code-usage" in out


def test_cli_session_format_default_name(tmp_path, capsys, monkeypatch) -> None:
    """Default --session-format=name: shows the human name, hides the UUID."""
    monkeypatch.setattr(
        "claude_code_usage.session_names.DEFAULT_LIVE_DIR", tmp_path / "no-live"
    )
    projects, cache_dir = _populate_with_session_names(tmp_path)
    rc = cli.main(
        [
            "query",
            "--projects-dir", str(projects),
            "--cache-dir", str(cache_dir),
            "--group-by", "session",
            "--format", "csv",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "20260509-test-named-session" in out
    assert "sess-sid-unna" in out  # fallback for unnamed session
    # uuid column hidden under default format
    assert "sid-named-1" not in out
    assert "sid-unnamed-2" not in out


def test_cli_session_format_uuid_keeps_old_behaviour(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        "claude_code_usage.session_names.DEFAULT_LIVE_DIR", tmp_path / "no-live"
    )
    projects, cache_dir = _populate_with_session_names(tmp_path)
    rc = cli.main(
        [
            "query",
            "--projects-dir", str(projects),
            "--cache-dir", str(cache_dir),
            "--group-by", "session",
            "--session-format", "uuid",
            "--format", "csv",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "sid-named-1" in out
    assert "sid-unnamed-2" in out
    assert "20260509-test-named-session" not in out


def test_cli_session_format_both_shows_name_and_uuid(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        "claude_code_usage.session_names.DEFAULT_LIVE_DIR", tmp_path / "no-live"
    )
    projects, cache_dir = _populate_with_session_names(tmp_path)
    rc = cli.main(
        [
            "query",
            "--projects-dir", str(projects),
            "--cache-dir", str(cache_dir),
            "--group-by", "session",
            "--session-format", "both",
            "--format", "csv",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "20260509-test-named-session" in out
    assert "sid-named-1" in out
    assert "session_id" in out  # both mode renames the uuid column for clarity


def test_cli_session_filter_accepts_name(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        "claude_code_usage.session_names.DEFAULT_LIVE_DIR", tmp_path / "no-live"
    )
    projects, cache_dir = _populate_with_session_names(tmp_path)
    rc = cli.main(
        [
            "query",
            "--projects-dir", str(projects),
            "--cache-dir", str(cache_dir),
            "--session", "20260509-test-named-session",
            "--group-by", "session",
            "--format", "csv",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "20260509-test-named-session" in out
    assert "sess-sid-unna" not in out


def _populate_with_hook_children(tmp_path: Path) -> tuple[Path, Path]:
    """Parent session + one hook child (has parent_session_id)."""
    projects = tmp_path / "projects"
    projects.mkdir()
    # Parent session
    (projects / "parent.jsonl").write_text(
        "\n".join(
            json.dumps(_good_record(f"u{i}", session_id="parent-sid"))
            for i in range(2)
        )
        + "\n"
    )
    # Hook session with session-name prefix pointing to parent
    hook_user = {
        "type": "user",
        "message": {"role": "user", "content": "20260509-parent-session: Review this shell command for security risks and side effects."},
    }
    (projects / "hook.jsonl").write_text(
        json.dumps(hook_user) + "\n"
        + json.dumps(_good_record("h1", session_id="hook-sid")) + "\n"
    )
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "session_names.json").write_text(
        json.dumps({"parent-sid": "20260509-parent-session"})
    )
    return projects, cache_dir


def test_cli_query_include_children_folds_hook_into_parent(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        "claude_code_usage.session_names.DEFAULT_LIVE_DIR", tmp_path / "no-live"
    )
    projects, cache_dir = _populate_with_hook_children(tmp_path)
    rc = cli.main(
        [
            "query",
            "--projects-dir", str(projects),
            "--cache-dir", str(cache_dir),
            "--group-by", "session",
            "--include-children",
            "--format", "csv",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    # hook session row should not appear; parent row should have child columns
    assert "hook-sid" not in out
    assert "child_session_count" in out


def test_cli_children_subcommand_lists_child_sessions(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        "claude_code_usage.session_names.DEFAULT_LIVE_DIR", tmp_path / "no-live"
    )
    projects, cache_dir = _populate_with_hook_children(tmp_path)
    rc = cli.main(
        [
            "children",
            "--projects-dir", str(projects),
            "--cache-dir", str(cache_dir),
            "parent-sid",
            "--format", "csv",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "hook-sid" in out


def test_cli_children_subcommand_returns_empty_for_unknown_parent(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        "claude_code_usage.session_names.DEFAULT_LIVE_DIR", tmp_path / "no-live"
    )
    projects, cache_dir = _populate_with_hook_children(tmp_path)
    rc = cli.main(
        [
            "children",
            "--projects-dir", str(projects),
            "--cache-dir", str(cache_dir),
            "no-such-session",
            "--format", "csv",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    # No rows; just headers or empty notice
    assert "hook-sid" not in out


def test_cli_warm_cache(tmp_path, capsys) -> None:
    projects, cache_dir = _populate(tmp_path)
    rc = cli.main(
        ["warm-cache", "--projects-dir", str(projects), "--cache-dir", str(cache_dir)]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Cache warmed" in out


def _populate_with_hook_and_regular(tmp_path: Path) -> tuple[Path, Path]:
    """Two sessions: one regular, one hook-security-review."""
    projects = tmp_path / "projects"
    projects.mkdir()
    # Regular session
    (projects / "regular.jsonl").write_text(
        json.dumps({"type": "user", "message": {"role": "user", "content": "Hello"}}) + "\n"
        + json.dumps(_good_record("u1", session_id="sid-regular")) + "\n"
    )
    # Hook session
    hook_user = {
        "type": "user",
        "message": {"role": "user", "content": "Review this shell command for security risks: ls"},
    }
    (projects / "hook.jsonl").write_text(
        json.dumps(hook_user) + "\n"
        + json.dumps(_good_record("h1", session_id="sid-hook")) + "\n"
    )
    cache_dir = tmp_path / "cache"
    return projects, cache_dir


def test_cli_query_exclude_hooks_removes_hook_rows(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        "claude_code_usage.session_names.DEFAULT_LIVE_DIR", tmp_path / "no-live"
    )
    projects, cache_dir = _populate_with_hook_and_regular(tmp_path)
    rc = cli.main(
        [
            "query",
            "--projects-dir", str(projects),
            "--cache-dir", str(cache_dir),
            "--group-by", "session",
            "--exclude-hooks",
            "--format", "csv",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "sid-regular" not in out  # UUID hidden by default name format
    assert "sid-hook" not in out
    # Regular session should appear, hook session should not
    import csv, io
    data_rows = list(csv.DictReader(io.StringIO(out)))
    assert len(data_rows) == 1


def test_cli_query_exclude_hooks_affects_total_cost(tmp_path, capsys, monkeypatch) -> None:
    """With hooks excluded, total cost is half of the full total."""
    monkeypatch.setattr(
        "claude_code_usage.session_names.DEFAULT_LIVE_DIR", tmp_path / "no-live"
    )
    projects, cache_dir = _populate_with_hook_and_regular(tmp_path)
    # Run without filter
    cli.main(["query", "--projects-dir", str(projects), "--cache-dir", str(cache_dir), "--format", "csv"])
    full_out = capsys.readouterr().out
    # Run with filter
    cli.main(["query", "--projects-dir", str(projects), "--cache-dir", str(cache_dir), "--exclude-hooks", "--format", "csv"])
    filtered_out = capsys.readouterr().out
    # Each session has equal token counts, so filtered total cost < full
    import csv, io
    full_cost = sum(float(r["cost_usd"]) for r in csv.DictReader(io.StringIO(full_out)) if "cost_usd" in r)
    filtered_cost = sum(float(r["cost_usd"]) for r in csv.DictReader(io.StringIO(filtered_out)) if "cost_usd" in r)
    assert filtered_cost < full_cost


def test_cli_query_include_hooks_default_includes_hooks(tmp_path, capsys, monkeypatch) -> None:
    """Default (no flag) includes hook sessions."""
    monkeypatch.setattr(
        "claude_code_usage.session_names.DEFAULT_LIVE_DIR", tmp_path / "no-live"
    )
    projects, cache_dir = _populate_with_hook_and_regular(tmp_path)
    rc = cli.main(
        [
            "query",
            "--projects-dir", str(projects),
            "--cache-dir", str(cache_dir),
            "--group-by", "session",
            "--format", "csv",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    import csv, io
    data_rows = list(csv.DictReader(io.StringIO(out)))
    assert len(data_rows) == 2
