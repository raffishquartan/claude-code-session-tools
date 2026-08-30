from __future__ import annotations

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
