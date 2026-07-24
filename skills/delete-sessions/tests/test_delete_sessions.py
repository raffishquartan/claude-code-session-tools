"""Tests for skills/delete-sessions/scripts/delete_sessions.py."""
from __future__ import annotations

import importlib
import json
import sys
from io import StringIO
from pathlib import Path
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# Path setup — local src wins over any installed wheel.
# ---------------------------------------------------------------------------
SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_ROOT / "scripts"
_REPO_SRC = str(Path(__file__).resolve().parents[3] / "src")
sys.path.insert(0, _REPO_SRC)
sys.path.insert(0, str(SCRIPTS_DIR))

import delete_sessions as ds  # noqa: E402  (sys.path mutation first)

from cc_session_tools.lib import sessions_db  # noqa: E402
from cc_session_tools.lib.sessions import transcript_dir_for_project  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_tag(uuid: str, tag: str) -> None:
    """Record uuid -> tag in sessions.db (session_tags table)."""
    sessions_db.write_tag(uuid, tag)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


def _make_empty_session(
    fake_home: Path,
    project: Path,
    basename: str,
    *,
    uuid: str = "uuid-1",
) -> tuple[Path, Path]:
    """Create an empty session. Returns (session_dir, jsonl_path)."""
    session_dir = project / "cc-sessions" / basename
    (session_dir / "working").mkdir(parents=True, exist_ok=True)
    (session_dir / "out").mkdir(exist_ok=True)

    tag = basename.split("-", 1)[1]
    td = transcript_dir_for_project(project)
    td.mkdir(parents=True, exist_ok=True)
    # Record the tag in sessions.db and register the session row so the
    # delete loop's sessions.db cleanup has records to remove.
    _write_tag(uuid, tag)
    sessions_db.ensure_session_row(project, basename)
    jsonl = td / f"{uuid}.jsonl"
    _write_jsonl(jsonl, [
        {"type": "user", "isMeta": True, "message": {"content": "hook output"}},
    ])
    return session_dir, jsonl


def _make_nonempty_session(
    fake_home: Path,
    project: Path,
    basename: str,
    *,
    uuid: str = "uuid-2",
) -> tuple[Path, Path]:
    """Create a non-empty session. Returns (session_dir, jsonl_path)."""
    session_dir = project / "cc-sessions" / basename
    (session_dir / "working").mkdir(parents=True, exist_ok=True)
    (session_dir / "out").mkdir(exist_ok=True)

    tag = basename.split("-", 1)[1]
    td = transcript_dir_for_project(project)
    td.mkdir(parents=True, exist_ok=True)
    # Record the tag in sessions.db and register the session row so the
    # delete loop's sessions.db cleanup has records to remove.
    _write_tag(uuid, tag)
    sessions_db.ensure_session_row(project, basename)
    jsonl = td / f"{uuid}.jsonl"
    _write_jsonl(jsonl, [
        {"type": "user", "isMeta": True, "message": {"content": "hook"}},
        {"type": "user", "message": {"content": "real user message"}},
    ])
    return session_dir, jsonl


def _run_main(
    args: list[str],
    *,
    fake_home: Path,
    project: Path,
    stdin_text: str = "",
) -> tuple[int, str, str]:
    """Run ds.main() with patched argv, HOME, and stdin.

    Returns (exit_code, stdout, stderr).
    """
    out = StringIO()
    err = StringIO()

    def fake_home_fn():
        return fake_home

    with (
        mock.patch("sys.argv", ["delete_sessions.py"] + args),
        mock.patch("sys.stdout", out),
        mock.patch("sys.stderr", err),
        mock.patch("sys.stdin", StringIO(stdin_text)),
        mock.patch("builtins.input", return_value=stdin_text.strip()),
        mock.patch.object(Path, "home", staticmethod(fake_home_fn)),
        mock.patch.dict("os.environ", {"HOME": str(fake_home)}, clear=False),
    ):
        try:
            rc = ds.main()
        except SystemExit as exc:
            rc = int(exc.code) if exc.code is not None else 0
    return rc, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude").mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    # Isolated sessions.db for tag + session-row storage.
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path / "db"))
    return home


@pytest.fixture
def project(fake_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "myproject"
    p.mkdir()
    (p / "cc-sessions").mkdir()
    # Make cwd == project so local discovery finds sessions there.
    monkeypatch.chdir(p)
    monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(p.parent))
    # Clear in-CC env vars so in-session detection does not fire in tests that
    # do not explicitly opt in (we run inside CC which sets CLAUDECODE=1).
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    return p


# ---------------------------------------------------------------------------
# Tests: pre-flight checks
# ---------------------------------------------------------------------------

class TestPreflightFormat:
    def test_bad_format_exits_1(self, fake_home, project):
        rc, out, err = _run_main(["not-a-valid-name"], fake_home=fake_home, project=project)
        assert rc == 1
        assert "invalid basename" in err or "session-name format" in err

    def test_valid_format_passes_format_check(self, fake_home, project):
        _make_empty_session(fake_home, project, "20260516-test-session")
        rc, out, err = _run_main(
            ["20260516-test-session"], fake_home=fake_home, project=project
        )
        # Should not fail with format error; may fail with existence or other check.
        assert "session-name format" not in err or rc != 1


class TestPreflightExistence:
    def test_nonexistent_basename_exits_1(self, fake_home, project):
        rc, out, err = _run_main(
            ["20260516-does-not-exist"], fake_home=fake_home, project=project
        )
        assert rc == 1
        assert "could not be found" in err or "not found" in err.lower()

    def test_existing_session_passes_existence_check(self, fake_home, project):
        _make_empty_session(fake_home, project, "20260516-exists")
        rc, out, err = _run_main(
            ["20260516-exists"], fake_home=fake_home, project=project
        )
        # Not an existence error; dry-run should print plan.
        assert "could not be found" not in err


class TestPreflightEmptyGuard:
    def test_nonempty_session_refused_by_default(self, fake_home, project):
        _make_nonempty_session(fake_home, project, "20260516-used-session")
        rc, out, err = _run_main(
            ["20260516-used-session"], fake_home=fake_home, project=project
        )
        assert rc == 1
        assert "not empty" in err or "non-empty" in err.lower()

    def test_nonempty_session_allowed_with_flag(self, fake_home, project):
        _make_nonempty_session(fake_home, project, "20260516-used-session")
        rc, out, err = _run_main(
            ["--allow-non-empty", "20260516-used-session"],
            fake_home=fake_home,
            project=project,
        )
        # Should reach plan/dry-run stage, not exit 1 due to empty guard.
        assert "not empty" not in err
        assert rc == 0  # dry-run exits 0

    def test_empty_session_passes_guard(self, fake_home, project):
        _make_empty_session(fake_home, project, "20260516-empty-session")
        rc, out, err = _run_main(
            ["20260516-empty-session"], fake_home=fake_home, project=project
        )
        assert "not empty" not in err
        assert rc == 0  # dry-run exits 0


class TestInSessionRefusal:
    def test_refuses_when_in_active_session(self, fake_home, project, monkeypatch):
        """If CLAUDECODE=1 and the session JSONL was modified recently, refuse."""
        session_dir, jsonl = _make_empty_session(
            fake_home, project, "20260516-active-session"
        )
        # Simulate: running inside CC and JSONL just modified.
        monkeypatch.setenv("CLAUDECODE", "1")
        # Touch the JSONL so mtime is recent.
        import time as time_mod
        jsonl.touch()
        rc, out, err = _run_main(
            ["20260516-active-session"],
            fake_home=fake_home,
            project=project,
        )
        assert rc == 2
        assert "REFUSED" in err or "cannot delete" in err.lower()

    def test_no_refusal_when_not_in_cc(self, fake_home, project, monkeypatch):
        """Without CLAUDECODE env var, in-session check does not fire."""
        _make_empty_session(fake_home, project, "20260516-inactive-session")
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        rc, out, err = _run_main(
            ["20260516-inactive-session"],
            fake_home=fake_home,
            project=project,
        )
        # Should NOT have refusal; should reach dry-run.
        assert rc != 2


# ---------------------------------------------------------------------------
# Tests: dry-run vs execute
# ---------------------------------------------------------------------------

class TestDryRunVsExecute:
    def test_dryrun_does_not_delete(self, fake_home, project):
        session_dir, jsonl = _make_empty_session(
            fake_home, project, "20260516-to-delete"
        )
        rc, out, err = _run_main(
            ["20260516-to-delete"], fake_home=fake_home, project=project
        )
        assert rc == 0
        assert session_dir.exists(), "dry-run should not have deleted the session dir"
        assert jsonl.exists(), "dry-run should not have deleted the JSONL"

    def test_execute_with_correct_code_deletes(self, fake_home, project, monkeypatch):
        """With --execute and correct confirmation code, artefacts are deleted."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        session_dir, jsonl = _make_empty_session(
            fake_home, project, "20260516-to-delete"
        )
        code = "12345678"
        with mock.patch.object(ds, "_generate_code", return_value=code):
            rc, out, err = _run_main(
                ["--execute", "20260516-to-delete"],
                fake_home=fake_home,
                project=project,
                stdin_text=code,
            )
        assert rc == 0
        assert not session_dir.exists(), "session dir should have been deleted"
        assert not jsonl.exists(), "JSONL should have been deleted"

    def test_execute_with_wrong_code_aborts(self, fake_home, project, monkeypatch):
        """With --execute and wrong confirmation code, nothing is deleted."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        session_dir, jsonl = _make_empty_session(
            fake_home, project, "20260516-abort-test"
        )
        code = "12345678"
        with mock.patch.object(ds, "_generate_code", return_value=code):
            rc, out, err = _run_main(
                ["--execute", "20260516-abort-test"],
                fake_home=fake_home,
                project=project,
                stdin_text="00000000",  # wrong code
            )
        assert rc == 1
        assert session_dir.exists(), "session dir should NOT have been deleted"
        assert jsonl.exists(), "JSONL should NOT have been deleted"

    def test_dryrun_prints_plan(self, fake_home, project):
        _make_empty_session(fake_home, project, "20260516-plan-test")
        rc, out, err = _run_main(
            ["20260516-plan-test"], fake_home=fake_home, project=project
        )
        assert "DELETION PLAN" in out
        assert "DRY-RUN" in out

    def test_execute_removes_sessions_db_records_too(self, fake_home, project, monkeypatch):
        """The session's sessions.db records (session_tags row + sessions row)
        are also removed during --execute."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        session_dir, jsonl = _make_empty_session(
            fake_home, project, "20260516-tag-test", uuid="tag-uuid"
        )
        # Tag + session row are recorded in sessions.db, keyed by UUID / (proj, basename).
        assert sessions_db.lookup_tags(["tag-uuid"]) == {"tag-uuid": "tag-test"}
        assert any(
            r.basename == "20260516-tag-test" for r in sessions_db.list_sessions()
        )
        code = "87654321"
        with mock.patch.object(ds, "_generate_code", return_value=code):
            rc, out, err = _run_main(
                ["--execute", "20260516-tag-test"],
                fake_home=fake_home,
                project=project,
                stdin_text=code,
            )
        assert rc == 0
        assert sessions_db.lookup_tags(["tag-uuid"]) == {}, "tag row should be gone"
        assert not any(
            r.basename == "20260516-tag-test" for r in sessions_db.list_sessions()
        ), "sessions row should be gone"


# ---------------------------------------------------------------------------
# Tests: multiple basenames
# ---------------------------------------------------------------------------

class TestMultipleBasenames:
    def test_multiple_empties_all_deleted(self, fake_home, project, monkeypatch):
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        session_a, jsonl_a = _make_empty_session(
            fake_home, project, "20260516-alpha", uuid="uuid-a"
        )
        session_b, jsonl_b = _make_empty_session(
            fake_home, project, "20260517-beta", uuid="uuid-b"
        )
        code = "11223344"
        with mock.patch.object(ds, "_generate_code", return_value=code):
            rc, out, err = _run_main(
                ["--execute", "20260516-alpha", "20260517-beta"],
                fake_home=fake_home,
                project=project,
                stdin_text=code,
            )
        assert rc == 0
        assert not session_a.exists()
        assert not session_b.exists()

    def test_one_nonexistent_blocks_all(self, fake_home, project):
        _make_empty_session(fake_home, project, "20260516-exists")
        rc, out, err = _run_main(
            ["20260516-exists", "20260516-missing"],
            fake_home=fake_home,
            project=project,
        )
        assert rc == 1
        assert "could not be found" in err or "not found" in err.lower()
