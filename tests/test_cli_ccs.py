from __future__ import annotations

import json as json_mod
from pathlib import Path

import pytest

from cc_session_tools.cli import ccs


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def fake_repos(fake_home, tmp_path, monkeypatch):
    repos = tmp_path / "repos"
    repos.mkdir()
    monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(repos))
    return repos


@pytest.fixture
def force_grep_path(monkeypatch):
    """Make shutil.which("rg") return None inside ccs so the grep-fallback
    code path is exercised regardless of whether rg is installed."""
    from cc_session_tools.cli import ccs as ccs_mod

    real_which = ccs_mod.shutil.which

    def fake_which(name, *args, **kwargs):
        if name == "rg":
            return None
        return real_which(name, *args, **kwargs)

    monkeypatch.setattr(ccs_mod.shutil, "which", fake_which)


@pytest.fixture
def force_rg_path(monkeypatch):
    """Make shutil.which("rg") return a real rg binary if installed; skip
    the test otherwise so we don't run rg-specific assertions on a host
    where rg isn't available."""
    import shutil as _shutil
    rg = _shutil.which("rg")
    if not rg:
        pytest.skip("rg (ripgrep) not installed; skipping rg-path test")


def _make_session(repos: Path, project: str, basename: str, *, contents: str | None = None) -> Path:
    sess = repos / project / "cc-sessions" / basename
    (sess / "working").mkdir(parents=True)
    if contents is not None:
        (sess / "working" / "WORKLOG.md").write_text(contents)
    return sess


def test_name_search_in_current_dir_lists_matches(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    _make_session(fake_repos, "myproj", "20260504-foo-bar")
    _make_session(fake_repos, "myproj", "20260503-other")
    monkeypatch.chdir(proj)

    rc = ccs.main(["foo"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "20260504-foo-bar" in out
    assert "20260503-other" not in out


def test_name_search_orders_descending_by_date(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    _make_session(fake_repos, "myproj", "20260101-foo-old")
    _make_session(fake_repos, "myproj", "20260504-foo-new")
    _make_session(fake_repos, "myproj", "20260301-foo-mid")
    monkeypatch.chdir(proj)

    rc = ccs.main(["foo"])
    assert rc == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines == [
        "20260504-foo-new",
        "20260301-foo-mid",
        "20260101-foo-old",
    ]


def test_name_search_no_match_returns_1(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    _make_session(fake_repos, "myproj", "20260504-foo")
    monkeypatch.chdir(proj)

    rc = ccs.main(["nope"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "no sessions match" in err


def test_global_name_search_includes_project_path(fake_repos, monkeypatch, capsys):
    _make_session(fake_repos, "alpha", "20260504-foo")
    _make_session(fake_repos, "beta", "20260503-foo")
    monkeypatch.chdir(fake_repos)  # cwd doesn't matter for --global

    rc = ccs.main(["foo", "--global"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "20260504-foo" in out
    assert "20260503-foo" in out
    assert str(fake_repos / "alpha") in out
    assert str(fake_repos / "beta") in out


def test_contents_search_finds_match_with_context(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    _make_session(
        fake_repos, "myproj", "20260504-eats",
        contents=(
            "The plan is as follows:\n"
            "  - Eat lots of flambe\n"
            "  - Dance\n"
        ),
    )
    monkeypatch.chdir(proj)

    rc = ccs.main(["flambe", "--contents"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "20260504-eats" in out
    assert "The plan is as follows" in out
    assert "Eat lots of flambe" in out
    assert "Dance" in out


def test_contents_search_no_match_returns_1(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    _make_session(fake_repos, "myproj", "20260504-foo", contents="nothing relevant\n")
    monkeypatch.chdir(proj)

    rc = ccs.main(["flambe", "--contents"])
    assert rc == 1


def test_global_contents_search_shows_project_path(fake_repos, monkeypatch, capsys):
    _make_session(
        fake_repos, "alpha", "20260504-foo",
        contents="alpha line\nflambe here\nafter\n",
    )
    monkeypatch.chdir(fake_repos)

    rc = ccs.main(["flambe", "--contents", "--global"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "20260504-foo" in out
    assert str(fake_repos / "alpha") in out
    assert "flambe here" in out


def test_no_cc_sessions_in_cwd_errors_for_local_search(fake_repos, monkeypatch, capsys, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    rc = ccs.main(["foo"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "no cc-sessions" in err


def test_global_search_exits_nonzero_with_marker_when_env_vars_unset(monkeypatch, capsys):
    # autouse fixture already clears env vars; this test confirms ccs --global
    # exits 1 and prints [CST-ROOTS-CONFIG-ERROR] to stderr.
    with pytest.raises(SystemExit) as exc_info:
        ccs.main(["foo", "--global"])
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "[CST-ROOTS-CONFIG-ERROR]" in err


class TestContentsSearchHeaderRgPath:
    """rg path: skips the indexing pre-pass, prints count + estimate immediately."""

    def test_initial_header_includes_session_count_and_query(
        self, fake_repos, monkeypatch, capsys, force_rg_path
    ):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-a", contents="x\n")
        _make_session(fake_repos, "myproj", "20260503-b", contents="y\n")
        monkeypatch.chdir(proj)
        ccs.main(["needle", "--contents"])
        err = capsys.readouterr().err
        assert "2 sessions" in err
        assert "'needle'" in err

    def test_final_summary_reports_session_count_and_elapsed(
        self, fake_repos, monkeypatch, capsys, force_rg_path
    ):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-a", contents="x\n")
        _make_session(fake_repos, "myproj", "20260503-b", contents="y\n")
        monkeypatch.chdir(proj)
        ccs.main(["needle", "--contents"])
        err = capsys.readouterr().err
        # New batched flow: single batch for <=10 sessions, final summary only
        assert "searched 2 sessions in" in err

    def test_final_summary_uses_elapsed_not_estimate(
        self, fake_repos, monkeypatch, capsys, force_rg_path
    ):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-a", contents="x\n")
        monkeypatch.chdir(proj)
        ccs.main(["needle", "--contents"])
        err = capsys.readouterr().err
        # New batched flow: final summary is elapsed only (no "estimate was")
        assert "searched 1 session in" in err

    def test_singular_session_phrasing(
        self, fake_repos, monkeypatch, capsys, force_rg_path
    ):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-only", contents="x\n")
        monkeypatch.chdir(proj)
        ccs.main(["needle", "--contents"])
        err = capsys.readouterr().err
        assert "1 session" in err
        assert "1 sessions" not in err

    def test_global_aggregates_across_roots(
        self, fake_repos, monkeypatch, capsys, force_rg_path
    ):
        _make_session(fake_repos, "alpha", "20260504-a", contents="x\n")
        _make_session(fake_repos, "beta", "20260503-b", contents="y\n")
        monkeypatch.chdir(fake_repos)
        ccs.main(["needle", "--contents", "--global"])
        err = capsys.readouterr().err
        assert "2 sessions" in err


class TestContentsSearchHeaderGrepFallback:
    """Grep fallback path: keeps the indexing pre-pass for size-cap enforcement."""

    def test_initial_header_printed_before_any_filesystem_walk(
        self, fake_repos, monkeypatch, capsys, force_grep_path
    ):
        from cc_session_tools.cli import ccs as ccs_mod

        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-a", contents="alpha\n")
        _make_session(fake_repos, "myproj", "20260503-b", contents="beta\n")
        monkeypatch.chdir(proj)

        def boom(*args, **kwargs):
            raise AssertionError(
                "ccs walked files before printing initial header"
            )

        monkeypatch.setattr(ccs_mod, "enumerate_session_files", boom)

        with pytest.raises(AssertionError, match="walked files before"):
            ccs.main(["needle", "--contents"])

        err = capsys.readouterr().err
        assert "2 sessions" in err

    def test_indexing_header_uses_indexing_verb(
        self, fake_repos, monkeypatch, capsys, force_grep_path
    ):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-a", contents="x\n")
        monkeypatch.chdir(proj)
        ccs.main(["needle", "--contents"])
        err = capsys.readouterr().err
        assert "indexing" in err.lower()

    def test_indexed_summary_reports_files_and_size(
        self, fake_repos, monkeypatch, capsys, force_grep_path
    ):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-a", contents="alpha\n" * 50)
        _make_session(fake_repos, "myproj", "20260503-b", contents="beta\n" * 50)
        monkeypatch.chdir(proj)
        ccs.main(["needle", "--contents"])
        err = capsys.readouterr().err
        assert "indexed" in err.lower()
        assert "files" in err
        assert any(unit in err for unit in (" B", " KB", " MB"))

    def test_header_not_printed_for_name_search(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-foo", contents="x\n")
        monkeypatch.chdir(proj)
        ccs.main(["foo"])
        err = capsys.readouterr().err
        assert "sessions" not in err


class TestContentsSearchSummary:
    def test_summary_reports_files_and_size_after_search(
        self, fake_repos, monkeypatch, capsys
    ):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-a", contents="alpha\n" * 100)
        _make_session(fake_repos, "myproj", "20260503-b", contents="beta\n" * 50)
        monkeypatch.chdir(proj)

        ccs.main(["needle", "--contents"])
        err = capsys.readouterr().err
        # Final summary line shows files searched and total size.
        assert "files" in err
        assert any(unit in err for unit in (" B", " KB", " MB"))

    def test_no_summary_for_name_search(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-foo", contents="x\n")
        monkeypatch.chdir(proj)

        ccs.main(["foo"])
        err = capsys.readouterr().err
        assert "files" not in err
        assert " KB" not in err
        assert " MB" not in err


class TestContentsSearchProgress:
    def test_progress_emitted_when_stderr_is_a_tty_grep_path(
        self, fake_repos, monkeypatch, capsys, force_grep_path
    ):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-a", contents="x\n")
        _make_session(fake_repos, "myproj", "20260503-b", contents="y\n")
        monkeypatch.chdir(proj)

        monkeypatch.setattr("sys.stderr.isatty", lambda: True)

        ccs.main(["needle", "--contents"])
        err = capsys.readouterr().err
        # Per-session progress fires per session in the grep fallback path.
        assert "\r" in err
        assert "/2" in err

    def test_progress_silent_when_stderr_not_a_tty(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-a", contents="x\n")
        monkeypatch.chdir(proj)
        # Default in pytest: stderr is not a TTY. Headers still print; progress does not.
        ccs.main(["needle", "--contents"])
        err = capsys.readouterr().err
        assert "\r" not in err


def test_exclude_hooks_hides_hook_sessions(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    _make_session(fake_repos, "myproj", "20260504-hook-security-check")
    _make_session(fake_repos, "myproj", "20260504-normal-work")
    monkeypatch.chdir(proj)

    rc = ccs.main(["2026", "--exclude-hooks"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "normal-work" in out
    assert "hook-security-check" not in out


def test_exclude_hooks_reports_count_on_stderr(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    _make_session(fake_repos, "myproj", "20260504-hook-security-check")
    _make_session(fake_repos, "myproj", "20260504-normal-work")
    monkeypatch.chdir(proj)

    ccs.main(["2026", "--exclude-hooks"])
    err = capsys.readouterr().err
    assert "1 hook" in err
    # Note: no "--include-hooks" hint in message (flag not implemented)


def test_without_exclude_hooks_includes_hook_sessions_by_default(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    _make_session(fake_repos, "myproj", "20260504-hook-security-check")
    monkeypatch.chdir(proj)

    rc = ccs.main(["hook"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "hook-security-check" in out


def test_since_filter_excludes_old_sessions(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    _make_session(fake_repos, "myproj", "20260101-old-work")
    _make_session(fake_repos, "myproj", "20260504-new-work")
    monkeypatch.chdir(proj)

    rc = ccs.main(["work", "--since", "20260301"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "new-work" in out
    assert "old-work" not in out


def test_before_filter_excludes_new_sessions(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    _make_session(fake_repos, "myproj", "20260101-old-work")
    _make_session(fake_repos, "myproj", "20260504-new-work")
    monkeypatch.chdir(proj)

    rc = ccs.main(["work", "--before", "20260301"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "old-work" in out
    assert "new-work" not in out


def test_days_filter_keeps_recent_sessions(fake_repos, monkeypatch, capsys):
    import datetime
    proj = fake_repos / "myproj"
    today = datetime.date.today()
    yesterday = (today - datetime.timedelta(days=1)).strftime("%Y%m%d")
    old = "20200101"
    _make_session(fake_repos, "myproj", f"{yesterday}-recent-work")
    _make_session(fake_repos, "myproj", f"{old}-ancient-work")
    monkeypatch.chdir(proj)

    rc = ccs.main(["work", "--days", "7"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "recent-work" in out
    assert "ancient-work" not in out


def test_invalid_since_date_exits_with_error(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    proj.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(proj)
    rc = ccs.main(["work", "--since", "not-a-date"])
    assert rc == 1
    assert "invalid date" in capsys.readouterr().err.lower()


class TestMaxFileSize:
    def test_oversized_files_skipped_and_reported_grep_path(
        self, fake_repos, monkeypatch, capsys, force_grep_path
    ):
        proj = fake_repos / "myproj"
        sess = _make_session(
            fake_repos, "myproj", "20260504-a",
            contents="needle in small file\n",
        )
        # Add a "huge" file - 5 MB, larger than the 1 MB cap we'll set below.
        (sess / "working" / "huge.bin").write_bytes(b"needle " * (5 * 1024 * 1024 // 7))
        monkeypatch.chdir(proj)

        ccs.main(["needle", "--contents", "--max-file-size", "1"])
        err = capsys.readouterr().err
        assert "skipped 1" in err

    def test_oversized_files_handled_silently_in_rg_path(
        self, fake_repos, monkeypatch, capsys, force_rg_path
    ):
        """rg has --max-filesize built in; we don't explicitly count or
        report skipped files in the rg path. The match should still be
        found in the small file."""
        proj = fake_repos / "myproj"
        sess = _make_session(
            fake_repos, "myproj", "20260504-a",
            contents="needle in small file\n",
        )
        (sess / "working" / "huge.bin").write_bytes(b"needle " * (5 * 1024 * 1024 // 7))
        monkeypatch.chdir(proj)

        rc = ccs.main(["needle", "--contents", "--max-file-size", "1"])
        assert rc == 0

    def test_default_cap_is_high_enough_for_normal_files(
        self, fake_repos, monkeypatch, capsys, force_grep_path
    ):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-a", contents="needle\n")
        monkeypatch.chdir(proj)
        ccs.main(["needle", "--contents"])
        err = capsys.readouterr().err
        assert "skipped" not in err


def test_json_output_name_search(fake_repos, monkeypatch, capsys):
    _make_session(fake_repos, "myproj", "20260504-foo-bar")
    _make_session(fake_repos, "myproj", "20260503-foo-baz")
    proj = fake_repos / "myproj"
    monkeypatch.chdir(proj)

    rc = ccs.main(["foo", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json_mod.loads(out)
    assert isinstance(data, list)
    assert len(data) == 2
    basenames = {d["basename"] for d in data}
    assert "20260504-foo-bar" in basenames
    assert all("project_dir" in d for d in data)


def test_null_output_name_search(fake_repos, monkeypatch, capsys):
    _make_session(fake_repos, "myproj", "20260504-foo-bar")
    proj = fake_repos / "myproj"
    monkeypatch.chdir(proj)

    rc = ccs.main(["foo", "--null"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "20260504-foo-bar\x00" in out


def test_json_no_results_returns_empty_array(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    proj.mkdir(parents=True, exist_ok=True)
    _make_session(fake_repos, "myproj", "20260504-unrelated")
    monkeypatch.chdir(proj)

    rc = ccs.main(["zzznomatch", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert json_mod.loads(out) == []


def test_default_global_env_var_enables_global_scope(fake_repos, monkeypatch, capsys):
    # Two projects, search from proj1 without --global.
    # _make_session uses mkdir(parents=True) so proj2 is created automatically.
    _make_session(fake_repos, "proj1", "20260504-proj1-session")
    _make_session(fake_repos, "proj2", "20260504-proj2-session")
    proj1 = fake_repos / "proj1"
    monkeypatch.chdir(proj1)
    monkeypatch.setenv("CCS_DEFAULT_GLOBAL", "1")

    rc = ccs.main(["session"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "proj1-session" in out
    assert "proj2-session" in out


def test_local_flag_overrides_default_global(fake_repos, monkeypatch, capsys):
    _make_session(fake_repos, "proj1", "20260504-proj1-session")
    _make_session(fake_repos, "proj2", "20260504-proj2-session")
    proj1 = fake_repos / "proj1"
    monkeypatch.chdir(proj1)
    monkeypatch.setenv("CCS_DEFAULT_GLOBAL", "1")

    rc = ccs.main(["session", "--local"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "proj1-session" in out
    assert "proj2-session" not in out


def test_did_you_mean_suggests_close_match(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    _make_session(fake_repos, "myproj", "20260504-config-cleanup")
    monkeypatch.chdir(proj)

    rc = ccs.main(["confg-cleanup"])  # typo
    assert rc == 1
    err = capsys.readouterr().err
    assert "did you mean" in err.lower()
    assert "config-cleanup" in err


def test_no_suggestion_when_completely_unrelated(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    _make_session(fake_repos, "myproj", "20260504-config-cleanup")
    monkeypatch.chdir(proj)

    rc = ccs.main(["zzzzzzz"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "did you mean" not in err.lower()


def test_osc8_link_wraps_path_in_escape_sequence():
    from cc_session_tools.cli.ccs import _osc8_link
    path = Path("/tmp/my-session")
    result = _osc8_link("my-session", path)
    assert "\033]8;;" in result
    assert "my-session" in result
    assert result.endswith("\033]8;;\033\\")


def test_name_search_no_osc8_in_non_tty(fake_repos, monkeypatch, capsys):
    # capsys stdout is not a TTY, so no OSC 8 should appear
    proj = fake_repos / "myproj"
    _make_session(fake_repos, "myproj", "20260504-foo")
    monkeypatch.chdir(proj)
    rc = ccs.main(["foo"])
    out = capsys.readouterr().out
    assert "\033]8" not in out


def test_contents_search_includes_transcript_dir(fake_repos, fake_home, monkeypatch, capsys, force_grep_path):
    # Create session and a matching transcript in ~/.claude/projects/
    proj = fake_repos / "myproj"
    _make_session(fake_repos, "myproj", "20260504-foo", contents="normal content")
    # Simulate a transcript dir using transcript_dir_for_project
    from cc_session_tools.lib.sessions import transcript_dir_for_project
    t_dir = transcript_dir_for_project(proj)
    t_dir.mkdir(parents=True)
    (t_dir / "abc123.jsonl").write_text('{"text": "unique-transcript-string"}')
    monkeypatch.chdir(proj)

    rc = ccs.main(["unique-transcript-string", "--contents"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "20260504-foo" in out


# ---------------------------------------------------------------------------
# Task 16: ccs picker integration
# ---------------------------------------------------------------------------

def test_ccs_picker_shown_for_small_result_set(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    for i in range(3):
        _make_session(fake_repos, "myproj", f"2026050{i+1}-foo-{i}")
    monkeypatch.chdir(proj)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    from cc_session_tools.lib import picker
    monkeypatch.setattr(picker, "pick_from_list", lambda _: None)

    rc = ccs.main(["foo"])
    assert rc == 0


def test_ccs_picker_execvp_on_selection(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    _make_session(fake_repos, "myproj", "20260504-foo-one")
    _make_session(fake_repos, "myproj", "20260503-foo-two")
    monkeypatch.chdir(proj)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    captured_exec = {}
    def fake_execvp(name, args):
        captured_exec["name"] = name
        captured_exec["args"] = args

    from cc_session_tools.lib import picker
    monkeypatch.setattr(picker, "pick_from_list", lambda _: 0)  # pick first (most recent)
    monkeypatch.setattr("os.execvp", fake_execvp)

    ccs.main(["foo"])
    assert captured_exec.get("name") == "ccr"
    assert "20260504-foo-one" in captured_exec.get("args", [])


def test_ccs_no_picker_for_more_than_10(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    for i in range(11):
        _make_session(fake_repos, "myproj", f"202605{i+1:02d}-foo-{i:02d}")
    monkeypatch.chdir(proj)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    pick_called = []
    from cc_session_tools.lib import picker
    monkeypatch.setattr(picker, "pick_from_list", lambda _: pick_called.append(1) or None)

    rc = ccs.main(["foo"])
    assert rc == 0
    assert len(pick_called) == 0  # picker not invoked
