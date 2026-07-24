"""Tests for ccs sentinel-based sorting (--order-by opened / --order-by active)."""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from cc_session_tools.cli import ccs
from cc_session_tools.cli.ccs import _Result, _sort_results


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path / "db"))
    return home


@pytest.fixture
def fake_repos(fake_home, tmp_path, monkeypatch):
    repos = tmp_path / "repos"
    repos.mkdir()
    monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(repos))
    return repos


def _make_session(repos: Path, project: str, basename: str) -> Path:
    from cc_session_tools.lib import sessions_db
    sess = repos / project / "cc-sessions" / basename
    (sess / "working").mkdir(parents=True)
    sessions_db.ensure_session_row(repos / project, basename)
    return sess


# ---------------------------------------------------------------------------
# _sort_results with order_by opened / active
# ---------------------------------------------------------------------------

class TestSortResultsSentinel:
    def _make_result(self, basename: str, project_dir: Path) -> _Result:
        return _Result(
            date_key="20260612",
            basename=basename,
            project_dir=project_dir,
            context_lines=[],
        )

    def test_sort_opened_session_with_sentinel_sorts_above_without(self, tmp_path):
        r1 = self._make_result("20260612-no-sentinel", tmp_path)
        r1.opened_mtime = 0.0

        r2 = self._make_result("20260612-with-sentinel", tmp_path)
        r2.opened_mtime = time.time()

        results = [r1, r2]
        sorted_results = _sort_results(results, "datetime", order_by="opened")
        assert sorted_results[0].basename == "20260612-with-sentinel"
        assert sorted_results[1].basename == "20260612-no-sentinel"

    def test_sort_active_session_with_sentinel_sorts_above_without(self, tmp_path):
        r1 = self._make_result("20260612-no-sentinel", tmp_path)
        r1.active_mtime = 0.0

        r2 = self._make_result("20260612-with-sentinel", tmp_path)
        r2.active_mtime = time.time()

        results = [r1, r2]
        sorted_results = _sort_results(results, "datetime", order_by="active")
        assert sorted_results[0].basename == "20260612-with-sentinel"
        assert sorted_results[1].basename == "20260612-no-sentinel"

    def test_sort_opened_most_recent_first(self, tmp_path):
        now = time.time()
        r1 = self._make_result("20260612-older", tmp_path)
        r1.opened_mtime = now - 3600

        r2 = self._make_result("20260612-newer", tmp_path)
        r2.opened_mtime = now

        results = [r1, r2]
        sorted_results = _sort_results(results, "datetime", order_by="opened")
        assert sorted_results[0].basename == "20260612-newer"

    def test_sort_active_most_recent_first(self, tmp_path):
        now = time.time()
        r1 = self._make_result("20260612-older", tmp_path)
        r1.active_mtime = now - 3600

        r2 = self._make_result("20260612-newer", tmp_path)
        r2.active_mtime = now

        results = [r1, r2]
        sorted_results = _sort_results(results, "datetime", order_by="active")
        assert sorted_results[0].basename == "20260612-newer"


# ---------------------------------------------------------------------------
# List mode output with order_by opened / active
# ---------------------------------------------------------------------------

class TestListModeSentinelOutput:
    def test_list_mode_opened_includes_label_and_timestamp(self, fake_repos, monkeypatch, capsys):
        from cc_session_tools.lib import sessions_db
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260612-a-sess")
        sessions_db.touch_last_opened(proj, "20260612-a-sess")
        monkeypatch.chdir(proj)

        rc = ccs.main(["--order-by", "opened"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "opened:" in out
        assert "20260612-a-sess" in out

    def test_list_mode_opened_shows_never_when_no_sentinel(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260612-no-sentinel")
        monkeypatch.chdir(proj)

        rc = ccs.main(["--order-by", "opened"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "(never)" in out

    def test_list_mode_active_includes_label_and_timestamp(self, fake_repos, monkeypatch, capsys):
        from cc_session_tools.lib import sessions_db
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260612-b-sess")
        sessions_db.touch_last_active(proj, "20260612-b-sess")
        monkeypatch.chdir(proj)

        rc = ccs.main(["--order-by", "active"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "active:" in out
        assert "20260612-b-sess" in out

    def test_list_mode_active_shows_never_when_no_sentinel(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260612-no-active")
        monkeypatch.chdir(proj)

        rc = ccs.main(["--order-by", "active"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "(never)" in out

    def test_order_by_opened_accepted_by_argparser(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260612-argstest")
        monkeypatch.chdir(proj)

        rc = ccs.main(["--order-by", "opened"])
        assert rc == 0

    def test_order_by_active_accepted_by_argparser(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260612-argstest2")
        monkeypatch.chdir(proj)

        rc = ccs.main(["--order-by", "active"])
        assert rc == 0

    def test_list_mode_opened_global_includes_proj_path(self, fake_repos, monkeypatch, capsys):
        from cc_session_tools.lib import sessions_db
        _make_session(fake_repos, "alpha", "20260612-alpha-sess")
        sessions_db.touch_last_opened(fake_repos / "alpha", "20260612-alpha-sess")
        monkeypatch.chdir(fake_repos)

        rc = ccs.main(["--global", "--order-by", "opened"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "opened:" in out
        assert "alpha" in out


# ---------------------------------------------------------------------------
# Search mode output with order_by opened / active
# ---------------------------------------------------------------------------

class TestSearchModeSentinelOutput:
    def test_search_mode_opened_includes_label(self, fake_repos, monkeypatch, capsys):
        from cc_session_tools.lib import sessions_db
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260612-search-opened")
        sessions_db.touch_last_opened(proj, "20260612-search-opened")
        monkeypatch.chdir(proj)

        rc = ccs.main(["search-opened", "--order-by", "opened"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "opened:" in out

    def test_search_mode_active_includes_label(self, fake_repos, monkeypatch, capsys):
        from cc_session_tools.lib import sessions_db
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260612-search-active")
        sessions_db.touch_last_active(proj, "20260612-search-active")
        monkeypatch.chdir(proj)

        rc = ccs.main(["search-active", "--order-by", "active"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "active:" in out

    def test_search_mode_opened_never_when_no_sentinel(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260612-search-no-sentinel")
        monkeypatch.chdir(proj)

        rc = ccs.main(["search-no-sentinel", "--order-by", "opened"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "(never)" in out


# ---------------------------------------------------------------------------
# Session-count scaling regression (the 2026-07-13 performance requirement)
# ---------------------------------------------------------------------------

class TestSessionEnumerationScaling:
    """Regression test for the 2026-07-13 design-spec performance requirement: ccl/ccr/ccs
    session-title/tag lookup must be an indexed sessions.db query making zero per-session
    filesystem stat() calls, not an O(n) directory walk that merely happens to run fast on a
    given machine. Scoped to titles/tags/metadata only — NOT session content search
    (--order-by update, which is unchanged by design and DOES still stat files - see D1)."""

    def _seed_sessions(self, fake_repos, count: int, start_at: int = 0) -> Path:
        from cc_session_tools.lib import sessions_db

        proj = fake_repos / "scaleproj"
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "cc-sessions").mkdir(exist_ok=True)
        for i in range(start_at, start_at + count):
            name = f"20260101-session-{i:05d}"
            (proj / "cc-sessions" / name).mkdir()
            sessions_db.touch_last_opened(proj, name)
        return proj

    def test_order_by_active_makes_no_per_session_stat_calls_regardless_of_count(
        self, fake_home, fake_repos, monkeypatch
    ):
        """The opened/active list-mode branch (Task 10 Step 3) reads mtimes from
        row_by_basename (an in-memory dict built from one sessions.db query), not from
        Path.stat() on each session's sentinel file — assert that directly by counting real
        Path.stat() invocations against the in-scope mechanism, which must stay flat as N
        grows from 50 to 2000.

        Scope note: the performance requirement (overview.md Section 8, design-spec
        Section 7.2) binds the session enumeration + title/tag/timestamp-metadata lookup
        path only, and EXPLICITLY excludes session-content inspection. `ccs`'s footer
        "(X empty)" count reads each session's transcript to decide emptiness — a
        pre-existing O(n) content-inspection cost, unchanged by this phase (plan Task 10
        Step 2), and out of scope. Those stats land on ~/.claude/projects/<encoded>/
        transcript dirs. The mechanism this test guards — the per-session sentinel/enumeration
        walk that was removed — lives under cc-sessions/, so we count only stats on
        cc-sessions/ paths. A future regression that reintroduced a per-session sentinel
        stat (cc-sessions/<basename>/.last-active) or a per-session directory walk would
        still be caught here; the out-of-scope transcript-content is_dir is not."""
        proj = self._seed_sessions(fake_repos, 50)
        monkeypatch.chdir(proj)

        stat_calls = []
        real_stat = Path.stat

        def _counting_stat(self, *a, **kw):
            # Count only stats against the session-enumeration/sentinel mechanism
            # (cc-sessions/), not out-of-scope transcript-content inspection
            # (~/.claude/projects/) — see the scope note in this test's docstring.
            if "cc-sessions" in self.parts:
                stat_calls.append(self)
            return real_stat(self, *a, **kw)

        with patch.object(Path, "stat", _counting_stat):
            rc_small = ccs.main(["--order-by", "active"])
            small_count = len(stat_calls)
        assert rc_small == 0

        self._seed_sessions(fake_repos, 1950, start_at=50)  # -> 2000 total
        stat_calls.clear()
        with patch.object(Path, "stat", _counting_stat):
            rc_large = ccs.main(["--order-by", "active"])
            large_count = len(stat_calls)
        assert rc_large == 0

        # A 40x growth in session count (50 -> 2000) must not grow the stat() call count at
        # all for the mtime-lookup path itself — any per-session stat call here is a direct
        # regression to the pre-migration _get_sentinel_mtime walk. Allow a small constant
        # slack (<=5) for incidental stats unrelated to sentinel lookup (e.g. cwd resolution),
        # never a count that scales with N.
        assert large_count <= small_count + 5, (
            f"stat() call count grew with session count ({small_count} -> {large_count} for "
            f"50 -> 2000 sessions) — this is the exact O(n) filesystem-walk regression this "
            f"test exists to catch."
        )

    def test_global_enumeration_under_absolute_time_bound_at_2000_sessions(
        self, fake_home, fake_repos, monkeypatch
    ):
        """Secondary sanity check, not the primary regression guard (see design note above) —
        a single indexed query + formatting 2000 rows should still complete quickly in absolute
        terms. Kept as a coarse smoke test; the stat-call-count test above is what actually
        proves the mechanism."""
        import time as _time

        self._seed_sessions(fake_repos, 2000)
        start = _time.perf_counter()
        rc = ccs.main(["--global"])
        elapsed = _time.perf_counter() - start
        assert rc == 0
        assert elapsed < 1.0


# ---------------------------------------------------------------------------
# --limit / -n
# ---------------------------------------------------------------------------

class TestLimitFlag:
    def test_limit_returns_only_n_most_recent_by_active(self, fake_home, fake_repos, monkeypatch, capsys):
        from cc_session_tools.lib import sessions_db
        proj = fake_repos / "myproj"
        proj.mkdir(parents=True)
        (proj / "cc-sessions").mkdir()
        for i in range(10):
            name = f"20260101-sess-{i:02d}"
            (proj / "cc-sessions" / name).mkdir()
            sessions_db.ensure_session_row(proj, name)
            sessions_db.touch_last_active(proj, name, when=float(i))
        monkeypatch.chdir(proj)

        rc = ccs.main(["--order-by", "active", "--limit", "3"])
        assert rc == 0
        out = capsys.readouterr().out
        session_lines = [ln for ln in out.splitlines() if "20260101-sess-" in ln]
        assert len(session_lines) == 3
        assert "20260101-sess-09" in session_lines[0]
        assert "20260101-sess-08" in session_lines[1]
        assert "20260101-sess-07" in session_lines[2]

    def test_limit_without_compatible_order_by_errors(self, fake_home, fake_repos, monkeypatch, capsys):
        rc = ccs.main(["--order-by", "start", "--limit", "3"])
        assert rc == 2
        assert "requires --order-by opened or --order-by active" in capsys.readouterr().err
