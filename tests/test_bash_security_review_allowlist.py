"""Hook-level tests for the reviewed-script allowlist tier of bash-security-review."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from hooks import bash_security_review as bsr
from hooks import cache as cache_mod
from hooks import script_allowlist as sal

SAFE_REVIEW = "SUMMARY: logs a row\nRISKS: none\nVERDICT: safe"
SUSPICIOUS_REVIEW = "SUMMARY: odd\nRISKS: writes elsewhere\nVERDICT: suspicious"
# The hook only reaches the LLM tier for commands that are not trivially read-only: `python3`
# invocations under 120 chars are Tier-0 trivial, so tests use a long argument, and a write-risk
# word so Tier 0.5 does not silently pass it either.
LONG_ARG = "mv " + "x" * 130


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture) -> Path:
    monkeypatch.setenv("CCST_HOOKS_DIR", str(tmp_path / "hooks"))
    monkeypatch.setenv("CCST_CACHE_DB", str(tmp_path / "cache.db"))
    monkeypatch.delenv("CCST_USE_COMMAND_CACHE", raising=False)
    monkeypatch.delenv("CCST_CLAUDE_BIN", raising=False)
    mocker.patch.object(bsr, "_resolve_claude_bin", return_value="/fake/claude")
    return tmp_path


@pytest.fixture
def project(env: Path) -> Path:
    root = env / "proj"
    (root / ".git").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "scripts" / "log.py").write_text("import sys\nprint(sys.argv)\n")
    return root


def _fire(project: Path, args: str = f"add --subject '{LONG_ARG}'", script: str = "scripts/log.py",
          interpreter: str = "python3") -> str:
    return json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": f"{interpreter} {script} {args}"},
        "cwd": str(project),
        "session_id": "s1",
    })


def _invocations(env: Path) -> list[tuple[int, str, str | None]]:
    conn = sqlite3.connect(env / "cache.db")
    try:
        return conn.execute(
            "SELECT exit_tier, verdict, cache_source FROM hook_invocations ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _allowlist(project: Path) -> None:
    script = (project / "scripts" / "log.py").resolve()
    cache_mod.allowlist_upsert(str(script), _sha(script), str(project.resolve()), source="manual")


# ---------- hits ----------

def test_allowlisted_script_with_new_arguments_skips_the_llm(
    env: Path, project: Path, mocker: MockerFixture
) -> None:
    _allowlist(project)
    spy = mocker.patch.object(bsr, "call_claude")

    rc = bsr.run(_fire(project, args=f"add --subject 'different {LONG_ARG}' --ts 2026-09-20"))

    assert rc == 0
    spy.assert_not_called()
    assert _invocations(env) == [(2, "safe", "script-allowlist")]
    entry = cache_mod.allowlist_get(str((project / "scripts" / "log.py").resolve()))
    assert entry is not None and entry.use_count == 1


@pytest.mark.parametrize("interpreter", ["python3", "uv run", "uv run python3"])
def test_interpreter_forms_share_one_entry(
    env: Path, project: Path, mocker: MockerFixture, interpreter: str
) -> None:
    _allowlist(project)
    spy = mocker.patch.object(bsr, "call_claude")

    bsr.run(_fire(project, interpreter=interpreter))

    spy.assert_not_called()


def test_hit_prints_a_review_block_naming_the_script(
    env: Path, project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _allowlist(project)

    bsr.run(_fire(project))

    err = capsys.readouterr().err
    assert "[security review]" in err
    assert "script allowlist" in err
    assert "scripts/log.py" in err


# ---------- non-hits ----------

def test_heuristic_flagged_arguments_escalate_even_for_an_allowlisted_script(
    env: Path, project: Path, mocker: MockerFixture
) -> None:
    _allowlist(project)
    spy = mocker.patch.object(bsr, "call_claude", return_value=(SAFE_REVIEW, None))

    bsr.run(_fire(project, args=f"add --file /etc/passwd --note '{LONG_ARG}'"))

    spy.assert_called_once()


def test_shell_composition_escalates(env: Path, project: Path, mocker: MockerFixture) -> None:
    _allowlist(project)
    spy = mocker.patch.object(bsr, "call_claude", return_value=(SAFE_REVIEW, None))

    bsr.run(_fire(project, args=f"add --n 1 && mv {LONG_ARG}"))

    spy.assert_called_once()


# ---------- hash mismatch ----------

def test_hash_mismatch_reviews_and_tells_the_user(
    env: Path, project: Path, mocker: MockerFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    _allowlist(project)
    script = project / "scripts" / "log.py"
    old = _sha(script)
    script.write_text("import os\nos.system('echo changed')\n")
    spy = mocker.patch.object(bsr, "call_claude", return_value=(SUSPICIOUS_REVIEW, None))

    bsr.run(_fire(project))

    spy.assert_called_once()
    err = capsys.readouterr().err
    assert "changed since it was allowlisted" in err
    assert old[:8] in err and _sha(script)[:8] in err
    assert _invocations(env)[-1] == (3, "suspicious", "script-allowlist-mismatch")


def test_mismatch_with_safe_rereview_updates_the_hash(
    env: Path, project: Path, mocker: MockerFixture
) -> None:
    _allowlist(project)
    script = project / "scripts" / "log.py"
    script.write_text("print('v2')\n")
    mocker.patch.object(bsr, "call_claude", return_value=(SAFE_REVIEW, None))

    bsr.run(_fire(project))

    entry = cache_mod.allowlist_get(str(script.resolve()))
    assert entry is not None and entry.sha256 == _sha(script)


def test_mismatch_with_unsafe_rereview_removes_the_entry(
    env: Path, project: Path, mocker: MockerFixture
) -> None:
    _allowlist(project)
    script = project / "scripts" / "log.py"
    script.write_text("print('v2')\n")
    mocker.patch.object(bsr, "call_claude", return_value=(SUSPICIOUS_REVIEW, None))

    bsr.run(_fire(project))

    assert cache_mod.allowlist_get(str(script.resolve())) is None


def test_mismatch_with_unavailable_review_leaves_the_entry_alone(
    env: Path, project: Path, mocker: MockerFixture
) -> None:
    _allowlist(project)
    script = project / "scripts" / "log.py"
    original = cache_mod.allowlist_get(str(script.resolve()))
    script.write_text("print('v2')\n")
    mocker.patch.object(bsr, "call_claude", return_value=(None, "timeout after 30s"))

    bsr.run(_fire(project))

    assert cache_mod.allowlist_get(str(script.resolve())) == original


def test_missing_script_never_matches(env: Path, project: Path, mocker: MockerFixture) -> None:
    _allowlist(project)
    (project / "scripts" / "log.py").unlink()
    spy = mocker.patch.object(bsr, "call_claude", return_value=(SAFE_REVIEW, None))

    bsr.run(_fire(project))

    # Not a parseable script invocation any more, so it takes the normal path.
    spy.assert_called_once()


# ---------- review prompt carries the script ----------

def test_review_prompt_includes_script_content_and_asks_about_arbitrary_arguments(
    env: Path, project: Path, mocker: MockerFixture
) -> None:
    spy = mocker.patch.object(bsr, "call_claude", return_value=(SAFE_REVIEW, None))

    bsr.run(_fire(project))

    prompt = spy.call_args.args[0]
    assert "import sys\nprint(sys.argv)\n" in prompt
    assert "arbitrary arguments" in prompt
    assert "untrusted" in prompt


def test_non_script_commands_get_the_plain_prompt(env: Path, project: Path, mocker: MockerFixture) -> None:
    spy = mocker.patch.object(bsr, "call_claude", return_value=(SAFE_REVIEW, None))
    payload = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": f"mv build_output_{'x' * 130} elsewhere"},
        "cwd": str(project),
        "session_id": "s1",
    })

    bsr.run(payload)

    assert "arbitrary arguments" not in spy.call_args.args[0]


# ---------- auto-add ----------

def test_safe_review_of_a_project_script_adds_an_entry_pinned_to_the_reviewed_bytes(
    env: Path, project: Path, mocker: MockerFixture
) -> None:
    script = project / "scripts" / "log.py"
    mocker.patch.object(bsr, "call_claude", return_value=(SAFE_REVIEW, None))

    bsr.run(_fire(project))

    entry = cache_mod.allowlist_get(str(script.resolve()))
    assert entry is not None
    assert entry.sha256 == _sha(script)
    assert entry.source == "auto"
    assert entry.project_root == str(project.resolve())


def test_second_call_with_other_arguments_then_skips_the_llm(
    env: Path, project: Path, mocker: MockerFixture
) -> None:
    spy = mocker.patch.object(bsr, "call_claude", return_value=(SAFE_REVIEW, None))

    bsr.run(_fire(project))
    bsr.run(_fire(project, args=f"add --subject 'other {LONG_ARG}'"))

    assert spy.call_count == 1


def test_no_auto_add_for_a_non_safe_verdict(env: Path, project: Path, mocker: MockerFixture) -> None:
    mocker.patch.object(bsr, "call_claude", return_value=(SUSPICIOUS_REVIEW, None))

    bsr.run(_fire(project))

    assert cache_mod.allowlist_list() == []


def test_no_auto_add_for_session_scratch_scripts(env: Path, project: Path, mocker: MockerFixture) -> None:
    scratch = project / "cc-sessions" / "20260920-x" / "working"
    scratch.mkdir(parents=True)
    (scratch / "tmp.py").write_text("print(1)\n")
    mocker.patch.object(bsr, "call_claude", return_value=(SAFE_REVIEW, None))

    bsr.run(_fire(project, script="cc-sessions/20260920-x/working/tmp.py"))

    assert cache_mod.allowlist_list() == []


def test_no_auto_add_for_scripts_outside_the_project(
    env: Path, project: Path, mocker: MockerFixture
) -> None:
    (env / "outside.py").write_text("print(1)\n")
    mocker.patch.object(bsr, "call_claude", return_value=(SAFE_REVIEW, None))

    bsr.run(_fire(project, script="../outside.py"))

    assert cache_mod.allowlist_list() == []


def test_no_auto_add_when_a_heuristic_flag_fired(env: Path, project: Path, mocker: MockerFixture) -> None:
    mocker.patch.object(bsr, "call_claude", return_value=(SAFE_REVIEW, None))

    bsr.run(_fire(project, args=f"add --file /etc/passwd --note '{LONG_ARG}'"))

    assert cache_mod.allowlist_list() == []


def test_no_auto_add_for_scripts_too_large_to_include_in_the_review(
    env: Path, project: Path, mocker: MockerFixture
) -> None:
    (project / "scripts" / "log.py").write_bytes(b"#" * (sal.MAX_REVIEW_SCRIPT_BYTES + 1))
    spy = mocker.patch.object(bsr, "call_claude", return_value=(SAFE_REVIEW, None))

    bsr.run(_fire(project))

    spy.assert_called_once()
    assert "arbitrary arguments" not in spy.call_args.args[0]
    assert cache_mod.allowlist_list() == []


def test_no_auto_add_for_unrecognised_forms(env: Path, project: Path, mocker: MockerFixture) -> None:
    mocker.patch.object(bsr, "call_claude", return_value=(SAFE_REVIEW, None))

    bsr.run(_fire(project, interpreter="python3 -u"))

    assert cache_mod.allowlist_list() == []


def test_store_failure_never_blocks_the_hook(
    env: Path, project: Path, mocker: MockerFixture
) -> None:
    mocker.patch.object(bsr, "call_claude", return_value=(SAFE_REVIEW, None))
    mocker.patch.object(cache_mod, "allowlist_get", side_effect=sqlite3.OperationalError("locked"))

    assert bsr.run(_fire(project)) == 0
