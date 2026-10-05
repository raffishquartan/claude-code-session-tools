from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.cli import ccd, ccr

SECRET = "sk-ant-test-secret"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    import shutil

    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path / "db"))
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/claude")
    keys = tmp_path / "keys"
    keys.write_text(f"work={SECRET}\npersonal=other-secret\n")
    keys.chmod(0o600)
    monkeypatch.setenv("CCST_API_KEYS_FILE", str(keys))
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth")
    repos = tmp_path / "repos"
    (repos / "proj").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(repos))
    monkeypatch.chdir(repos / "proj")


@pytest.fixture
def launched(monkeypatch):
    got: dict = {}
    monkeypatch.setattr(ccd, "launch_claude", lambda cmd, env: got.update(cmd=list(cmd), env=dict(env)))
    monkeypatch.setattr(
        ccr, "launch_claude_resume", lambda cmd, env, cwd=None: got.update(cmd=list(cmd), env=dict(env))
    )
    return got


def _make_session(tmp_path: Path, basename: str) -> None:
    from cc_session_tools.lib import sessions_db

    proj = tmp_path / "repos" / "proj"
    sess = proj / "cc-sessions" / basename
    (sess / "working").mkdir(parents=True)
    (sess / "out").mkdir()
    sessions_db.ensure_session_row(proj, basename, uuid=f"uuid-{basename}")


def _tty(monkeypatch, answer: str | None):
    monkeypatch.setattr("sys.stdin.isatty", lambda: answer is not None, raising=False)
    if answer is not None:
        monkeypatch.setattr("builtins.input", lambda _="": answer)


def test_ccdapi_named_key(launched):
    assert ccd.main(["-k", "work", "mytag"], api_key=True) == 0
    assert launched["env"]["ANTHROPIC_API_KEY"] == SECRET
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in launched["env"]
    assert SECRET not in " ".join(launched["cmd"])
    assert launched["env"]["CLD_SESSION_MODE"] == "new"


def test_ccdapi_menu_pick(launched, monkeypatch):
    _tty(monkeypatch, "1")  # sorted: personal, work
    assert ccd.main(["mytag"], api_key=True) == 0
    assert launched["env"]["ANTHROPIC_API_KEY"] == "other-secret"


def test_ccdapi_menu_cancel_does_not_launch(launched, monkeypatch, tmp_path):
    _tty(monkeypatch, "q")
    assert ccd.main(["mytag"], api_key=True) == 0
    assert launched == {}
    assert not (tmp_path / "repos" / "proj" / "cc-sessions").exists()


def test_ccdapi_unknown_label(launched, capsys):
    assert ccd.main(["-k", "nope", "mytag"], api_key=True) == 1
    err = capsys.readouterr().err
    assert "personal, work" in err
    assert launched == {}
    assert SECRET not in err


def test_ccdapi_no_tty_no_label(launched, monkeypatch, capsys):
    _tty(monkeypatch, None)
    assert ccd.main(["mytag"], api_key=True) == 1
    assert "no terminal" in capsys.readouterr().err
    assert launched == {}


def test_ccdapi_missing_file(launched, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CCST_API_KEYS_FILE", str(tmp_path / "absent"))
    assert ccd.main(["-k", "work", "mytag"], api_key=True) == 1
    assert "not found" in capsys.readouterr().err
    assert launched == {}


def test_ccdapi_dry_run_hides_key(launched, capsys):
    assert ccd.main(["--dry-run", "-k", "work", "mytag"], api_key=True) == 0
    out = capsys.readouterr().out
    assert "api_key_label: work" in out
    assert SECRET not in out
    assert launched == {}


def test_plain_ccd_does_not_touch_api_key(launched):
    assert ccd.main(["mytag"]) == 0
    assert "ANTHROPIC_API_KEY" not in launched["env"]
    assert launched["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth"


def test_ccrapi_named_key(launched, tmp_path):
    _make_session(tmp_path, "20260504-foo-bar")
    assert ccr.main(["-k", "work", "foo-bar"], api_key=True) == 0
    assert launched["env"]["ANTHROPIC_API_KEY"] == SECRET
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in launched["env"]
    assert "--resume" in launched["cmd"]
    assert SECRET not in " ".join(launched["cmd"])


def test_ccrapi_menu_pick(launched, monkeypatch, tmp_path):
    _make_session(tmp_path, "20260504-foo-bar")
    _tty(monkeypatch, "2")
    assert ccr.main(["foo-bar"], api_key=True) == 0
    assert launched["env"]["ANTHROPIC_API_KEY"] == SECRET


def test_ccrapi_unknown_label_fails_before_launch(launched, tmp_path):
    _make_session(tmp_path, "20260504-foo-bar")
    assert ccr.main(["-k", "nope", "foo-bar"], api_key=True) == 1
    assert launched == {}


def test_ccrapi_cancelled_menu(launched, monkeypatch, tmp_path):
    _make_session(tmp_path, "20260504-foo-bar")
    _tty(monkeypatch, "q")
    assert ccr.main(["foo-bar"], api_key=True) == 0
    assert launched == {}


def test_ccrapi_no_tty_no_label(launched, monkeypatch, tmp_path, capsys):
    _make_session(tmp_path, "20260504-foo-bar")
    _tty(monkeypatch, None)
    assert ccr.main(["foo-bar"], api_key=True) == 1
    assert "no terminal" in capsys.readouterr().err
    assert launched == {}


def test_ccrapi_missing_file(launched, monkeypatch, tmp_path, capsys):
    _make_session(tmp_path, "20260504-foo-bar")
    monkeypatch.setenv("CCST_API_KEYS_FILE", str(tmp_path / "absent"))
    assert ccr.main(["-k", "work", "foo-bar"], api_key=True) == 1
    assert "not found" in capsys.readouterr().err
    assert launched == {}


def test_ccdapi_competing_auth_dropped_from_launch_env(launched, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "ambient")
    assert ccd.main(["-k", "work", "mytag"], api_key=True) == 0
    assert "ANTHROPIC_AUTH_TOKEN" not in launched["env"]


def test_ccdapi_existing_session_hint_names_ccrapi(launched, capsys):
    assert ccd.main(["-k", "work", "mytag"], api_key=True) == 0  # creates the session dir
    import json
    from datetime import datetime
    from cc_session_tools.lib import sessions_db
    from cc_session_tools.lib.sessions import transcript_dir_for_project
    proj = Path.cwd()
    basename = f"{datetime.now().strftime('%Y%m%d')}-mytag"
    t_dir = transcript_dir_for_project(proj)
    t_dir.mkdir(parents=True, exist_ok=True)
    sessions_db.write_tag(f"uuid-{basename}", "mytag")
    (t_dir / f"uuid-{basename}.jsonl").write_text(
        json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n"
    )
    assert ccd.main(["-k", "work", "mytag"], api_key=True) == 1
    err = capsys.readouterr().err
    assert "ccdapi: session" in err
    assert "ccrapi mytag" in err
