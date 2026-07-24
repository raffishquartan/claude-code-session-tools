# tests/messaging/test_store.py
from __future__ import annotations

import re
from pathlib import Path

import pytest

from cc_session_tools.lib.messaging import store


def test_store_root_honours_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path / "msgs"))
    assert store.store_root() == tmp_path / "msgs"


def test_store_root_defaults_to_data_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("CCST_MESSAGES_ROOT", raising=False)
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path / "dh"))
    assert store.store_root() == tmp_path / "dh"


def test_db_path_is_ccmsg_db_under_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    assert store.db_path() == tmp_path / "ccmsg.db"


def test_generate_id_is_sortable_and_unique() -> None:
    a = store.generate_id()
    b = store.generate_id()
    assert re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{4}", a)
    assert a != b


def test_slug_subject_is_kebab_and_bounded() -> None:
    assert store.slug_subject("Hello, World! A very LONG subject line here") == "hello-world-a-very-long-subject"
    assert store.slug_subject("") == "untitled"


def test_other_paths_slug_is_stable_hash_plus_basename() -> None:
    s1 = store.other_paths_slug(Path("/example/weird path/My Project"))
    s2 = store.other_paths_slug(Path("/example/weird path/My Project"))
    assert s1 == s2
    assert re.fullmatch(r"[0-9a-f]{8}-my-project", s1)


def test_partition_for_strict_root_is_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj_root = tmp_path / "proj"
    (proj_root / "alpha").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_SESSION_TOOLS_PROJ_ROOT", str(proj_root))
    assert store.partition_for_cwd(proj_root / "alpha") == "projects/alpha"


def test_partition_for_loose_root_is_repos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "repos"
    (repo_root / "beta").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(repo_root))
    assert store.partition_for_cwd(repo_root / "beta") == "repos/beta"


def test_partition_for_unknown_cwd_is_other_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_TOOLS_PROJ_ROOT", raising=False)
    part = store.partition_for_cwd(tmp_path / "nowhere" / "thing")
    assert part.startswith("other-paths/")


def test_partition_for_project_strict_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = tmp_path / "proj"
    (proj / "alpha").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_SESSION_TOOLS_PROJ_ROOT", str(proj))
    monkeypatch.delenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", raising=False)
    assert store.partition_for_project("alpha") == "projects/alpha"


def test_partition_for_project_loose_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repos = tmp_path / "repos"
    (repos / "beta").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(repos))
    monkeypatch.delenv("CLAUDE_SESSION_TOOLS_PROJ_ROOT", raising=False)
    assert store.partition_for_project("beta") == "repos/beta"


def test_partition_for_project_unknown_is_global(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CLAUDE_SESSION_TOOLS_PROJ_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", raising=False)
    assert store.partition_for_project("ghost") == "_global"
