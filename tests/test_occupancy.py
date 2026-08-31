from __future__ import annotations

import os
import subprocess
import sys

import pytest

import cc_session_tools.lib.occupancy as occupancy


def test_occupied_true_when_a_claude_pid_matches_the_project_root(monkeypatch, tmp_path):
    project_root = tmp_path / "proj"
    project_root.mkdir()
    monkeypatch.setattr(occupancy, "_claude_pids", lambda: [111, 222])
    monkeypatch.setattr(
        occupancy,
        "_cwd_of_pid",
        lambda pid: project_root if pid == 222 else tmp_path / "other",
    )
    assert occupancy.is_occupied(project_root) is True


def test_occupied_false_when_no_pid_matches(monkeypatch, tmp_path):
    project_root = tmp_path / "proj"
    monkeypatch.setattr(occupancy, "_claude_pids", lambda: [111])
    monkeypatch.setattr(occupancy, "_cwd_of_pid", lambda pid: tmp_path / "other")
    assert occupancy.is_occupied(project_root) is False


def test_excludes_the_given_pid(monkeypatch, tmp_path):
    project_root = tmp_path / "proj"
    monkeypatch.setattr(occupancy, "_claude_pids", lambda: [222])
    monkeypatch.setattr(occupancy, "_cwd_of_pid", lambda pid: project_root)
    assert occupancy.is_occupied(project_root, exclude_pid=222) is False


def test_fails_safe_occupied_when_cwd_cannot_be_resolved(monkeypatch, tmp_path):
    project_root = tmp_path / "proj"
    monkeypatch.setattr(occupancy, "_claude_pids", lambda: [111])

    def raise_err(pid: int) -> None:
        raise OSError("no such process")

    monkeypatch.setattr(occupancy, "_cwd_of_pid", raise_err)
    assert occupancy.is_occupied(project_root) is True


def test_two_different_projects_do_not_cross_contaminate(monkeypatch, tmp_path):
    # A live pid rooted in project A must not make project B report occupied,
    # and vice versa - the whole point of this check is telling them apart.
    project_a = tmp_path / "proj-a"
    project_b = tmp_path / "proj-b"
    project_a.mkdir()
    project_b.mkdir()
    monkeypatch.setattr(occupancy, "_claude_pids", lambda: [111, 222])

    def cwd_of_pid(pid: int):
        return {111: project_a, 222: project_b}[pid]

    monkeypatch.setattr(occupancy, "_cwd_of_pid", cwd_of_pid)
    assert occupancy.is_occupied(project_a) is True
    assert occupancy.is_occupied(project_b) is True

    # Now only pid 111 (project A) is live - project B must report clear.
    monkeypatch.setattr(occupancy, "_claude_pids", lambda: [111])
    assert occupancy.is_occupied(project_a) is True
    assert occupancy.is_occupied(project_b) is False


def test_fails_safe_occupied_when_pgrep_itself_fails(monkeypatch, tmp_path):
    """Regression test for a code-review finding: a pgrep-level failure (not installed, a
    permission error, the timeout firing) must not be swallowed into an empty PID list - that
    would be indistinguishable from "genuinely no claude process running" to every caller, the
    opposite of every other failure path in this module, which all fail toward occupied."""
    project_root = tmp_path / "proj"

    def raise_err() -> list[int]:
        raise OSError("pgrep: command not found")

    monkeypatch.setattr(occupancy, "_claude_pids", raise_err)
    assert occupancy.is_occupied(project_root) is True


def test_fails_safe_occupied_when_cwd_resolution_raises_a_subprocess_error(monkeypatch, tmp_path):
    """Regression test for a code-review finding: is_occupied() originally only caught OSError
    around _cwd_of_pid(), but the macOS lsof branch's failure modes (a nonzero exit from the
    ordinary process-already-exited race, or the timeout firing) raise
    subprocess.CalledProcessError/TimeoutExpired - SubprocessError subclasses, not OSError
    subclasses - which escaped uncaught and crashed the caller instead of failing safe."""
    project_root = tmp_path / "proj"
    monkeypatch.setattr(occupancy, "_claude_pids", lambda: [111])

    def raise_err(pid: int) -> None:
        raise subprocess.CalledProcessError(1, ["lsof"])

    monkeypatch.setattr(occupancy, "_cwd_of_pid", raise_err)
    assert occupancy.is_occupied(project_root) is True


def test_claude_pids_itself_raises_rather_than_swallowing_a_real_pgrep_failure(monkeypatch):
    """Pins _claude_pids()'s actual subprocess-calling behaviour directly (mocking
    subprocess.run, not the _claude_pids/_cwd_of_pid wrappers the other tests use) - the module
    docstring promises this function raises on a genuine failure rather than returning [], and
    this is the one test that can't be satisfied by mocking the wrapper itself."""
    def raise_timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd=["pgrep"], timeout=5)

    monkeypatch.setattr(subprocess, "run", raise_timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        occupancy._claude_pids()


def test_cwd_of_pid_on_darwin_raises_on_a_nonzero_lsof_exit(monkeypatch):
    """Pins _cwd_of_pid()'s actual macOS subprocess-calling behaviour directly (mocking
    subprocess.run and sys.platform, not the wrapper itself) - a nonzero lsof exit (the ordinary
    race where the target process has already exited) must raise, not return a bogus path."""
    monkeypatch.setattr(occupancy.sys, "platform", "darwin")

    def raise_called_process_error(*args: object, **kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, ["lsof"])

    monkeypatch.setattr(subprocess, "run", raise_called_process_error)
    with pytest.raises(subprocess.CalledProcessError):
        occupancy._cwd_of_pid(111)


# ---------- launching_claude_pid ----------
#
# Regression coverage for a real bug: a hook's `command` runs via `/bin/sh -c "<command>"`, and on
# any system where `/bin/sh` is dash rather than bash (Debian/Ubuntu/WSL2's default) that leaves a
# real `sh` process between `claude` and the hook - confirmed empirically by installing a
# diagnostic SessionStart hook in a scratch project and walking `/proc/<pid>` for the real chain:
# `ccst` (the hook) -> `sh -c ...` -> `claude` -> the shell that launched it. `os.getppid()` alone
# (the hook's previous `session_pid`) resolves to the `sh` wrapper, which never appears in
# `_claude_pids()`'s `pgrep -x claude` results - so the occupancy exclusion silently matched
# nothing, and every SessionStart hook found its own just-launched session "occupying" its own
# project.


def test_launching_claude_pid_returns_the_start_pid_when_it_is_already_claude(monkeypatch):
    monkeypatch.setattr(occupancy, "_comm_of", lambda pid: "claude")

    def explode(pid: int) -> int | None:
        raise AssertionError("must not climb past a pid that is already claude")

    monkeypatch.setattr(occupancy, "_ppid_of", explode)
    assert occupancy.launching_claude_pid(111) == 111


def test_launching_claude_pid_climbs_past_an_sh_wrapper(monkeypatch):
    """The confirmed real shape: hook (not claude) -> sh -c wrapper (not claude) -> claude."""
    comms = {21732: "sh", 22984: "claude"}
    ppids = {21732: 22984}
    monkeypatch.setattr(occupancy, "_comm_of", lambda pid: comms.get(pid))
    monkeypatch.setattr(occupancy, "_ppid_of", lambda pid: ppids.get(pid))

    assert occupancy.launching_claude_pid(21732) == 22984


def test_launching_claude_pid_gives_up_after_max_hops(monkeypatch):
    """An unrecognised process-tree shape (never reaches a `claude` comm) must degrade to None
    rather than loop forever."""
    monkeypatch.setattr(occupancy, "_comm_of", lambda pid: "sh")
    monkeypatch.setattr(occupancy, "_ppid_of", lambda pid: pid + 1)

    assert occupancy.launching_claude_pid(1, max_hops=3) is None


def test_launching_claude_pid_returns_none_when_the_chain_cannot_be_resolved(monkeypatch):
    """A dead/unreadable ancestor (permission error, already exited) - same "can't tell, don't
    exclude anything" fallback every other unresolvable case in this module already takes."""
    monkeypatch.setattr(occupancy, "_comm_of", lambda pid: "sh")
    monkeypatch.setattr(occupancy, "_ppid_of", lambda pid: None)

    assert occupancy.launching_claude_pid(111) is None


def test_launching_claude_pid_stops_on_a_self_referential_ppid(monkeypatch):
    """pid 1 (init/systemd) reports itself as its own parent on Linux - climbing must not spin
    forever on that edge case."""
    monkeypatch.setattr(occupancy, "_comm_of", lambda pid: "init")
    monkeypatch.setattr(occupancy, "_ppid_of", lambda pid: pid)

    assert occupancy.launching_claude_pid(1) is None


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads the real /proc")
def test_ppid_of_reads_the_real_proc_status_file_on_linux():
    """Pins _ppid_of()'s actual Linux parsing against this real test process's own /proc entry,
    rather than a mock - the PPid line's exact format is what this depends on."""
    assert occupancy._ppid_of(os.getpid()) == os.getppid()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads the real /proc")
def test_comm_of_reads_the_real_proc_comm_file_on_linux():
    """/proc/<pid>/comm for this test process is whatever interpreter is running it (pytest's own
    entry point, truncated to 15 chars per the kernel's comm limit) - just confirms a real read
    round-trips, not any specific value."""
    comm = occupancy._comm_of(os.getpid())
    assert comm
    assert "/" not in comm
