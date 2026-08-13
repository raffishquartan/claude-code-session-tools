from __future__ import annotations

import os
from pathlib import Path

import pytest

from cc_session_tools.cli import ccr


@pytest.fixture(autouse=True)
def _claude_on_path(monkeypatch):
    """Pretend `claude` is on PATH by default so tests don't fail in CI
    where Claude Code isn't installed. Tests that exercise the missing-PATH
    branch override this with their own monkeypatch.setattr after the fact."""
    import shutil as _shutil
    monkeypatch.setattr(_shutil, "which", lambda name: "/usr/bin/claude")


@pytest.fixture
def captured_launch(monkeypatch):
    captured: dict = {}

    def fake_launch(cmd, env, cwd=None):
        captured["cmd"] = list(cmd)
        captured["env"] = dict(env)
        captured["cwd"] = cwd

    monkeypatch.setattr(ccr, "launch_claude_resume", fake_launch)
    return captured


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path / "db"))
    return home


@pytest.fixture
def fake_repos(fake_home, tmp_path, monkeypatch):
    repos = tmp_path / "repos"
    repos.mkdir()
    monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(repos))
    return repos


def _make_session(repos: Path, project: str, basename: str) -> Path:
    from cc_session_tools.lib import sessions_db
    sess = repos / project / "cc-sessions" / basename
    (sess / "working").mkdir(parents=True)
    (sess / "out").mkdir()
    sessions_db.ensure_session_row(repos / project, basename)
    return sess


def test_ccr_unique_match_launches_resume(fake_repos, captured_launch):
    _make_session(fake_repos, "myproj", "20260504-foo-bar")

    rc = ccr.main(["foo-bar"])
    assert rc == 0

    cmd = captured_launch["cmd"]
    assert cmd[0] == "claude"
    assert "--resume" in cmd
    assert "20260504-foo-bar" in cmd
    assert "--remote-control" in cmd


def test_ccr_sets_session_start_hook_env_for_resume(fake_repos, captured_launch):
    _make_session(fake_repos, "myproj", "20260504-foo-bar")
    rc = ccr.main(["foo-bar"])
    assert rc == 0

    env = captured_launch["env"]
    assert env["CLD_SESSION_TAG"] == "foo-bar"
    assert env["CLD_SESSION_MODE"] == "resume"
    assert env["CLD_SESSION_DIR"].endswith("cc-sessions/20260504-foo-bar")
    assert env["CLAUDE_CODE_TASK_LIST_ID"] == "myproj"


def test_ccr_no_match_returns_1(fake_repos, capsys, captured_launch):
    _make_session(fake_repos, "myproj", "20260504-foo")
    rc = ccr.main(["nope"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "no sessions match" in err


def test_ccr_multi_match_lists_and_returns_0(fake_repos, capsys, captured_launch):
    _make_session(fake_repos, "myproj", "20260504-foo-one")
    _make_session(fake_repos, "myproj", "20260503-foo-two")
    rc = ccr.main(["foo"])
    assert rc == 0
    # Should not have launched claude
    assert "cmd" not in captured_launch
    out = capsys.readouterr().out
    assert "20260504-foo-one" in out
    assert "20260503-foo-two" in out


def test_ccr_changes_to_project_dir_before_launch(fake_repos, captured_launch):
    sess = _make_session(fake_repos, "myproj", "20260504-foo")
    project_dir = sess.parent.parent
    rc = ccr.main(["foo"])
    assert rc == 0
    assert captured_launch["cwd"] == project_dir


# ---------------------------------------------------------------------------
# Task 13: exact-match fast-path
# ---------------------------------------------------------------------------

def test_ccr_exact_basename_skips_substring_ambiguity(fake_repos, captured_launch):
    # "20260504-foo" is an exact basename but also a substring of "20260504-foo-bar"
    _make_session(fake_repos, "proj1", "20260504-foo")
    _make_session(fake_repos, "proj2", "20260504-foo-bar")

    rc = ccr.main(["20260504-foo"])
    assert rc == 0
    assert "20260504-foo" in captured_launch["cmd"]
    assert "20260504-foo-bar" not in captured_launch["cmd"]


def test_ccr_falls_back_to_substring_when_no_exact_match(fake_repos, captured_launch):
    _make_session(fake_repos, "proj1", "20260504-improve-ccx")

    rc = ccr.main(["improve"])
    assert rc == 0
    assert "20260504-improve-ccx" in captured_launch["cmd"]


# ---------------------------------------------------------------------------
# Task 14: PATH check for claude binary
# ---------------------------------------------------------------------------

def test_ccr_fails_clearly_when_claude_not_on_path(fake_repos, monkeypatch, capsys):
    _make_session(fake_repos, "proj1", "20260504-foo")
    import shutil as _shutil
    monkeypatch.setattr(_shutil, "which", lambda name: None)

    rc = ccr.main(["foo"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "claude" in err.lower()
    assert ("not found" in err.lower() or "path" in err.lower())


# ---------------------------------------------------------------------------
# Task 15: claude flag pass-through
# ---------------------------------------------------------------------------

def test_ccr_passes_through_valid_claude_flags(fake_repos, captured_launch, monkeypatch):
    _make_session(fake_repos, "proj1", "20260504-foo")
    monkeypatch.setattr(ccr, "get_claude_flags", lambda: {"--model", "--debug", "--append-system-prompt"})
    import shutil as _shutil
    monkeypatch.setattr(_shutil, "which", lambda name: "/usr/bin/claude")

    rc = ccr.main(["foo", "--model", "sonnet"])
    assert rc == 0
    assert "--model" in captured_launch["cmd"]
    assert "sonnet" in captured_launch["cmd"]


def test_ccr_rejects_unknown_claude_flags(fake_repos, monkeypatch, capsys):
    _make_session(fake_repos, "proj1", "20260504-foo")
    import cc_session_tools.lib.claude_flags as cf
    monkeypatch.setattr(cf, "get_claude_flags", lambda: {"--model", "--debug"})

    rc = ccr.main(["foo", "--not-a-real-flag"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "--not-a-real-flag" in err


# ---------------------------------------------------------------------------
# Task 17: ccr picker integration
# ---------------------------------------------------------------------------

def test_ccr_picker_shown_for_2_to_10_matches(fake_repos, captured_launch, monkeypatch):
    _make_session(fake_repos, "proj1", "20260504-foo-one")
    _make_session(fake_repos, "proj2", "20260503-foo-two")
    import shutil as _shutil
    monkeypatch.setattr(_shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    from cc_session_tools.lib import picker
    monkeypatch.setattr(picker, "pick_from_list", lambda _: 0)  # pick first

    rc = ccr.main(["foo"])
    assert rc == 0
    assert "20260504-foo-one" in captured_launch["cmd"]


def test_ccr_keeps_rerrun_message_for_more_than_10(fake_repos, monkeypatch, capsys):
    for i in range(11):
        _make_session(fake_repos, f"proj{i}", f"20260501-foo-{i:02d}")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    rc = ccr.main(["foo"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Multiple sessions" in out


# ---------------------------------------------------------------------------
# Task 18: --debug flag and CCX_DEBUG env var
# ---------------------------------------------------------------------------

def test_ccr_debug_flag_produces_output(fake_repos, captured_launch, monkeypatch, capsys):
    _make_session(fake_repos, "proj1", "20260504-foo")
    monkeypatch.delenv("CCX_DEBUG", raising=False)

    ccr.main(["foo", "--debug"])
    err = capsys.readouterr().err
    assert "[CCX_DEBUG]" in err


# ---------------------------------------------------------------------------
# Duplicate-transcript picker: two jsonls share one session tag (e.g. after
# hitting Ctrl-L twice mid-session clears a copy alongside the original).
# ---------------------------------------------------------------------------

def _write_jsonl(project_dir: Path, uuid: str, basename: str, padding: str = "") -> Path:
    import json

    from cc_session_tools.lib.sessions import transcript_dir_for_project
    tdir = transcript_dir_for_project(project_dir)
    tdir.mkdir(parents=True, exist_ok=True)
    p = tdir / f"{uuid}.jsonl"
    p.write_text(json.dumps({"type": "custom-title", "customTitle": basename}) + "\n" + padding)
    return p


def test_ccr_multiple_transcripts_same_tag_shows_picker(fake_repos, captured_launch, monkeypatch):
    sess = _make_session(fake_repos, "myproj", "20260504-foo-bar")
    project_dir = sess.parent.parent
    old = _write_jsonl(project_dir, "uuid-old", "20260504-foo-bar")
    _write_jsonl(project_dir, "uuid-new", "20260504-foo-bar", padding="x" * 500)
    import os
    import time
    os.utime(old, (time.time() - 3600, time.time() - 3600))

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    from cc_session_tools.lib import picker
    picked: dict = {}

    def fake_pick(labels):
        picked["labels"] = labels
        return 0

    monkeypatch.setattr(picker, "pick_from_list", fake_pick)

    rc = ccr.main(["foo-bar"])
    assert rc == 0
    assert "uuid-new" in captured_launch["cmd"]  # sorted most-recent-first, index 0
    assert any("B" in label for label in picked["labels"])  # size shown
    assert any(":" in label for label in picked["labels"])  # timestamp shown


def test_ccr_multiple_transcripts_non_interactive_picks_most_recent(
    fake_repos, captured_launch, monkeypatch, capsys
):
    sess = _make_session(fake_repos, "myproj", "20260504-foo-bar")
    project_dir = sess.parent.parent
    old = _write_jsonl(project_dir, "uuid-old", "20260504-foo-bar")
    _write_jsonl(project_dir, "uuid-new", "20260504-foo-bar")
    import os
    import time
    os.utime(old, (time.time() - 3600, time.time() - 3600))

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    rc = ccr.main(["foo-bar"])
    assert rc == 0
    assert "uuid-new" in captured_launch["cmd"]
    err = capsys.readouterr().err
    assert "share the session tag" in err


def test_ccr_duplicate_transcript_picker_cancel_returns_0(fake_repos, captured_launch, monkeypatch):
    sess = _make_session(fake_repos, "myproj", "20260504-foo-bar")
    project_dir = sess.parent.parent
    _write_jsonl(project_dir, "uuid-a", "20260504-foo-bar")
    _write_jsonl(project_dir, "uuid-b", "20260504-foo-bar")

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    from cc_session_tools.lib import picker
    monkeypatch.setattr(picker, "pick_from_list", lambda labels: None)

    rc = ccr.main(["foo-bar"])
    assert rc == 0
    assert "cmd" not in captured_launch


def test_ccr_single_transcript_unaffected(fake_repos, captured_launch):
    sess = _make_session(fake_repos, "myproj", "20260504-foo-bar")
    project_dir = sess.parent.parent
    _write_jsonl(project_dir, "uuid-only", "20260504-foo-bar")

    rc = ccr.main(["foo-bar"])
    assert rc == 0
    assert "uuid-only" in captured_launch["cmd"]


# ---------------------------------------------------------------------------
# Task 8: surface corrupted-row misses (invisible to exact-match fast path,
# find_matching_sessions, AND find_orphan_transcripts alike)
# ---------------------------------------------------------------------------

def test_ccr_warns_when_fragment_matches_only_a_corrupted_row(fake_repos, capsys, captured_launch):
    """A row with a non-absolute project_dir is invisible to both the exact-match fast path
    and find_matching_sessions's root filter, and find_orphan_transcripts skips it too (its
    cc-sessions/<name>/ dir exists on disk). Without a diagnostic this is a silent 'not found' -
    point the user at ccst repair sessions instead."""
    from cc_session_tools.lib import sessions_db

    conn = sessions_db.connect()
    conn.execute(
        "INSERT INTO sessions (project_dir, basename, start_date, discovered_at) "
        "VALUES ('.', '20260101-corrupt', '20260101', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    rc = ccr.main(["corrupt"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "no sessions match" in err
    assert "ccst repair sessions" in err
    assert "20260101-corrupt" in err


def test_ccr_no_corrupted_row_warning_for_ordinary_out_of_root_miss(fake_repos, capsys, captured_launch):
    """A session that simply isn't under a configured root (ordinary scoping) must NOT trigger
    the corrupted-row diagnostic - that would be misleading."""
    from pathlib import Path
    from cc_session_tools.lib import sessions_db

    sessions_db.ensure_session_row(Path("/some/other/root/proj"), "20260101-elsewhere")

    rc = ccr.main(["elsewhere"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "no sessions match" in err
    assert "ccst repair sessions" not in err


def test_ccr_warns_about_corrupted_sibling_even_when_a_real_match_resumes(
    fake_repos, capsys, captured_launch
):
    """A fragment that matches one real (resumable) session AND one corrupted row must still
    resume the real session - but the corrupted sibling must not go unmentioned, or the user
    never learns an unreachable duplicate exists."""
    from cc_session_tools.lib import sessions_db

    _make_session(fake_repos, "myproj", "20260504-shared-real")
    conn = sessions_db.connect()
    conn.execute(
        "INSERT INTO sessions (project_dir, basename, start_date, discovered_at) "
        "VALUES ('.', '20260101-shared-corrupt', '20260101', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    rc = ccr.main(["shared"])

    assert rc == 0
    assert "20260504-shared-real" in captured_launch["cmd"]
    err = capsys.readouterr().err
    assert "ccst repair sessions" in err
    assert "20260101-shared-corrupt" in err
