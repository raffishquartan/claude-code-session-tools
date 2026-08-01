# ccst pm-update-central-files — session-output index + skill migration (Plan C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `update-central-files` from `claude-code-config-sync` (CCCS) into
`claude-code-session-tools` (CCST) as `pm-update-central-files` — establishing the `pm-` prefix
for cross-project, project-management-family skills — and give it a new AUTO item that registers
each session's `out/` deliverables into the project's **session-output index**, a `record_group`
on Plan A's exact `ccst pdata` schema/CLI. A `ccsched` job on a 7-day cadence, provisioned
automatically by `ccst install-everything`, reconciles the index against disk as a safety net for
sessions that end without the skill running.

**Architecture:** A new `lib/pdata/session_output.py` module is pure orchestration on top of
Plan A's existing `service.py`/`repository.py` — no schema or CLI-primitive changes to Plan A's
code. It adds one new record_group (`session-output`, one row per indexed `out/` file, with a
`session_tag` extension field) and one singleton-state record_group
(`session-output-watermark`, spec §4.3's named pattern, one row per project) that tracks the
reconciliation job's own incremental cursor — the base schema itself is untouched. A new `ccst
pdata reconcile-session-output` CLI verb drives it; a new `ccst ccsched-jobs install` verb
(backed by a shared `lib/scheduler/bundled_jobs.py` list, so the installer and `ccst doctor`
can never list different jobs) provisions the 7-day job at install time. The skill itself moves
from CCCS to CCST as a plain markdown file — CCST's existing `_discover_skills()`/`ccst skills
install` machinery picks it up automatically, no source change needed for that half.

**Tech Stack:** Python 3.11+, stdlib `sqlite3` (via Plan A's `lib/pdata`), `argparse`, `pytest`
(subprocess CLI tests matching Plan A's `tests/test_ccst_pdata_cli.py`). The CCCS-side half of
this plan is plain-text edits (Markdown/JSON) in a second repository, no Python.

---

## Dependency on Plan A

This plan assumes **Plan A** (`docs/superpowers/plans/2026-07-30-ccst-pdata-core.md`) has already
executed in this same worktree/branch — every task below calls `service.add_record`,
`service.update_record`, `service.list_records`, `service.query_records`,
`service.schema_add_field`, `service.Record`, `service.VersionConflictError`, and
`repository.list_extension_columns`, all of which are Plan A deliverables, not something this
plan defines. If `src/cc_session_tools/lib/pdata/service.py` does not exist yet when you start
Task 1, stop and run Plan A first.

The version-bump task (Task 11) does **not** assume any specific predecessor version. Plan B is
declared an independent sibling of this plan (both depend only on Plan A, neither reads the
other), so whichever of B/C actually lands second will find the version already bumped once by
the other. Task 11 reads whatever version is on disk at the time it actually runs and bumps its
minor component by one from there — if that is `1.1.0` (Plan A only), the result is `1.2.0`; if
Plan B already landed and bumped to `1.2.0`, the result is `1.3.0`. Either way it stays a
**minor** bump — see Versioning below.

## Scope

**In scope** (spec `2026-07-26-per-project-data-store-spec.md` §8 in full, using the standalone
prompt `prompt-update-central-files-session-output-index.md` for the fine-grained requirements
it captured):

- Moving `skills/update-central-files/` from CCCS to CCST, renamed `pm-update-central-files`,
  establishing the `pm-` prefix convention (spec §8, prompt item 2).
- Removing the CCCS copy and every reference to the old name/location in that repo (prompt
  item 4): `skills/update-central-files/` itself, its `README.md` Custom Skills table row, its
  `config/external-dependencies.json` `provides.skills` entry (added, not removed — see below),
  and the `Skill(update-central-files)` entry in `config/settings.json`'s permission allowlist.
- The session-output index: `record_group = 'session-output'` on Plan A's exact schema/CLI, plus
  a `session-output-watermark` singleton row (spec §4.3's named pattern) tracking the
  reconciliation job's incremental cursor.
- The new AUTO item in `pm-update-central-files`'s `SKILL.md` that registers a session's `out/`
  files at session end (spec §8 item 1).
- The 7-day `ccsched` reconciliation job (spec §8 item 2) and its **automatic provisioning at
  install time** (prompt's "Install-time requirement", 2026-07-26 addendum) via a new `ccst
  ccsched-jobs install` verb wired into `ccst install-everything`.
- A `ccst doctor` check confirming the job is registered and enabled (mirrors the existing
  `check_skill_symlink`/`check_hook_registered` per-concern pattern; WARN not FAIL — see
  Versioning below for why FAIL isn't required here).

**Explicitly out of scope** (deferred to other plans, per the dispatching prompt and Plan A's own
Scope section):

- `ccst pdata init` / migration, `pm-project-init` skill (spec §7 — Plan B).
- `pm-pdata-schema-design` / `pm-pdata-conflict-resolution` skills, `ccst pdata verify` and its
  own `ccsched` job (spec §8.1/§8.2 — Plan D). The job this plan adds is a *different* job
  (session-output reconciliation, not pdata integrity verification) — designing its own
  provisioning is explicitly this plan's job per the dispatching prompt, not Plan D's.
- Deciding whether `out/` files are ever "promoted" into a project's durable data store — the
  session-output index is a discovery/reference layer over `out/`, not a promotion mechanism
  (prompt's own explicit non-goal).
- Renaming any *other* skill to a `pm-` prefix, or extending this migration to any project's
  CLAUDE.md beyond CCCS/CCST (the broader per-project inventory in spec §7.2 is separate work).
  A full cross-repo grep for stale `/update-central-files` mentions in other projects' CLAUDE.md
  files is a known follow-up, not performed by this plan — flag it to Chris if picked up later.
- `ccst pdata export`, `ccst pdata init` (Plan A's own "Project lifecycle" exclusions still hold).

## Versioning

Per this repo's `CLAUDE.md` version policy: this plan adds new CLI verbs (`ccst pdata
reconcile-session-output`, `ccst ccsched-jobs install`), a new record_group used through Plan A's
unmodified schema, and moves a skill between repos. Nothing existing on disk becomes unreadable
by old code — this is a **minor** bump. Because Plan B is sequenced independently and this plan
does not read its content, the exact resulting version number is **not** hardcoded here — Task 11
reads `pyproject.toml`'s version at the time this plan is actually executed and bumps the minor
component by one from whatever is on disk then (matching Plan D's own dynamic-read pattern for
exactly this reason). The new `ccst doctor` check (Task 9) is **WARN**, not FAIL: a missing/disabled ccsched job is
recoverable by re-running `ccst ccsched-jobs install --apply` and risks staleness, not silent data
loss — the FAIL-level doctor-check requirement in this repo's version policy applies specifically
to breaking on-disk migrations, which this plan does not perform.

## Necessary implementation decisions beyond the spec's literal text

1. **Watermark, not `MAX(created_at)`.** The reconciliation job needs an incremental cursor
   ("only check files newer than last time") without touching Plan A's shared base schema. Spec
   §4.3 already names the right shape for this — **singleton state** — so the cursor is just
   another `record_group` (`session-output-watermark`), one row per project, `content` holding
   the epoch cursor as a string. This was chosen over `MAX(created_at) WHERE record_group=?`
   specifically to avoid adding a composite index to Plan A's shared `records` table for one
   feature's benefit, and to avoid coupling the cursor to session-output rows' own timestamps
   (which a human could later edit via `ccst pdata update` without meaning to move the cursor).
2. **`content` for a reconciliation-created row is the file's basename, not a description; `created_at`
   likewise differs between the two insert paths — both asymmetries are intentional.** The
   spec says the index carries "a one-line description if inferable" — that inference is a
   judgement call only the `pm-update-central-files` **skill** (running as Claude, with the
   session's context) can make. The **`ccst pdata reconcile-session-output` CLI is plain code**
   with no access to that context, so its backfill inserts always use the file's basename as
   `content`. This asymmetry is intentional, not a shortcut to fix later.

   The same two paths also disagree on `created_at`: the skill's `ccst pdata add` call
   deliberately omits `--created-at` (Plan A's `pdata add` does support the flag, per spec §5 —
   the skill simply chooses not to pass it) and so always defaults to wall-clock "now", while
   `reconcile_project` passes `created_at=mtime`. This is the right
   default for each path rather than a bug to unify: the skill's AUTO item runs at session
   wrap-up, seconds to minutes after the file was written, so "now" and the file's real mtime
   are close enough not to matter; the reconciliation job, by contrast, is specifically the
   *catch-up* path for files a crashed or non-interactive session never got to register, often
   days later — using wall-clock "now" there would record a materially wrong creation time, so
   the job's use of the file's actual on-disk mtime is required, not incidental.
3. **The reconciliation job's watermark only advances when the job itself runs a full disk
   walk — the skill's AUTO item never touches it.** The skill only ever looks at the *current*
   session's `out/` directory, never the whole `cc-sessions/*/out/` tree, so it has no valid
   "everything before this timestamp is accounted for" claim to make. Both paths dedupe by
   `file_path` before inserting (Task 5's `_is_already_registered`), so it is always safe for the
   job to later re-scan a file the skill already registered — it will find the existing row and
   skip it, never double-insert. This is what makes the two independent write paths (skill,
   job) safe without any cross-coordination beyond the shared uniqueness check.
4. **`--all-projects` project discovery is deliberately narrower than "every directory under a
   session root."** `CLAUDE_SESSION_TOOLS_REPO_ROOT`/`_PROJ_ROOT` (see `lib/roots.py`, already
   shipped) list every top-level directory as a "project", but most `~/repos/*` entries are
   ordinary code repos with no `cc-sessions/` at all. Naively calling `repository.connect()` for
   every such directory would silently create a near-empty `.db` file for dozens of unrelated
   repos. `discover_projects_with_sessions()` (Task 1) filters to directories that actually
   contain a `cc-sessions/` subdirectory — the same signal the skill itself depends on.
5. **`session_tag` is a real extension column, not something recovered from `file_path` at query
   time.** `file_path` (`cc-sessions/<tag>/out/<name>`) already encodes the session tag, but the
   base schema has no index on `file_path`, so "what did session X produce" would be an
   unindexed `LIKE` scan. A `session_tag TEXT` extension field (via `schema_add_field`, called
   idempotently by `ensure_session_output_schema`, Task 3) makes that a normal, indexable query
   — consistent with spec §4.3's own rejection of leaning on unstructured `content`/`file_path`
   scans for anything genuinely queried.
6. **`set_watermark` retries exactly once on a version conflict, and does not swallow a second
   failure.** The watermark row can, in principle, be written by two concurrent
   `reconcile_project` calls for the same project — e.g. the 7-day scheduled job's own
   `--all-projects` sweep landing at the same moment as someone manually running `ccst pdata
   reconcile-session-output --project <name>` for that same project (Decision 3 already rules out
   the `pm-update-central-files` skill's AUTO item as a source of this race: that item never calls
   `set_watermark` at all, it only ever calls `ccst pdata add`/`query`/`reconcile-session-output
   --schema-only`, none of which touch the watermark row). Since
   the row's value is a performance optimization (a stale watermark only means "re-scan a bit more
   next time", never data loss — the `file_path` dedupe check is the actual correctness
   guarantee), a single refetch-and-retry is the right amount of resilience: enough to absorb a
   genuine rare race, without silently discarding a write forever if the row is conflicting on
   every attempt (per this repo's "no fallback swallowing" coding standard, a second failure
   propagates as an exception rather than being caught again).
   This retry only covers the **update** path (an existing watermark row whose version moved under
   us). The **create** path (a project's very first watermark write) has a narrower, lower-stakes
   sibling race: two concurrent first-time callers can both observe no existing row and both
   insert one, leaving two rows where the singleton convention expects one. This plan does not add
   DB-level uniqueness to close that window — doing so would mean touching Plan A's shared base
   schema, which this plan's Architecture section explicitly rules out. The blast radius is bounded
   rather than silently wrong: `get_watermark`/`_get_watermark_record` always resolve the same
   lowest-`id` row deterministically, so every future read and write converges on that one row and
   the second row is simply inert (unread, unwritten) — no file is ever double-registered or
   dropped, because `_is_already_registered`'s `file_path` dedupe check (Task 5) is the actual
   correctness guarantee, not the watermark. A real fix (a unique index or an atomic
   check-and-insert) is future work if this becomes a problem in practice; landing it on someone's
   very first `reconcile-session-output` call for a project (the scheduled job's first `--all-
   projects` sweep racing a simultaneous manual `--project` run, the same pairing as the update-path
   race above) requires two independent writers to both hit a brand-new project in the same
   instant, which this plan accepts as an acceptably rare, non-corrupting edge case rather than a
   reason to touch Plan A's schema.
7. **`ccst ccsched-jobs install` is its own noun, backed by a shared bundled-jobs list, not a
   private helper wired only into `install-everything`.** Every other install step
   (`skills`, `hooks`, `shell`, `claude-md`) is independently invocable — matching that
   convention (this repo's "match existing conventions" coding standard) means this step must be
   too. The list of jobs to provision lives in `lib/scheduler/bundled_jobs.py`, a small
   dependency-free module, so both the CLI installer (`cli/ccst.py`) and the doctor check
   (`lib/doctor.py`) import the *same* list and can never drift apart on what "should be
   registered" means. This also gives Plan D's own pdata-verify job a one-line place to add
   itself later instead of inventing a second bundled-jobs mechanism.
8. **Job registration is add-if-missing, never overwrite, decided by an explicit pre-check —
   not by catching `ccsched add`'s duplicate-id `RegistryError`.** `ccst ccsched-jobs install
   --apply` skips any job id already present in the registry, even if its cadence/timeout
   differs from the bundled default — a human may have deliberately edited it (e.g. via
   `ccsched edit`), and silently stomping that edit on every `install-everything --apply` re-run
   would be a surprising footgun. `registry.add_job()` would in fact raise `RegistryError` on a
   duplicate id and could be used as the "already there" signal, but this repo's own Python
   coding standard ("do not use exceptions for control flow") rules that out — `_cmd_
   ccsched_jobs_install` (Task 8) instead reads `registry.load_registry()` once up front and
   checks membership in a plain `set` of existing ids before ever calling `add_job`, so a
   duplicate id is never an exception in the first place, just a skipped loop iteration.

## File structure

```
src/cc_session_tools/lib/pdata/session_output.py    project discovery, out/ walk, schema
                                                     bootstrap, watermark, reconcile orchestration
src/cc_session_tools/lib/scheduler/bundled_jobs.py  shared list of CCST-bundled ccsched jobs
                                                     (single source of truth for installer + doctor)
src/cc_session_tools/cli/ccst.py                    (modified) new "pdata reconcile-session-output"
                                                     verb; new "ccsched-jobs install" noun; wired
                                                     into install-everything as a 6th step
src/cc_session_tools/lib/doctor.py                  (modified) check_ccsched_job_registered() +
                                                     wiring into run_all_checks()

tests/pdata/test_session_output.py                  new
tests/test_ccst_pdata_reconcile_cli.py              new (subprocess CLI tests, matches
                                                     tests/test_ccst_pdata_cli.py's own pattern)
tests/test_scheduler_bundled_jobs.py                new
tests/test_ccst_ccsched_jobs_cli.py                 new
tests/test_ccst_install_everything.py               (modified) — new 6th step
tests/test_ccst_doctor.py                           (modified) — new check

skills/pm-update-central-files/SKILL.md             new (moved + renamed + extended from CCCS)

pyproject.toml                                      (modified) — version bump
CHANGELOG.md                                        (modified) — [Unreleased] entry

--- separate repository: claude-code-config-sync ---

skills/update-central-files/SKILL.md                deleted
README.md                                           (modified) — remove Custom Skills row
config/external-dependencies.json                   (modified) — add pm-update-central-files to
                                                     cc-session-tools.provides.skills; bump
                                                     pinned_version to match CCST's Task 11
config/settings.json                                (modified) — rename the permission-allowlist
                                                     entry Skill(update-central-files) ->
                                                     Skill(pm-update-central-files)
```

---

## Task 1: `lib/pdata/session_output.py` — project discovery

**Files:**
- Create: `src/cc_session_tools/lib/pdata/session_output.py`
- Create: `tests/pdata/test_session_output.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/pdata/test_session_output.py
from __future__ import annotations

import pytest

from cc_session_tools.lib import roots
from cc_session_tools.lib.pdata import session_output


def _make_project(base, name, *, with_sessions: bool) -> None:
    proj = base / name
    proj.mkdir(parents=True)
    if with_sessions:
        (proj / "cc-sessions").mkdir()


def test_discover_projects_with_sessions_filters_to_cc_sessions_dirs(monkeypatch, tmp_path):
    repo_root = tmp_path / "repos"
    repo_root.mkdir()
    _make_project(repo_root, "has-sessions", with_sessions=True)
    _make_project(repo_root, "no-sessions", with_sessions=False)
    monkeypatch.setenv(roots.REPO_ROOT_ENV, str(repo_root))
    monkeypatch.delenv(roots.PROJ_ROOT_ENV, raising=False)

    found = session_output.discover_projects_with_sessions()

    names = [name for name, _ in found]
    assert names == ["has-sessions"]


def test_discover_projects_with_sessions_dedupes_across_roots(monkeypatch, tmp_path):
    repo_root = tmp_path / "repos"
    proj_root = tmp_path / "cc-claude-code"
    repo_root.mkdir()
    proj_root.mkdir()
    _make_project(repo_root, "shared-name", with_sessions=True)
    _make_project(proj_root, "shared-name", with_sessions=True)
    monkeypatch.setenv(roots.REPO_ROOT_ENV, str(repo_root))
    monkeypatch.setenv(roots.PROJ_ROOT_ENV, str(proj_root))

    found = session_output.discover_projects_with_sessions()

    names = [name for name, _ in found]
    assert names.count("shared-name") == 1
    # REPO_ROOT_ENV is processed first in roots.load_session_roots()'s own ordering.
    assert dict(found)["shared-name"] == repo_root / "shared-name"


def test_discover_projects_with_sessions_raises_when_no_roots_configured(monkeypatch):
    monkeypatch.delenv(roots.REPO_ROOT_ENV, raising=False)
    monkeypatch.delenv(roots.PROJ_ROOT_ENV, raising=False)
    with pytest.raises(roots.RootsConfigError):
        session_output.discover_projects_with_sessions()


def test_find_project_root_returns_none_for_unknown_project(monkeypatch, tmp_path):
    repo_root = tmp_path / "repos"
    repo_root.mkdir()
    _make_project(repo_root, "known", with_sessions=True)
    monkeypatch.setenv(roots.REPO_ROOT_ENV, str(repo_root))
    monkeypatch.delenv(roots.PROJ_ROOT_ENV, raising=False)

    assert session_output.find_project_root("known") == repo_root / "known"
    assert session_output.find_project_root("unknown") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pdata/test_session_output.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cc_session_tools.lib.pdata.session_output'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/pdata/session_output.py
"""Session-output index (spec §8): registers cc-sessions/*/out/ deliverables into the
'session-output' record_group on Plan A's unmodified ccst pdata schema/CLI, and reconciles that
index against disk via a 7-day ccsched job. Pure orchestration on top of lib.pdata.service —
this module adds no new tables or CLI primitives to Plan A's schema itself."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from cc_session_tools.lib import roots

if TYPE_CHECKING:
    # Type-only import: every runtime use of `service` in this module is a local import inside
    # the function that needs it (see e.g. ensure_session_output_schema, reconcile_project),
    # matching this module's (and the rest of the codebase's) convention of deferring
    # lib.pdata.service imports to keep CLI startup fast. `_get_watermark_record`'s return
    # annotation (below) still needs the real `Record` type for mypy --strict's
    # disallow-untyped-defs check (Task 11 runs it against this exact file) — a TYPE_CHECKING-only
    # import gives mypy the name without adding a runtime import to the module's top level.
    from cc_session_tools.lib.pdata.service import Record


def discover_projects_with_sessions() -> list[tuple[str, Path]]:
    """(project_name, project_root) for every direct subdirectory of a configured session root
    that contains a cc-sessions/ directory — the same signal the pm-update-central-files skill
    itself depends on. Deliberately narrower than "every directory under a session root" (see
    plan Decision 4): most ~/repos/* entries are ordinary code repos with no session history,
    and connecting to a nonexistent project would silently create an empty project .db for each
    one. Raises roots.RootsConfigError if neither session root env var is configured — same
    contract as roots.load_session_roots().
    """
    found: dict[str, Path] = {}
    for root in roots.load_session_roots():
        for entry in sorted(root.iterdir()):
            if entry.name in found:
                continue
            if entry.is_dir() and (entry / "cc-sessions").is_dir():
                found[entry.name] = entry
    return sorted(found.items())


def find_project_root(project: str) -> Path | None:
    """The configured session root's copy of <project>, if it has a cc-sessions/ directory.
    Used by the single-project (--project NAME) CLI path; --all-projects uses
    discover_projects_with_sessions() directly. Raises roots.RootsConfigError under the same
    conditions as discover_projects_with_sessions()."""
    for name, root in discover_projects_with_sessions():
        if name == project:
            return root
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_session_output.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/session_output.py tests/pdata/test_session_output.py
git commit -m "feat(pdata): add session-output project discovery"
```

---

## Task 2: `session_output.py` — out/ file walk + session-tag extraction

**Files:**
- Modify: `src/cc_session_tools/lib/pdata/session_output.py`
- Modify: `tests/pdata/test_session_output.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/pdata/test_session_output.py
import os
import time


def _touch(path, mtime: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("content")
    os.utime(path, (mtime, mtime))


def test_find_new_out_files_only_returns_files_newer_than_watermark(tmp_path):
    project_root = tmp_path / "proj"
    old_file = project_root / "cc-sessions" / "20260701-a" / "out" / "old.md"
    new_file = project_root / "cc-sessions" / "20260710-b" / "out" / "new.md"
    _touch(old_file, 1000)
    _touch(new_file, 2000)

    found = session_output.find_new_out_files(project_root, since_mtime=1500)

    paths = [p for p, _ in found]
    assert new_file in paths
    assert old_file not in paths


def test_find_new_out_files_ignores_non_out_dirs_and_missing_cc_sessions(tmp_path):
    project_root = tmp_path / "proj"
    working_file = project_root / "cc-sessions" / "20260701-a" / "working" / "WORKLOG.md"
    _touch(working_file, 5000)

    found = session_output.find_new_out_files(project_root, since_mtime=0)

    assert found == []


def test_find_new_out_files_returns_empty_list_for_project_with_no_cc_sessions(tmp_path):
    project_root = tmp_path / "proj"
    project_root.mkdir()
    assert session_output.find_new_out_files(project_root, since_mtime=0) == []


def test_session_tag_from_relpath_extracts_the_session_directory_name():
    rel = "cc-sessions/20260710-foo-bar/out/report.md"
    assert session_output.session_tag_from_relpath(rel) == "20260710-foo-bar"


@pytest.mark.parametrize(
    "bad_rel",
    ["out/report.md", "cc-sessions/report.md", "working/foo/out/x.md"],
)
def test_session_tag_from_relpath_rejects_malformed_paths(bad_rel):
    with pytest.raises(ValueError, match="cc-sessions"):
        session_output.session_tag_from_relpath(bad_rel)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pdata/test_session_output.py -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'find_new_out_files'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/cc_session_tools/lib/pdata/session_output.py

def find_new_out_files(project_root: Path, *, since_mtime: int) -> list[tuple[Path, int]]:
    """Every regular file under <project_root>/cc-sessions/*/out/ (any depth) whose mtime is at
    or after since_mtime, as (absolute_path, mtime_epoch_seconds) pairs. Returns [] if
    project_root has no cc-sessions/ directory at all (a genuinely new/empty project — same
    "safe against an empty folder" stance as the rest of this feature).

    Deliberately >= rather than a strict >: mtime is truncated to whole seconds (see the `int()`
    below), so two files written within the same wall-clock second can share an mtime. A strict >
    against a watermark that already equals that second would permanently exclude whichever of
    those files gets scanned after the watermark has already advanced to it — a silent,
    unrecoverable gap in the safety-net job's own coverage. >= re-admits already-registered files
    as *candidates* every time their mtime matches the watermark, but `_is_already_registered`'s
    file_path dedupe check (Task 5) makes re-including them a no-op — a little repeated scanning
    work, never a repeated write, and never a missed file."""
    cc_sessions = project_root / "cc-sessions"
    if not cc_sessions.is_dir():
        return []
    results: list[tuple[Path, int]] = []
    for session_dir in sorted(cc_sessions.iterdir()):
        out_dir = session_dir / "out"
        if not out_dir.is_dir():
            continue
        for file in sorted(out_dir.rglob("*")):
            if not file.is_file():
                continue
            mtime = int(file.stat().st_mtime)
            if mtime >= since_mtime:
                results.append((file, mtime))
    return results


def session_tag_from_relpath(rel_path: str) -> str:
    """Extract the <session_tag> from a 'cc-sessions/<session_tag>/out/...' relative path (the
    shape every path passed here is guaranteed to have, since it always comes from
    find_new_out_files's own walk — this raises loudly rather than guessing if that ever stops
    being true, per this repo's 'throw loudly on an impossible state' coding standard)."""
    parts = Path(rel_path).parts
    if len(parts) < 4 or parts[0] != "cc-sessions" or parts[2] != "out":
        raise ValueError(
            f"expected a 'cc-sessions/<tag>/out/...' path, got {rel_path!r}"
        )
    return parts[1]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_session_output.py -v`
Expected: PASS (9 tests total in the file)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/session_output.py tests/pdata/test_session_output.py
git commit -m "feat(pdata): add session-output out/ file walk and session-tag extraction"
```

---

## Task 3: `session_output.py` — idempotent schema bootstrap

**Files:**
- Modify: `src/cc_session_tools/lib/pdata/session_output.py`
- Modify: `tests/pdata/test_session_output.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/pdata/test_session_output.py

def test_ensure_session_output_schema_creates_session_tag_column(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    from cc_session_tools.lib.pdata import repository

    session_output.ensure_session_output_schema("testproj")

    conn = repository.connect("testproj")
    try:
        cols = repository.list_extension_columns(conn, session_output.SESSION_OUTPUT_GROUP)
        assert "session_tag" in cols
    finally:
        conn.close()


def test_ensure_session_output_schema_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    session_output.ensure_session_output_schema("testproj")
    session_output.ensure_session_output_schema("testproj")  # must not raise


def test_ensure_session_output_schema_creates_file_path_index(monkeypatch, tmp_path):
    # Task 5's _is_already_registered dedupe check filters on file_path within the
    # session-output record_group. Plan A's base schema only indexes record_group and
    # updated_at (not file_path) — without a targeted index here, that check is an unindexed
    # scan across every session-output row ever written for this project, which by design
    # accumulates forever, directly contradicting spec Goal G5 ("cost never scales with
    # accumulated history"). This index is scoped to this one record_group via a partial index
    # (WHERE record_group = 'session-output'), so it stays a session-output-only concern and
    # does not touch Plan A's own shared index list.
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    from cc_session_tools.lib.pdata import repository

    session_output.ensure_session_output_schema("testproj")

    conn = repository.connect("testproj")
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' "
            "AND name = 'idx_records_session_output_file_path'"
        ).fetchall()
        assert len(rows) == 1
    finally:
        conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pdata/test_session_output.py -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'ensure_session_output_schema'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/cc_session_tools/lib/pdata/session_output.py

SESSION_OUTPUT_GROUP = "session-output"
WATERMARK_GROUP = "session-output-watermark"


def ensure_session_output_schema(project: str) -> None:
    """Idempotent — safe to call on every reconcile run and from the pm-update-central-files
    skill's own AUTO item (schema_add_field no-ops if the column already exists, per Plan A).

    Also creates a partial index on records(file_path), scoped to record_group='session-output'
    via a SQLite partial-index WHERE clause. Without it, _is_already_registered's per-file dedupe
    check (Task 5) — and the equivalent `ccst pdata query --where "file_path = ..."` call the
    pm-update-central-files skill's own AUTO item makes — is an unindexed scan across every
    session-output row this project has ever accumulated (Plan A's base schema indexes only
    record_group and updated_at). That scan grows without bound for a catch-all project like
    `oneshot`, contradicting spec Goal G5 ("cost never scales with accumulated history"). Scoping
    the index to this one record_group keeps it a session-output-only concern rather than a
    change to Plan A's shared base-schema index list."""
    from cc_session_tools.lib.pdata import repository, service

    service.schema_add_field(
        project=project,
        record_group=SESSION_OUTPUT_GROUP,
        field_name="session_tag",
        sql_type="TEXT",
        description="The cc-sessions/<session_tag>/ directory this out/ file was produced by",
        default=None,
    )

    conn = repository.connect(project)
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_records_session_output_file_path "
            f"ON records(file_path) WHERE record_group = '{SESSION_OUTPUT_GROUP}'"
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_session_output.py -v`
Expected: PASS (12 tests total in the file)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/session_output.py tests/pdata/test_session_output.py
git commit -m "feat(pdata): add idempotent session-output schema bootstrap"
```

---

## Task 4: `session_output.py` — watermark read/write with bounded conflict retry

**Files:**
- Modify: `src/cc_session_tools/lib/pdata/session_output.py`
- Modify: `tests/pdata/test_session_output.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/pdata/test_session_output.py

def test_get_watermark_defaults_to_zero_for_new_project(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    assert session_output.get_watermark("testproj") == 0


def test_set_watermark_then_get_watermark_round_trips(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    session_output.set_watermark("testproj", 1234)
    assert session_output.get_watermark("testproj") == 1234

    session_output.set_watermark("testproj", 5678)  # update path, not just create
    assert session_output.get_watermark("testproj") == 5678


def test_set_watermark_retries_once_on_version_conflict(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    from cc_session_tools.lib.pdata import service

    session_output.set_watermark("testproj", 100)  # creates the row

    real_update_record = service.update_record
    calls = {"n": 0}

    def flaky_update_record(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            record = session_output._get_watermark_record("testproj")
            raise service.VersionConflictError(
                current={"id": record.id, "version": record.version + 1},
                attempted=kwargs,
            )
        return real_update_record(**kwargs)

    monkeypatch.setattr(service, "update_record", flaky_update_record)

    session_output.set_watermark("testproj", 200)  # must not raise

    assert calls["n"] == 2
    assert session_output.get_watermark("testproj") == 200


def test_set_watermark_propagates_conflict_that_persists_after_retry(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    from cc_session_tools.lib.pdata import service

    session_output.set_watermark("testproj", 100)

    def always_conflicts(**kwargs):
        record = session_output._get_watermark_record("testproj")
        raise service.VersionConflictError(
            current={"id": record.id, "version": record.version + 1},
            attempted=kwargs,
        )

    monkeypatch.setattr(service, "update_record", always_conflicts)

    with pytest.raises(service.VersionConflictError):
        session_output.set_watermark("testproj", 200)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pdata/test_session_output.py -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'get_watermark'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/cc_session_tools/lib/pdata/session_output.py

def _get_watermark_record(project: str) -> Record | None:
    from cc_session_tools.lib.pdata import service, store

    if not store.db_path(project).exists():
        # A project with no .db file yet has no watermark row, by definition. Short-circuits
        # before repository.connect() (called inside service.list_records), which always
        # creates the .db file and its base schema as a side effect of connecting — even for a
        # read-only lookup. Without this check, get_watermark() would create the project's .db
        # file on every call, including reconcile_project's dry_run=True branch, contradicting
        # its docstring's claim that dry-run "writes nothing". set_watermark()'s own create path
        # (record is None) still creates the file as intended, via its own service.add_record
        # call further down — this short-circuit only skips the unnecessary read-only connect.
        return None

    records = service.list_records(project=project, record_group=WATERMARK_GROUP, limit=1)
    return records[0] if records else None


def get_watermark(project: str) -> int:
    """The epoch-seconds cursor of the last file successfully reconciled for this project, or 0
    if reconciliation has never run (a full backfill on first run, same "safe against an empty
    baseline" stance as the rest of this feature)."""
    record = _get_watermark_record(project)
    return int(record.content) if record is not None else 0


def set_watermark(project: str, epoch: int) -> None:
    """Create-or-update the single session-output-watermark row for this project (spec §4.3
    singleton-state pattern — see plan Decision 1). On a version conflict (a concurrent writer,
    e.g. a manual `ccst pdata reconcile-session-output --project <name>` run landing at the same
    moment as the scheduled job's own `--all-projects` sweep — see plan Decision 6; the
    pm-update-central-files skill's AUTO item never calls this function at all, per Decision 3),
    refetch and retry exactly once; a conflict that persists after the retry propagates rather
    than being silently discarded."""
    from cc_session_tools.lib.pdata import service

    record = _get_watermark_record(project)
    if record is None:
        service.add_record(
            project=project, record_group=WATERMARK_GROUP, content=str(epoch),
            file_path=None, fields={}, created_at=epoch,
        )
        return
    try:
        service.update_record(
            project=project, record_id=record.id, expected_version=record.version,
            content=str(epoch), file_path=None, fields={}, updated_at=epoch,
        )
    except service.VersionConflictError:
        record = _get_watermark_record(project)
        assert record is not None
        service.update_record(
            project=project, record_id=record.id, expected_version=record.version,
            content=str(epoch), file_path=None, fields={}, updated_at=epoch,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_session_output.py -v`
Expected: PASS (16 tests total in the file)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/session_output.py tests/pdata/test_session_output.py
git commit -m "feat(pdata): add session-output watermark with bounded conflict retry"
```

---

## Task 5: `session_output.py` — `reconcile_project` orchestration

**Files:**
- Modify: `src/cc_session_tools/lib/pdata/session_output.py`
- Modify: `tests/pdata/test_session_output.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/pdata/test_session_output.py

def test_reconcile_project_registers_new_files_and_advances_watermark(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    project_root = tmp_path / "proj"
    out_file = project_root / "cc-sessions" / "20260710-foo" / "out" / "report.md"
    _touch(out_file, 2000)

    result = session_output.reconcile_project("testproj", project_root)

    assert result.scanned == 1
    assert result.registered == 1
    assert result.watermark == 2000
    assert session_output.get_watermark("testproj") == 2000

    from cc_session_tools.lib.pdata import service

    rows = service.list_records(project="testproj", record_group=session_output.SESSION_OUTPUT_GROUP)
    assert len(rows) == 1
    assert rows[0].file_path == "cc-sessions/20260710-foo/out/report.md"
    assert rows[0].content == "report.md"
    assert rows[0].fields["session_tag"] == "20260710-foo"


def test_reconcile_project_skips_already_registered_files(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    project_root = tmp_path / "proj"
    out_file = project_root / "cc-sessions" / "20260710-foo" / "out" / "report.md"
    _touch(out_file, 2000)

    session_output.reconcile_project("testproj", project_root)
    # A second run with no new files must not re-insert or error.
    result = session_output.reconcile_project("testproj", project_root)

    assert result.registered == 0

    from cc_session_tools.lib.pdata import service

    rows = service.list_records(project="testproj", record_group=session_output.SESSION_OUTPUT_GROUP)
    assert len(rows) == 1


def test_reconcile_project_dry_run_does_not_write(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    project_root = tmp_path / "proj"
    out_file = project_root / "cc-sessions" / "20260710-foo" / "out" / "report.md"
    _touch(out_file, 2000)

    result = session_output.reconcile_project("testproj", project_root, dry_run=True)

    assert result.registered == 1  # reports what WOULD be registered
    assert session_output.get_watermark("testproj") == 0  # but writes nothing

    from cc_session_tools.lib.pdata import service

    rows = service.list_records(project="testproj", record_group=session_output.SESSION_OUTPUT_GROUP)
    assert rows == []


def test_reconcile_project_dry_run_on_new_project_creates_no_db_file(monkeypatch, tmp_path):
    # A dry-run that only reads should not have the side effect of creating the project's .db
    # file — repository.connect() creates it and its base schema on every call (no readonly path
    # in Plan A), so this specifically exercises the case where _is_already_registered and
    # get_watermark's own store.db_path().exists() short-circuits are the only thing preventing
    # that connect() call from ever happening.
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    from cc_session_tools.lib.pdata import store

    project_root = tmp_path / "proj"
    out_file = project_root / "cc-sessions" / "20260710-foo" / "out" / "report.md"
    _touch(out_file, 2000)

    session_output.reconcile_project("newproj", project_root, dry_run=True)

    assert not store.db_path("newproj").exists()


def test_reconcile_project_handles_empty_project(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    project_root = tmp_path / "empty-proj"
    project_root.mkdir()

    result = session_output.reconcile_project("testproj", project_root)

    assert result.scanned == 0
    assert result.registered == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pdata/test_session_output.py -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'reconcile_project'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/cc_session_tools/lib/pdata/session_output.py

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    project: str
    scanned: int
    registered: int
    watermark: int


def _is_already_registered(project: str, rel_path: str) -> bool:
    from cc_session_tools.lib.pdata import service, store

    if not store.db_path(project).exists():
        # A project with no .db file yet has no registered rows, by definition. Short-circuits
        # before repository.connect() (called inside service.query_records), which always
        # creates the .db file and its base schema as a side effect of connecting — even for a
        # read-only lookup (Plan A's repository.connect() applies `ddl=_BASE_DDL` on every
        # connect, with no readonly path). Without this check, reconcile_project's dry_run=True
        # branch would create the project's .db file via this very lookup, contradicting its own
        # docstring's claim that dry-run "writes nothing".
        return False

    matches = service.query_records(
        project=project, record_group=SESSION_OUTPUT_GROUP,
        where=[f"file_path = {rel_path}"], limit=1,
    )
    return len(matches) > 0


def reconcile_project(
    project: str, project_root: Path, *, dry_run: bool = False,
) -> ReconcileResult:
    """Backfill session-output rows for every cc-sessions/*/out/ file newer than this project's
    watermark that isn't already registered (spec §8 item 2). Safe to call repeatedly, safe to
    call late, safe to coalesce (the ccsched contract, see manage-recurring-cc-jobs-using-ccsched
    skill) — every insert is guarded by _is_already_registered's file_path dedupe check, and the
    watermark only ever advances, never rewinds. dry_run reports what WOULD be registered without
    writing anything (including the watermark)."""
    from cc_session_tools.lib.pdata import service

    if not dry_run:
        ensure_session_output_schema(project)

    since = get_watermark(project)
    candidates = find_new_out_files(project_root, since_mtime=since)

    registered = 0
    watermark = since
    for file_path, mtime in candidates:
        watermark = max(watermark, mtime)
        rel_path = str(file_path.relative_to(project_root))
        if _is_already_registered(project, rel_path):
            continue
        if not dry_run:
            service.add_record(
                project=project, record_group=SESSION_OUTPUT_GROUP,
                content=file_path.name, file_path=rel_path,
                fields={"session_tag": session_tag_from_relpath(rel_path)},
                created_at=mtime,
            )
        registered += 1

    if not dry_run and watermark > since:
        set_watermark(project, watermark)

    return ReconcileResult(
        project=project, scanned=len(candidates), registered=registered, watermark=watermark,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_session_output.py -v`
Expected: PASS (21 tests total in the file)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/session_output.py tests/pdata/test_session_output.py
git commit -m "feat(pdata): add reconcile_project orchestration for the session-output index"
```

---

## Task 6: CLI `ccst pdata reconcile-session-output`

**Files:**
- Modify: `src/cc_session_tools/cli/ccst.py`
- Create: `tests/test_ccst_pdata_reconcile_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ccst_pdata_reconcile_cli.py
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def _run(env: dict, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True, text=True,
        cwd=str(Path(__file__).parent),
        env=env,
    )


@pytest.fixture
def base_env(tmp_path):
    env = os.environ.copy()
    env["CCST_PROJECT_DB_DIR"] = str(tmp_path / "project-db")
    return env


def _touch(path: Path, mtime: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    os.utime(path, (mtime, mtime))


def test_reconcile_requires_project_or_all_projects(base_env):
    r = _run(base_env, "pdata", "reconcile-session-output")
    assert r.returncode == 2


def test_reconcile_rejects_both_project_and_all_projects(base_env, tmp_path):
    r = _run(
        base_env, "pdata", "reconcile-session-output",
        "--project", "x", "--all-projects",
    )
    assert r.returncode == 2


def test_reconcile_single_project_reports_scanned_and_registered(base_env, tmp_path):
    repo_root = tmp_path / "repos"
    _touch(repo_root / "myproj" / "cc-sessions" / "20260710-a" / "out" / "r.md", 2000)
    base_env["CLAUDE_SESSION_TOOLS_REPO_ROOT"] = str(repo_root)

    r = _run(base_env, "pdata", "reconcile-session-output", "--project", "myproj")

    assert r.returncode == 0, r.stderr
    assert "myproj" in r.stdout
    assert "registered 1" in r.stdout


def test_reconcile_unknown_project_errors(base_env, tmp_path):
    repo_root = tmp_path / "repos"
    repo_root.mkdir()
    base_env["CLAUDE_SESSION_TOOLS_REPO_ROOT"] = str(repo_root)

    r = _run(base_env, "pdata", "reconcile-session-output", "--project", "nope")

    assert r.returncode == 1
    assert "nope" in r.stderr


def test_reconcile_all_projects(base_env, tmp_path):
    repo_root = tmp_path / "repos"
    _touch(repo_root / "a" / "cc-sessions" / "20260710-x" / "out" / "r.md", 2000)
    _touch(repo_root / "b" / "cc-sessions" / "20260710-y" / "out" / "r.md", 2000)
    base_env["CLAUDE_SESSION_TOOLS_REPO_ROOT"] = str(repo_root)

    r = _run(base_env, "pdata", "reconcile-session-output", "--all-projects")

    assert r.returncode == 0, r.stderr
    assert "a:" in r.stdout
    assert "b:" in r.stdout


def test_reconcile_no_roots_configured_errors(base_env):
    for var in ("CLAUDE_SESSION_TOOLS_REPO_ROOT", "CLAUDE_SESSION_TOOLS_PROJ_ROOT"):
        base_env.pop(var, None)

    r = _run(base_env, "pdata", "reconcile-session-output", "--all-projects")

    assert r.returncode == 1
    assert "CST-ROOTS-CONFIG-ERROR" in r.stderr


def test_reconcile_dry_run(base_env, tmp_path):
    repo_root = tmp_path / "repos"
    _touch(repo_root / "myproj" / "cc-sessions" / "20260710-a" / "out" / "r.md", 2000)
    base_env["CLAUDE_SESSION_TOOLS_REPO_ROOT"] = str(repo_root)

    r = _run(
        base_env, "pdata", "reconcile-session-output",
        "--project", "myproj", "--dry-run",
    )

    assert r.returncode == 0, r.stderr
    assert "dry-run" in r.stdout


def test_reconcile_schema_only_bootstraps_schema_without_scanning(base_env, tmp_path):
    repo_root = tmp_path / "repos"
    _touch(repo_root / "myproj" / "cc-sessions" / "20260710-a" / "out" / "r.md", 2000)
    base_env["CLAUDE_SESSION_TOOLS_REPO_ROOT"] = str(repo_root)

    r = _run(
        base_env, "pdata", "reconcile-session-output",
        "--project", "myproj", "--schema-only",
    )

    assert r.returncode == 0, r.stderr
    assert "schema ensured" in r.stdout

    # The file was NOT scanned/registered by --schema-only.
    r2 = _run(base_env, "pdata", "query", "--project", "myproj", "--group", "session-output")
    assert r2.stdout.strip() == ""


def test_reconcile_schema_only_rejects_all_projects(base_env):
    r = _run(
        base_env, "pdata", "reconcile-session-output",
        "--all-projects", "--schema-only",
    )
    assert r.returncode == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ccst_pdata_reconcile_cli.py -v`
Expected: FAIL — `reconcile-session-output` is not a recognised `pdata` verb (argparse error,
exit 2 for the "invalid choice" cases; the others fail differently once the verb exists but
before the handler is wired).

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/cc_session_tools/cli/ccst.py, in the "---------- pdata ----------" section

def _cmd_pdata_reconcile_session_output(args: argparse.Namespace) -> int:
    from cc_session_tools.lib import roots
    from cc_session_tools.lib.pdata import session_output

    if args.schema_only and args.all_projects:
        print(
            "ccst pdata: --schema-only requires --project (not --all-projects)",
            file=sys.stderr,
        )
        return 2

    try:
        if args.all_projects:
            targets = session_output.discover_projects_with_sessions()
        else:
            root = session_output.find_project_root(args.project)
            if root is None:
                print(
                    f"ccst pdata: no project {args.project!r} found with a cc-sessions/ "
                    f"directory under $CLAUDE_SESSION_TOOLS_REPO_ROOT or "
                    f"$CLAUDE_SESSION_TOOLS_PROJ_ROOT",
                    file=sys.stderr,
                )
                return 1
            targets = [(args.project, root)]
    except roots.RootsConfigError as exc:
        print(f"ccst pdata: {exc}", file=sys.stderr)
        return 1

    if args.schema_only:
        # Bootstraps the session_tag column and the file_path partial index (Task 3) without
        # scanning cc-sessions/*/out/ — the fast path the pm-update-central-files skill's own
        # AUTO item calls before its per-file registration loop, so that loop's dedupe query
        # (Task 5's _is_already_registered) runs against the index instead of an unindexed scan
        # (spec Goal G5). A full (non-schema-only) reconcile also ensures the schema as a side
        # effect (see reconcile_project), so this flag only matters when the caller wants the
        # schema bootstrapped WITHOUT also backfilling every unregistered file.
        (name, _root), = targets
        session_output.ensure_session_output_schema(name)
        print(f"{name}: schema ensured")
        return 0

    for name, root in targets:
        result = session_output.reconcile_project(name, root, dry_run=args.dry_run)
        suffix = " (dry-run)" if args.dry_run else ""
        print(f"{name}: scanned {result.scanned}, registered {result.registered}{suffix}")
    return 0
```

```python
# add to pdata_sub in _build_parser(), src/cc_session_tools/cli/ccst.py

    pdata_reconcile_parser = pdata_sub.add_parser(
        "reconcile-session-output",
        help="Backfill the session-output index from cc-sessions/*/out/ on disk (idempotent)",
    )
    pdata_reconcile_target = pdata_reconcile_parser.add_mutually_exclusive_group(required=True)
    pdata_reconcile_target.add_argument("--project", metavar="NAME")
    pdata_reconcile_target.add_argument("--all-projects", action="store_true")
    pdata_reconcile_parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what would be registered without writing anything",
    )
    pdata_reconcile_parser.add_argument(
        "--schema-only", action="store_true",
        help="Bootstrap the session-output schema/index for --project and exit "
             "(no file scan or registration)",
    )
```

```python
# add to the pdata dispatch block in main(), src/cc_session_tools/cli/ccst.py

        if args.verb == "reconcile-session-output":
            sys.exit(_cmd_pdata_reconcile_session_output(args))
```

Also append a line to `ccst.py`'s module docstring (matching its existing per-noun listing
style, immediately after the `pdata` lines Plan A added):

```
  pdata reconcile-session-output Backfill the session-output index from cc-sessions/*/out/ on
                                 disk for one project (--project NAME) or every discovered
                                 project (--all-projects). Provisioned as a 7-day ccsched job by
                                 `ccst ccsched-jobs install` — see ccst ccsched-jobs --help.
                                 --schema-only bootstraps the schema/index for --project without
                                 scanning or registering files.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ccst_pdata_reconcile_cli.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/ccst.py tests/test_ccst_pdata_reconcile_cli.py
git commit -m "feat(pdata): add ccst pdata reconcile-session-output"
```

---

## Task 7: `lib/scheduler/bundled_jobs.py` — shared bundled-job registry

**Files:**
- Create: `src/cc_session_tools/lib/scheduler/bundled_jobs.py`
- Create: `tests/test_scheduler_bundled_jobs.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scheduler_bundled_jobs.py
from __future__ import annotations

from cc_session_tools.lib.scheduler import bundled_jobs


def test_bundled_jobs_contains_session_output_reconcile_job():
    ids = [job.job_id for job in bundled_jobs.BUNDLED_CCSCHED_JOBS]
    assert "pm-session-output-reconcile" in ids


def test_session_output_job_command_matches_the_reconcile_cli():
    job = next(
        j for j in bundled_jobs.BUNDLED_CCSCHED_JOBS
        if j.job_id == "pm-session-output-reconcile"
    )
    assert job.command == ("ccst", "pdata", "reconcile-session-output", "--all-projects")
    assert job.cadence == "every:7d"
    assert job.coalesce == "one"


def test_bundled_job_ids_are_unique():
    ids = [job.job_id for job in bundled_jobs.BUNDLED_CCSCHED_JOBS]
    assert len(ids) == len(set(ids))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scheduler_bundled_jobs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cc_session_tools.lib.scheduler.bundled_jobs'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/scheduler/bundled_jobs.py
"""Single source of truth for ccsched jobs CCST provisions automatically at install time. Both
`ccst ccsched-jobs install` (cli/ccst.py) and the `ccst doctor` check (lib/doctor.py) import this
list so the installer and the health check can never disagree about what should be registered
(plan Decision 7). Add a new BundledJob here — do not invent a second place to list one."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BundledJob:
    job_id: str
    cadence: str
    coalesce: str
    catchup_window: str
    timeout: str
    surface: bool
    command: tuple[str, ...]


BUNDLED_CCSCHED_JOBS: tuple[BundledJob, ...] = (
    BundledJob(
        job_id="pm-session-output-reconcile",
        cadence="every:7d",
        coalesce="one",
        catchup_window="7d",
        timeout="300s",
        surface=True,
        command=("ccst", "pdata", "reconcile-session-output", "--all-projects"),
    ),
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_scheduler_bundled_jobs.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/bundled_jobs.py tests/test_scheduler_bundled_jobs.py
git commit -m "feat(scheduler): add shared bundled-ccsched-jobs registry"
```

---

## Task 8: CLI `ccst ccsched-jobs install` + wiring into `install-everything`

**Files:**
- Modify: `src/cc_session_tools/cli/ccst.py`
- Create: `tests/test_ccst_ccsched_jobs_cli.py`
- Modify: `tests/test_ccst_install_everything.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ccst_ccsched_jobs_cli.py
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def _run(env: dict, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True, text=True,
        cwd=str(Path(__file__).parent),
        env=env,
    )


@pytest.fixture
def base_env(tmp_path):
    env = os.environ.copy()
    env["CCST_DATA_HOME"] = str(tmp_path / "data-home")
    return env


def test_dry_run_reports_would_register(base_env):
    r = _run(base_env, "ccsched-jobs", "install")
    assert r.returncode == 0, r.stderr
    assert "would register: pm-session-output-reconcile" in r.stdout


def test_apply_registers_the_job(base_env):
    r = _run(base_env, "ccsched-jobs", "install", "--apply")
    assert r.returncode == 0, r.stderr
    assert "registered: pm-session-output-reconcile" in r.stdout


def test_apply_is_idempotent_on_rerun(base_env):
    _run(base_env, "ccsched-jobs", "install", "--apply")
    r = _run(base_env, "ccsched-jobs", "install", "--apply")
    assert r.returncode == 0, r.stderr
    assert "already registered: pm-session-output-reconcile" in r.stdout
```

```python
# modify tests/test_ccst_install_everything.py — install-everything itself takes NO path-
# override flags (confirmed by reading the file before writing this task: every existing test
# calls `_run("install-everything", ...)` with no target-path arguments and lets skills/hooks/
# shell/claude-md run against the real ~/.claude paths — see test_apply_flag_accepted's own
# comment, "We don't assert rc==0 because the real ~/.claude/skills state varies per
# environment"). Isolation for the ccsched-jobs step specifically comes from the CCST_DATA_HOME
# env var (which lib.scheduler.store.connect() already respects), matching that same existing
# convention rather than inventing new CLI flags this task does not add.

# 1. The step count grew from 5 to 6 — every hardcoded "N/5" assertion must become "N/6" (or the
#    literal new step count), or these three existing tests fail on the renumbering from Task 8's
#    own diff, independent of anything about ccsched jobs. `_cmd_install_everything` itself no
#    longer hardcodes these numbers (they're computed from len(steps) at runtime — see below), but
#    the *tests* still assert on actual printed output, so the literal strings below are what this
#    plan's own steps list produces today. IMPORTANT for whoever adds a further bundled job later
#    (e.g. Plan D's pdata-verify job, which belongs in the SAME `BUNDLED_CCSCHED_JOBS` list per
#    the shared-bundled-jobs design — see Decision 7 and the post-plan note at the end of this
#    file, NOT a second install-everything step): appending to that one list means these six
#    assertions become seven ("1/7".."7/7"), which is expected, ordinary test churn from
#    extending a shared list — it is not evidence of a collision, and no other plan should ever
#    need to touch `_cmd_install_everything`'s step list itself for a new bundled job.

# test_dry_run_is_default — replace the five "N/5" assertions:
    assert "1/6" in result.stdout
    assert "2/6" in result.stdout
    assert "3/6" in result.stdout
    assert "4/6" in result.stdout
    assert "5/6" in result.stdout
    assert "6/6" in result.stdout

# test_no_pypi_flag_accepted — replace:
    assert "5/5" in result.stdout
# with:
    assert "6/6" in result.stdout

# test_section_headers_present — add one more assertion alongside the existing ones:
    assert "Scheduled jobs" in out

# 2. New test, appended to the file:

def test_install_everything_registers_bundled_ccsched_jobs(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["CCST_DATA_HOME"] = str(tmp_path / "data-home")

    result = subprocess.run(
        [
            sys.executable, "-m", "cc_session_tools.cli.ccst", "install-everything",
            "--apply", "--no-pypi",
        ],
        capture_output=True, text=True, cwd=str(Path(__file__).parent.parent), env=env,
    )

    # Not asserting overall returncode == 0 — matches test_apply_flag_accepted's own convention:
    # the real ~/.claude/skills, ~/.claude/settings.json, shell rc, and global CLAUDE.md steps
    # run against whatever this machine's actual state is and may legitimately warn/fail outside
    # a fully-provisioned dev environment. Only the new step's own behaviour is under test here.
    assert "unrecognized arguments" not in result.stderr
    assert "Scheduled jobs" in result.stdout
    assert "registered: pm-session-output-reconcile" in result.stdout
```

`os` and `subprocess`/`sys` must be imported at the top of `tests/test_ccst_install_everything.py`
for this new test if not already present — check the existing top-of-file imports first (the
file's own `_run` helper already uses `subprocess`/`sys`/`Path`, so only `os` is likely new).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ccst_ccsched_jobs_cli.py tests/test_ccst_install_everything.py -v`
Expected: FAIL — `ccsched-jobs` is not a recognised noun; the install-everything test fails on
the missing "Scheduled jobs" step.

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/cc_session_tools/cli/ccst.py, new "---------- ccsched-jobs install ----------" section

def _cmd_ccsched_jobs_install(args: argparse.Namespace) -> int:
    """Register CCST's bundled ccsched jobs (lib/scheduler/bundled_jobs.py) if not already
    present. Idempotent and non-destructive: an existing job id is left completely untouched,
    even if its cadence/timeout has since been hand-edited (plan Decision 8) — "already there" is
    decided by an explicit membership check against registry.load_registry()'s existing ids
    before add_job is ever called, not by attempting the add and catching ccsched add's own
    duplicate-id RegistryError (this repo's "no exceptions for control flow" coding standard
    rules that out — see Decision 8)."""
    from cc_session_tools.lib.scheduler import bundled_jobs, registry
    from cc_session_tools.lib.scheduler.jobspec import validate_job_fields

    existing_ids = {spec.job_id for spec in registry.load_registry()}
    for job in bundled_jobs.BUNDLED_CCSCHED_JOBS:
        if job.job_id in existing_ids:
            print(f"  already registered: {job.job_id}")
            continue
        if not args.apply:
            print(f"  would register: {job.job_id}")
            continue
        spec = validate_job_fields(
            job_id=job.job_id, cadence=job.cadence, coalesce=job.coalesce,
            command=list(job.command), surface=job.surface, enabled=True,
            catchup_window=job.catchup_window, timeout=job.timeout,
        )
        registry.add_job(spec)
        print(f"  registered: {job.job_id}")

    if not args.apply:
        print("\nDry run — re-run with --apply to register any missing job(s)")
    return 0
```

```python
# add a new top-level noun in _build_parser(), src/cc_session_tools/cli/ccst.py

    # ---- ccsched-jobs ----
    ccsched_jobs_parser = sub.add_parser(
        "ccsched-jobs", help="Provision CCST-bundled ccsched jobs",
    )
    ccsched_jobs_sub = ccsched_jobs_parser.add_subparsers(dest="verb", metavar="<verb>")
    ccsched_jobs_sub.required = True
    ccsched_jobs_install_parser = ccsched_jobs_sub.add_parser(
        "install", help="Register bundled jobs not already present (dry run by default)",
    )
    ccsched_jobs_install_parser.add_argument(
        "--apply", action="store_true", help="Register jobs (default: dry run)",
    )
```

```python
# add to main() dispatch, src/cc_session_tools/cli/ccst.py

    if args.noun == "ccsched-jobs":
        if args.verb == "install":
            sys.exit(_cmd_ccsched_jobs_install(args))
```

**This is the single shared integration point for every CCST-bundled scheduled job.** Plan D's own
`pdata-verify` job (Decision 7 above, and the hand-off note at the end of this plan) must be
provisioned by appending a second `BundledJob` entry to `lib/scheduler/bundled_jobs.py`, **not**
by adding its own separate `install-everything` step, its own separate dispatch entry, or its own
separate provisioning module. If Plan D's own plan document defines any of those, that is exactly
the collision this note exists to prevent — reconcile by deleting Plan D's separate
step/dispatch-entry/mechanism entirely and appending its job to the shared `BUNDLED_CCSCHED_JOBS`
list instead; `ccst ccsched-jobs install` and the doctor check (Task 9) already iterate the whole
list, so a second bundled job needs zero further `install-everything` wiring.

Extend the existing `steps`/`dispatch` tables (found in `_cmd_install_everything`) with a new
"Scheduled jobs" entry. The `_INSTALL_STEPS` module constant is dead code today — nothing in the
codebase reads it; `_cmd_install_everything` already builds its own inline `steps` list instead —
so this task deletes it rather than hand-syncing a second copy of the step list (this repo's "one
source of truth" coding standard: two lists describing the same six steps drift the moment one is
edited and the other isn't). The step-number labels (`"1/6"`, `"2/6"`, ...) are also no longer
hardcoded into each tuple: they're computed from `len(steps)` at runtime, so a later change to the
step count (e.g. a second bundled job correctly landing in this same list, per the paragraph
above) renumbers automatically with no further source edits needed here — only the test
assertions need to catch up (see the test block above):

```python
# modify src/cc_session_tools/cli/ccst.py — delete _INSTALL_STEPS (dead code: nothing in the
# codebase reads it today) and change _cmd_install_everything to compute step numbers from
# len(steps) at runtime instead of hardcoding them into each step's label

def _cmd_install_everything(args: argparse.Namespace) -> int:
    """Run all install steps in sequence, then health-check."""
    apply: bool = args.apply
    no_pypi: bool = args.no_pypi

    steps: list[tuple[str, str, object]] = [
        (
            "Skills",
            "skills",
            argparse.Namespace(source=None, target=None, apply=apply, force=False),
        ),
        (
            "Hooks",
            "hooks",
            argparse.Namespace(
                source=None,
                hook=None,
                target=str(Path.home() / ".claude" / "settings.json"),
                apply=apply,
            ),
        ),
        (
            "Shell helpers",
            "shell",
            argparse.Namespace(apply=apply, rc_file=None),
        ),
        (
            "Global CLAUDE.md",
            "claude-md",
            argparse.Namespace(target=None, apply=apply),
        ),
        (
            "Scheduled jobs",
            "ccsched-jobs",
            argparse.Namespace(apply=apply),
        ),
    ]

    dispatch: dict[str, object] = {
        "skills": _cmd_skills_install,
        "hooks": _cmd_hooks_install,
        "shell": _cmd_shell_install,
        "claude-md": _cmd_claude_md_install,
        "ccsched-jobs": _cmd_ccsched_jobs_install,
    }

    # +1 accounts for the trailing health check below, which isn't itself a `steps` entry.
    total_steps = len(steps) + 1
    overall_rc = 0
    for i, (label, key, step_args) in enumerate(steps, start=1):
        print(f"\n=== {i}/{total_steps}  {label} ===")
        rc = dispatch[key](step_args)  # type: ignore[operator]
        if rc != 0:
            overall_rc = rc

    print(f"\n=== {total_steps}/{total_steps}  Health check ===")
```

Also update `ccst.py`'s module docstring — the `install-everything` one-liner near the top of the
file — to mention the new step (matching how Plan D's own equivalent task updates this same
docstring):

```
  install-everything             Run all install steps (skills, hooks, shell,
                                 claude-md, scheduled jobs) then health-check.
                                 Dry run by default; pass --apply to write changes.
```

(Everything else in `_cmd_install_everything` is unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ccst_ccsched_jobs_cli.py tests/test_ccst_install_everything.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/ccst.py tests/test_ccst_ccsched_jobs_cli.py tests/test_ccst_install_everything.py
git commit -m "feat(scheduler): add ccst ccsched-jobs install and wire it into install-everything"
```

---

## Task 9: `ccst doctor` — bundled ccsched job check

**Files:**
- Modify: `src/cc_session_tools/lib/doctor.py`
- Modify: `tests/test_ccst_doctor.py`

- [ ] **Step 1: Write the failing test**

```python
# modify the top-of-file import block in tests/test_ccst_doctor.py: add
# `check_ccsched_job_registered` to the `from cc_session_tools.lib.doctor import (...)` block,
# alphabetically alongside the other `check_*` names already imported there. No new top-level
# imports needed otherwise — every new test below uses the `monkeypatch` fixture, matching this
# file's own existing convention, rather than `os.environ.copy()`.

# then append to tests/test_ccst_doctor.py:

def test_check_ccsched_job_registered_ok_when_present_and_enabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path))
    from cc_session_tools.lib.scheduler import registry
    from cc_session_tools.lib.scheduler.jobspec import validate_job_fields

    spec = validate_job_fields(
        job_id="pm-session-output-reconcile", cadence="every:7d", coalesce="one",
        command=["ccst", "pdata", "reconcile-session-output", "--all-projects"],
        surface=True, enabled=True, catchup_window="7d", timeout="300s",
    )
    registry.add_job(spec)

    result = check_ccsched_job_registered("pm-session-output-reconcile")
    assert result.status == Status.OK


def test_check_ccsched_job_registered_warns_when_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path))
    result = check_ccsched_job_registered("pm-session-output-reconcile")
    assert result.status == Status.WARN
    assert "not registered" in result.reason


def test_check_ccsched_job_registered_warns_when_disabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path))
    from cc_session_tools.lib.scheduler import registry
    from cc_session_tools.lib.scheduler.jobspec import validate_job_fields

    spec = validate_job_fields(
        job_id="pm-session-output-reconcile", cadence="every:7d", coalesce="one",
        command=["ccst", "pdata", "reconcile-session-output", "--all-projects"],
        surface=True, enabled=True, catchup_window="7d", timeout="300s",
    )
    registry.add_job(spec)
    registry.set_enabled("pm-session-output-reconcile", False)

    result = check_ccsched_job_registered("pm-session-output-reconcile")
    assert result.status == Status.WARN
    assert "disabled" in result.reason


def test_run_all_checks_includes_bundled_ccsched_job_checks(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path))
    settings = tmp_path / "settings.json"
    settings.write_text('{"hooks": {}}')
    bundle = Path(__file__).parent.parent / "config" / "hooks-bundle.json"
    results = run_all_checks(
        installed_version="1.2.0",
        settings_path=settings,
        bundle_path=bundle,
        skills_source_dir=None,
        skills_target_dir=tmp_path / "skills",
        env={"CLAUDE_SESSION_TOOLS_REPO_ROOT": None, "CLAUDE_SESSION_TOOLS_PROJ_ROOT": None},
        skip_pypi=True,
    )
    names = [r.name for r in results]
    assert "ccsched-job:pm-session-output-reconcile" in names
```

`registry.set_enabled(job_id, enabled)` is the confirmed API (`lib/scheduler/registry.py`,
already used by `cli/ccsched.py`'s own `_cmd_set_enabled` for `ccsched enable`/`disable`) —
`registry.set_enabled("pm-session-output-reconcile", False)` in the test above is correct as
written.

Also confirm `test_ccst_doctor.py`'s existing top-of-file imports already bring in `os` for the
`os.environ.copy()` call above; add the import if not already present, following whatever style
the rest of the file already uses for environment-dependent tests.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ccst_doctor.py -v`
Expected: FAIL with `NameError: name 'check_ccsched_job_registered' is not defined`

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/cc_session_tools/lib/doctor.py

def check_ccsched_job_registered(job_id: str) -> CheckResult:
    """WARN (not FAIL) if a CCST-bundled ccsched job (lib/scheduler/bundled_jobs.py) is missing
    or disabled — recoverable by re-running `ccst ccsched-jobs install --apply` /
    `ccsched enable <id>`, not a silent-data-loss risk (this repo's version policy reserves FAIL
    for breaking on-disk migrations, which this is not)."""
    from cc_session_tools.lib.scheduler import registry

    name = f"ccsched-job:{job_id}"
    try:
        specs = registry.load_registry()
    except registry.RegistryError as exc:
        return CheckResult(name=name, status=Status.WARN, reason=f"ccsched.db unreadable: {exc}")
    for spec in specs:
        if spec.job_id == job_id:
            if spec.enabled:
                return CheckResult(name=name, status=Status.OK, reason="registered and enabled")
            return CheckResult(
                name=name, status=Status.WARN,
                reason=f"registered but disabled — run 'ccsched enable {job_id}'",
            )
    return CheckResult(
        name=name, status=Status.WARN,
        reason="not registered — run 'ccst ccsched-jobs install --apply'",
    )
```

```python
# add to run_all_checks() in src/cc_session_tools/lib/doctor.py, after the "Skill symlinks" block

    # Bundled ccsched jobs
    from cc_session_tools.lib.scheduler import bundled_jobs

    for job in bundled_jobs.BUNDLED_CCSCHED_JOBS:
        results.append(check_ccsched_job_registered(job.job_id))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ccst_doctor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/doctor.py tests/test_ccst_doctor.py
git commit -m "feat(doctor): add bundled ccsched job health check"
```

---

## Task 10: Move + rename the skill — `skills/pm-update-central-files/SKILL.md` (CCST)

**Files:**
- Create: `skills/pm-update-central-files/SKILL.md`

No test-first step for this task — it is a documentation/skill-definition file, not code. `ccst
skills install`/`ccst doctor`'s existing symlink check (Task 4's `check_skill_symlink`, already
shipped) picks up any directory under `skills/` with a `SKILL.md` automatically, with no source
change — confirmed by reading `_discover_skills()` in `cli/ccst.py` before writing this task.

- [ ] **Step 1: Create the skill directory and file**

```bash
mkdir -p skills/pm-update-central-files
```

- [ ] **Step 2: Write the full `SKILL.md`**, adapted from CCCS's
`skills/update-central-files/SKILL.md` (renamed throughout, plus the new session-output AUTO
item):

```markdown
---
name: pm-update-central-files
description: Use when the user is about to end a session or wants all coordination and record-keeping files brought up to date before exit. Triggers on "/pm-update-central-files", "wrap up the session", "update all the files", "before I exit", "bring everything up to date", "sync session state", or any prompt that signals pre-exit cleanup. Auto-updates items already mandated by global CLAUDE.md (WORKLOG.md, auto-memory, session-output index) without asking; presents a checkpoint table only for items that need human judgement (out/ deliverable renames, project CLAUDE.md edits, central coordination files, git commits).
---

# Update Central Files (pm-)

## Overview

Brings every file the session is responsible for up to date before the user exits. Invoked when the user wants to close the session cleanly without having to itemise each file type that needs updating.

The user runs multiple sessions that coordinate via shared files in a parent folder. Missing an update here means the next session starts with stale state. This is a `pm-` (project-management-family) skill: it manages per-project state, not this-project-only state — see `ccst pdata --help` for the shared per-project data store it now writes into (spec section 8, `2026-07-26-per-project-data-store-spec.md`).

## When to Use

- User invokes `/pm-update-central-files` directly
- User says "wrap up the session", "update all the files", "before I exit", "bring everything up to date"
- User signals the session is ending and updates have been deferred

Do NOT use for:
- Mid-session incremental saves - just write the specific file
- Fresh sessions that have not produced anything yet
- Sessions that were pure read/exploration with no state changes

## Two classes of items

**AUTO** items are already mandated by global CLAUDE.md (WORKLOG, memory rules) or are pure bookkeeping with no judgement call (the session-output index). Asking permission for them wastes the user's cycles since the answer is already required to be yes. Apply them immediately and report what was done.

**CHECKPOINT** items involve human judgement (file rename decisions, durable-fact gates, commit messages, per-file diff calls). Present these as a table and wait for approval.

### AUTO items

1. **WORKLOG.md** (`cc-sessions/<session>/working/WORKLOG.md`) - Rewrite in full reflecting the complete session history. NEVER ask permission - just do it. If it does not exist, create it.
2. **Auto-memory** - Apply CLAUDE.md memory rules: write durable, non-obvious items (user / feedback / project / reference); update or remove stale entries; keep `MEMORY.md` index in sync. NEVER write memory for ephemeral session content or things derivable from git.
3. **Session-output index** (`ccst pdata`, spec section 8) - for every file under this session's `cc-sessions/<session>/out/` that is not already registered, add it to the project's `session-output` index. `<project>` is the working directory's basename (the current project).

   First, ensure the extension schema (and its file_path index — see `ccst pdata
   reconcile-session-output`'s `--schema-only`) exists (idempotent - safe to re-run every time):
   ```
   ccst pdata reconcile-session-output --project <project> --schema-only
   ```
   Then, for each file `<name>` under `cc-sessions/<session>/out/`, check whether it is already registered:
   ```
   ccst pdata query --project <project> --group session-output --where "file_path = cc-sessions/<session>/out/<name>" --limit 1
   ```
   If that returns zero rows, register it:
   ```
   ccst pdata add --project <project> --group session-output --content "<one-line description, or the filename if none is obvious>" --file "cc-sessions/<session>/out/<name>" --field session_tag=<session>
   ```
   NEVER ask permission - like WORKLOG/memory, this is pure bookkeeping, not a judgement call (the only judgement involved - the one-line description - has a safe fallback: the filename). A 7-day `ccsched` job (`pm-session-output-reconcile`) backfills anything this step misses (a crashed or non-interactive session) - see `ccst pdata reconcile-session-output --help`. Do not overwrite an already-registered file's row; the reconciliation job's own semantics are also insert-if-missing, never update-on-every-run, so the two paths stay consistent.

### CHECKPOINT items

1. `cc-sessions/<session>/out/` deliverables that need version bumps:
    - File has a version suffix and content changed this session -> bump (e.g. `.v3.md` -> `.v4.md`). Surface in the table; safe to apply on approval.
    - File has NO version suffix and was updated -> ask whether to rename existing to `.v1.<ext>` before writing `.v2.<ext>`. NEVER rename without approval.
2. **Project `CLAUDE.md`** (working dir) - if a durable project-level fact was learned this session that warrants codifying. Do NOT add session narrative.
3. **Central coordination files** in the parent folder (INDEX.md, PEOPLE.md, STATUS.md, timelines, tracking logs) - per-file judgement. Read first, diff mentally, propose minimal updates.
4. **Git** - if the working directory is a git repo with uncommitted session changes, show `git status` and ask whether to commit. NEVER auto-commit (per global CLAUDE.md). Follow the one-branch-per-feature and small-coherent-commits rules.
5. **Correspondence audit** (conditional - see Step 1b) - only in projects where the working directory has a `correspondence/` folder and a project CLAUDE.md that mandates archiving. Surfaces any session-referenced messages not archived to `correspondence/`. Present as NEEDS ACTION (list each gap with "archive via archive-correspondence skill") or OK (no gaps). Never skip this row in qualifying projects.

## Process

### Step 1: Survey

Before doing anything, list:
- `cc-sessions/<session>/` contents (WORKLOG.md status, `out/`, `working/`)
- Working directory for a project `CLAUDE.md`
- Parent folder of working directory for central coordination files (files only, not just dir listing)
- `git status` if a repo
- `~/.claude/projects/<...>/memory/` to see what already exists
- `ccst pdata list --project <project> --group session-output` to see what the index already has for this project (informs Step 2's session-output AUTO item - only new files need adding)

### Step 1b: Correspondence audit (conditional)

Run this sub-step only if BOTH are true:
- The working directory contains a `correspondence/` folder
- The project CLAUDE.md exists and contains mandatory correspondence-archiving rules (look for the word "correspondence" near words like "mandatory", "MUST", or "archive")

If either condition is absent, mark correspondence audit as N/A in the Step 3 table and proceed.

If both conditions are met:

1. **Recall all correspondence references from this session.** Scan the session WORKLOG.md and recall tool calls from memory for any mention of specific messages retrieved, sent, or discussed - by ID, date, sender, subject, or platform (OFW, Gmail, WhatsApp, SMS, email). Look especially for:
   - Calls to platform MCP tools that return message content (`our-family-wizard_get_message`, `our-family-wizard_list_messages`, `gmail_read_message`, `whatsapp_list_messages`, or equivalents)
   - Messages sent via this session
   - WORKLOG entries describing correspondence as "outstanding", "not archived", "retrieved", "read", or "discussed"
   - Message IDs, dates, senders, or subjects mentioned in the context of fetching correspondence

2. **List `correspondence/`.** Run `ls <working-dir>/correspondence/` and note the filenames.

3. **Cross-check each reference.** The naming pattern is `<yyyy.MM.dd> <HHmm> - <sender> <channel> to <recipient>.<ext>`. A reference is covered if a `.md` file exists for that message (matching date, sender, and channel). A visual record (`.png` or `.pdf`) should also be present - note `.md`-only entries as a secondary gap.

4. **Compile the gap list.** Any referenced message with no matching `.md` in `correspondence/` is an unarchived gap. Messages flagged as "outstanding" in the WORKLOG count as gaps even if not individually identified in step 1.

5. **Add to the Step 3 CHECKPOINT table.** One row per unarchived message (or a single OK row if none). Proposed action for each gap: "Archive via archive-correspondence skill".

### Step 2: Apply AUTO items immediately

Write WORKLOG.md, write/update memory and `MEMORY.md` index, and register this session's new `out/` files into the session-output index. Do not ask - the global rules already mandate the first two, and the third is pure bookkeeping with a safe fallback (see AUTO item 3). In your response, briefly state what auto-applied (one line each), including a count of session-output rows added.

### Step 3: Present CHECKPOINT items as a table

Only the items that genuinely need user input. Columns: target, proposed verdict (UPDATE / SKIP / NEEDS INPUT), one-line detail. Wait for approval before writing anything in this list. If the table has zero rows, say so explicitly: "No checkpoint items - wrap-up complete after auto-applies."

### Step 4: Apply approved CHECKPOINT updates

Work through the approved list. Minimal, surgical edits.

### Step 5: Final report

Summarise: what auto-applied, what checkpoint-approved-and-applied, what skipped, what still open. Include file paths so the user can spot-check.

## Common Mistakes

- Asking permission for WORKLOG / memory / session-output-index updates - these are AUTO; the answer is already mandated
- Rewriting WORKLOG.md with only the latest chunk instead of the full session history
- Forgetting to bump version numbers on versioned deliverables
- Renaming an unversioned file to `.v1.<ext>` without asking first
- Adding session narrative to project CLAUDE.md (that is what WORKLOG and memory are for)
- Duplicating memory entries - check `MEMORY.md` for an existing one to update first
- Auto-committing to git without approval
- Skipping central coordination files because they "look old" - stable does not mean stale
- Treating `working/` files as deliverables requiring version bumps - they are scratch
- Skipping the correspondence audit in a project that mandates archiving - retrieved messages left unarchived will start the next session with stale state and may miss the gap-closing window
- Re-registering an already-indexed `out/` file (or updating its content) on every session-end run - the session-output index is insert-if-missing, never update-on-every-run; leave already-registered rows alone

## Red Flags

- About to ask "should I update WORKLOG.md" - STOP, just write it
- Asking "should I add a memory entry" before applying CLAUDE.md memory rules - STOP, apply the rules
- About to skip the session-output index step because "nothing in out/ looks important" - STOP, register every file, not just the ones that look significant
- About to apply a CHECKPOINT item without showing the user what's about to change - STOP, present the table
- Assuming no central coordination files exist without having listed the parent folder
- Creating a `.v2` without confirming the existing file is already `.v1` or needs renaming
- Writing memory entries that summarise this session rather than capture durable facts
- About to report wrap-up complete in a project with a `correspondence/` directory without having run the correspondence audit - STOP, check Step 1b first

## Quick Reference

| File type | Location | Class | Action |
|---|---|---|---|
| WORKLOG.md | `cc-sessions/<session>/working/` | AUTO | Rewrite in full, no ask |
| Memory + MEMORY.md | `~/.claude/projects/<...>/memory/` | AUTO | Apply CLAUDE.md memory rules, no ask |
| Session-output index | `ccst pdata` `session-output` group | AUTO | Register new `out/` files, no ask; never update already-registered rows |
| Versioned deliverable (`.vN.ext`) | `cc-sessions/<session>/out/` | CHECKPOINT | Bump version, surface in table |
| Unversioned deliverable | `cc-sessions/<session>/out/` | CHECKPOINT | Ask before renaming to `.v1.<ext>` |
| Scratch | `cc-sessions/<session>/working/` | AUTO | Overwrite in place |
| Project CLAUDE.md | Working dir | CHECKPOINT | Surface durable-fact change for approval |
| Central files | Parent folder | CHECKPOINT | Read, diff, propose minimal update |
| Git | Working dir repo | CHECKPOINT | Show status, ask for commit |
| Correspondence audit | `correspondence/` in working dir | CHECKPOINT (conditional) | Run Step 1b; surface gaps with "archive via archive-correspondence skill" |
```

- [ ] **Step 3: Verify the doctor/symlink machinery picks it up with no source change**

Run: `uv run python -m cc_session_tools.cli.ccst skills install --source skills --target /tmp/ccst-skills-check --apply`
Expected: output includes `linked: /tmp/ccst-skills-check/pm-update-central-files -> .../skills/pm-update-central-files`, confirming `_discover_skills()` found it automatically. Clean up: `rm -rf /tmp/ccst-skills-check`.

- [ ] **Step 4: Commit**

```bash
git add skills/pm-update-central-files/SKILL.md
git commit -m "feat(skills): add pm-update-central-files (moved from claude-code-config-sync)"
```

---

## Task 11: Full suite, version bump, CHANGELOG (CCST side)

**Files:**
- Modify: `pyproject.toml`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS, 0 failures.

- [ ] **Step 2: Run `mypy --strict` on everything this plan touched**

Run: `uv run mypy --strict src/cc_session_tools/lib/pdata/session_output.py src/cc_session_tools/lib/scheduler/bundled_jobs.py src/cc_session_tools/lib/doctor.py src/cc_session_tools/cli/ccst.py`
Expected: no errors.

- [ ] **Step 3: Bump the version (minor)**

Read the current `version` in `pyproject.toml` at the time this task actually runs (do not assume
a specific predecessor number — Plan B may or may not have already landed and bumped it, see the
Versioning section above) and bump its minor component by one, e.g. if the file currently reads
`1.1.0` (Plan A only) this task sets it to `1.2.0`; if Plan B already landed and bumped to
`1.2.0`, this task sets it to `1.3.0`.

```toml
# pyproject.toml
[project]
name = "cc-session-tools"
version = "<current-minor + 1>"
```

- [ ] **Step 4: Add the CHANGELOG entry**

```markdown
### Added

- **Session-output index + `pm-update-central-files`.** `ccst pdata reconcile-session-output`
  backfills a per-project `session-output` record_group (on `ccst pdata`'s existing schema/CLI)
  from every `cc-sessions/*/out/` file on disk, incrementally via a per-project watermark.
  `ccst ccsched-jobs install` (wired into `ccst install-everything` as a 6th step) provisions a
  7-day job that runs it automatically; `ccst doctor` gets a matching health check. The
  `update-central-files` skill moves here from `claude-code-config-sync`, renamed
  `pm-update-central-files` (establishing the `pm-` prefix for cross-project,
  project-management-family skills), and gains an AUTO item that registers each session's `out/`
  deliverables into the index. See `docs/superpowers/plans/2026-07-30-ccst-pm-update-central-files.md`.
```

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "chore(pdata): bump version for session-output index + pm-update-central-files"
```

---

## Task 12 (separate repository: `claude-code-config-sync`) — remove the old skill

**Repository:** `~/repos/claude-code-config-sync` (not the CCST worktree — switch directories for
this task and the two after it).

**Files:**
- Delete: `skills/update-central-files/SKILL.md`
- Modify: `README.md`

- [ ] **Step 1: Create a feature branch** (per this repo's own `CLAUDE.md` convention)

```bash
cd ~/repos/claude-code-config-sync
git checkout -b f/20260730-migrate-update-central-files-to-ccst
```

- [ ] **Step 2: Confirm the CCST copy is deployed and working before removing this one**

Run: `ccst skills install --apply` (from the CCST repo/worktree, once Tasks 1-11 are merged and
`uv tool install --reinstall ~/repos/claude-code-session-tools` has been run — see this repo's
own CLAUDE.md "After merging a PR" section). Confirm `~/.claude/skills/pm-update-central-files`
now exists and is a symlink into the CCST skill directory before proceeding — non-destructive
migration discipline (verify new before removing old), matching this whole design's own
migration-safety principle.

- [ ] **Step 3: Remove the skill and its README row**

```bash
git rm skills/update-central-files/SKILL.md
```

Edit `README.md`'s Custom Skills table: delete the row
`| `update-central-files` | Pre-exit wrap-up: WORKLOG / memory / deliverable rename / git / Evernote tags | Stable |`.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "chore: remove update-central-files (migrated to CCST as pm-update-central-files)"
```

---

## Task 13 (CCCS) — declare the skill as externally-managed + bump the pin

**Files:**
- Modify: `config/external-dependencies.json`
- Modify: `config/settings.json`

- [ ] **Step 1: Add the skill to `cc-session-tools`'s `provides.skills`, and bump `pinned_version`**

Per this repo's own `CLAUDE.md` sync-discipline rule ("when wiring a new skill ... that comes
from an external repo, add it to that repo's `provides.skills` array in the SAME commit... if the
functionality is new, also bump `pinned_version`"). `pinned_version` must match the actual
`version` Task 11 left in CCST's `pyproject.toml` — do not hardcode `1.2.0` here: Task 11 itself
reads whatever version is on disk when it runs and bumps the minor component from there (see this
plan's Versioning section), so the value that belongs here is whatever that task actually
produced (`1.2.0` if only Plan A had landed first, `1.3.0` if Plan B had already bumped it, etc.).
The `cc-session-tools` entry in `config/external-dependencies.json`'s top-level
`external_dependencies` array becomes (in full — only `pinned_version` changed and
`pm-update-central-files` appended to `provides.skills`, at the end of the list to match how the
two most-recently-added entries, `select-agent-model` and `do-executor-critic-assessor-loop`,
were themselves appended rather than inserted alphabetically; `description`, `install_steps`, and
`provides.hook_verbs` are all byte-for-byte unchanged; `<ccst-version>` below is a placeholder —
substitute the real version string):

```json
{
  "name": "cc-session-tools",
  "description": "Session analysis, hook shims, shell helpers, and session-management skills",
  "pinned_version": "<ccst-version>",
  "install_steps": [
    {
      "label": "clone repo (if not already present)",
      "command": "git clone https://github.com/raffishquartan/claude-code-session-tools.git ~/repos/claude-code-session-tools"
    },
    {
      "label": "run install-everything (installs CLIs, skills, hooks, shell helpers)",
      "command": "bash ~/repos/claude-code-session-tools/install-everything.sh"
    }
  ],
  "provides": {
    "skills": [
      "analyse-cc-usage",
      "delete-sessions",
      "find-claude-code-session",
      "generate-8digit-code",
      "list-empty-sessions",
      "manage-recurring-cc-jobs-using-ccsched",
      "move-session",
      "reduce-persistent-context",
      "send-session-message",
      "update-command-cache",
      "select-agent-model",
      "do-executor-critic-assessor-loop",
      "pm-update-central-files"
    ],
    "hook_verbs": [
      "session-tag",
      "bash-security-review",
      "confirm-8digit",
      "after-response",
      "catchup",
      "last-screenshot",
      "marker-allow",
      "messaging-deliver",
      "worklog-guard",
      "bash-hard-deny",
      "pending-migration"
    ]
  }
}
```

- [ ] **Step 2: Update the permission allowlist entry in `config/settings.json`**

Find `"Skill(update-central-files)"` in the `permissions.allow` array and rename it:

```json
"Skill(pm-update-central-files)"
```

- [ ] **Step 3: Verify drift**

Run: `bash hooks/session-start/check-config-drift.sh`
Expected: exits 0, no drift warning about `update-central-files`/`pm-update-central-files`.

- [ ] **Step 4: Commit**

```bash
git add config/external-dependencies.json config/settings.json
git commit -m "chore: declare pm-update-central-files as externally-managed, bump cc-session-tools pin"
```

- [ ] **Step 5: Push and open a PR** (per this repo's own CLAUDE.md — `main` is protected)

```bash
git push -u origin f/20260730-migrate-update-central-files-to-ccst
gh pr create --title "Migrate update-central-files to CCST as pm-update-central-files" --body "$(cat <<'EOF'
## Summary
- Removes skills/update-central-files/ — it now lives in claude-code-session-tools as
  pm-update-central-files (see that repo's docs/superpowers/plans/2026-07-30-ccst-pm-update-central-files.md).
- Declares it externally-managed in config/external-dependencies.json, bumps the
  cc-session-tools pin to match the version CCST's own Task 11 landed on.
- Renames the Skill(update-central-files) permission-allowlist entry in config/settings.json.

## Test plan
- [ ] bash hooks/session-start/check-config-drift.sh exits 0
- [ ] `~/.claude/skills/pm-update-central-files` resolves to a valid CCST skill symlink after
      `ccst skills install --apply`
EOF
)"
```

---

## Task 14 (machine state, not a commit) — redirect the deployed copy

This is a one-time local-machine step, not a code change — run it once both the CCST PR (Tasks
1-11) and the CCCS PR (Tasks 12-13) have merged and CCST has been reinstalled per this repo's own
CLAUDE.md ("After merging a PR": `uv tool install --reinstall ~/repos/claude-code-session-tools`).

- [ ] **Step 1: Remove the stale deployed copy**

CCCS deploys custom skills as a **plain copy**, not a symlink (see its README's Step 7), so
deleting the CCCS repo's source directory (Task 12) does not remove the already-deployed copy at
`~/.claude/skills/update-central-files/`. Remove it explicitly:

```bash
rm -rf ~/.claude/skills/update-central-files
```

- [ ] **Step 2: Install the new symlink**

```bash
ccst skills install --apply
```

Expected: output includes `linked: ~/.claude/skills/pm-update-central-files -> .../skills/pm-update-central-files`.

- [ ] **Step 3: Provision the ccsched job on this machine**

```bash
ccst ccsched-jobs install --apply
```

Expected: `registered: pm-session-output-reconcile` (or `already registered:` if
`install-everything` already ran and provisioned it as part of Tasks 1-11's own verification).

- [ ] **Step 4: Confirm `~/.claude/settings.json`'s own permission allowlist (if it independently
carries a copy of the CCCS `config/settings.json` entries) is updated too**

```bash
grep -n "Skill(update-central-files)" ~/.claude/settings.json
```

If this matches, edit `~/.claude/settings.json` by hand to rename that entry to
`"Skill(pm-update-central-files)"` — this file is not managed by this plan's automated tooling,
so the rename here is manual, matching how any other locally-drifted settings.json entry would
be reconciled per CCCS's own drift-capture workflow (see that repo's CLAUDE.md, "Capturing local
changes back into the repo").

- [ ] **Step 5: Verify**

```bash
ccst doctor
```

Expected: `skill:pm-update-central-files` and `ccsched-job:pm-session-output-reconcile` both
report `OK`.

---

## Post-plan note for whoever picks up Plan D (verify/scheduling/skills)

**This is a binding constraint on Plan D, not a suggestion — read it before writing Plan D's own
Task 8/9.** `lib/scheduler/bundled_jobs.py` (Task 7) is the single place a new install-time
ccsched job gets declared. Plan D's `ccst pdata verify` job MUST add a second `BundledJob` entry
to that same list rather than inventing its own provisioning mechanism (its own
`verify_job.py`/`ensure_pdata_verify_job()` module, its own `registry.add_job()` call, or any
other parallel path). `ccst ccsched-jobs install` (Task 8, this plan) and the doctor check
(Task 9, this plan) already iterate the whole `BUNDLED_CCSCHED_JOBS` list, so a second bundled job
needs zero further wiring beyond appending one `BundledJob` tuple to it.

Concretely, this means Plan D must NOT:
- Define its own `_INSTALL_STEPS`/`steps`/`dispatch` entry for a "pdata-verify job" step in
  `install-everything` — this plan's own "Scheduled jobs" step (`ccst ccsched-jobs install`)
  already provisions every entry in `BUNDLED_CCSCHED_JOBS`, including a second one Plan D adds.
  Two independent plans both rewriting `_cmd_install_everything`'s `_INSTALL_STEPS`/`steps`/
  `dispatch` tables to insert their own step at the same position is exactly the collision this
  note exists to prevent — there is only one such step, and this plan (Task 8) is the one that
  adds it.
- Define its own `check_pdata_verify_job_registered`-style doctor check — `check_ccsched_job_registered`
  (Task 9, this plan) is already generic over any `job_id` and is called once per entry in
  `BUNDLED_CCSCHED_JOBS`; Plan D's job gets a health check for free by being in that list.

If Plan D's own plan document currently does any of the above, the fix is to delete that content
from Plan D and replace it with a single `BundledJob(...)` entry appended to
`BUNDLED_CCSCHED_JOBS` in `lib/scheduler/bundled_jobs.py` — not to reconcile two competing
mechanisms after the fact.

Plan D's `pm-pdata-schema-design`/`pm-pdata-conflict-resolution` skills should also use the `pm-`
prefix convention this plan establishes (Task 10) — no separate decision needed there.
