"""CLI tests for ``ccst telemetry recover``."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cc_session_tools.cli import ccst
from cc_session_tools.lib import command_recovery, telemetry_store
from hooks.bash_hard_deny import check_command

KEY = "-home-alice-repos-demo"
SESSION = "33333333-3333-4333-8333-333333333333"


def _sha(command: str) -> str:
    return hashlib.sha256(command.encode()).hexdigest()


def _bash_line(command: str) -> str:
    record = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": command}},
    ]}}
    return json.dumps(record, separators=(",", ":"))


def _fire(hooks_dir: Path, input_hash: str, session: str = SESSION) -> None:
    conn = telemetry_store.connect(hooks_dir)
    conn.execute(
        "INSERT INTO telemetry_events "
        "(ts, hook, event, tool, session_id, cwd_short, decision, cache, verdict, input_hash) "
        "VALUES ('2026-11-01T00:00:00Z', 'bash-security-review', 'PreToolUse', 'Bash', ?, 'x', "
        "'allow', 'miss', 'safe', ?)",
        (session, f"sha256:{input_hash}"),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    projects = tmp_path / "projects"
    (projects / KEY).mkdir(parents=True)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    return projects, hooks


def _run(
    dirs: tuple[Path, Path], *args: str, gate: bool = True,
) -> subprocess.CompletedProcess[str]:
    projects, hooks = dirs
    env = dict(os.environ)
    env.pop("CCST_FIRES_ACCESS", None)
    if gate:
        env["CCST_FIRES_ACCESS"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", "telemetry", "recover",
         "--projects-dir", str(projects), "--hooks-dir", str(hooks), *args],
        capture_output=True, text=True, env=env,
    )


def _run_in_process(
    monkeypatch: pytest.MonkeyPatch, dirs: tuple[Path, Path], *args: str,
) -> int:
    projects, hooks = dirs
    monkeypatch.setattr(sys, "argv", [
        "ccst", "telemetry", "recover",
        "--projects-dir", str(projects), "--hooks-dir", str(hooks), *args,
    ])
    with pytest.raises(SystemExit) as exc:
        ccst.main()
    assert isinstance(exc.value.code, int)
    return exc.value.code


def _forbid_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> object:
        raise AssertionError("nothing may be read")

    monkeypatch.setattr(command_recovery, "iter_bash_commands", boom)
    monkeypatch.setattr(telemetry_store, "connect", boom)


def test_gate_refuses_and_reads_nothing(
    monkeypatch: pytest.MonkeyPatch, dirs: tuple[Path, Path], capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("CCST_FIRES_ACCESS", raising=False)
    _forbid_reads(monkeypatch)
    rc = _run_in_process(monkeypatch, dirs, "a" * 64)
    captured = capsys.readouterr()
    assert rc == 2
    assert "CCST_FIRES_ACCESS=1" in captured.err
    assert captured.out == ""


def test_gate_refusal_subprocess_creates_no_db(dirs: tuple[Path, Path]) -> None:
    result = _run(dirs, "a" * 64, gate=False)
    assert result.returncode == 2
    assert "CCST_FIRES_ACCESS=1" in result.stderr
    assert not (dirs[1] / telemetry_store.DB_FILENAME).exists()


@pytest.mark.parametrize("bad", ["abc1234", "zzzzzzzz", "a" * 65])
def test_malformed_argument_exits_2_naming_it(
    monkeypatch: pytest.MonkeyPatch, dirs: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str], bad: str,
) -> None:
    monkeypatch.setenv("CCST_FIRES_ACCESS", "1")
    _forbid_reads(monkeypatch)
    rc = _run_in_process(monkeypatch, dirs, "a" * 64, bad)
    captured = capsys.readouterr()
    assert rc == 2
    assert repr(bad) in captured.err
    assert captured.out == ""


@pytest.mark.parametrize("bad", ["-1", "ten"])
def test_invalid_max_chars_rejected(dirs: tuple[Path, Path], bad: str) -> None:
    result = _run(dirs, f"--max-chars={bad}", "a" * 64)
    assert result.returncode == 2
    assert "--max-chars" in result.stderr
    assert result.stdout == ""


def test_command_line_columns(dirs: tuple[Path, Path]) -> None:
    projects, hooks = dirs
    command = "git log --oneline"
    path = projects / KEY / f"{SESSION}.jsonl"
    path.write_text(_bash_line(command) + "\n")
    _fire(hooks, _sha(command))
    arg = "SHA256:" + _sha(command)[:12].upper()
    result = _run(dirs, arg)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["\t".join(
        [_sha(command)[:12], "command", _sha(command), SESSION, f"{path}:1", command]
    )]


def test_duplicates_collapse_and_non_command_statuses_exit_0(dirs: tuple[Path, Path]) -> None:
    _, hooks = dirs
    _fire(hooks, _sha("never in a transcript"))
    lost = _sha("never in a transcript")
    stranger = _sha("never fired")
    result = _run(dirs, lost, stranger, lost.upper())
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert [line.split("\t")[:2] for line in lines] == [[lost, "session"], [stranger, "unknown"]]
    assert lines[0].split("\t")[3] == SESSION


def test_max_chars_truncates_and_zero_disables(dirs: tuple[Path, Path]) -> None:
    projects, hooks = dirs
    command = "echo " + "x" * 300
    (projects / KEY / f"{SESSION}.jsonl").write_text(_bash_line(command) + "\n")
    _fire(hooks, _sha(command))
    default = _run(dirs, _sha(command)).stdout.rstrip("\n").split("\t")[-1]
    assert default == command[:200] + "…[+105 chars]"
    short = _run(dirs, "--max-chars", "10", _sha(command)).stdout.rstrip("\n").split("\t")[-1]
    assert short == command[:10] + "…[+295 chars]"
    full = _run(dirs, "--max-chars", "0", _sha(command)).stdout.rstrip("\n").split("\t")[-1]
    assert full == command


def test_scan_all_finds_unbacked_hash(dirs: tuple[Path, Path]) -> None:
    projects, hooks = dirs
    command = "echo on disk only"
    (projects / KEY / f"{SESSION}.jsonl").write_text(_bash_line(command) + "\n")
    telemetry_store.connect(hooks).close()
    assert _run(dirs, _sha(command)).stdout.split("\t")[1] == "unknown"
    assert _run(dirs, "--scan-all", _sha(command)).stdout.split("\t")[1] == "command"


def test_recovery_writes_nothing_to_telemetry(dirs: tuple[Path, Path]) -> None:
    projects, hooks = dirs
    command = "pwd"
    (projects / KEY / f"{SESSION}.jsonl").write_text(_bash_line(command) + "\n")
    _fire(hooks, _sha(command))
    _fire(hooks, _sha("other"))

    def snapshot() -> tuple[int, int]:
        conn = telemetry_store.connect(hooks)
        try:
            count, max_id = conn.execute(
                "SELECT COUNT(*), MAX(id) FROM telemetry_events"
            ).fetchone()
        finally:
            conn.close()
        return count, max_id

    before = snapshot()
    result = _run(dirs, _sha(command))
    assert result.stdout.split("\t")[1] == "command"
    assert snapshot() == before


@pytest.mark.parametrize("command", [
    "ccst telemetry recover 0123abcd",
    "CCST_FIRES_ACCESS=1 ccst telemetry recover --max-chars 0 0123abcd",
])
def test_bash_hard_deny_allows_recover(monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    monkeypatch.delenv("CCST_FIRES_ACCESS", raising=False)
    assert check_command(command) is None


def test_usage_block_lists_recover() -> None:
    assert ccst.__doc__ is not None
    assert "telemetry recover" in ccst.__doc__
