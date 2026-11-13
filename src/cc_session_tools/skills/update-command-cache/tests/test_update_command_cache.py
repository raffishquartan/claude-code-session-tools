from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import pytest

_SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
_SCRIPT_PATH = _SCRIPT_DIR / "update_command_cache.py"
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from cc_session_tools.lib import command_recovery, telemetry_store  # noqa: E402
import update_command_cache as ucc  # noqa: E402


def _insert(
    hooks_dir: Path, *, hook: str, verdict: str, cache: str, input_hash: str,
    ts: str = "2026-07-01T00:00:00Z", session_id: str = "s1",
) -> None:
    conn = telemetry_store.connect(hooks_dir)
    conn.execute(
        "INSERT INTO telemetry_events "
        "(ts, hook, event, tool, session_id, cwd_short, decision, cache, verdict, input_hash) "
        "VALUES (?, ?, 'PreToolUse', 'Bash', ?, 'x', 'allow', ?, ?, ?)",
        (ts, hook, session_id, cache, verdict, input_hash),
    )
    conn.commit()
    conn.close()


def test_collect_candidates_finds_uncached_safe_fires(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ucc, "cache_lookup", lambda sha: None)
    _insert(tmp_path, hook="bash-security-review", verdict="safe", cache="miss", input_hash="sha256:aa")
    rows = ucc.read_telemetry_events(hooks_dir=tmp_path)
    candidates = ucc.collect_candidates(rows)
    assert [c["sha"] for c in candidates] == ["aa"]


def test_collect_candidates_skips_non_bash_security_review_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ucc, "cache_lookup", lambda sha: None)
    _insert(tmp_path, hook="bash-hard-deny", verdict="safe", cache="miss", input_hash="sha256:bb")
    rows = ucc.read_telemetry_events(hooks_dir=tmp_path)
    assert ucc.collect_candidates(rows) == []


def test_collect_candidates_skips_cache_hits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ucc, "cache_lookup", lambda sha: None)
    _insert(tmp_path, hook="bash-security-review", verdict="safe", cache="hit", input_hash="sha256:cc")
    rows = ucc.read_telemetry_events(hooks_dir=tmp_path)
    assert ucc.collect_candidates(rows) == []


def test_collect_candidates_skips_already_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ucc, "cache_lookup", lambda sha: object())  # already cached
    _insert(tmp_path, hook="bash-security-review", verdict="safe", cache="miss", input_hash="sha256:dd")
    rows = ucc.read_telemetry_events(hooks_dir=tmp_path)
    assert ucc.collect_candidates(rows) == []


def test_no_broken_repo_relative_sys_path_bootstrap() -> None:
    """Regression test for ccmsg 20260912T122716Z-c24b bug 1: the script computed its own
    `sys.path` entry as `Path(__file__).parents[3] / "src"`, which resolves to a nonexistent
    directory (`.../cc_session_tools/src`) and does nothing useful - `hooks` and
    `cc_session_tools.lib` are real top-level installed packages (like `hooks.stats`, exposed
    via the `cccs-stats` console-script entry point) that any interpreter with `cc-session-tools`
    installed already imports directly, with no bootstrap. A bare system `python3` that lacks the
    package installed fails with `ModuleNotFoundError` regardless of any path hack - that failure
    is expected and correct, not something to paper over with more path manipulation."""
    src = _SCRIPT_PATH.read_text()
    assert "sys.path" not in src
    assert "_REPO_ROOT" not in src


def test_script_runs_as_real_subprocess_with_package_installed(tmp_path: Path) -> None:
    """The script must run correctly with no bootstrap of its own, under any interpreter that
    actually has cc-session-tools installed - here, the same interpreter pytest itself runs
    under (the dev checkout's venv)."""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), "list"],
        env={"CCST_FIRES_ACCESS": "1", "CCST_DATA_HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "ModuleNotFoundError" not in result.stderr


# ---------- list previews (recovered through cc_session_tools.lib.command_recovery) ----------

_KEY = "-home-alice-repos-demo"
_SESSION = "44444444-4444-4444-8444-444444444444"


def _sha(command: str) -> str:
    return hashlib.sha256(command.encode()).hexdigest()


def _transcript(home: Path, commands: list[str], session: str = _SESSION) -> Path:
    path = home / ".claude" / "projects" / _KEY / f"{session}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": c}},
        ]}}, separators=(",", ":"))
        for c in commands
    ]
    path.write_text("".join(line + "\n" for line in lines))
    return path


@pytest.fixture
def list_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """HOME and the telemetry dir redirected at tmp_path, the gate open, the cache empty."""
    home = tmp_path / "home"
    home.mkdir()
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CCST_HOOKS_DIR", str(hooks))
    monkeypatch.setenv("CCST_FIRES_ACCESS", "1")
    monkeypatch.setenv("CCST_CACHE_DB", str(tmp_path / "command-cache.db"))
    return home, hooks


def _count_reads(monkeypatch: pytest.MonkeyPatch) -> Counter[Path]:
    counts: Counter[Path] = Counter()
    real = command_recovery.iter_bash_commands

    def counting(path: Path) -> Iterator[tuple[int, str]]:
        counts[path] += 1
        return real(path)

    monkeypatch.setattr(command_recovery, "iter_bash_commands", counting)
    return counts


def test_list_prints_preview_with_one_recovery_pass(
    list_env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home, hooks = list_env
    short = "git status"
    long = "echo " + "x" * 300
    path = _transcript(home, [short, long])
    for command in (short, long):
        _insert(hooks, hook="bash-security-review", verdict="safe", cache="miss",
                input_hash=f"sha256:{_sha(command)}", session_id=_SESSION)
    calls: list[list[str]] = []
    real_recover = command_recovery.recover

    def spy(args: list[str], **kw: object) -> list[command_recovery.Recovery]:
        calls.append(list(args))
        return real_recover(args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(command_recovery, "recover", spy)
    counts = _count_reads(monkeypatch)
    assert ucc.main(["list"]) == 0
    out = capsys.readouterr().out
    assert len(calls) == 1
    assert sorted(calls[0]) == sorted([_sha(short), _sha(long)])
    assert counts == Counter({path: 1})
    assert "command: git status" in out
    assert "command: " + long[:200] + "…[+105 chars]" in out
    assert "cannot be resolved" not in out
    assert "does not yet have the original commands" not in out


def test_list_no_preview_opens_no_transcript(
    list_env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home, hooks = list_env
    _transcript(home, ["git status"])
    _insert(hooks, hook="bash-security-review", verdict="safe", cache="miss",
            input_hash=f"sha256:{_sha('git status')}", session_id=_SESSION)
    counts = _count_reads(monkeypatch)
    assert ucc.main(["list", "--no-preview"]) == 0
    out = capsys.readouterr().out
    assert counts == Counter()
    assert "git status" not in out
    assert _sha("git status")[:12] in out


def test_list_shows_status_for_unrecoverable_candidate(
    list_env: tuple[Path, Path], capsys: pytest.CaptureFixture[str],
) -> None:
    _, hooks = list_env
    _insert(hooks, hook="bash-security-review", verdict="safe", cache="miss",
            input_hash=f"sha256:{_sha('lost')}", session_id=_SESSION)
    assert ucc.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "status=session" in out
    assert "cannot be resolved" not in out
    assert "does not yet have the original commands" not in out


def test_no_subcommand_lists_with_preview_in_subprocess(tmp_path: Path) -> None:
    home = tmp_path / "home"
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    _transcript(home, ["git status"])
    _insert(hooks, hook="bash-security-review", verdict="safe", cache="miss",
            input_hash=f"sha256:{_sha('git status')}", session_id=_SESSION)
    result = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH)],
        env={"CCST_FIRES_ACCESS": "1", "HOME": str(home), "CCST_HOOKS_DIR": str(hooks),
             "CCST_DATA_HOME": str(tmp_path / "data"),
             "CCST_CACHE_DB": str(tmp_path / "command-cache.db"), "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "command: git status" in result.stdout


def test_refusal_names_a_working_command(tmp_path: Path) -> None:
    """The re-run command in the refusal text, run as printed, lists successfully."""
    env = {"HOME": str(tmp_path / "home"), "CCST_HOOKS_DIR": str(tmp_path / "hooks"),
           "CCST_DATA_HOME": str(tmp_path / "data"),
           "CCST_CACHE_DB": str(tmp_path / "command-cache.db"), "PATH": "/usr/bin:/bin"}
    (tmp_path / "hooks").mkdir()
    refused = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), "list"], env=env, capture_output=True, text=True,
    )
    assert refused.returncode == 2
    [rerun] = [
        line.split("Re-run as: ", 1)[1]
        for line in refused.stderr.splitlines() if "Re-run as: " in line
    ]
    result = subprocess.run(rerun, shell=True, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "candidate" in result.stdout or "No safe-verdict fires" in result.stdout


def _docstring_usage_lines() -> list[str]:
    doc = ucc.__doc__
    assert doc is not None
    block = doc.split("Usage:", 1)[1].split("\n\n", 1)[0]
    return [line.strip() for line in block.strip().splitlines()]


def _example_argv(usage: str) -> list[str]:
    """Turn one documented usage line into a concrete argv: drop the env and
    interpreter prefix, open optional brackets, fill placeholders."""
    rest = usage.split("update_command_cache.py", 1)[1]
    rest = rest.replace("[", "").replace("]", "").replace("...", "x")
    rest = rest.replace("<sha>", "a" * 64).replace("<safe|suspicious|dangerous>", "safe")
    return shlex.split(rest)


def test_docstring_usage_matches_argparse() -> None:
    parser = ucc.build_parser()
    usages = _docstring_usage_lines()
    documented = set()
    for usage in usages:
        argv = _example_argv(usage)
        parser.parse_args(argv)  # SystemExit (an argparse error) fails the test
        if argv:
            documented.add(argv[0])
    subparsers = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    assert documented == set(subparsers.choices)
