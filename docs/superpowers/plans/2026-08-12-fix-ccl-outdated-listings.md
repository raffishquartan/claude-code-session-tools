# Fix ccl/ccs --global Missing Recent Sessions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `ccl`/`ccs --global` from silently under-reporting recent sessions, make every
rejection/omission of a bad row loudly visible (to both the user and to Claude Code, if run
inside a session), and repair the 9 already-corrupted rows in Chris's live `sessions.db`.

**Architecture:** Two independent, confirmed bugs compound to produce the symptom (see
"Diagnosis" below). Fix both at their write-time and read-time boundaries with visible
diagnostics at every point a row is rejected or omitted, add a `ccst doctor` tripwire, and ship
a `ccst repair sessions` command — parallel to the existing `ccst migrate <store>` family — so
both the 9 known-bad rows and any future recurrence can be fixed non-destructively.

**Tech Stack:** Python 3.12+, stdlib `sqlite3` via this repo's `lib/db.py` connection helper,
`pytest`.

---

## Diagnosis (context for the engineer — already root-caused, not open questions)

1. **Corrupted write.** `sessions.db`'s writer functions
   (`sessions_db.ensure_session_row`/`touch_last_opened`/`touch_last_active`) store
   `str(project_dir)` verbatim with no absolute-path validation. `cccs_hooks/session_tag.py` and
   `cccs_hooks/after_response.py` both derive `project_dir` by parsing the `CLD_SESSION_DIR` env
   var as `Path(CLD_SESSION_DIR).parent.parent`, assuming it is always shaped
   `<project_dir>/cc-sessions/<basename>`. When `CLD_SESSION_DIR` was instead the bare
   two-segment relative string `cc-sessions/<basename>`, `.parent.parent` collapsed to `Path('.')`
   and that got written straight into the DB. 9 rows in Chris's live
   `~/.local/share/claude/sessions.db` have `project_dir = '.'` today, spanning 2026-08-02
   through 2026-08-12 — the exact mechanism that first produced a relative `CLD_SESSION_DIR`
   wasn't pinned down during diagnosis (not needed — the fix closes the single write chokepoint
   regardless of the upstream cause).
2. **Silent drop + limit starvation.** `ccs.py`'s `_collect_session_rows` filters `--global` rows
   with `r.project_dir.parent in roots` — `Path('.').parent == Path('.')`, which never matches a
   configured (always-absolute) root, so corrupted rows vanish from every `--global` listing.
   Worse: for `--global --limit N --order-by {opened,active}`, the DB-side `ORDER BY ... LIMIT N`
   runs *before* that root filter. Since the 8 corrupted rows are (by definition of the bug) among
   the most-recently-touched rows in the whole table, they occupy slots in the top-N window,
   which then get discarded — so fewer than N rows survive, and the true Nth-most-recent in-root
   row never gets fetched at all. This is what made `ccl --limit 20 --order-by active --global`
   return only 12 results, bottoming out 10 days stale.
3. Nothing on disk is lost — `claude --resume` reads transcripts directly, and `ccr`'s on-disk
   fallback (`find_orphan_transcripts`) still finds these sessions. Only `sessions.db`-backed
   `--global` listings are affected.
4. **Every rejection/omission must be visibly logged**, not just silently corrected — this was
   explicitly requested after the first review pass: a silent no-op is exactly what let the
   original bug go unnoticed for 10 days. Tasks 1 and 3 below both add stderr diagnostics for
   this reason, not just Task 2's hook-level messages.

## Version note

Corrected twice during planning. First pass wrongly assumed `1.4.1` (what the local `main`
checkout's `pyproject.toml` showed) was current. It wasn't: the local clone was 10 commits behind
`origin/main` (see Task 0) — `origin/main` is already at `2.1.1` (confirmed via `git fetch` +
`git show origin/main:pyproject.toml`, matching the real published PyPI version and `ccst
--version`'s actual output). There is a single version for the whole `cc-session-tools` package
(`ccd`/`ccr`/`ccs`/`ccst`/`ccmsg`/`ccsched` all share it; `ccst` does not carry its own
independent version number).

Per this repo's documented policy (`.claude/CLAUDE.md`), a **major** bump is reserved for changes
that make existing on-disk data unreadable by old code until a migration runs — not the case here
(Task 6's repair is non-destructive and optional; nothing about the on-disk format changes). This
work adds a new CLI command (`ccst repair sessions`) and a new doctor check, which is a **minor**
bump from the real current `2.1.1`: **`2.2.0`**.

(The unrelated `docs/superpowers/plans/2026-08-02-multi-laptop-sync-00-overview.md` plan set
still says "Current: 1.4.1 ... target 1.5.0" — it's stale for the same reason this plan initially
was, and will need re-checking against the real version before it lands. Not this plan's problem
to fix, flagging only so nobody double-books a version number off that doc's stale claim.)

## File structure

| File | Change |
|---|---|
| `src/cc_session_tools/lib/sessions_db.py` | Reject + **log** non-absolute `project_dir` in the 3 writers |
| `src/cccs_hooks/after_response.py` | Skip + log when `CLD_SESSION_DIR` isn't absolute |
| `src/cccs_hooks/session_tag.py` | Same guard, mirrored |
| `src/cc_session_tools/cli/ccs.py` | Fix limit-before-filter in `_collect_session_rows` + **log** when corrupted rows are present |
| `src/cc_session_tools/lib/sessions_repair.py` | New — resolve/fix non-absolute rows |
| `src/cc_session_tools/lib/doctor.py` | New WARN check wired into `run_all_checks` |
| `src/cc_session_tools/cli/ccst.py` | New top-level `ccst repair sessions` command (parallel to `ccst migrate <store>`) + doctor wiring |
| `CHANGELOG.md`, `pyproject.toml` | Version bump (Task 7) |

Matching test files: `tests/test_sessions_db.py`, `tests/test_after_response.py`,
`tests/test_session_tag.py`, `tests/test_ccs_sentinel_sort.py`, `tests/test_sessions_repair.py`
(new), `tests/test_ccst_doctor.py`, `tests/test_ccst_repair_sessions_cli.py` (new).

---

### Task 0: Sync with origin/main, then worktree setup

**Discovered during planning:** the local `main` checkout this plan was built against was 10
commits behind `origin/main` (missing PRs #85/#87/#88/#89, including two version bumps to
`2.0.0` and `2.1.0` — real current published version is `2.1.1`, not the `1.4.1` this plan
initially assumed). Every file/line-number reference in this plan was verified against that
stale snapshot. Sync first so the worktree — and every "around line N" reference below — is
built against what's actually on `origin/main` today; re-check any line number that has drifted
before editing.

- [x] **Step 1: Fast-forward local `main`**

```bash
cd <repo root>
git fetch origin main
git merge-base --is-ancestor HEAD origin/main && git merge --ff-only origin/main
git log --oneline -1   # confirm HEAD now matches origin/main
```

Adapted at execution time: this repo's `enforce-git-branch-policy.sh` PreToolUse hook blocks any
mutating git command (including `merge --ff-only`) while on `main`, and its `CCCS_ALLOW_MAIN=1`
bypass is read from the hook process's own environment — not from an env var prefix inside the
Bash tool's command string — so it cannot be set from within a single tool call. Local `main` was
therefore left untouched; the worktree below was branched directly off `origin/main` instead,
which achieves the same "built on current origin/main" goal without touching the protected branch.

- [x] **Step 2: Create the worktree**

```bash
git worktree add .worktrees/fix-ccl-outdated-listings -b f/20260812-fix-ccl-outdated-listings origin/main
cd .worktrees/fix-ccl-outdated-listings
uv sync --extra dev
```

Done — worktree created at `origin/main`'s tip (`a03d602`, `pyproject.toml` reads `2.1.1`,
confirming the plan's version-note baseline), dev deps synced successfully.

---

### Task 1: Harden `sessions_db.py`'s write boundary — reject AND log non-absolute `project_dir`

**Status: ✅ Complete.** Implemented, spec-reviewed (compliant), code-quality-reviewed (Approved
after fixing one Important + two Minor issues — docstring narrated a dated incident instead of
standing rationale; `source` param inconsistently positional/keyword-only; two rejection tests had
thinner assertions than the third). Final commit: `f1011df6f1a39d99e9b475460df13cbc33ab10ed` on
branch `f/20260812-fix-ccl-outdated-listings`.

**Files:**
- Modify: `src/cc_session_tools/lib/sessions_db.py:159-256` (the 3 writer functions + imports)
- Test: `tests/test_sessions_db.py`

- [ ] **Step 1: Write the failing tests**

Add near `test_ensure_session_row_rejects_non_session_basename` (around line 109):

```python
def test_ensure_session_row_rejects_relative_project_dir_and_logs(db_path, capsys):
    sessions_db.ensure_session_row(Path("."), "20260713-my-feature", path=db_path)
    assert sessions_db.list_sessions(path=db_path) == []
    err = capsys.readouterr().err
    assert "[sessions-db]" in err
    assert "20260713-my-feature" in err
    assert "not absolute" in err
    assert "ccst repair sessions" in err


def test_touch_last_opened_rejects_relative_project_dir_and_logs(db_path, capsys):
    sessions_db.touch_last_opened(Path("cc-sessions/x"), "20260713-foo", path=db_path)
    assert sessions_db.list_sessions(path=db_path) == []
    assert "[sessions-db]" in capsys.readouterr().err


def test_touch_last_active_rejects_relative_project_dir_and_logs(db_path, capsys):
    sessions_db.touch_last_active(Path("cc-sessions/x"), "20260713-foo", path=db_path)
    assert sessions_db.list_sessions(path=db_path) == []
    assert "[sessions-db]" in capsys.readouterr().err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sessions_db.py -k relative_project_dir -v`
Expected: FAIL (rows get written, and/or no stderr output)

- [ ] **Step 3: Add the guard + visible diagnostic**

In `src/cc_session_tools/lib/sessions_db.py`, add `import sys` to the existing import block
(alongside `import os`, `import sqlite3`, `import time`), then add a module-level helper right
before `ensure_session_row` (around line 158):

```python
def _reject_non_absolute_project_dir(project_dir: Path, basename: str, source: str) -> None:
    """Print a clear, unmissable diagnostic to stderr — visible to a human reading the
    terminal AND to Claude Code, which surfaces hook stderr as tool/hook output. A silent
    reject here is exactly what let the 2026-08-12 ccl-outdated-listings bug run for 10 days
    before anyone noticed: this row is now permanently invisible to `ccl`/`ccs --global`
    until 'ccst repair sessions' fixes it, so every rejection must say so out loud."""
    print(
        f"[sessions-db] REJECTED write from {source}: project_dir={str(project_dir)!r} for "
        f"session {basename!r} is not absolute. This session will be MISSING from `ccl`/"
        "`ccs --global` listings until fixed — run 'ccst repair sessions --dry-run' to see "
        "it, then --execute to fix.",
        file=sys.stderr,
    )


def _is_valid_project_dir(project_dir: Path, basename: str, *, source: str) -> bool:
    if project_dir.is_absolute():
        return True
    _reject_non_absolute_project_dir(project_dir, basename, source)
    return False
```

Then in each of `ensure_session_row`, `touch_last_opened`, `touch_last_active`, immediately after
the existing `if start_date is None: return` guard, add (using each function's own name as the
`source` label):

```python
    if not _is_valid_project_dir(project_dir, basename, source="ensure_session_row"):
        return
```

(`source="touch_last_opened"` / `source="touch_last_active"` in the other two functions
respectively.) Update each function's docstring with one added sentence: "Also a no-op — with a
loud stderr diagnostic — when `project_dir` is not absolute; see `_is_valid_project_dir`."

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sessions_db.py -v`
Expected: PASS (full file, to confirm no regression on the existing basename-guard tests)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/sessions_db.py tests/test_sessions_db.py
git commit -m "fix(sessions-db): reject non-absolute project_dir at the write boundary, loudly

A relative CLD_SESSION_DIR silently produced project_dir='.' rows that were
permanently invisible to ccl/ccs --global for 10 days before anyone noticed.
sessions.db is the single write chokepoint for every caller (hooks, ccd,
migration scripts) — validating here closes the vulnerability regardless of
how a relative Path leaks in upstream, and now prints a clear stderr
diagnostic (visible to both the user and Claude Code) every time it fires,
so this can never again fail silently."
```

---

### Task 2: Guard the two hooks that derive `project_dir` from `CLD_SESSION_DIR`

**Status: ✅ Complete.** Implemented, spec-reviewed (compliant — including verifying the
`session_tag.py` test's deviation from the plan's placeholder, asserting `list_sessions() == []`
rather than file-non-existence, was correct given `write_tag()`'s unconditional write), code-
quality-reviewed (Approved, no blocking issues — two minor notes left as-is: pre-existing
shape-check duplication between the two hooks, and Task 1's backstop guard being now unreachable
from these two call sites by design). Commit: `64ffa3665373817f0acd4bedc2707615149da526` on
branch `f/20260812-fix-ccl-outdated-listings`.

**Visibility note (binding on this task):** a Claude Code hook that exits 0 has its **stderr
discarded** unless the user is running `claude --debug`/`/debug` — confirmed against Claude
Code's hook contract during planning. A plain `print(..., file=sys.stderr)` here would NOT be
visible to the user or to Claude, contradicting the actual requirement. The two hook-visibility
mechanisms that work are hook-event-specific:
- **SessionStart** (`session_tag.py`) uniquely supports both `hookSpecificOutput.additionalContext`
  (fed into Claude's own context) and `systemMessage` (shown to the user in the transcript) on
  the SAME JSON stdout blob — use both.
- **Stop** (`after_response.py`) does not support `additionalContext` at all (no context
  injection back to Claude from a Stop hook); `systemMessage` on stdout JSON is the only
  reliable user-visible channel. There is no mechanism to make a Stop hook's own diagnostic
  visible to Claude at fire time — be upfront about that limitation rather than overclaiming.

This does NOT apply to Task 1 (`sessions_db.py`) or Task 3 (`ccs.py`): neither is a Claude Code
hook. `sessions_db.py`'s stderr print is correct as-is for its actual callers (`ccd.py`, the
migrate scripts) — when Claude or the user runs a plain CLI, its stderr is always visible
directly, no hook-contract caveat applies. Same for `ccs.py`'s new warning in Task 3 — it only
ever runs as an ordinary CLI invocation. Pre-existing stderr-only error logging already in these
two hooks (their `except (OSError, sqlite3.Error)` branches) has the same invisibility gap, but
changing that is out of scope here — it predates this plan and isn't part of the reported bug.

**Files:**
- Modify: `src/cccs_hooks/after_response.py:19-28`
- Modify: `src/cccs_hooks/session_tag.py:103-119` (also touches the final JSON-emitting block)
- Test: `tests/test_after_response.py`, `tests/test_session_tag.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_after_response.py`, after `test_last_active_not_recorded_when_dir_not_shaped_like_cc_sessions`
(add `import json` to the file's imports if not already present):

```python
def test_relative_cld_session_dir_is_skipped_with_visible_system_message(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """CLD_SESSION_DIR shaped like the bare 'cc-sessions/<basename>' relative form (the
    2026-08-12 ccl-outdated-listings incident) must never reach sessions_db — skip the write
    and surface a visible warning via the hook's systemMessage JSON field. Plain stderr is
    discarded for an exit-0 Claude Code hook outside --debug mode; systemMessage is the only
    mechanism that reliably reaches the user for a Stop hook (see Task 2's visibility note)."""
    monkeypatch.setenv("CLD_SESSION_DIR", "cc-sessions/20260812-fix-ccst")
    db_dir = tmp_path / "db"
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(db_dir))

    rc = after_response.main()

    assert rc == 0
    assert not (db_dir / "sessions.db").exists()
    payload = json.loads(capsys.readouterr().out)
    assert "not absolute" in payload["systemMessage"]
```

Add the mirrored test to `tests/test_session_tag.py`, after
`test_last_opened_not_recorded_when_dir_not_shaped_like_cc_sessions` (match that test's existing
fixture setup exactly — it also sets `CLD_SESSION_TAG`, required for `session_tag.main()` to do
anything at all, and reads its payload via `sys.stdin`, NOT an argv list — check how
`test_records_tag_in_sessions_db` feeds stdin and match that exactly, not the placeholder shape
below; add `import json` if not already present):

```python
def test_last_opened_not_recorded_when_cld_session_dir_is_relative(tmp_path, monkeypatch, capsys):
    """Mirrors test_after_response.py's equivalent test. SessionStart uniquely supports both
    additionalContext (Claude sees it) and systemMessage (user sees it) — both must carry
    the diagnostic (see Task 2's visibility note)."""
    monkeypatch.setenv("CLD_SESSION_TAG", "fix-ccst")
    monkeypatch.setenv("CLD_SESSION_DIR", "cc-sessions/20260812-fix-ccst")
    db_dir = tmp_path / "db"
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(db_dir))
    # feed stdin exactly as test_records_tag_in_sessions_db does — copy that setup here

    rc = session_tag.main()

    assert rc == 0
    assert not (db_dir / "sessions.db").exists()
    payload = json.loads(capsys.readouterr().out)
    assert "not absolute" in payload["systemMessage"]
    assert "not absolute" in payload["hookSpecificOutput"]["additionalContext"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_after_response.py tests/test_session_tag.py -k relative -v`
Expected: FAIL (no `systemMessage` in stdout yet; possibly also fails because a row *is* written)

- [ ] **Step 3: Add the guard to `after_response.py`**

Add `import json` to the file's imports, then replace lines 19-28 of
`src/cccs_hooks/after_response.py`:

```python
    session_dir_str = os.environ.get("CLD_SESSION_DIR", "")
    if session_dir_str:
        session_dir_path = Path(session_dir_str)
        if session_dir_path.parent.name == "cc-sessions":
            if not session_dir_path.is_absolute():
                msg = (
                    f"[after-response] CLD_SESSION_DIR is not absolute "
                    f"({session_dir_str!r}); skipping sessions.db update to avoid writing "
                    "a corrupt project_dir. Run 'ccst repair sessions --dry-run' if "
                    "ccl/ccs --global later looks incomplete."
                )
                print(json.dumps({"systemMessage": msg}))
            else:
                try:
                    sessions_db.touch_last_active(
                        session_dir_path.parent.parent, session_dir_path.name
                    )
                except (OSError, sqlite3.Error) as exc:
                    print(f"[after-response] Failed to record .last-active: {exc}", file=sys.stderr)
```

(The normal successful-write path stays exactly as before — silent, no stdout — matching this
hook's existing "never warns" design; only the new corrupted-path branch emits `systemMessage`.)

- [ ] **Step 4: Add the mirrored guard to `session_tag.py`**

Replace lines 103-119 of `src/cccs_hooks/session_tag.py` (the `CLD_SESSION_DIR` block through the
final `print(json.dumps(...))` that emits `additionalContext`):

```python
    session_dir_str = os.environ.get("CLD_SESSION_DIR", "")
    dir_rejected_msg: str | None = None
    if session_dir_str:
        session_dir_path = Path(session_dir_str)
        if session_dir_path.parent.name == "cc-sessions":
            if not session_dir_path.is_absolute():
                dir_rejected_msg = (
                    f"CLD_SESSION_DIR is not absolute ({session_dir_str!r}); skipping "
                    "sessions.db update to avoid writing a corrupt project_dir. Run "
                    "'ccst repair sessions --dry-run' if ccl/ccs --global later looks "
                    "incomplete."
                )
            else:
                try:
                    sessions_db.touch_last_opened(
                        session_dir_path.parent.parent, session_dir_path.name
                    )
                except (OSError, sqlite3.Error) as exc:
                    print(f"[session-tag] Failed to record .last-opened: {exc}", file=sys.stderr)

    session_dir = session_dir_str or f"cc-sessions/{date.today():%Y%m%d}-{tag}"
    mode = os.environ.get("CLD_SESSION_MODE", "new")
    context = _additional_context_message(tag, session_dir, mode)
    output: dict[str, object] = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": (
                context if dir_rejected_msg is None
                else f"{context}\n\n[session-tag] {dir_rejected_msg}"
            ),
        }
    }
    if dir_rejected_msg is not None:
        output["systemMessage"] = f"[session-tag] {dir_rejected_msg}"
    print(json.dumps(output))

    return 0
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_after_response.py tests/test_session_tag.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/cccs_hooks/after_response.py src/cccs_hooks/session_tag.py \
        tests/test_after_response.py tests/test_session_tag.py
git commit -m "fix(hooks): skip sessions.db write when CLD_SESSION_DIR is not absolute

Both hooks derived project_dir via Path(CLD_SESSION_DIR).parent.parent with no
absoluteness check, so a relative CLD_SESSION_DIR silently wrote project_dir='.'
into sessions.db. Skip the write instead — sessions_db.py's own guard
(previous commit) is the backstop. Surface the anomaly via each hook's actual
visible-output channel rather than stderr (discarded for an exit-0 Claude Code
hook outside --debug mode): SessionStart's systemMessage + additionalContext
reach both the user and Claude; Stop's systemMessage is the only channel it
has, since Stop hooks don't support context injection back to Claude."
```

---

### Task 3: Fix `ccs.py`'s limit-before-filter for `--global`, and log when rows are omitted

**Status: ✅ Complete.** Implemented, spec-reviewed (compliant). First code-quality pass found a
real regression the plan's spec didn't anticipate: `--limit` had no positivity check, and a
negative value would silently truncate results from the wrong end (SQLite treats a negative
`LIMIT` as unbounded; Python's `list[:N]` slicing drops from the end for negative `N`) instead of
erroring — fixed by validating `args.limit >= 1` at the CLI boundary, mirroring the existing
`--limit`-requires-`--order-by` check; also added a dedicated test isolating the table-exhaustion
loop-exit branch. Re-reviewed and Approved (one pre-existing-pattern mypy note left as non-
blocking — a cross-variable type invariant mypy can't see, matches the plan's exact signature, no
mypy step in this repo's CI). Final commit: `2105fe034031bc35565cbfc85a8e2aad5ceb4cc2` on branch
`f/20260812-fix-ccl-outdated-listings`.

**Files:**
- Modify: `src/cc_session_tools/cli/ccs.py:391-421`
- Test: `tests/test_ccs_sentinel_sort.py`

- [ ] **Step 1: Write the failing tests**

Add a new class to `tests/test_ccs_sentinel_sort.py`, after `TestLimitFlag`:

```python
class TestGlobalLimitRootFilter:
    """Regression test for the 2026-08-12 ccl-outdated-listings bug: --global --limit
    pushed a DB-side LIMIT before the Python-side root filter, so out-of-root rows sitting
    ahead of legitimate ones in last_active order silently starved the result below the
    requested limit."""

    def test_returns_exactly_limit_in_root_rows_not_starved_by_foreign_rows(
        self, fake_home, fake_repos, monkeypatch, capsys
    ):
        from cc_session_tools.lib import sessions_db

        # 8 rows OUTSIDE the configured root, all more recently active than every
        # in-root row below — simulates the corrupted project_dir='.' rows (whose
        # project_dir.parent is never in roots) occupying the top of the LIMIT window.
        foreign = fake_home / "foreign-project"
        (foreign / "cc-sessions").mkdir(parents=True)
        for i in range(8):
            name = f"20260101-foreign-{i:02d}"
            (foreign / "cc-sessions" / name).mkdir()
            sessions_db.ensure_session_row(foreign, name)
            sessions_db.touch_last_active(foreign, name, when=1000.0 + i)

        proj = fake_repos / "myproj"
        (proj / "cc-sessions").mkdir(parents=True)
        for i in range(5):
            name = f"20260101-real-{i:02d}"
            (proj / "cc-sessions" / name).mkdir()
            sessions_db.ensure_session_row(proj, name)
            sessions_db.touch_last_active(proj, name, when=500.0 + i)

        monkeypatch.chdir(fake_repos)
        rc = ccs.main(["--global", "--order-by", "active", "--limit", "5"])
        assert rc == 0

        out = capsys.readouterr().out
        session_lines = [ln for ln in out.splitlines() if "20260101-real-" in ln]
        assert len(session_lines) == 5, (
            f"expected all 5 in-root sessions, got {len(session_lines)} — "
            "the 8 foreign rows starved the LIMIT window"
        )
        assert "foreign" not in out
        # Most-recent-first: real-04 (when=504.0) before real-00 (when=500.0).
        assert "20260101-real-04" in session_lines[0]

    def test_logs_when_non_absolute_rows_are_present_and_omitted(
        self, fake_home, fake_repos, monkeypatch, capsys
    ):
        """Legitimate out-of-root rows (a project simply not under a configured root) are
        NOT flagged — that's normal scoping, not a bug. Only rows with a non-absolute
        (corrupted) project_dir trigger the diagnostic, since those are the ones
        'ccst repair sessions' can actually fix."""
        from cc_session_tools.lib import sessions_db

        db_path = sessions_db.default_db_path if False else None  # placeholder, see below
        conn = sessions_db.connect()
        conn.execute(
            "INSERT INTO sessions (project_dir, basename, start_date, discovered_at, last_active) "
            "VALUES ('.', '20260101-corrupt', '20260101', '2026-01-01T00:00:00Z', 9999.0)"
        )
        conn.commit()
        conn.close()

        proj = fake_repos / "myproj"
        (proj / "cc-sessions" / "20260101-real").mkdir(parents=True)
        sessions_db.ensure_session_row(proj, "20260101-real")

        monkeypatch.chdir(fake_repos)
        rc = ccs.main(["--global"])
        assert rc == 0

        err = capsys.readouterr().err
        assert "ccst repair sessions" in err
        assert "1" in err  # 1 corrupted row
```

(The `db_path = ... if False else None` line in the second test is a placeholder for wherever
this file's fixtures point `sessions_db.connect()` by default under `fake_home` — check
`fake_home`'s fixture body at the top of the file; it sets `CCST_SESSIONS_DIR` via monkeypatch,
so a bare `sessions_db.connect()` with no `path=` already targets the right file. Delete the
placeholder line once confirmed.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ccs_sentinel_sort.py -k "starved or logs_when_non_absolute" -v`
Expected: FAIL (fewer than 5 `real-` lines in the first test; no stderr diagnostic in the second)

- [ ] **Step 3: Implement the fix**

Replace `_collect_session_rows` in `src/cc_session_tools/cli/ccs.py` (lines 391-421) — keep the
existing docstring, append the paragraph below, and change the `do_global` branch:

```python
def _collect_session_rows(
    do_global: bool, *, order_by: str | None = None, limit: int | None = None
) -> list["sessions_db.SessionRow"]:
    """Return sessions.db rows for the search scope. Replaces the old
    O(roots x projects x sessions) filesystem walk (_collect_pairs +
    iter_sessions) with one indexed query.

    order_by/limit push an indexed ORDER BY ... LIMIT into SQL (see
    sessions_db.list_sessions) when order_by is a DB-backed column - this is
    what makes "most recent N" an O(log n) lookup, not a fetch-everything-then-
    slice. For --global, the root filter is applied after the DB query — see
    _fetch_global_limited() for how the limit case stays correct despite that,
    and _warn_if_corrupted_rows_present() for the stderr diagnostic printed
    whenever a non-absolute project_dir row is found and omitted.

    `order_by` uses ccs's flag vocabulary ("opened"/"active"); it is mapped
    here to the sessions.db column names ("last_opened"/"last_active").
    """
    db_order_by = _SENTINEL_TO_COLUMN.get(order_by) if order_by else None
    db_limit = limit if db_order_by else None

    if do_global:
        try:
            roots = load_session_roots()
        except RootsConfigError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        if db_limit is None:
            rows = sessions_db.list_sessions(order_by=db_order_by, limit=None)
            _warn_if_corrupted_rows_present(rows)
            return [r for r in rows if r.project_dir.parent in roots]
        return _fetch_global_limited(roots, db_order_by, db_limit)

    cwd = Path.cwd().resolve()
    return sessions_db.list_sessions(
        project_dir=cwd, order_by=db_order_by, limit=db_limit
    )


_GLOBAL_LIMIT_GROWTH_FACTOR = 4


def _warn_if_corrupted_rows_present(rows: list["sessions_db.SessionRow"]) -> None:
    """Print a visible (user- and Claude-Code-visible, via stderr) heads-up when any fetched
    row has a non-absolute project_dir — these are the specific rows `ccst repair sessions`
    can fix, as opposed to ordinary out-of-root rows (a project simply not under a configured
    root), which are normal scoping and not flagged."""
    corrupted = sum(1 for r in rows if not r.project_dir.is_absolute())
    if corrupted:
        print(
            f"ccs: {corrupted} session(s) in sessions.db have an invalid (non-absolute) "
            "project_dir and are missing from this --global listing — run "
            "'ccst repair sessions --dry-run' to see them, then --execute to fix.",
            file=sys.stderr,
        )


def _fetch_global_limited(
    roots: list[Path], db_order_by: str, db_limit: int
) -> list["sessions_db.SessionRow"]:
    """Fetch the db_limit most-recent in-root rows for --global.

    A plain `ORDER BY ... LIMIT db_limit` applied before the root filter can
    starve the result: any out-of-root (or corrupted — see the 2026-08-12
    ccl-outdated-listings incident) row sitting ahead of a legitimate one in
    last_active/last_opened order consumes a LIMIT slot that never gets
    root-filtered back in. Grow the DB-side fetch window geometrically until
    either db_limit in-root rows have survived the filter or the table is
    exhausted (`len(rows) < fetch` signals exhaustion) — this keeps the
    common case (most rows are in-root) at one query while staying correct
    when it isn't. The corrupted-row warning fires once, on the final fetch.
    """
    fetch = db_limit
    while True:
        rows = sessions_db.list_sessions(order_by=db_order_by, limit=fetch)
        filtered = [r for r in rows if r.project_dir.parent in roots]
        if len(filtered) >= db_limit or len(rows) < fetch:
            _warn_if_corrupted_rows_present(rows)
            return filtered[:db_limit]
        fetch *= _GLOBAL_LIMIT_GROWTH_FACTOR
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ccs_sentinel_sort.py -v`
Expected: PASS (full file — confirms `TestLimitFlag` and `TestSessionEnumerationScaling` still
pass unchanged, since neither exercises `--global` with a limit)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/ccs.py tests/test_ccs_sentinel_sort.py
git commit -m "fix(ccs): don't let out-of-root rows starve --global --limit; warn when omitted

_collect_session_rows pushed ORDER BY ... LIMIT N into SQL before the
Python-side root filter ran, so out-of-root/corrupted rows ahead of
legitimate ones in recency order silently reduced the result below N. Grow
the DB-side fetch window until N in-root rows survive or the table is
exhausted, instead of fetching exactly N once. Also print a stderr
diagnostic whenever a non-absolute (corrupted) project_dir row is found and
omitted from a --global listing, pointing at 'ccst repair sessions' — a
silent omission is exactly what let the original bug run for 10 days."
```

---

### Task 4: `sessions_repair` module — resolve and fix non-absolute rows

**Status: ✅ Complete.** Implemented (byte-for-byte matched the plan's prescribed code), spec-
reviewed (compliant, after trimming a dated-incident docstring inherited verbatim from the plan's
own text — a real "comments describe what IS, not what WAS" violation). Code-quality review then
caught a genuine Critical bug the plan didn't anticipate: `repair()`'s conflict detection only
checked resolutions against a pre-batch DB snapshot, not against target keys claimed by OTHER
rows in the SAME batch — two bad rows sharing a basename that resolve to the same on-disk match
would both be applied, and the second `UPDATE` would raise an uncaught `sqlite3.IntegrityError`
that aborted the whole batch. Fixed via a two-pass `Counter`-based check (reports ALL colliding
rows as conflicts, not just whichever is processed second); independently reproduced and verified
fixed on re-review, plus a mixed-batch test added per a follow-up non-blocking suggestion. Final
commit: `f0a48925af4285931a01d667d13c2a2e3124f7d8` on branch `f/20260812-fix-ccl-outdated-listings`.

**Files:**
- Create: `src/cc_session_tools/lib/sessions_repair.py`
- Test: `tests/test_sessions_repair.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sessions_repair.py`:

```python
"""Tests for sessions_repair — resolving/fixing sessions.db rows with a
non-absolute project_dir (the 2026-08-12 ccl-outdated-listings incident)."""
from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib import sessions_db, sessions_repair


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "sessions.db"


def test_find_non_absolute_rows_returns_only_bad_rows(db_path):
    sessions_db.ensure_session_row(Path("/repos/good"), "20260101-good", path=db_path)
    conn = sessions_db.connect(path=db_path)
    conn.execute(
        "INSERT INTO sessions (project_dir, basename, start_date, discovered_at) "
        "VALUES ('.', '20260101-bad', '20260101', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    bad = sessions_repair.find_non_absolute_rows(path=db_path)
    assert [r.basename for r in bad] == ["20260101-bad"]


def test_repair_dry_run_does_not_modify_db(tmp_path, db_path):
    root = tmp_path / "repos"
    proj = root / "myproj"
    (proj / "cc-sessions" / "20260101-bad").mkdir(parents=True)
    conn = sessions_db.connect(path=db_path)
    conn.execute(
        "INSERT INTO sessions (project_dir, basename, start_date, discovered_at) "
        "VALUES ('.', '20260101-bad', '20260101', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    report = sessions_repair.repair([root], path=db_path, dry_run=True)

    assert report.repaired == [("20260101-bad", proj)]
    rows = sessions_db.list_sessions(path=db_path)
    assert rows[0].project_dir == Path(".")  # unchanged


def test_repair_execute_updates_project_dir_and_preserves_timestamps(tmp_path, db_path):
    root = tmp_path / "repos"
    proj = root / "myproj"
    (proj / "cc-sessions" / "20260101-bad").mkdir(parents=True)
    conn = sessions_db.connect(path=db_path)
    conn.execute(
        "INSERT INTO sessions (project_dir, basename, start_date, discovered_at, last_active) "
        "VALUES ('.', '20260101-bad', '20260101', '2026-01-01T00:00:00Z', 12345.0)"
    )
    conn.commit()
    conn.close()

    report = sessions_repair.repair([root], path=db_path, dry_run=False)

    assert report.repaired == [("20260101-bad", proj)]
    rows = sessions_db.list_sessions(path=db_path)
    assert rows[0].project_dir == proj
    assert rows[0].last_active == 12345.0  # preserved, not reset


def test_repair_reports_unresolved_when_no_on_disk_match(tmp_path, db_path):
    root = tmp_path / "repos"
    root.mkdir()
    conn = sessions_db.connect(path=db_path)
    conn.execute(
        "INSERT INTO sessions (project_dir, basename, start_date, discovered_at) "
        "VALUES ('.', '20260101-orphan', '20260101', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    report = sessions_repair.repair([root], path=db_path, dry_run=False)

    assert report.repaired == []
    assert report.unresolved == ["20260101-orphan"]


def test_repair_reports_ambiguous_when_multiple_on_disk_matches(tmp_path, db_path):
    root = tmp_path / "repos"
    (root / "proj-a" / "cc-sessions" / "20260101-dup").mkdir(parents=True)
    (root / "proj-b" / "cc-sessions" / "20260101-dup").mkdir(parents=True)
    conn = sessions_db.connect(path=db_path)
    conn.execute(
        "INSERT INTO sessions (project_dir, basename, start_date, discovered_at) "
        "VALUES ('.', '20260101-dup', '20260101', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    report = sessions_repair.repair([root], path=db_path, dry_run=False)

    assert report.repaired == []
    assert len(report.ambiguous["20260101-dup"]) == 2


def test_repair_no_bad_rows_returns_empty_report(db_path):
    sessions_db.ensure_session_row(Path("/repos/good"), "20260101-good", path=db_path)
    report = sessions_repair.repair([Path("/repos")], path=db_path, dry_run=False)
    assert report.repaired == []
    assert report.unresolved == []
    assert report.ambiguous == {}


def test_repair_reports_conflict_instead_of_crashing_when_target_row_already_exists(
    tmp_path, db_path
):
    """A correct (proj, basename) row can already exist alongside a corrupted ('.', basename)
    row for the SAME basename — e.g. ccd.py's ensure_session_row() wrote the correct row at
    session-creation time, independently of whatever later corrupted a duplicate/stale row for
    the same basename. Resolving the bad row to `proj` and UPDATE-ing it would then violate the
    (project_dir, basename) PRIMARY KEY. This must be reported as a conflict, not raise."""
    root = tmp_path / "repos"
    proj = root / "myproj"
    (proj / "cc-sessions" / "20260101-dup").mkdir(parents=True)
    sessions_db.ensure_session_row(proj, "20260101-dup", path=db_path)  # the correct row
    conn = sessions_db.connect(path=db_path)
    conn.execute(
        "INSERT INTO sessions (project_dir, basename, start_date, discovered_at) "
        "VALUES ('.', '20260101-dup', '20260101', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    report = sessions_repair.repair([root], path=db_path, dry_run=False)

    assert report.repaired == []
    assert report.conflicts == ["20260101-dup"]
    rows = sessions_db.list_sessions(path=db_path)
    assert len(rows) == 2  # both rows still present, untouched
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sessions_repair.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cc_session_tools.lib.sessions_repair'`

- [ ] **Step 3: Implement the module**

Create `src/cc_session_tools/lib/sessions_repair.py`:

```python
"""Repair sessions.db rows whose project_dir was written as a non-absolute Path
(see the 2026-08-12 ccl-outdated-listings incident, and sessions_db.py's write-boundary
guard that now prevents new occurrences). Resolves each affected row's correct
project_dir by locating its on-disk cc-sessions/<basename>/ directory under the
configured roots, then updates the row in place, preserving its timestamps. Never
guesses when a basename's on-disk location is ambiguous (found under >1 root/project)
or missing (found under none) — those are reported, not silently resolved."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from cc_session_tools.lib import sessions_db


@dataclass(slots=True)
class RepairReport:
    """Mutable accumulator, built up in place by repair() — not a frozen value object."""

    repaired: list[tuple[str, Path]] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    ambiguous: dict[str, list[Path]] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)


def find_non_absolute_rows(*, path: Path | None = None) -> list[sessions_db.SessionRow]:
    """Every sessions.db row whose project_dir is not an absolute path."""
    return [r for r in sessions_db.list_sessions(path=path) if not r.project_dir.is_absolute()]


def _resolve_on_disk(basename: str, roots: list[Path]) -> list[Path]:
    """Every project directly under a configured root whose cc-sessions/<basename>/
    exists on disk — the set of plausible correct project_dir values for this row."""
    matches: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for proj in root.iterdir():
            if proj.is_dir() and (proj / "cc-sessions" / basename).is_dir():
                matches.append(proj)
    return matches


def repair(
    roots: list[Path], *, path: Path | None = None, dry_run: bool = True
) -> RepairReport:
    """Resolve and (unless dry_run) fix every non-absolute project_dir row.

    dry_run=True (the default) only computes and returns the report. dry_run=False
    updates sessions.db in place — one UPDATE per uniquely-resolved row, leaving
    start_date/last_opened/last_active/discovered_at untouched. Callers that want a
    safety copy first should back up the DB file (e.g. via db.backup_to()) before
    calling with dry_run=False; this function does not.

    A resolved row is never blindly UPDATEd: (project_dir, basename) is the table's
    PRIMARY KEY, so if a correct row for (resolved_dir, basename) already exists —
    e.g. ccd.py's ensure_session_row() wrote it independently at session-creation
    time — writing the corrupted row on top would violate that key. Such rows are
    reported as `conflicts` and left untouched rather than raising mid-batch.
    """
    bad_rows = find_non_absolute_rows(path=path)
    report = RepairReport()
    if not bad_rows:
        return report

    resolutions: list[tuple[sessions_db.SessionRow, Path]] = []
    for row in bad_rows:
        candidates = _resolve_on_disk(row.basename, roots)
        if len(candidates) == 1:
            resolutions.append((row, candidates[0]))
        elif len(candidates) == 0:
            report.unresolved.append(row.basename)
        else:
            report.ambiguous[row.basename] = candidates

    if not resolutions:
        return report

    # Split out any resolution whose target (new_dir, basename) already has a row —
    # applying it would violate the PRIMARY KEY. Check before writing anything, not
    # via try/except around the UPDATE, so one conflict can't abort sibling rows'
    # otherwise-valid updates.
    existing_keys = {
        (r.project_dir, r.basename) for r in sessions_db.list_sessions(path=path)
    }
    applyable: list[tuple[sessions_db.SessionRow, Path]] = []
    for row, new_dir in resolutions:
        if (new_dir, row.basename) in existing_keys:
            report.conflicts.append(row.basename)
        else:
            applyable.append((row, new_dir))
            report.repaired.append((row.basename, new_dir))

    if dry_run or not applyable:
        return report

    conn = sessions_db.connect(path=path)
    try:
        for row, new_dir in applyable:
            conn.execute(
                "UPDATE sessions SET project_dir = ? WHERE project_dir = ? AND basename = ?",
                (str(new_dir), str(row.project_dir), row.basename),
            )
        conn.commit()
    finally:
        conn.close()
    return report
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sessions_repair.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/sessions_repair.py tests/test_sessions_repair.py
git commit -m "feat(sessions-repair): resolve and fix non-absolute project_dir rows

Locates the correct project_dir for a corrupted row by finding its on-disk
cc-sessions/<basename>/ under the configured roots. Reports (never guesses)
when a basename is unresolvable, ambiguous, or would conflict with an
already-correct row for the same (project_dir, basename) primary key.
Dry-run by default at the caller layer — this module itself just takes
dry_run as a plain bool."
```

---

### Task 5: `ccst doctor` check + new top-level `ccst repair sessions` command

**Status: ✅ Complete.** Implemented, spec-reviewed (compliant). The implementer's own self-review
caught a real bug before formal review: every diagnostic shipped in Tasks 1-3 tells users to run
`ccst repair sessions --dry-run`, but the CLI as first built only had `--execute` (dry-run was an
implicit default, no explicit flag) — that documented command would have errored. Fixed by adding
an explicit `--dry-run` flag, mutually exclusive with `--execute`. Code-quality review then found
two further Important issues: the backup-verification test only checked the backup file existed,
not that it held genuine pre-repair content (now opens it and asserts the unrepaired value);  and
the commit message inaccurately claimed this matches `_cmd_sessions_migrate`'s backup convention —
that command actually backs up pre-migration *source* flat files, never `sessions.db` itself, and
only after migration succeeds. `_cmd_repair_sessions` is genuinely the first direct
`sessions.db` backup-before-write in the codebase; the message was corrected. Also added: an
early guard so `--execute` against a nonexistent `sessions.db` path fails loudly instead of
`sqlite3.connect()` silently auto-creating an empty file to back up. Re-reviewed and Approved, no
outstanding issues. Final commit: `2a6fbfee5334476886c1f4bfbd79df4bcea7915a` on branch
`f/20260812-fix-ccl-outdated-listings`.

`ccst` already has a top-level `ccst migrate <store>` family (`ccst migrate ccsched`, `ccst
migrate ccmsg`, `ccst migrate telemetry`, `ccst migrate all`) for one-shot store migrations. This
task adds a parallel top-level `ccst repair <store>` family, starting with `ccst repair sessions`
— chosen over nesting under the existing `ccst sessions <verb>` group specifically so future
repairs for other stores (`ccst repair ccmsg`, etc.) have an obvious, discoverable home.

**Files:**
- Modify: `src/cc_session_tools/lib/doctor.py` (add check function + wire into `run_all_checks`)
- Modify: `src/cc_session_tools/cli/ccst.py` (new top-level `repair` command, doctor wiring)
- Test: `tests/test_ccst_doctor.py`, `tests/test_ccst_repair_sessions_cli.py` (new)

- [ ] **Step 1: Write the failing doctor-check test**

Match this file's existing import style for `check_pdata_verify`'s tests (direct-name imports,
not a module-prefixed `doctor.` reference):

```python
def test_check_sessions_project_dir_absolute_warns_on_bad_row(tmp_path):
    from cc_session_tools.lib import sessions_db
    from cc_session_tools.lib.doctor import Status, check_sessions_project_dir_absolute

    db_path = tmp_path / "sessions.db"
    conn = sessions_db.connect(path=db_path)
    conn.execute(
        "INSERT INTO sessions (project_dir, basename, start_date, discovered_at) "
        "VALUES ('.', '20260101-bad', '20260101', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    results = check_sessions_project_dir_absolute(db_path)
    assert len(results) == 1
    assert results[0].status == Status.WARN
    assert "1 " in results[0].reason
    assert "ccst repair sessions" in results[0].reason


def test_check_sessions_project_dir_absolute_ok_when_clean(tmp_path):
    from cc_session_tools.lib import sessions_db
    from cc_session_tools.lib.doctor import Status, check_sessions_project_dir_absolute

    db_path = tmp_path / "sessions.db"
    sessions_db.ensure_session_row(tmp_path / "repos" / "proj", "20260101-good", path=db_path)

    results = check_sessions_project_dir_absolute(db_path)
    assert results[0].status == Status.OK
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ccst_doctor.py -k project_dir_absolute -v`
Expected: FAIL with `AttributeError`/`ImportError` — `check_sessions_project_dir_absolute` doesn't
exist yet

- [ ] **Step 3: Add the check to `doctor.py`**

Add after `check_pdata_verify` (around line 597 of `src/cc_session_tools/lib/doctor.py`):

```python
def check_sessions_project_dir_absolute(sessions_db_path: Path) -> list[CheckResult]:
    """WARN if any sessions.db row has a non-absolute project_dir. Every reader that
    scopes --global by root (ccs.py, sessions.find_matching_sessions) compares
    project_dir.parent against a resolved, absolute root — a relative project_dir can
    never match, so the row becomes permanently invisible to --global listings without
    ever raising an error (the 2026-08-12 ccl-outdated-listings incident). Not FAIL: it
    degrades --global listings but does not block core functionality, and
    `ccst repair sessions` fixes it non-destructively."""
    from cc_session_tools.lib import sessions_repair

    bad = sessions_repair.find_non_absolute_rows(path=sessions_db_path)
    if not bad:
        return [CheckResult(
            name="sessions:project-dir-absolute", status=Status.OK,
            reason="all sessions.db rows have an absolute project_dir",
        )]
    return [CheckResult(
        name="sessions:project-dir-absolute", status=Status.WARN,
        reason=(
            f"{len(bad)} sessions.db row(s) have a non-absolute project_dir and are "
            "invisible to `ccl`/`ccs --global` — run 'ccst repair sessions --dry-run' "
            "to see them, then --execute to fix"
        ),
    )]
```

Wire it into `run_all_checks` (around line 603-718): add a `sessions_db_path: Path | None = None`
parameter to the signature, and near the `pdata_verify_projects` block add:

```python
    # Non-absolute project_dir rows in sessions.db (spec: 2026-08-12 ccl-outdated-listings fix)
    if sessions_db_path is not None:
        results.extend(check_sessions_project_dir_absolute(sessions_db_path))
```

- [ ] **Step 4: Wire the doctor CLI caller**

In `src/cc_session_tools/cli/ccst.py`'s `_cmd_doctor` (starts at line 580), `store_paths["sessions"]`
(built around line 654, via the existing `from cc_session_tools.lib.sessions_db import
default_db_path as sessions_db_path` alias at line 646) is already the exact value needed — reuse
it rather than importing `sessions_db` fresh and calling `default_db_path()` a second time. Find
the `run_all_checks(` call (around line 678) and add:

```python
        sessions_db_path=store_paths["sessions"],
```

as a new keyword argument. No new import needed.

- [ ] **Step 5: Run doctor tests to verify pass**

Run: `uv run pytest tests/test_ccst_doctor.py -v`
Expected: PASS

- [ ] **Step 6: Write the failing CLI test for `ccst repair sessions`**

Create `tests/test_ccst_repair_sessions_cli.py`, matching `tests/test_ccst_pdata_verify_cli.py`'s
real-subprocess `base_env`/`_run` convention. Read that file's `base_env` fixture first and copy
its shape — it is a flat `os.environ.copy()` dict setting only `CCST_PROJECT_DB_DIR` /
`CCST_PROJECTS_ROOT`, redirected into `tmp_path`. It does **not** set `CCST_SESSIONS_DIR`, and it
does **not** set `CLAUDE_SESSION_TOOLS_REPO_ROOT`/`_PROJ_ROOT` — both are needed here on top of
the copied fixture: `CCST_SESSIONS_DIR` (so the subprocess reads/writes the test's `sessions.db`)
and one of the two roots vars (so `load_session_roots()` inside `ccst repair sessions` doesn't
raise `RootsConfigError`), pointed at the same `tmp_path` root the seeded on-disk `cc-sessions/`
directories live under:

```python
"""CLI tests for `ccst repair sessions` — see tests/test_ccst_pdata_verify_cli.py for the
base_env/_run convention this mirrors, extended with CCST_SESSIONS_DIR and a roots env var."""
from __future__ import annotations

import sqlite3

import pytest

# Copy base_env/_run from test_ccst_pdata_verify_cli.py verbatim, then extend base_env to
# also set CCST_SESSIONS_DIR=<tmp_path>/sessions-dir and
# CLAUDE_SESSION_TOOLS_REPO_ROOT=<tmp_path>/repos (creating that repos dir).


def _seed_bad_row(sessions_db_path, repos_root):
    (repos_root / "myproj" / "cc-sessions" / "20260101-bad").mkdir(parents=True)
    sessions_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(sessions_db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sessions (project_dir TEXT NOT NULL, basename TEXT NOT NULL, "
        "start_date TEXT NOT NULL, last_opened REAL, last_active REAL, discovered_at TEXT NOT NULL, "
        "PRIMARY KEY (project_dir, basename))"
    )
    conn.execute(
        "INSERT INTO sessions (project_dir, basename, start_date, discovered_at) "
        "VALUES ('.', '20260101-bad', '20260101', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()


def test_repair_dry_run_reports_without_modifying(base_env):
    # seed via _seed_bad_row using base_env's sessions-dir/repos-root, run
    # `ccst repair sessions --dry-run` via _run, assert the report mentions
    # 20260101-bad and the row is still project_dir='.' afterwards.
    ...


def test_repair_execute_updates_row_and_backs_up_first(base_env):
    # same seed, run `ccst repair sessions --execute`, assert the row's
    # project_dir is now the resolved absolute path AND a backup file exists
    # under sessions_db_path.parent / "repair-backups" /.
    ...
```

(The two test bodies are intentionally left as a documented TODO shape, per the "Execution note"
at the end of this plan — fill them in for real once `base_env` is copied and extended.)

- [ ] **Step 7: Add the top-level `ccst repair` command**

In `src/cc_session_tools/cli/ccst.py`, mirror the existing `migrate_parser`/`migrate_sub` block
(around line 1985) — add this as a new top-level command, sibling to `migrate`, not nested under
the existing `sessions` noun's subparser:

```python
    repair_parser = sub.add_parser("repair", help="Repair known sessions.db/store corruption")
    repair_sub = repair_parser.add_subparsers(dest="verb", metavar="<verb>")
    repair_sub.required = True
    r_sessions = repair_sub.add_parser(
        "sessions",
        help=(
            "Fix sessions.db rows whose project_dir is not an absolute path (invisible "
            "to `ccl`/`ccs --global`) by re-resolving them from on-disk cc-sessions/ "
            "directories. Dry-run by default."
        ),
    )
    r_sessions.add_argument(
        "--execute", action="store_true",
        help="Apply the repair (default: dry-run, print what would change).",
    )
    r_sessions.add_argument(
        "--sessions-db", default=None, metavar="PATH",
        help="sessions.db path override (default: from CCST_SESSIONS_DIR)",
    )
```

Add the handler near `_cmd_sessions_list` (around line 1233):

```python
def _cmd_repair_sessions(args: argparse.Namespace) -> int:
    from cc_session_tools.lib import db as db_lib
    from cc_session_tools.lib import sessions_db, sessions_repair
    from cc_session_tools.lib.roots import RootsConfigError, load_session_roots

    db_path = Path(args.sessions_db) if args.sessions_db else sessions_db.default_db_path()
    try:
        roots = load_session_roots()
    except RootsConfigError as e:
        print(str(e), file=sys.stderr)
        return 1

    if args.execute:
        backup_dir = sessions_db.default_db_path().parent / "repair-backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = backup_dir / f"sessions-{stamp}.db"
        db_lib.backup_to(db_path, backup_path)
        print(f"Backed up sessions.db to {backup_path}")

    report = sessions_repair.repair(roots, path=db_path, dry_run=not args.execute)
    if not any((report.repaired, report.unresolved, report.ambiguous, report.conflicts)):
        print("No non-absolute project_dir rows found in sessions.db.")
        return 0

    verb = "fixed" if args.execute else "would fix"
    for basename, new_dir in report.repaired:
        print(f"  {verb}: {basename} -> {new_dir}")
    for basename in report.unresolved:
        print(
            f"  UNRESOLVED (no on-disk cc-sessions/{basename} under any root): {basename}",
            file=sys.stderr,
        )
    for basename, candidates in report.ambiguous.items():
        print(f"  AMBIGUOUS ({len(candidates)} candidates, skipped): {basename}", file=sys.stderr)
    for basename in report.conflicts:
        print(
            f"  CONFLICT (a correct row for this basename already exists, skipped): {basename}",
            file=sys.stderr,
        )

    if not args.execute and report.repaired:
        print(f"\n{len(report.repaired)} row(s) would be repaired. Re-run with --execute to apply.")
    return 1 if (report.unresolved or report.ambiguous or report.conflicts) else 0
```

Wire dispatch — find where `args.noun == "migrate"` is dispatched (around line 2139) and add a
sibling block, matching that block's shape exactly:

```python
    if args.noun == "repair":
        if args.verb == "sessions":
            sys.exit(_cmd_repair_sessions(args))
```

- [ ] **Step 8: Finish the CLI tests from Step 6 and run everything**

Fill in the two `test_ccst_repair_sessions_cli.py` bodies for real using the copied+extended
`base_env` fixture, then:

Run: `uv run pytest tests/test_ccst_doctor.py tests/test_ccst_repair_sessions_cli.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add src/cc_session_tools/lib/doctor.py src/cc_session_tools/cli/ccst.py \
        tests/test_ccst_doctor.py tests/test_ccst_repair_sessions_cli.py
git commit -m "feat(ccst): add top-level 'repair sessions' command + doctor tripwire

ccst doctor now WARNs when sessions.db has rows invisible to --global listings
(the 2026-08-12 ccl-outdated-listings failure mode), pointing at the new
'ccst repair sessions' command — a new top-level 'repair' command family,
parallel to the existing 'migrate' family, so future repairs for other
stores have an obvious home. repair --execute backs up sessions.db via
db.backup_to() before writing, matching the existing migration-backup
convention in _cmd_sessions_migrate."
```

---

### Task 6: Repair Chris's live `sessions.db` (manual, confirm-first — not part of automated execution)

This mutates Chris's real, in-use `~/.local/share/claude/sessions.db`, not a test fixture. Do not
run this automatically as part of plan execution — surface it back to Chris once Tasks 1-5 are
merged/installed, and run it only with his go-ahead. `ccst repair sessions --execute` backs up
`sessions.db` (via `db.backup_to()`) to `~/.local/share/claude/repair-backups/sessions-<UTC
timestamp>.db` *before* writing anything, per Task 5 Step 7 — confirm that backup file exists
before treating `--execute` as done:

```bash
uv tool install --reinstall ~/repos/claude-code-session-tools   # after merge, per repo CLAUDE.md
ccst repair sessions --dry-run     # confirm it resolves all 9 known-bad rows, none ambiguous/unresolved/conflicting
ccst repair sessions --execute     # backs itself up first; then apply
ls -la ~/.local/share/claude/repair-backups/   # confirm the backup file landed
ccst doctor | grep sessions        # confirm the check now reads OK
ccl --order-by active --global --limit 20   # confirm the original symptom is gone
```

- [ ] **Step 1: Run the five commands above once Tasks 1-5 are installed, after Chris confirms.**

---

### Task 7: Version bump + changelog

**Status: ✅ Complete.** Baseline confirmed at `2.1.1` (matching the plan's Task 0 sync), bumped to
`2.2.0`, CHANGELOG entry added, full suite green (exit 0), committed directly (mechanical,
low-judgment — done inline rather than via a dispatched subagent). Commit: `1f22f30` on branch
`f/20260812-fix-ccl-outdated-listings`.

**Files:**
- Modify: `pyproject.toml`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Check the current version is still the expected baseline**

```bash
git fetch origin main
grep '^version' pyproject.toml   # only meaningful after Task 0's fast-forward — should read 2.1.1
```

If it reads anything other than `2.1.1`, another release landed on `main` after this plan was
written — bump to the next free minor above whatever it actually shows, not blindly `2.2.0`.

- [ ] **Step 2: Bump `pyproject.toml`**

```toml
version = "2.2.0"
```

- [ ] **Step 3: Add the CHANGELOG entry**

In `CHANGELOG.md`, replace the empty `## [Unreleased]` section with:

```markdown
## [Unreleased]

## [2.2.0] - 2026-08-12

### Fixed

- **`ccl`/`ccs --global` silently dropped and under-counted recent sessions.** A relative
  `CLD_SESSION_DIR` could cause `sessions.db` writers to store `project_dir='.'`, which the
  `--global` root filter then silently excluded — and for `--global --limit N`, those excluded
  rows occupied slots in the DB-side `LIMIT N` window ahead of legitimate rows, so fewer than `N`
  results came back even when `N` valid ones existed. `sessions_db.py`'s three writers now reject
  non-absolute `project_dir` at the write boundary, printing a clear stderr diagnostic every time
  instead of failing silently; the `session-tag`/`after-response` hooks skip the write and
  surface the anomaly via their actual visible-output channel (SessionStart's `systemMessage` +
  `additionalContext`, Stop's `systemMessage`) rather than stderr, which a Claude Code hook
  discards on exit 0 outside `--debug` mode; and `ccs.py`'s `--global --limit` path grows its
  DB-side fetch window instead of fetching exactly `N` once, and now warns on stderr whenever a
  corrupted row is found and omitted from a listing.

### Added

- `ccst repair sessions [--dry-run|--execute]` — a new top-level `repair` command family
  (parallel to the existing `migrate` family) — resolves and fixes sessions.db rows with a
  non-absolute `project_dir` by locating their on-disk `cc-sessions/` directory. Backs up
  `sessions.db` via `db.backup_to()` before `--execute` writes.
- `ccst doctor` now WARNs when any `sessions.db` row has a non-absolute `project_dir`.
```

- [ ] **Step 4: Run the full test suite**

```bash
uv run pytest -q
```

Expected: all pass, 0 failures.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "chore(release): bump to 2.2.0 for ccl-outdated-listings fix"
```

---

### Task 8: Surface corrupted-row misses in `ccr` (added after final whole-branch review)

**Status: ✅ Complete.** Implemented, spec-reviewed (compliant). Code-quality review found three
Important issues: a coexistence gap (the check only ran on a total miss, so a corrupted row
sharing a fragment with a real, resumable session was never surfaced — fixed by making the check
run unconditionally whenever a corrupted row matches the fragment, with a new test proving both
the real session still resumes AND the warning still prints); a duplicated absoluteness predicate
that should reuse `sessions_repair.find_non_absolute_rows()` instead of re-deriving it inline
(fixed); and a missing CHANGELOG bullet for this diagnostic (added). Re-reviewed and Approved, no
outstanding issues. Final commit: `0c8aa730fbe2813f707bb677bad7f0276c5c67ff` on branch
`f/20260812-fix-ccl-outdated-listings`.

**Discovered during the post-Task-7 final review**, not in the original plan: the Diagnosis
section's point 3 claimed *"`ccr`'s on-disk fallback (`find_orphan_transcripts`) still finds these
sessions"* for a corrupted row. That claim is wrong. `find_orphan_transcripts`
(`src/cc_session_tools/lib/sessions.py:79-129`) explicitly skips any basename whose
`cc-sessions/<name>/` directory already exists on disk (lines 118-122) — but that directory
existing is exactly what defines this corruption (only the DB row is wrong, not the filesystem).
`ccr`'s exact-match fast path (`src/cc_session_tools/cli/ccr.py:97-107`) and
`find_matching_sessions` (`src/cc_session_tools/lib/sessions.py:57-76`) both filter by
`row.project_dir.parent in roots`, identically to the pre-fix `ccs.py` — so a corrupted row is
invisible to every `ccr` lookup path, with `ccr` printing a plain `"no sessions match"` and no
pointer to the fix. This is the same silent-omission failure class Diagnosis point 4 says the
whole plan exists to eliminate — this task closes the last remaining gap.

**Files:**
- Modify: `src/cc_session_tools/cli/ccr.py` (the `if not matches:` block, around line 118-120)
- Test: `tests/test_cli_ccr.py`

- [ ] **Step 1: Write the failing tests**

Add near the end of `tests/test_cli_ccr.py`:

```python
def test_ccr_warns_when_fragment_matches_only_a_corrupted_row(fake_repos, capsys, captured_launch):
    """A row with a non-absolute project_dir is invisible to both the exact-match fast path
    and find_matching_sessions's root filter, and find_orphan_transcripts skips it too (its
    cc-sessions/<name>/ dir exists on disk). Without a diagnostic this is a silent 'not found' -
    point the user at ccst repair sessions instead."""
    from cc_session_tools.lib import sessions_db

    conn = sessions_db.connect()
    conn.execute(
        "INSERT INTO sessions (project_dir, basename, start_date, discovered_at) "
        "VALUES ('.', '20260101-corrupt', '20260101', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    rc = ccr.main(["corrupt"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "no sessions match" in err
    assert "ccst repair sessions" in err
    assert "20260101-corrupt" in err


def test_ccr_no_corrupted_row_warning_for_ordinary_out_of_root_miss(fake_repos, capsys, captured_launch):
    """A session that simply isn't under a configured root (ordinary scoping) must NOT trigger
    the corrupted-row diagnostic - that would be misleading."""
    from pathlib import Path
    from cc_session_tools.lib import sessions_db

    sessions_db.ensure_session_row(Path("/some/other/root/proj"), "20260101-elsewhere")

    rc = ccr.main(["elsewhere"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "no sessions match" in err
    assert "ccst repair sessions" not in err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli_ccr.py -k corrupted -v`
Expected: FAIL (no "ccst repair sessions" text in stderr yet)

- [ ] **Step 3: Add the diagnostic**

Add a helper in `src/cc_session_tools/cli/ccr.py` (near the top, after imports):

```python
def _warn_if_fragment_matches_corrupted_row(fragment: str) -> None:
    """Print a stderr diagnostic when `fragment` matches a sessions.db row whose project_dir
    is non-absolute. Such a row is invisible to every lookup path here — the exact-match fast
    path and find_matching_sessions both filter by root, and find_orphan_transcripts skips it
    because its cc-sessions/<name>/ directory already exists on disk — so without this it reads
    as an ordinary "not found" rather than a fixable data problem. Mirrors ccs.py's
    _warn_if_corrupted_rows_present."""
    from cc_session_tools.lib import sessions_db

    corrupted = [
        row for row in sessions_db.list_sessions()
        if fragment in row.basename and not row.project_dir.is_absolute()
    ]
    if corrupted:
        names = ", ".join(row.basename for row in corrupted)
        print(
            f"ccr: {len(corrupted)} session(s) matching '{fragment}' have an invalid "
            f"(non-absolute) project_dir and can't be resumed by name: {names} — run "
            "'ccst repair sessions --dry-run' to see them, then --execute to fix.",
            file=sys.stderr,
        )
```

Then in `main()`'s `if not matches:` block (around line 118-120):

```python
    if not matches:
        print(f"ccr: no sessions match '{args.fragment}'", file=sys.stderr)
        _warn_if_fragment_matches_corrupted_row(args.fragment)
        return 1
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli_ccr.py -v`
Expected: PASS (full file, no regression)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/ccr.py tests/test_cli_ccr.py
git commit -m "fix(ccr): warn when a name-fragment match is invisible due to a corrupted project_dir

Diagnosis point 3 of this plan claimed ccr's on-disk fallback
(find_orphan_transcripts) still finds a session with a corrupted
project_dir - it doesn't: that fallback explicitly skips any basename whose
cc-sessions/<name>/ directory already exists on disk, which is exactly the
corrupted-row case. ccr's exact-match path and find_matching_sessions both
filter by root the same way the pre-fix ccs.py did, so a corrupted row was
invisible everywhere in ccr with no diagnostic - a plain 'no sessions match'.
Closes the last silent-omission gap this plan set out to eliminate."
```

---

## Execution note on Task 5's Step 6/8

The CLI-test scaffolding in Task 5 Step 6 is intentionally incomplete (marked as a TODO shape) —
it depends on copying `test_ccst_pdata_verify_cli.py`'s exact `base_env` fixture and extending it
with two more env vars, which this plan doesn't reproduce verbatim to avoid drifting from
whatever that fixture looks like by the time this plan executes. Read that file first, copy its
fixture shape, extend it as described in Step 6, then complete Step 6's two test bodies before
writing the CLI code in Step 7 — this task is TDD in spirit even though Step 6 is split into a
stub-then-complete rather than a single write.
