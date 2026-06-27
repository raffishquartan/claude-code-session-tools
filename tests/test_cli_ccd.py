from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from cc_session_tools.cli import ccd
from cc_session_tools.lib.sessions import (
    _session_tags_dir,
    session_tag,
    transcript_dir_for_project,
)


@pytest.fixture
def captured_launch(monkeypatch):
    captured: dict = {}

    def fake_launch(cmd, env):
        captured["cmd"] = list(cmd)
        captured["env"] = dict(env)

    monkeypatch.setattr(ccd, "launch_claude", fake_launch)
    return captured


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    # Redirect the flat session-tags dir so transcript lookup is hermetic and
    # never reads the developer's real ~/.cache/claude/session-tags.
    monkeypatch.setenv("CCCS_SESSION_TAGS_DIR", str(tmp_path / "session-tags"))
    return home


def _write_transcript(proj: Path, basename: str, *, user_typed: bool) -> Path:
    """Fabricate a JSONL transcript for `basename` under proj's transcript dir.

    user_typed=True  -> contains a real typed message (is_empty_session -> False).
    user_typed=False -> contains only a SessionStart hook record (still "empty").
    """
    t_dir = transcript_dir_for_project(proj)
    t_dir.mkdir(parents=True, exist_ok=True)
    stem = f"uuid-{basename}"
    tags_dir = _session_tags_dir()
    tags_dir.mkdir(parents=True, exist_ok=True)
    (tags_dir / f"{stem}.tag").write_text(session_tag(basename) or basename)
    if user_typed:
        rec = {"type": "user", "message": {"content": "do the thing"}}
    else:
        rec = {
            "type": "user",
            "isMeta": True,
            "message": {"content": "<command-name>SessionStart</command-name>"},
        }
    jsonl = t_dir / f"{stem}.jsonl"
    jsonl.write_text(json.dumps(rec) + "\n")
    return jsonl


def _set_repo_root(monkeypatch, path: Path) -> None:
    monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(path))


def _set_proj_root(monkeypatch, path: Path) -> None:
    monkeypatch.setenv("CLAUDE_SESSION_TOOLS_PROJ_ROOT", str(path))


def test_ccd_creates_session_dir_and_launches_claude(
    fake_home, tmp_path, monkeypatch, captured_launch
):
    repos = tmp_path / "repos"
    proj = repos / "myproj"
    proj.mkdir(parents=True)
    _set_repo_root(monkeypatch, repos)

    monkeypatch.chdir(proj)
    # Bypass strict-root prompts (proj is under repos, not the strict root).
    rc = ccd.main(["mytag"])
    assert rc == 0

    cmd = captured_launch["cmd"]
    assert cmd[0] == "claude"
    assert "-n" in cmd
    name_idx = cmd.index("-n") + 1
    session_name = cmd[name_idx]
    assert session_name.endswith("-mytag")
    assert "--remote-control" in cmd
    rc_idx = cmd.index("--remote-control") + 1
    assert cmd[rc_idx] == session_name

    sess_dir = proj / "cc-sessions" / session_name
    assert sess_dir.is_dir()
    assert (sess_dir / "working").is_dir()
    assert (sess_dir / "out").is_dir()


def test_ccd_sets_env_vars_for_session_start_hook(
    fake_home, tmp_path, monkeypatch, captured_launch
):
    repos = tmp_path / "repos"
    proj = repos / "myproj"
    proj.mkdir(parents=True)
    _set_repo_root(monkeypatch, repos)
    monkeypatch.chdir(proj)

    ccd.main(["mytag"])

    env = captured_launch["env"]
    assert env["CLD_SESSION_TAG"] == "mytag"
    assert env["CLD_SESSION_MODE"] == "new"
    assert Path(env["CLD_SESSION_DIR"]).is_absolute()
    assert env["CLD_SESSION_DIR"].endswith("-mytag")
    # Project is direct child of repos root => task list id = project name.
    assert env["CLAUDE_CODE_TASK_LIST_ID"] == "myproj"


def test_ccd_does_not_set_task_list_id_when_outside_roots(
    fake_home, tmp_path, monkeypatch, captured_launch
):
    repos = tmp_path / "repos"
    repos.mkdir()
    _set_repo_root(monkeypatch, repos)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    rc = ccd.main(["--force", "mytag"])
    assert rc == 0
    assert "CLAUDE_CODE_TASK_LIST_ID" not in captured_launch["env"]


def test_ccd_chdirs_to_resolved_real_path_before_launch(
    fake_home, tmp_path, monkeypatch, captured_launch
):
    """ccd must chdir to the canonical, symlink-resolved project path before
    launching `claude`, so Claude Code records its ~/.claude/projects/<encoded-cwd>/
    key against the canonical path (matches the original bash `cd "$real_pwd"`)."""
    repos = tmp_path / "repos"
    proj = repos / "myproj"
    proj.mkdir(parents=True)
    _set_repo_root(monkeypatch, repos)

    # Approach via a symlink to verify resolution to the real path.
    link = tmp_path / "link-to-proj"
    link.symlink_to(proj)
    monkeypatch.chdir(link)

    captured_chdir: list[Path] = []
    real_chdir = __import__("os").chdir

    def fake_chdir(p):
        captured_chdir.append(Path(p))
        real_chdir(p)

    monkeypatch.setattr("os.chdir", fake_chdir)

    rc = ccd.main(["mytag"])
    assert rc == 0
    # ccd should have chdir'd to the canonical resolved path, not the symlink.
    assert captured_chdir, "ccd did not call os.chdir before launch"
    assert captured_chdir[-1] == proj.resolve()


def test_ccd_strict_root_missing_prefix_offers_prompt_before_validation(
    fake_home, tmp_path, monkeypatch, captured_launch, capsys
):
    """Under the strict (PROJ) root, a tag missing the '<project>-' prefix
    must trigger the missing-prefix prompt rather than failing validation
    outright. If the user accepts ('y'), the prompt prepends the project
    name and the session is created with the corrected tag."""
    proj_root = tmp_path / "cc-claude-code"
    proj = proj_root / "oneshot"
    proj.mkdir(parents=True)
    _set_proj_root(monkeypatch, proj_root)
    monkeypatch.chdir(proj)

    # Simulate the user typing 'y' to accept the suggested 'oneshot-' prefix.
    monkeypatch.setattr("builtins.input", lambda: "y")

    rc = ccd.main(["test-claude-usage-skill"])
    assert rc == 0, capsys.readouterr().err

    cmd = captured_launch["cmd"]
    name_idx = cmd.index("-n") + 1
    session_name = cmd[name_idx]
    # Tag was corrected from "test-claude-usage-skill" to "oneshot-test-claude-usage-skill"
    assert session_name.endswith("-oneshot-test-claude-usage-skill")

    # The prompt's user-facing text should have explained what happened.
    err = capsys.readouterr().err
    assert "not a recognised project" in err or "prepend the current project" in err


def test_ccd_strict_root_missing_prefix_decline_exits_without_validation_error(
    fake_home, tmp_path, monkeypatch, captured_launch, capsys
):
    """If the user declines the missing-prefix prompt, ccd exits cleanly
    rather than falling through to a less helpful 'validation failed'
    message."""
    proj_root = tmp_path / "cc-claude-code"
    proj = proj_root / "oneshot"
    proj.mkdir(parents=True)
    _set_proj_root(monkeypatch, proj_root)
    monkeypatch.chdir(proj)

    monkeypatch.setattr("builtins.input", lambda: "n")

    with pytest.raises(SystemExit) as exc_info:
        ccd.main(["test-claude-usage-skill"])
    assert exc_info.value.code == 1

    err = capsys.readouterr().err
    # We must NOT have shown the noisy validation-failed error before the prompt.
    assert "validation failed" not in err


def test_ccd_rejects_duplicate_session_that_received_user_input(
    fake_home, tmp_path, monkeypatch, capsys, captured_launch
):
    """A leftover dir whose transcript shows real user input is a genuine
    duplicate: ccd must refuse and point at ccr."""
    repos = tmp_path / "repos"
    proj = repos / "myproj"
    proj.mkdir(parents=True)
    _set_repo_root(monkeypatch, repos)
    monkeypatch.chdir(proj)

    date_str = datetime.now().strftime("%Y%m%d")
    basename = f"{date_str}-mytag"
    (proj / "cc-sessions" / basename).mkdir(parents=True)
    _write_transcript(proj, basename, user_typed=True)

    rc = ccd.main(["mytag"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "already started today" in err
    assert "ccr" in err  # remediation hint


def test_ccd_reuses_empty_session_dir_with_no_transcript(
    fake_home, tmp_path, monkeypatch, capsys, captured_launch
):
    """A leftover scaffold dir from a session that never started (no transcript,
    e.g. claude aborted on a malformed settings.json) must be reusable - it is
    the only way to recover, since ccr cannot resume a non-existent transcript."""
    repos = tmp_path / "repos"
    proj = repos / "myproj"
    proj.mkdir(parents=True)
    _set_repo_root(monkeypatch, repos)
    monkeypatch.chdir(proj)

    date_str = datetime.now().strftime("%Y%m%d")
    existing = proj / "cc-sessions" / f"{date_str}-mytag"
    (existing / "working").mkdir(parents=True)
    (existing / "out").mkdir(parents=True)

    rc = ccd.main(["mytag"])
    assert rc == 0, capsys.readouterr().err
    # Launched claude for the reused session name.
    cmd = captured_launch["cmd"]
    assert cmd[cmd.index("-n") + 1] == f"{date_str}-mytag"
    # Scaffold dirs still present (mkdir exist_ok did not blow up).
    assert (existing / "working").is_dir()
    assert (existing / "out").is_dir()


def test_ccd_reuses_session_dir_with_only_hook_transcript(
    fake_home, tmp_path, monkeypatch, capsys, captured_launch
):
    """A dir whose transcript holds only SessionStart hook output (no user-typed
    message) counts as empty and is reused rather than blocking the tag."""
    repos = tmp_path / "repos"
    proj = repos / "myproj"
    proj.mkdir(parents=True)
    _set_repo_root(monkeypatch, repos)
    monkeypatch.chdir(proj)

    date_str = datetime.now().strftime("%Y%m%d")
    basename = f"{date_str}-mytag"
    (proj / "cc-sessions" / basename).mkdir(parents=True)
    _write_transcript(proj, basename, user_typed=False)

    rc = ccd.main(["mytag"])
    assert rc == 0, capsys.readouterr().err
    assert captured_launch["cmd"][0] == "claude"


def test_cld_session_dir_is_absolute(fake_home, tmp_path, monkeypatch, captured_launch):
    """CLD_SESSION_DIR must be an absolute path regardless of working directory."""
    repos = tmp_path / "repos"
    proj = repos / "myproj"
    proj.mkdir(parents=True)
    _set_repo_root(monkeypatch, repos)
    monkeypatch.chdir(proj)
    ccd.main(["mytag"])
    env = captured_launch["env"]
    assert Path(env["CLD_SESSION_DIR"]).is_absolute()
