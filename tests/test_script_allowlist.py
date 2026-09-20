"""Tests for hooks.script_allowlist: shell-structure scanner, invocation parser, store."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from hooks import cache as cache_mod
from hooks import script_allowlist as sal


# ---------- shell-structure scanner ----------

@pytest.mark.parametrize(
    "command",
    [
        "python3 scripts/x.py add --n 3",
        'python3 scripts/x.py add --subject "A; B | C"',
        "python3 scripts/x.py --subject 'a | b && c > d'",
        "python3 scripts/x.py '$(id)' '`id`' '$HOME'",
        'python3 scripts/x.py "it\'s fine" plain',
        "python3 scripts/x.py --subject 'multi\nline subject'",
        r"python3 scripts/x.py one\ two",
        "./scripts/y.sh arg",
    ],
)
def test_simple_commands_are_accepted(command: str) -> None:
    assert sal.is_simple_command(command)


@pytest.mark.parametrize(
    "command",
    [
        "python3 scripts/x.py | tee out",
        "python3 scripts/x.py; rm y",
        "python3 scripts/x.py && ls",
        "python3 scripts/x.py || ls",
        "python3 scripts/x.py > out.txt",
        "python3 scripts/x.py < in.txt",
        "python3 scripts/x.py 2>&1",
        "python3 scripts/x.py $(whoami)",
        "python3 scripts/x.py `id`",
        'python3 scripts/x.py "$(id)"',
        'python3 scripts/x.py "`id`"',
        'python3 scripts/x.py "$HOME"',
        "python3 scripts/x.py $HOME",
        "python3 scripts/x.py &",
        "python3 scripts/x.py\nrm -rf y",
        "python3 scripts/x.py 'unterminated",
        "python3 scripts/x.py <(cat y)",
        "(python3 scripts/x.py)",
    ],
)
def test_composed_or_expanding_commands_are_rejected(command: str) -> None:
    assert not sal.is_simple_command(command)


# ---------- invocation parser ----------

@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    scripts = root / "scripts"
    scripts.mkdir()
    for name in ("x.py", "y.sh", "z.js"):
        (scripts / name).write_text(f"# {name}\n")
    (scripts / "y.sh").chmod(0o755)
    (scripts / "x.py").chmod(0o644)
    return root


def _parse(project: Path, command: str, cwd: Path | None = None) -> sal.ScriptRef | None:
    return sal.parse_invocation(command, str(cwd or project))


@pytest.mark.parametrize(
    "command",
    [
        "python scripts/x.py a",
        "python3 scripts/x.py a",
        "python3.12 scripts/x.py a",
        "uv run scripts/x.py a",
        "uv run python scripts/x.py a",
        "uv run python3 scripts/x.py a",
        "bash scripts/y.sh a",
        "sh scripts/y.sh a",
        "zsh scripts/y.sh a",
        "node scripts/z.js a",
        "./scripts/y.sh a",
        "scripts/y.sh a",
    ],
)
def test_recognised_interpreter_forms_resolve_to_the_same_script(project: Path, command: str) -> None:
    ref = _parse(project, command)

    assert ref is not None
    assert ref.path.name in {"x.py", "y.sh", "z.js"}
    assert ref.path.parent == (project / "scripts").resolve()
    assert ref.project_root == project.resolve()


@pytest.mark.parametrize(
    "command",
    [
        "python3 -c 'print(1)'",
        "python3 -m pkg",
        "python3 -u scripts/x.py",
        "uv run --with rich scripts/x.py",
        "bash -c 'scripts/y.sh'",
        "FOO=1 python3 scripts/x.py",
        "env python3 scripts/x.py",
        "python3 scripts/missing.py",
        "ls scripts",
        "scripts/x.py a",  # direct invocation of a non-executable file
        "python3 scripts/x.py | cat",  # composition
        "python3",
        "",
    ],
)
def test_unrecognised_or_unsafe_shapes_do_not_parse(project: Path, command: str) -> None:
    assert _parse(project, command) is None


def test_relative_path_resolves_against_cwd(project: Path) -> None:
    ref = _parse(project, "python3 x.py", cwd=project / "scripts")

    assert ref is not None
    assert ref.path == (project / "scripts" / "x.py").resolve()


def test_absolute_path_inside_project_is_accepted(project: Path) -> None:
    ref = _parse(project, f"python3 {project / 'scripts' / 'x.py'}")

    assert ref is not None


def test_path_escaping_the_project_is_rejected(project: Path, tmp_path: Path) -> None:
    (tmp_path / "outside.py").write_text("x")

    assert _parse(project, "python3 ../outside.py") is None


def test_symlink_escaping_the_project_is_rejected(project: Path, tmp_path: Path) -> None:
    (tmp_path / "outside.py").write_text("x")
    (project / "scripts" / "link.py").symlink_to(tmp_path / "outside.py")

    assert _parse(project, "python3 scripts/link.py") is None


def test_script_under_cc_sessions_is_rejected(project: Path) -> None:
    scratch = project / "cc-sessions" / "20260920-thing" / "working"
    scratch.mkdir(parents=True)
    (scratch / "tmp.py").write_text("x")

    assert _parse(project, "python3 cc-sessions/20260920-thing/working/tmp.py") is None


def test_without_a_git_root_the_cwd_is_the_project_boundary(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    (plain / "scripts").mkdir(parents=True)
    (plain / "scripts" / "x.py").write_text("x")
    (tmp_path / "sibling.py").write_text("x")

    assert sal.parse_invocation("python3 scripts/x.py", str(plain)) is not None
    assert sal.parse_invocation("python3 ../sibling.py", str(plain)) is None


def test_git_worktree_dotgit_file_counts_as_a_root(tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    (wt / "scripts").mkdir(parents=True)
    (wt / ".git").write_text("gitdir: /elsewhere\n")
    (wt / "scripts" / "x.py").write_text("x")

    ref = sal.parse_invocation("python3 x.py", str(wt / "scripts"))

    assert ref is not None
    assert ref.project_root == wt.resolve()


def test_a_git_repo_at_home_does_not_widen_the_project_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    (home / ".git").mkdir(parents=True)
    (home / "other").mkdir()
    (home / "other" / "elsewhere.py").write_text("x")
    (home / "proj").mkdir()
    (home / "proj" / "x.py").write_text("x")
    monkeypatch.setenv("HOME", str(home))

    assert sal.parse_invocation("python3 x.py", str(home / "proj")) is not None
    assert sal.parse_invocation("python3 ../other/elsewhere.py", str(home / "proj")) is None


# ---------- reading + hashing ----------

def test_read_script_returns_bytes_and_their_sha256(project: Path) -> None:
    ref = _parse(project, "python3 scripts/x.py")
    assert ref is not None

    content = sal.read_script(ref)

    assert content.sha256 == hashlib.sha256((project / "scripts" / "x.py").read_bytes()).hexdigest()
    assert content.text == "# x.py\n"


def test_read_script_over_size_bound_hashes_but_has_no_review_text(project: Path) -> None:
    big = project / "scripts" / "big.py"
    big.write_bytes(b"#" * (sal.MAX_REVIEW_SCRIPT_BYTES + 1))
    ref = _parse(project, "python3 scripts/big.py")
    assert ref is not None

    content = sal.read_script(ref)

    assert content.text is None
    assert content.sha256 == hashlib.sha256(big.read_bytes()).hexdigest()


# ---------- store ----------

@pytest.fixture(autouse=True)
def _cache_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_CACHE_DB", str(tmp_path / "cache.db"))


def test_store_round_trip() -> None:
    cache_mod.allowlist_upsert("/p/scripts/x.py", "a" * 64, "/p", source="auto")

    entry = cache_mod.allowlist_get("/p/scripts/x.py")

    assert entry is not None
    assert (entry.script_path, entry.sha256, entry.project_root, entry.source) == (
        "/p/scripts/x.py", "a" * 64, "/p", "auto",
    )
    assert entry.use_count == 0
    assert entry.last_used is None


def test_store_get_missing_returns_none() -> None:
    assert cache_mod.allowlist_get("/nope.py") is None


def test_store_upsert_updates_hash_and_keeps_source() -> None:
    cache_mod.allowlist_upsert("/p/x.py", "a" * 64, "/p", source="manual")
    cache_mod.allowlist_upsert("/p/x.py", "b" * 64, "/p", source="auto")

    entry = cache_mod.allowlist_get("/p/x.py")

    assert entry is not None
    assert entry.sha256 == "b" * 64
    assert entry.source == "manual"


def test_store_touch_records_use() -> None:
    cache_mod.allowlist_upsert("/p/x.py", "a" * 64, "/p", source="auto")

    cache_mod.allowlist_touch("/p/x.py")
    cache_mod.allowlist_touch("/p/x.py")

    entry = cache_mod.allowlist_get("/p/x.py")
    assert entry is not None
    assert entry.use_count == 2
    assert entry.last_used is not None


def test_store_remove_reports_whether_anything_was_removed() -> None:
    cache_mod.allowlist_upsert("/p/x.py", "a" * 64, "/p", source="auto")

    assert cache_mod.allowlist_remove("/p/x.py") is True
    assert cache_mod.allowlist_remove("/p/x.py") is False
    assert cache_mod.allowlist_get("/p/x.py") is None


def test_store_list_is_sorted_by_path() -> None:
    cache_mod.allowlist_upsert("/p/b.py", "a" * 64, "/p", source="auto")
    cache_mod.allowlist_upsert("/p/a.py", "a" * 64, "/p", source="auto")

    assert [e.script_path for e in cache_mod.allowlist_list()] == ["/p/a.py", "/p/b.py"]


def test_store_survives_stale_pruning_of_the_command_cache() -> None:
    cache_mod.allowlist_upsert("/p/x.py", "a" * 64, "/p", source="auto")
    cache_mod.cache_record("f" * 64, "safe", "none", "ls")  # triggers _prune_stale

    assert cache_mod.allowlist_get("/p/x.py") is not None

