# ccst pdata init — unified init/migration mechanism (Plan B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `ccst pdata init --project <name> [--rehearse <path>] [--write]` — the unified
day-one/migration mechanism (spec §7) that classifies a project's files (folder-owned vs
db-owned), lets a human review/override the classification, then writes, verifies, backs up,
and cuts over into that project's `ccst pdata` store (Plan A) — plus the `ccst doctor` check for
undeleted migrated-source originals (spec §7.1 step 7) and the `pm-project-init` skill that
drives the whole thing with judgement.

**Architecture:** `ccst pdata init` is a thin CLI verb over a new `lib/pdata/init_service.py`
orchestration layer that calls Plan A's existing `service.py` (`add_record`, `schema_add_field`,
`delete_record`, `get_record`) for every DB write — this plan adds **zero** changes to Plan A's
shipped files. A deliberately conservative file-tree classifier (`classify.py`) proposes CSV/JSON
files as db-owned (their structure is genuinely machine-inferable) and defaults every other file
to folder-owned (a markdown/text file's shape is a per-project judgement call this plan does not
attempt to guess). The proposal is persisted to a project-local JSON file that is never
regenerated once it exists, so a human's hand-edited overrides survive between the no-flag dry
run and the `--write` invocation. `--rehearse <path>` redirects the project root, the `.db`
target, and the pre-cutover backup directory to a self-contained rehearsal sandbox — the `.db` by
reusing Plan A's own `CCST_PROJECT_DB_DIR` test seam, the backup by reusing this plan's own
`CCST_PDATA_BACKUP_DIR` seam — so rehearsal touches nothing real, including the backup tarball a
real `--write` would otherwise deposit outside the sandbox.

**Tech Stack:** Python 3.11+, stdlib `csv`/`json`/`tarfile`/`re`, `argparse`, `pytest`
(subprocess CLI tests matching `tests/test_ccst_pdata_cli.py`).

---

## Scope

**In scope** (spec `2026-07-26-per-project-data-store-spec.md` §7 in full):

- The unified init/migration mechanism: `ccst pdata init --project <name> [--rehearse <path>]
  [--write]`.
- The 8-step procedure in §7.1: rehearsal (step 0), dry-run classification (step 1), human
  review/override (step 2), write phase (step 3), verification (step 4 — entry-count parity, a
  content spot-check, and file_path resolution, plus a human-readable diff report printed on
  success; see Decision 9 for why this report is not a separate pre-cutover interactive gate),
  backup (step 5), cutover (step 6), manual-delete-only archived originals (step 7), rollback
  (step 8).
- The `ccst doctor` check for pending migrations, mirroring the existing
  `check_pending_data_store_migration()` pattern (§7.1 step 7).
- The `pm-project-init` skill (§7's own description: drives `ccst pdata init` and interprets its
  output — judgement reviewing the classification report, not mechanical CLI plumbing).

**Explicitly out of scope** (per the spec and the dispatching prompt — not designed here):

- **The per-project migration content itself** — which files become which `record_group`s, with
  which extension columns, for any of the 10 real `~/cc/<project>` directories. That is
  operational execution using this plan's tooling (driven by `pm-project-init`, informed by the
  already-compiled `2026-07-29-per-project-data-store-inventory.md`), not part of this plan. This
  plan's classifier and its tests use small synthetic fixture projects, never a real project's
  files.
- `ccst pdata verify` and its `ccsched` job (spec §8.2).
- `pm-pdata-schema-design` / `pm-pdata-conflict-resolution` skills (spec §8.1).
- `ccst pdata export` (spec §5 "Project lifecycle").
- `pm-update-central-files` rename and the session-output index (spec §8).
- The backlog in spec §9 (backup mechanism for per-project `.db` files as a general concern,
  common-store timestamp columns, a web UI, etc.) — this plan's own backup step (§7.1 step 5) is
  in scope; the general-purpose backup *mechanism* backlog item is not.
- `pod-reference-people`'s cross-project data gap (spec §10) — unrelated to init/migration.

## Versioning (read before the last task)

Per this repo's `CLAUDE.md` version policy, a major bump is required only for a change that
relocates/reformats/breaks existing on-disk data. This plan adds wholly new, additive
capabilities on top of Plan A (a new CLI verb, new project-local files under `~/cc/<project>/`
that don't exist today, a new doctor check) and changes nothing about how existing `ccst pdata`
data is read or written. That makes this plan a **minor** bump on top of whatever Plan A shipped
(Plan A's own plan bumps `1.0.0` → `1.1.0`). Because Plan C (`pm-update-central-files`) is
sequenced independently and this plan does not read its content, the exact resulting version
number is **not** hardcoded here — the last task reads `pyproject.toml`'s version at the time
this plan is actually executed and bumps the minor component by one from whatever is on disk
then (matching Plan C's and Plan D's own dynamic-read pattern for exactly this reason: two
independent additive plans landing in an undetermined order must not both claim the same next
version number). If only Plan A has landed, that's `1.1.0` → `1.2.0`; if Plan C already landed
first, it's `1.2.0` → `1.3.0`. The `v2.0.0` bump the parent spec's header references belongs to
whichever later effort actually performs the breaking part for a specific real project
(relocating/archiving that project's existing flat files) — running `ccst pdata init --write`
against a real project is an operational action taken with this plan's tooling, not a change to
CCST's own versioned behaviour.

## Necessary implementation decisions beyond the spec's literal text

The spec (read in full before writing code) leaves several mechanics implicit. These are binding
for this plan:

1. **Project root resolution.** `ccst pdata init` needs to know where a project's files live —
   something Plan A never needed, since none of its commands touch the filesystem beyond the
   `.db` itself. New env var `CCST_PROJECTS_ROOT` (default `~/cc`, matching every project
   directory referenced throughout the spec and inventory); project root =
   `<CCST_PROJECTS_ROOT>/<project>`, created (`mkdir -p`-style) if missing, which is what makes
   "safe to run against a project with an empty folder... also how a genuinely new project gets
   its `.db`" (spec §5) true even before the folder exists yet.
2. **`--rehearse <path>` redirects the project root directly to `<path>`.** Spec §7.1 step 0 says
   "Copy the whole project directory elsewhere; run the full procedure... against the copy" — the
   copy itself is a preparatory action for whoever runs the procedure (the `pm-project-init`
   skill or Chris directly, via a plain `cp -r`), not something `ccst pdata init` automates. This
   keeps `--rehearse`'s own contract to one concern: read/write against `<path>` instead of
   `<CCST_PROJECTS_ROOT>/<project>`.
3. **Rehearsal DB and backup isolation each reuse an env-var seam.** DB access reuses Plan A's
   existing `CCST_PROJECT_DB_DIR` seam (`store.PROJECT_DB_DIR_ENV`); the pre-cutover backup
   (Task 5/9) reuses this plan's own `CCST_PDATA_BACKUP_DIR` seam (`backup.BACKUP_DIR_ENV`). Both
   are redirected for the duration of a rehearsed run to self-contained directories under
   `<rehearse-path>/` — `.ccst-pdata-rehearsal-db/` and `.ccst-pdata-rehearsal-backups/`
   respectively — via `init_paths.project_db_dir_override()` and the equally-shaped
   `init_paths.backup_dir_override()` (Task 5), both entered together by `write()` for the
   duration of the whole write/verify/backup phase. This is what gives rehearsal "zero risk to the
   live project" (spec §7.1 step 0) on the DB side **and** the backup artifact: without this
   second seam, a rehearsed `--write` would still deposit a `<project>-<epoch>.tar.gz` into the
   same directory, with the same filename shape, that a genuine production migration's backup
   would use — indistinguishable from a real backup by name alone. Neither seam adds a second
   connection path to Plan A's `repository.py` — Plan A's shipped files are untouched by this
   plan.
4. **Classification-proposal persistence realizes the human-review gate (step 2).** The dry-run
   pass writes `<project_root>/.ccst-pdata-proposal.json` **only if it doesn't already exist** —
   every later no-flag invocation returns the file completely unchanged, so a human's overrides
   (record_group renames, folder-owned→db-owned flips, field-type corrections) are never silently
   clobbered by a re-run. Deleting the file is how a fresh classification pass is forced; there is
   no separate `--reclassify` flag, keeping the CLI's flag surface exactly the two the spec names.
   `--write` always operates on whatever is currently on disk at that path and errors clearly if
   it doesn't exist yet (must dry-run first).
5. **Auto-classification is deliberately conservative for free text.** Only `.csv` and `.json`
   files get an automatic `db-owned` proposal, because their internal structure is genuinely
   machine-inferable (a header row, a JSON array-of-objects, a JSON object) without any
   project-specific judgement. Every `.md`/`.txt`/unrecognized-extension file defaults to
   `folder-owned` — this plan's classifier does **not** try to guess whether a given markdown file
   is an append-only log, a versioned plan document, or a stacked-snapshot journal (spec §4.3);
   that distinction is exactly the per-project judgement call excluded from this plan's scope.
   Known binary/media extensions are also auto-classified `folder-owned` (high-confidence, rarely
   wrong). The `pm-project-init` skill's entire value-add is reviewing this conservative default
   and flipping individual entries, informed by spec §3's framework and (for a real project) the
   already-compiled inventory doc.
6. **Five generic import strategies**, chosen to cover every content-modelling shape the
   inventory found with no per-project logic baked in: `whole-file` (one row, the whole file),
   `delimited-sections` (one row per heading-delimited section — covers both the append-only-log
   shape and the stacked-dated-snapshots shape from spec §4.3, since the spec itself notes
   stacked snapshots need no new mechanism beyond "one row per snapshot, ordered by
   `created_at`"), `csv-rows`, `json-array-rows`, and `json-singleton` (covers spec §4.3's
   singleton-state shape, e.g. `shop-reminder-state.json`).
7. **Field values are always passed to `service.add_record`/`schema_add_field` as strings**,
   matching how `ccst pdata add --field k=v` already only ever sends strings from its own CLI
   boundary (Plan A never coerces `--field` values). SQLite's column-affinity rules convert a
   well-formed numeric string into the extension column's real `INTEGER`/`REAL` storage class on
   insert, so no importer-side type coercion is needed or attempted.
8. **Write-phase atomicity is soft-delete-based rollback, not a single wrapping SQL transaction.**
   Plan A's `service.add_record`/`schema_add_field` each open and commit their own connection per
   call — this plan does not reach into `repository.py`'s transaction primitives to batch them,
   since Plan A's shipped files stay untouched (Decision 3 above extends this principle). Instead,
   every record id inserted during one `--write` run is tracked in memory; if verification fails
   for any entry (or an entry raises `ValueError`/`OSError`/`csv.Error` mid-import, e.g. an
   absolute `file_path_column` value or a malformed CSV row), every id inserted so far in that run
   is soft-deleted (spec §4.5, via `service.delete_record(..., expected_version=1)` — always
   version 1, since these rows were just inserted moments earlier in the same single-threaded run)
   before the command exits non-zero. Each `delete_record` call in that rollback loop is itself
   wrapped, since `service.RecordNotFoundError`/`VersionConflictError` are plain `Exception`
   subclasses the earlier `except (ValueError, OSError, csv.Error)` does not catch — a rollback
   failure is collected into the returned `WriteFailure` rather than allowed to crash the loop and
   leave some rows soft-deleted and others still live. No hard delete, full auditability via
   `--include-deleted`, and backup/cutover never run unless every entry succeeded and verification
   passed. This soft-delete rollback covers `records` rows only — it does not undo the
   `schema_add_field` DDL (new `ext_<group>` tables/columns, `record_group_fields` rows) that ran
   earlier in the same failed `--write`. That is a deliberate scope limit, not an oversight:
   `schema_add_field` is idempotent (Plan A never errors on re-adding the same field), so a
   corrected re-run of `--write` simply reuses the schema already in place rather than needing it
   rebuilt. The one visible side effect is that `ccst pdata schema list/show` can show a
   `record_group`/columns with zero live rows between a failed attempt and its corrected re-run —
   harmless, but worth documenting so it isn't mistaken for data loss.
9. **Verification's human-readable diff report (spec §7.1 step 4) is printed after a successful
   `--write`, not gated behind a separate pre-cutover confirmation prompt.** The spec's step
   ordering (verify, then back up, then cut over) reads as three sequential sub-steps within one
   procedure, and this plan's `--write` stays the single atomic call Decision 8 describes — there
   is no interactive pause between verification passing and backup/cutover running. The human
   review the spec is protecting (catching a wrong classification before it's irreversible) is
   real, but this plan places that gate earlier and cheaper: step 2's proposal review plus step
   0's `--rehearse` both happen before `--write` is ever invoked, and a rehearsed or previously-
   reviewed run's diff report is then a confirmation of what already ran, not a blocking prompt.
   If a real migration's diff report ever shows something unexpected, spec §7.1 step 8's rollback
   procedure (Decision 11 below) is the recovery path — nothing here is destructive before that
   point, since cutover only ever renames/archives, never deletes (step 6).
10. **Exit codes:** `2` for a CLI/validation error (bad project name, no proposal file found yet),
    `1` for a verification failure (nothing was cut over, DB rows soft-deleted per Decision 8),
    `0` for a clean dry run or a fully committed-and-cut-over write. These are a self-contained
    scheme for this one verb, not a reuse of Plan A's `get`/`update`/`delete` exit-code convention
    (Plan A's own Decision 6: `1`=not found, `2`=validation error, `3`=version conflict) — `ccst
    pdata init` never surfaces a not-found or version-conflict condition to its own exit code (its
    rollback loop catches those exceptions internally, per Decision 8), so reusing `1`/`3` for a
    categorically different meaning here would misinform any caller that already relies on Plan
    A's convention for those codes.
11. **Rollback (spec §7.1 step 8) is a documented human-directed procedure, not a new CLI
    subcommand.** The spec's own literal CLI contract for `ccst pdata init` names only
    `--project`/`--rehearse`/`--write`. Restoring the step-5 backup tar over the project folder,
    then clearing any bad DB rows with Plan A's already-shipped `ccst pdata delete`, is sufficient
    without this plan inventing and maintaining a `--rollback` flag. The `pm-project-init` skill
    (Task 13) documents the exact steps.
12. **The doctor check is WARN-only, never FAIL.** Unlike the CCST-infra migration
    `check_pending_data_store_migration()` mirrors (a version-upgrade situation every install must
    eventually pass through), a project that has never run `ccst pdata init` is a completely
    normal, unremarkable state — there is no "silently sitting on unmigrated data" risk to FAIL on.
    Only archived-but-undeleted originals under `.pdata-migrated/` (spec §7.1 step 7) are worth a
    nudge, and a WARN is the right severity for "safe to remove once verified."
13. **Classification never silently proposes merging into an already-live `record_group`.**
    `dry_run()` queries the project's existing `record_group`s via Plan A's already-shipped
    `service.schema_list()` and threads them through to `classify.walk_and_classify()`, which folds
    them into the same collision-disambiguation pass it already runs for two new files that would
    otherwise collide on the same auto-derived name (Decision 5's `_default_record_group`). Without
    this, a project's first classification pass after it already has live `ccst pdata` activity —
    from an earlier `ccst pdata init` run, from Plan A's `service.add_record` used directly, or
    from an unrelated mechanism such as Plan C's `session-output`/`session-output-watermark`
    groups — could silently propose importing a brand-new file's rows into that already-populated,
    possibly system-managed group, with no warning in the dry-run report. This is a read-only query
    against the project's own `.db` (no schema change, no new seam) so it costs nothing extra
    against Decision 3's "Plan A's shipped files are untouched" principle.
14. **A field name's `sql_type` must agree across every manifest entry that shares a
    `record_group`.** Multiple entries feeding one `record_group` is a legitimate, supported shape
    (Plan D's own row-count-parity fixture relies on it) — but Plan A's `schema_add_field`/
    `add_extension_column` silently no-ops when a field name already has a column, so two entries
    proposing the same field name with different `sql_type`s would otherwise have the second
    type dropped with zero error, warning, or diff-report mention. `write()` validates this across
    the whole manifest before any DDL or row import runs (a validation error, exit 2 per Decision
    10 — not a verification failure with partially-inserted rows to roll back), rejecting the
    incompatible pair up front instead of silently corrupting one side's data.

## File structure

```
src/cc_session_tools/lib/pdata/
  init_paths.py    project/DB/backup path resolution for init, --rehearse redirection (db and
                   backup dir both), fixed filenames
  manifest.py      ManifestEntry/FieldSpec/Manifest dataclasses, JSON load/save/load_or_create
  classify.py      heuristic file-tree walker producing a fresh Manifest's entries
  importers.py     the 5 import strategies -> ImportRow, and the strategy dispatch table
  init_service.py  dry_run()/write() orchestration on top of Plan A's service.py
  backup.py        pre-cutover tar.gz snapshot
  cutover.py       archive migrated-source files + MANIFEST.md log

src/cc_session_tools/lib/doctor.py   (modified — new check_pending_pdata_migration + wiring)
src/cc_session_tools/cli/ccst.py     (modified — new "pdata init" verb + doctor wiring)

tests/pdata/
  test_init_paths.py
  test_manifest.py
  test_classify.py
  test_importers.py
  test_backup.py
  test_cutover.py
  test_init_service.py

tests/test_ccst_pdata_init_cli.py     (subprocess CLI tests, one file per
                                        tests/test_ccst_pdata_cli.py precedent)
tests/test_ccst_doctor.py             (modified — new check_pending_pdata_migration tests)

skills/pm-project-init/SKILL.md       (new)

pyproject.toml   (modified — version bump, last task)
CHANGELOG.md     (modified — [Unreleased] entry, last task)
```

---

## Task 1: `lib/pdata/init_paths.py` — project root + rehearsal DB redirection

**Files:**
- Create: `src/cc_session_tools/lib/pdata/init_paths.py`
- Test: `tests/pdata/test_init_paths.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/pdata/test_init_paths.py
from __future__ import annotations

import os

import pytest

from cc_session_tools.lib.pdata import init_paths, store


def test_default_projects_root(monkeypatch, tmp_path):
    monkeypatch.delenv(init_paths.PROJECTS_ROOT_ENV, raising=False)
    monkeypatch.setattr(
        "cc_session_tools.lib.pdata.init_paths.Path.home", lambda: tmp_path
    )
    assert init_paths.default_projects_root() == tmp_path / "cc"


def test_default_projects_root_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "custom"))
    assert init_paths.default_projects_root() == tmp_path / "custom"


def test_resolve_project_root_creates_default_location(monkeypatch, tmp_path):
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path))
    root = init_paths.resolve_project_root("demo", rehearse=None)
    assert root == tmp_path / "demo"
    assert root.is_dir()


def test_resolve_project_root_rehearse_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "real"))
    rehearsal = tmp_path / "rehearsal-copy"
    rehearsal.mkdir()
    root = init_paths.resolve_project_root("demo", rehearse=rehearsal)
    assert root == rehearsal


def test_resolve_project_root_rejects_unsafe_project_name(monkeypatch, tmp_path):
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path))
    with pytest.raises(ValueError, match="project"):
        init_paths.resolve_project_root("../escape", rehearse=None)


def test_project_db_dir_override_noop_without_rehearse(monkeypatch):
    monkeypatch.delenv(store.PROJECT_DB_DIR_ENV, raising=False)
    with init_paths.project_db_dir_override(None):
        assert store.PROJECT_DB_DIR_ENV not in os.environ


def test_project_db_dir_override_redirects_and_restores(monkeypatch, tmp_path):
    monkeypatch.setenv(store.PROJECT_DB_DIR_ENV, "/original/value")
    rehearse_path = tmp_path / "rehearsal"
    with init_paths.project_db_dir_override(rehearse_path):
        assert os.environ[store.PROJECT_DB_DIR_ENV] == str(
            rehearse_path / init_paths.REHEARSAL_DB_DIRNAME
        )
    assert os.environ[store.PROJECT_DB_DIR_ENV] == "/original/value"


def test_project_db_dir_override_clears_when_previously_unset(monkeypatch, tmp_path):
    monkeypatch.delenv(store.PROJECT_DB_DIR_ENV, raising=False)
    rehearse_path = tmp_path / "rehearsal"
    with init_paths.project_db_dir_override(rehearse_path):
        assert store.PROJECT_DB_DIR_ENV in os.environ
    assert store.PROJECT_DB_DIR_ENV not in os.environ
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/chris/repos/claude-code-session-tools/.worktrees/pdata-core && uv run pytest tests/pdata/test_init_paths.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cc_session_tools.lib.pdata.init_paths'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/pdata/init_paths.py
"""Path/env resolution for `ccst pdata init` (spec §7): where a project's source tree
lives, where its dry-run classification proposal is written, and how --rehearse
redirects both the project root and the .db target without touching the real
project or Plan A's shipped connection helpers.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from cc_session_tools.lib.pdata import store

PROJECTS_ROOT_ENV = "CCST_PROJECTS_ROOT"
PROPOSAL_FILENAME = ".ccst-pdata-proposal.json"
MIGRATED_ARCHIVE_DIRNAME = ".pdata-migrated"
MIGRATED_MANIFEST_FILENAME = "MANIFEST.md"
REHEARSAL_DB_DIRNAME = ".ccst-pdata-rehearsal-db"

# Directories the classifier (Task 3) never walks into — repo/tool bookkeeping, not
# project content in the sense spec §7.1's classification pass cares about.
EXCLUDED_DIR_NAMES = frozenset({
    ".git", ".claude", "cc-sessions", MIGRATED_ARCHIVE_DIRNAME, REHEARSAL_DB_DIRNAME,
})


def default_projects_root() -> Path:
    override = os.environ.get(PROJECTS_ROOT_ENV)
    return Path(override).expanduser() if override else Path.home() / "cc"


def resolve_project_root(project: str, *, rehearse: Path | None) -> Path:
    """The directory ccst pdata init reads/writes files against: --rehearse's path
    when given (spec §7.1 step 0 — the copy already happened before this call), else
    <projects_root>/<project>, created if this is a genuinely new project (spec:
    "safe to run against an empty folder... also how a genuinely new project gets
    its .db")."""
    store.validate_project_name(project)
    if rehearse is not None:
        return rehearse
    root = default_projects_root() / project
    root.mkdir(parents=True, exist_ok=True)
    return root


@contextmanager
def project_db_dir_override(rehearse: Path | None) -> Iterator[None]:
    """Redirect every service.py/repository.py call inside the `with` block to a
    self-contained rehearsal .db instead of the real project-db/<project>.db, by
    reusing Plan A's own CCST_PROJECT_DB_DIR test seam (store.PROJECT_DB_DIR_ENV) —
    rehearsal touches zero Plan A code. No-op when rehearse is None."""
    if rehearse is None:
        yield
        return
    override_dir = rehearse / REHEARSAL_DB_DIRNAME
    previous = os.environ.get(store.PROJECT_DB_DIR_ENV)
    os.environ[store.PROJECT_DB_DIR_ENV] = str(override_dir)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(store.PROJECT_DB_DIR_ENV, None)
        else:
            os.environ[store.PROJECT_DB_DIR_ENV] = previous
```

Note: `backup_dir_override()` (this plan's other rehearsal-isolation seam, redirecting
`backup.BACKUP_DIR_ENV`) is deliberately **not** part of this task, even though it belongs in this
same module — it needs `backup.BACKUP_DIR_ENV`, which doesn't exist until Task 5 creates
`backup.py`. Task 5 adds it to this same module (with its own tests) once that dependency is
available, mirroring how Task 3 defers `manifest.load_or_create()` for the same reason.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_init_paths.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/init_paths.py tests/pdata/test_init_paths.py
git commit -m "feat(pdata-init): add project-root and rehearsal-db path resolution"
```

---

## Task 2: `lib/pdata/manifest.py` — classification manifest dataclasses + persistence

**Files:**
- Create: `src/cc_session_tools/lib/pdata/manifest.py`
- Test: `tests/pdata/test_manifest.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/pdata/test_manifest.py
from __future__ import annotations

import json

import pytest

from cc_session_tools.lib.pdata import manifest


def test_manifest_entry_folder_owned_needs_no_group():
    entry = manifest.ManifestEntry(path="CLAUDE.md", classification="folder-owned")
    assert entry.record_group is None


def test_manifest_entry_db_owned_requires_record_group_and_strategy():
    with pytest.raises(ValueError, match="record_group"):
        manifest.ManifestEntry(path="ideas.csv", classification="db-owned")


def test_manifest_entry_db_owned_requires_valid_strategy():
    with pytest.raises(ValueError, match="strategy"):
        manifest.ManifestEntry(
            path="ideas.csv", classification="db-owned", record_group="ideas",
            strategy="not-a-real-strategy",
        )


def test_manifest_entry_rejects_invalid_classification():
    with pytest.raises(ValueError, match="classification"):
        manifest.ManifestEntry(path="x", classification="bogus")


def test_db_group_and_db_strategy_accessors():
    entry = manifest.ManifestEntry(
        path="ideas.csv", classification="db-owned", record_group="ideas",
        strategy="csv-rows",
    )
    assert entry.db_group() == "ideas"
    assert entry.db_strategy() == "csv-rows"


def test_db_group_asserts_on_folder_owned_entry():
    entry = manifest.ManifestEntry(path="CLAUDE.md", classification="folder-owned")
    with pytest.raises(AssertionError):
        entry.db_group()


def test_manifest_rejects_duplicate_entry_paths():
    """init_service.write()/_verify() key their per-entry bookkeeping by entry.path
    (Task 9's `entry_rows: dict[str, list[tuple[int, ImportRow]]]`), and
    cutover.archive_entries renames each entry's source file by path once — two
    entries sharing a path would silently overwrite each other's tracked rows and
    then raise an unhandled FileNotFoundError on the second rename attempt, well
    after backup/verification already reported success. Reject the collision at
    construction so it can never reach write()."""
    with pytest.raises(ValueError, match="duplicate"):
        manifest.Manifest(
            project="demo",
            entries=[
                manifest.ManifestEntry(path="ideas.csv", classification="folder-owned"),
                manifest.ManifestEntry(path="ideas.csv", classification="folder-owned"),
            ],
        )


def test_save_then_load_round_trips(tmp_path):
    field = manifest.FieldSpec(name="priority", sql_type="INTEGER", column="priority")
    entries = [
        manifest.ManifestEntry(path="CLAUDE.md", classification="folder-owned"),
        manifest.ManifestEntry(
            path="ideas.csv", classification="db-owned", record_group="ideas",
            strategy="csv-rows", content_column="idea", fields=[field],
        ),
    ]
    m = manifest.Manifest(project="demo", entries=entries)
    path = tmp_path / "proposal.json"
    manifest.save(m, path)

    loaded = manifest.load(path)
    assert loaded.project == "demo"
    assert len(loaded.entries) == 2
    assert loaded.entries[1].fields[0].name == "priority"
    assert loaded.entries[1].fields[0].sql_type == "INTEGER"


def test_load_raises_value_error_on_missing_required_key(tmp_path):
    """A hand-edited proposal missing "project" must raise ValueError (caught by
    _cmd_pdata_init's exit-2 path), never an uncaught KeyError."""
    path = tmp_path / "proposal.json"
    path.write_text(json.dumps({"entries": [{"path": "x", "classification": "folder-owned"}]}))
    with pytest.raises(ValueError, match="malformed manifest"):
        manifest.load(path)


def test_load_raises_value_error_on_non_dict_entry(tmp_path):
    """A hand-edited proposal whose "entries" contains a non-object element must
    raise ValueError, never an uncaught AttributeError/TypeError."""
    path = tmp_path / "proposal.json"
    path.write_text(json.dumps({"project": "demo", "entries": ["not-an-object"]}))
    with pytest.raises(ValueError, match="malformed manifest"):
        manifest.load(path)
```

Note: `load_or_create()` (spec §7.1 step 2's persistence contract) is deliberately **not** part
of this task, even though it belongs in this file — it needs `classify.walk_and_classify()`,
which doesn't exist until Task 3. Task 3 adds it to this same module (with its own tests) once
that dependency is available, so this task stays fully green in isolation rather than shipping a
step whose own "run it, expect PASS" instruction would actually fail.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pdata/test_manifest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cc_session_tools.lib.pdata.manifest'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/pdata/manifest.py
"""Classification manifest for `ccst pdata init` (spec §7.1 steps 1-2).

A Manifest is the single artifact carrying a project's per-file classification
between invocations. The first dry run creates it fresh from
classify.walk_and_classify(); every later dry run returns the file completely
unchanged so a human's overrides (record_group renames, folder-owned overrides,
field-type corrections) are never silently clobbered. `--write` always operates
on whatever is currently on disk.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

_VALID_CLASSIFICATIONS = {"folder-owned", "db-owned"}
_VALID_STRATEGIES = {
    "whole-file", "delimited-sections", "csv-rows", "json-array-rows", "json-singleton",
}


@dataclass
class FieldSpec:
    name: str
    sql_type: str
    column: str | None = None
    description: str | None = None
    default: object | None = None


@dataclass
class ManifestEntry:
    path: str
    classification: str
    reviewed: bool = False
    record_group: str | None = None
    strategy: str | None = None
    delimiter: str | None = None
    content_column: str | None = None
    file_path_column: str | None = None
    fields: list[FieldSpec] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.classification not in _VALID_CLASSIFICATIONS:
            raise ValueError(
                f"invalid classification {self.classification!r} for {self.path!r}"
            )
        if self.classification == "db-owned":
            if not self.record_group:
                raise ValueError(f"db-owned entry {self.path!r} needs a record_group")
            if self.strategy not in _VALID_STRATEGIES:
                raise ValueError(f"invalid strategy {self.strategy!r} for {self.path!r}")

    def db_group(self) -> str:
        """The validated non-None record_group (guaranteed by __post_init__ for every
        db-owned entry) — narrows the type once here instead of an assert/ignore at
        every call site."""
        assert self.record_group is not None, "db_group() called on a non-db-owned entry"
        return self.record_group

    def db_strategy(self) -> str:
        assert self.strategy is not None, "db_strategy() called on a non-db-owned entry"
        return self.strategy


@dataclass
class Manifest:
    project: str
    entries: list[ManifestEntry]

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for entry in self.entries:
            if entry.path in seen:
                raise ValueError(f"duplicate entry path in manifest: {entry.path!r}")
            seen.add(entry.path)


def save(m: Manifest, path: Path) -> None:
    data = {"project": m.project, "entries": [asdict(e) for e in m.entries]}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load(path: Path) -> Manifest:
    """Raises ValueError (never a raw KeyError/TypeError/AttributeError) for any
    malformed proposal file — a missing "project"/"entries" key, an "entries" that
    isn't a list, an entry that isn't an object, or an entry with an unexpected
    field shape. This is a real hand-edit surface (pm-project-init Step 4
    instructs editing this file directly), so a shape error here must reach
    _cmd_pdata_init's existing `except (FileNotFoundError, ValueError)` and
    dry_run's `except ValueError` and produce the documented exit-2 validation
    error instead of an uncaught traceback."""
    data = json.loads(path.read_text(encoding="utf-8"))
    try:
        entries = []
        for raw_entry in data["entries"]:
            raw_fields = raw_entry.get("fields", [])
            field_specs = [FieldSpec(**rf) for rf in raw_fields]
            entry_kwargs = {k: v for k, v in raw_entry.items() if k != "fields"}
            entries.append(ManifestEntry(**entry_kwargs, fields=field_specs))
        return Manifest(project=data["project"], entries=entries)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"malformed manifest at {path}: {exc}") from exc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_manifest.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/manifest.py tests/pdata/test_manifest.py
git commit -m "feat(pdata-init): add classification manifest dataclasses and persistence"
```

---

## Task 3: `lib/pdata/classify.py` — conservative file-tree classifier + `manifest.load_or_create()`

**Files:**
- Create: `src/cc_session_tools/lib/pdata/classify.py`
- Modify: `src/cc_session_tools/lib/pdata/manifest.py` (adds `load_or_create()` — deferred from
  Task 2 because it needs this task's `classify.walk_and_classify()`)
- Test: `tests/pdata/test_classify.py`
- Modify: `tests/pdata/test_manifest.py` (adds `load_or_create()` tests)

- [ ] **Step 1: Write the failing test**

```python
# tests/pdata/test_classify.py
from __future__ import annotations

import json

from cc_session_tools.lib.pdata import classify, naming


def test_binary_extension_classified_folder_owned(tmp_path):
    (tmp_path / "photo.png").write_bytes(b"\x89PNG\r\n")
    entries = classify.walk_and_classify(tmp_path)
    assert entries == [
        classify.ManifestEntry(path="photo.png", classification="folder-owned")
    ]


def test_markdown_defaults_folder_owned_not_guessed(tmp_path):
    """The classifier must NOT try to decide whether a .md file is a log or a
    versioned doc — that judgement call is out of this plan's scope (see plan
    Decision 5)."""
    (tmp_path / "ccst-ideas.md").write_text("## idea one\nbody\n## idea two\nbody\n")
    entries = classify.walk_and_classify(tmp_path)
    assert entries == [
        classify.ManifestEntry(path="ccst-ideas.md", classification="folder-owned")
    ]


def test_csv_classified_db_owned_with_fields_from_header(tmp_path):
    (tmp_path / "ideas.csv").write_text("idea,priority\nfirst,1\nsecond,2\n")
    entries = classify.walk_and_classify(tmp_path)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.classification == "db-owned"
    assert entry.strategy == "csv-rows"
    assert entry.record_group == "ideas"
    assert {f.name for f in entry.fields} == {"idea", "priority"}


def test_csv_content_and_file_path_columns_recognized(tmp_path):
    (tmp_path / "log.csv").write_text("content,file_path,sender\nhi,a.pdf,bob\n")
    entry = classify.walk_and_classify(tmp_path)[0]
    assert entry.content_column == "content"
    assert entry.file_path_column == "file_path"
    assert {f.name for f in entry.fields} == {"sender"}


def test_json_object_classified_singleton(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"last_shop": "2026-01-01", "count": 3}))
    entry = classify.walk_and_classify(tmp_path)[0]
    assert entry.classification == "db-owned"
    assert entry.strategy == "json-singleton"
    assert entry.record_group == "state"
    field_types = {f.name: f.sql_type for f in entry.fields}
    assert field_types["last_shop"] == "TEXT"
    assert field_types["count"] == "INTEGER"


def test_json_array_of_objects_classified_array_rows(tmp_path):
    (tmp_path / "chars.json").write_text(json.dumps([{"name": "a"}, {"name": "b"}]))
    entry = classify.walk_and_classify(tmp_path)[0]
    assert entry.strategy == "json-array-rows"
    assert {f.name for f in entry.fields} == {"name"}


def test_unparsable_json_falls_back_to_folder_owned(tmp_path):
    (tmp_path / "broken.json").write_text("{not json")
    entry = classify.walk_and_classify(tmp_path)[0]
    assert entry.classification == "folder-owned"


def test_excluded_directories_are_skipped(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("junk")
    (tmp_path / "cc-sessions").mkdir()
    (tmp_path / "cc-sessions" / "notes.md").write_text("junk")
    (tmp_path / "CLAUDE.md").write_text("# demo\n")
    entries = classify.walk_and_classify(tmp_path)
    assert [e.path for e in entries] == ["CLAUDE.md"]


def test_entries_sorted_by_relative_path(tmp_path):
    (tmp_path / "b.csv").write_text("x\n1\n")
    (tmp_path / "a.csv").write_text("x\n1\n")
    entries = classify.walk_and_classify(tmp_path)
    assert [e.path for e in entries] == ["a.csv", "b.csv"]


def test_colliding_basenames_in_different_subdirs_get_disambiguated_record_groups(tmp_path):
    """_default_record_group derives its proposal from path.stem alone, so
    a/notes.csv and b/notes.csv would otherwise both propose record_group=notes —
    silently merging two unrelated files' rows into one shared group at --write
    time with no error. walk_and_classify must detect the collision across the
    whole manifest and disambiguate every colliding entry."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "notes.csv").write_text("x\n1\n")
    (tmp_path / "b" / "notes.csv").write_text("x\n2\n")
    entries = classify.walk_and_classify(tmp_path)
    groups = {e.path: e.record_group for e in entries}
    assert len(set(groups.values())) == 2, groups


def test_existing_record_group_forces_disambiguation_even_for_a_single_file(tmp_path):
    """A record_group can already be live and populated — from an earlier ccst
    pdata init run, or from an unrelated mechanism entirely (e.g. Plan C's
    session-output groups) — even when only one new file in this pass proposes
    that name. walk_and_classify must never silently propose merging into it."""
    (tmp_path / "notes.csv").write_text("x\n1\n")
    entries = classify.walk_and_classify(
        tmp_path, existing_record_groups=frozenset({"notes"})
    )
    assert entries[0].record_group != "notes"


def test_csv_header_colliding_with_reserved_base_column_is_renamed(tmp_path):
    """A header literally named `version`/`id`/`created_at`/etc. would otherwise
    pass classification silently and only fail inside schema_add_field at
    --write time — past the human-review step the spec relies on. classify.py
    must rename it to a non-reserved, still-valid field name up front so the
    dry-run report already shows the name that will actually be used."""
    (tmp_path / "docs.csv").write_text("version,note\n1.0,first\n")
    entry = classify.walk_and_classify(tmp_path)[0]
    field_names = {f.name for f in entry.fields}
    assert "version" not in field_names
    for name in field_names:
        naming.validate_field_name(name)  # must not raise
```

```python
# append to tests/pdata/test_manifest.py — deferred from Task 2, see that task's note

def test_load_or_create_writes_fresh_proposal_when_missing(tmp_path):
    project_root = tmp_path / "demo"
    project_root.mkdir()
    (project_root / "CLAUDE.md").write_text("# demo\n")
    proposal_path = project_root / "proposal.json"

    m = manifest.load_or_create(project_root, "demo", proposal_path)
    assert proposal_path.exists()
    assert [e.path for e in m.entries] == ["CLAUDE.md"]


def test_load_or_create_never_overwrites_existing_proposal(tmp_path):
    project_root = tmp_path / "demo"
    project_root.mkdir()
    (project_root / "CLAUDE.md").write_text("# demo\n")
    proposal_path = project_root / "proposal.json"

    manifest.load_or_create(project_root, "demo", proposal_path)
    # Hand-edit exactly as a human override would (spec §7.1 step 2).
    edited = manifest.load(proposal_path)
    edited.entries[0].reviewed = True
    manifest.save(edited, proposal_path)

    # A new file appears on disk between the two calls — must NOT be picked up,
    # and the hand-made edit must survive untouched.
    (project_root / "new-file.md").write_text("later\n")
    reloaded = manifest.load_or_create(project_root, "demo", proposal_path)
    assert [e.path for e in reloaded.entries] == ["CLAUDE.md"]
    assert reloaded.entries[0].reviewed is True


def test_load_or_create_passes_existing_record_groups_to_classifier(tmp_path):
    project_root = tmp_path / "demo"
    project_root.mkdir()
    (project_root / "notes.csv").write_text("x\n1\n")
    proposal_path = project_root / "proposal.json"

    m = manifest.load_or_create(
        project_root, "demo", proposal_path,
        existing_record_groups=frozenset({"notes"}),
    )
    assert m.entries[0].record_group != "notes"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pdata/test_classify.py tests/pdata/test_manifest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cc_session_tools.lib.pdata.classify'` for
the first file; `AttributeError: module ... has no attribute 'load_or_create'` for the second.

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/pdata/classify.py
"""Heuristic file-tree classifier for `ccst pdata init` (spec §7.1 step 1, §3).

Deliberately conservative for free text: only CSV and JSON get an automatic
db-owned proposal, because their internal structure is genuinely machine-inferable
without judgement. A markdown/text file's shape (an append-only log vs. a
versioned plan doc vs. a stacked-snapshot journal — spec §4.3) cannot be told
apart by a generic scan; guessing wrong here would silently bake a per-project
judgement call into shared tooling, which this plan must not do (see the
per-project inventory doc, not this module, for those calls). Every markdown/
text/unknown-extension file defaults to folder-owned; a human (via the
pm-project-init skill) reviews the printed report and flips individual entries
to db-owned with an explicit record_group/strategy.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from cc_session_tools.lib.pdata import naming
from cc_session_tools.lib.pdata.init_paths import EXCLUDED_DIR_NAMES, PROPOSAL_FILENAME
from cc_session_tools.lib.pdata.manifest import FieldSpec, ManifestEntry

_BINARY_EXTENSIONS = frozenset({
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".docx", ".xlsx", ".pptx",
    ".zip", ".mp3", ".mp4", ".heic", ".mov",
})


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return re.sub(r"-+", "-", slug) or "group"


def _slugify_field_name(header: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", header.lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)
    if not slug or not slug[0].isalpha():
        slug = f"f_{slug}" if slug else "field"
    return slug


def _safe_field_name(candidate: str, seen: set[str]) -> str:
    """Guarantees the returned name both passes naming.validate_field_name (rejecting
    a collision with a reserved base column like `id`/`version`/`created_at`/
    `updated_at`/`record_group`/`deleted_at`, or the ext table's own `record_id`) and
    doesn't repeat a name already used elsewhere in this same entry. Without this, a
    plausible real header (e.g. a CSV column literally named `version`) would pass
    classification silently and only fail inside schema_add_field at --write time,
    aborting and soft-deleting the whole run — well past the human-review step the
    spec's dry-run report exists for. Terminates because each branch strictly
    lengthens the candidate with a suffix that is itself always a valid, non-reserved
    identifier fragment."""
    name = candidate
    while True:
        try:
            naming.validate_field_name(name)
        except ValueError:
            name = f"{name}_field"
            continue
        if name in seen:
            name = f"{name}_2"
            continue
        return name


def _default_record_group(path: Path) -> str:
    group = _slugify(path.stem)
    naming.validate_record_group(group)
    return group


def _classify_csv(rel_path: str, abs_path: Path) -> ManifestEntry:
    with abs_path.open(newline="", encoding="utf-8") as f:
        header = next(csv.reader(f), [])
    content_column = next((h for h in header if h.strip().lower() == "content"), None)
    file_path_column = next(
        (h for h in header if h.strip().lower() in ("file_path", "path")), None
    )
    seen: set[str] = set()
    fields: list[FieldSpec] = []
    for h in header:
        if h in (content_column, file_path_column):
            continue
        name = _safe_field_name(_slugify_field_name(h), seen)
        seen.add(name)
        fields.append(FieldSpec(name=name, sql_type="TEXT", column=h))
    return ManifestEntry(
        path=rel_path, classification="db-owned", strategy="csv-rows",
        record_group=_default_record_group(abs_path),
        content_column=content_column, file_path_column=file_path_column, fields=fields,
    )


def _infer_sql_type(value: object) -> str:
    if isinstance(value, bool):
        return "INTEGER"
    if isinstance(value, int):
        return "INTEGER"
    if isinstance(value, float):
        return "REAL"
    return "TEXT"


def _classify_json(rel_path: str, abs_path: Path) -> ManifestEntry:
    try:
        data = json.loads(abs_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ManifestEntry(path=rel_path, classification="folder-owned")

    if isinstance(data, list) and data and all(isinstance(el, dict) for el in data):
        sample = data[0]
        seen: set[str] = set()
        fields = []
        for k, v in sample.items():
            name = _safe_field_name(_slugify_field_name(k), seen)
            seen.add(name)
            fields.append(FieldSpec(name=name, sql_type=_infer_sql_type(v), column=k))
        return ManifestEntry(
            path=rel_path, classification="db-owned", strategy="json-array-rows",
            record_group=_default_record_group(abs_path), fields=fields,
        )
    if isinstance(data, dict):
        seen = set()
        fields = []
        for k, v in data.items():
            name = _safe_field_name(_slugify_field_name(k), seen)
            seen.add(name)
            fields.append(FieldSpec(name=name, sql_type=_infer_sql_type(v), column=k))
        return ManifestEntry(
            path=rel_path, classification="db-owned", strategy="json-singleton",
            record_group=_default_record_group(abs_path), fields=fields,
        )
    return ManifestEntry(path=rel_path, classification="folder-owned")


def classify_path(rel_path: str, abs_path: Path) -> ManifestEntry:
    suffix = abs_path.suffix.lower()
    if suffix in _BINARY_EXTENSIONS:
        return ManifestEntry(path=rel_path, classification="folder-owned")
    if suffix == ".csv":
        return _classify_csv(rel_path, abs_path)
    if suffix == ".json":
        return _classify_json(rel_path, abs_path)
    return ManifestEntry(path=rel_path, classification="folder-owned")


def _disambiguate_record_groups(
    entries: list[ManifestEntry], *, existing_record_groups: frozenset[str] = frozenset(),
) -> None:
    """_default_record_group derives its proposal from path.stem alone, so two
    files sharing a basename in different subdirectories (e.g. a/notes.csv and
    b/notes.csv) would otherwise both auto-propose the same record_group —
    silently merging their unrelated rows into one shared group at --write time
    with no error or warning. Detect every such collision across the whole
    manifest and disambiguate each colliding entry by prefixing its parent
    directory's slug, so the dry-run report already shows the group name that
    will actually be used.

    `existing_record_groups` (the project's already-live record_groups, per
    service.schema_list — populated by an earlier ccst pdata init run, by Plan
    A's own service.add_record usage, or by an unrelated mechanism like Plan C's
    session-output groups) is folded into the same collision check: a freshly
    proposed group colliding with an existing live one is disambiguated exactly
    like an in-pass collision, even when only one new file in this pass proposes
    that name. Without this, a first classification pass against a project with
    prior pdata activity (or a forced reclassification after deleting the
    proposal file) could silently propose merging new content into an
    already-populated, possibly system-managed record_group with zero warning in
    the dry-run report."""
    by_group: dict[str, list[ManifestEntry]] = {}
    for entry in entries:
        if entry.classification == "db-owned":
            by_group.setdefault(entry.db_group(), []).append(entry)
    for group, colliding in by_group.items():
        if len(colliding) < 2 and group not in existing_record_groups:
            continue
        seen: set[str] = set(existing_record_groups)
        for entry in colliding:
            parent = Path(entry.path).parent
            prefix = _slugify(str(parent)) if str(parent) != "." else "root"
            candidate = f"{prefix}-{group}"
            while candidate in seen:
                candidate = f"{candidate}-2"
            seen.add(candidate)
            entry.record_group = candidate


def walk_and_classify(
    project_root: Path, *, existing_record_groups: frozenset[str] = frozenset(),
) -> list[ManifestEntry]:
    """Walk project_root, classifying every file not inside an excluded directory
    and not the proposal file itself. Returns entries sorted by relative path for
    deterministic report output. `existing_record_groups` — see
    _disambiguate_record_groups — lets a caller (manifest.load_or_create) pass in
    the project's already-live record_groups so a fresh classification pass never
    silently proposes merging into one of them."""
    entries: list[ManifestEntry] = []
    for abs_path in sorted(project_root.rglob("*")):
        if not abs_path.is_file():
            continue
        rel = abs_path.relative_to(project_root)
        if any(part in EXCLUDED_DIR_NAMES for part in rel.parts[:-1]):
            continue
        if abs_path.name == PROPOSAL_FILENAME:
            continue
        entries.append(classify_path(str(rel), abs_path))
    _disambiguate_record_groups(entries, existing_record_groups=existing_record_groups)
    return entries
```

```python
# append to src/cc_session_tools/lib/pdata/manifest.py:

def load_or_create(
    project_root: Path, project: str, proposal_path: Path,
    *, existing_record_groups: frozenset[str] = frozenset(),
) -> Manifest:
    """Never overwrites an existing proposal file (spec §7.1 step 2 — a human's
    overrides must survive a re-run of the dry-run pass). First call for a project
    creates it fresh from classify.walk_and_classify(); every later call returns the
    file exactly as it is on disk. Delete the file to force a fresh classification
    pass. `existing_record_groups` — the project's already-live record_groups —
    is threaded through to the classifier so a fresh pass (first-ever run against
    a project with prior pdata activity, or a forced reclassification) never
    silently proposes merging a new file into an already-populated group; see
    classify._disambiguate_record_groups."""
    from cc_session_tools.lib.pdata import classify  # local import: breaks the
    # classify<->manifest cycle (classify.py imports these dataclasses at module level)

    if proposal_path.exists():
        return load(proposal_path)
    entries = classify.walk_and_classify(
        project_root, existing_record_groups=existing_record_groups
    )
    m = Manifest(project=project, entries=entries)
    save(m, proposal_path)
    return m
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_classify.py tests/pdata/test_manifest.py -v`
Expected: PASS (12 + 13 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/classify.py src/cc_session_tools/lib/pdata/manifest.py tests/pdata/test_classify.py tests/pdata/test_manifest.py
git commit -m "feat(pdata-init): add conservative CSV/JSON-aware file-tree classifier and manifest.load_or_create"
```

---

## Task 4: `lib/pdata/importers.py` — the five import strategies

**Files:**
- Create: `src/cc_session_tools/lib/pdata/importers.py`
- Test: `tests/pdata/test_importers.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/pdata/test_importers.py
from __future__ import annotations

import json

import pytest

from cc_session_tools.lib.pdata import importers
from cc_session_tools.lib.pdata.manifest import FieldSpec, ManifestEntry


def test_import_whole_file_one_row(tmp_path):
    (tmp_path / "note.md").write_text("hello world\n")
    entry = ManifestEntry(path="note.md", classification="db-owned",
                           record_group="notes", strategy="whole-file")
    rows = importers.import_entry(tmp_path, entry)
    assert len(rows) == 1
    assert rows[0].content == "hello world\n"
    assert rows[0].file_path is None
    assert rows[0].fields == {}


def test_import_delimited_sections_splits_on_default_heading(tmp_path):
    (tmp_path / "log.md").write_text("## first\nbody one\n## second\nbody two\n")
    entry = ManifestEntry(path="log.md", classification="db-owned",
                           record_group="log", strategy="delimited-sections")
    rows = importers.import_entry(tmp_path, entry)
    assert len(rows) == 2
    assert rows[0].content.startswith("## first")
    assert rows[1].content.startswith("## second")


def test_import_delimited_sections_custom_delimiter(tmp_path):
    (tmp_path / "log.md").write_text("# Snapshot A\nx\n# Snapshot B\ny\n")
    entry = ManifestEntry(
        path="log.md", classification="db-owned", record_group="log",
        strategy="delimited-sections", delimiter=r"(?m)^# .*$",
    )
    rows = importers.import_entry(tmp_path, entry)
    assert len(rows) == 2
    assert "Snapshot A" in rows[0].content
    assert "Snapshot B" in rows[1].content


def test_import_csv_rows_maps_fields_content_and_file_path(tmp_path):
    (tmp_path / "log.csv").write_text(
        "content,file_path,sender\nhi there,notes/a.pdf,bob\n"
    )
    entry = ManifestEntry(
        path="log.csv", classification="db-owned", record_group="log",
        strategy="csv-rows", content_column="content", file_path_column="file_path",
        fields=[FieldSpec(name="sender", sql_type="TEXT", column="sender")],
    )
    rows = importers.import_entry(tmp_path, entry)
    assert len(rows) == 1
    assert rows[0].content == "hi there"
    assert rows[0].file_path == "notes/a.pdf"
    assert rows[0].fields == {"sender": "bob"}


def test_import_csv_rows_without_content_column_serializes_row(tmp_path):
    (tmp_path / "ideas.csv").write_text("idea,priority\nfirst,1\n")
    entry = ManifestEntry(
        path="ideas.csv", classification="db-owned", record_group="ideas",
        strategy="csv-rows",
        fields=[FieldSpec(name="idea", sql_type="TEXT", column="idea"),
                FieldSpec(name="priority", sql_type="INTEGER", column="priority")],
    )
    rows = importers.import_entry(tmp_path, entry)
    parsed = json.loads(rows[0].content)
    assert parsed == {"idea": "first", "priority": "1"}
    assert rows[0].fields == {"idea": "first", "priority": "1"}


def test_import_json_array_rows(tmp_path):
    (tmp_path / "chars.json").write_text(json.dumps([{"name": "a"}, {"name": "b"}]))
    entry = ManifestEntry(
        path="chars.json", classification="db-owned", record_group="chars",
        strategy="json-array-rows",
        fields=[FieldSpec(name="name", sql_type="TEXT", column="name")],
    )
    rows = importers.import_entry(tmp_path, entry)
    assert len(rows) == 2
    assert rows[0].fields == {"name": "a"}
    assert rows[1].fields == {"name": "b"}


def test_import_json_singleton_one_row(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"count": 3}))
    entry = ManifestEntry(
        path="state.json", classification="db-owned", record_group="state",
        strategy="json-singleton",
        fields=[FieldSpec(name="count", sql_type="INTEGER", column="count")],
    )
    rows = importers.import_entry(tmp_path, entry)
    assert len(rows) == 1
    assert rows[0].fields == {"count": "3"}


def test_import_row_field_values_are_always_strings(tmp_path):
    """Plan Decision 7: field values are always strings, matching how `ccst pdata
    add --field k=v` already only ever sends strings."""
    (tmp_path / "state.json").write_text(json.dumps({"count": 3, "ratio": 1.5}))
    entry = ManifestEntry(
        path="state.json", classification="db-owned", record_group="state",
        strategy="json-singleton",
        fields=[FieldSpec(name="count", sql_type="INTEGER", column="count"),
                FieldSpec(name="ratio", sql_type="REAL", column="ratio")],
    )
    rows = importers.import_entry(tmp_path, entry)
    assert all(isinstance(v, str) for v in rows[0].fields.values())


def test_count_source_rows_matches_import_entry_for_every_strategy(tmp_path):
    """init_service's entry-count parity check (spec §7.1 step 4) relies on this
    being a genuinely independent re-count, not a re-read of import_entry's own
    result — but it must still agree with import_entry's row count on well-formed
    input."""
    (tmp_path / "ideas.csv").write_text("idea,priority\nfirst,1\nsecond,2\n")
    csv_entry = ManifestEntry(path="ideas.csv", classification="db-owned",
                               record_group="ideas", strategy="csv-rows")
    assert importers.count_source_rows(tmp_path, csv_entry) == len(
        importers.import_entry(tmp_path, csv_entry)
    )

    (tmp_path / "chars.json").write_text(json.dumps([{"name": "a"}, {"name": "b"}]))
    json_entry = ManifestEntry(path="chars.json", classification="db-owned",
                                record_group="chars", strategy="json-array-rows")
    assert importers.count_source_rows(tmp_path, json_entry) == 2

    (tmp_path / "state.json").write_text(json.dumps({"count": 3}))
    singleton_entry = ManifestEntry(path="state.json", classification="db-owned",
                                     record_group="state", strategy="json-singleton")
    assert importers.count_source_rows(tmp_path, singleton_entry) == 1

    (tmp_path / "log.md").write_text("## first\nbody one\n## second\nbody two\n")
    sections_entry = ManifestEntry(path="log.md", classification="db-owned",
                                    record_group="log", strategy="delimited-sections")
    assert importers.count_source_rows(tmp_path, sections_entry) == 2


def test_import_json_array_rows_rejects_non_array_shape(tmp_path):
    """A hand-edited manifest entry (pm-project-init Step 4) can assign
    'json-array-rows' to a file that is actually a JSON object — must raise
    ValueError, not AttributeError from a bare list iteration/`.get()` call."""
    (tmp_path / "chars.json").write_text(json.dumps({"not": "a list"}))
    entry = ManifestEntry(path="chars.json", classification="db-owned",
                           record_group="chars", strategy="json-array-rows")
    with pytest.raises(ValueError, match="json-array-rows"):
        importers.import_entry(tmp_path, entry)


def test_import_json_array_rows_rejects_non_object_elements(tmp_path):
    """Same strategy, but the file is a JSON array of scalars, not objects."""
    (tmp_path / "chars.json").write_text(json.dumps(["not", "objects"]))
    entry = ManifestEntry(path="chars.json", classification="db-owned",
                           record_group="chars", strategy="json-array-rows")
    with pytest.raises(ValueError, match="json-array-rows"):
        importers.import_entry(tmp_path, entry)


def test_import_json_singleton_rejects_non_object_shape(tmp_path):
    """A hand-edited manifest entry can assign 'json-singleton' to a file that is
    actually a JSON array — must raise ValueError, not AttributeError from a bare
    `.get()` call on a list."""
    (tmp_path / "state.json").write_text(json.dumps(["a", "b"]))
    entry = ManifestEntry(path="state.json", classification="db-owned",
                           record_group="state", strategy="json-singleton")
    with pytest.raises(ValueError, match="json-singleton"):
        importers.import_entry(tmp_path, entry)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pdata/test_importers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cc_session_tools.lib.pdata.importers'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/pdata/importers.py
"""Import strategies for `ccst pdata init`'s write phase (spec §7.1 step 3).

Each strategy reads one source file and yields ImportRow tuples — exactly the
(content, file_path, fields, created_at) shape service.add_record already
accepts (Plan A, spec §5). Field values are always passed through as str,
matching how `ccst pdata add --field k=v` already only ever sends strings from
the CLI boundary — SQLite's column-affinity rules convert a well-formed numeric
string into the extension column's real INTEGER/REAL storage class on insert,
so no importer-side type coercion is needed (plan Decision 7).
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path

from cc_session_tools.lib.pdata.manifest import ManifestEntry

_DEFAULT_SECTION_DELIMITER = r"(?m)^## .*$"


@dataclass
class ImportRow:
    content: str
    file_path: str | None
    fields: dict[str, str]
    created_at: int


def _mtime(path: Path) -> int:
    return int(path.stat().st_mtime)


def _stringify(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def import_whole_file(project_root: Path, entry: ManifestEntry) -> list[ImportRow]:
    path = project_root / entry.path
    return [ImportRow(content=path.read_text(encoding="utf-8"), file_path=None,
                       fields={}, created_at=_mtime(path))]


def import_delimited_sections(project_root: Path, entry: ManifestEntry) -> list[ImportRow]:
    path = project_root / entry.path
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(entry.delimiter or _DEFAULT_SECTION_DELIMITER)
    starts = [m.start() for m in pattern.finditer(text)]
    created_at = _mtime(path)
    if not starts:
        return [ImportRow(content=text, file_path=None, fields={}, created_at=created_at)]
    starts.append(len(text))
    return [
        ImportRow(content=text[starts[i]:starts[i + 1]].strip(), file_path=None,
                  fields={}, created_at=created_at)
        for i in range(len(starts) - 1)
    ]


def import_csv_rows(project_root: Path, entry: ManifestEntry) -> list[ImportRow]:
    path = project_root / entry.path
    created_at = _mtime(path)
    rows: list[ImportRow] = []
    with path.open(newline="", encoding="utf-8") as f:
        for raw_row in csv.DictReader(f):
            fields = {
                spec.name: _stringify(raw_row.get(spec.column or spec.name))
                for spec in entry.fields
            }
            if entry.content_column:
                content = raw_row.get(entry.content_column) or ""
            else:
                content = json.dumps(raw_row, ensure_ascii=False)
            file_path = raw_row.get(entry.file_path_column) if entry.file_path_column else None
            rows.append(ImportRow(content=content, file_path=file_path or None,
                                   fields=fields, created_at=created_at))
    return rows


def import_json_array_rows(project_root: Path, entry: ManifestEntry) -> list[ImportRow]:
    path = project_root / entry.path
    created_at = _mtime(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    # A hand-edited manifest entry (pm-project-init Step 4 instructs editing this
    # file directly) can assign 'json-array-rows' to a file whose actual JSON
    # shape isn't an array of objects. Validate the shape here and raise
    # ValueError — the exact exception type write()'s per-entry
    # `except (ValueError, OSError, csv.Error)` already catches — rather than
    # letting a bare list/dict access raise AttributeError/TypeError, which would
    # crash the CLI with a raw traceback and skip the soft-delete rollback for
    # every id already inserted earlier in the same run.
    if not isinstance(data, list):
        raise ValueError(
            f"{entry.path}: strategy 'json-array-rows' requires a JSON array, "
            f"got {type(data).__name__}"
        )
    rows = []
    for index, element in enumerate(data):
        if not isinstance(element, dict):
            raise ValueError(
                f"{entry.path}: strategy 'json-array-rows' requires an array of "
                f"objects, but element {index} is a {type(element).__name__}"
            )
        fields = {
            spec.name: _stringify(element.get(spec.column or spec.name))
            for spec in entry.fields
        }
        rows.append(ImportRow(content=json.dumps(element, ensure_ascii=False),
                               file_path=None, fields=fields, created_at=created_at))
    return rows


def import_json_singleton(project_root: Path, entry: ManifestEntry) -> list[ImportRow]:
    path = project_root / entry.path
    created_at = _mtime(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    # Same rationale as import_json_array_rows above: a hand-edited entry can
    # assign 'json-singleton' to a file whose actual top-level JSON value isn't
    # an object (e.g. an array or scalar) — validate up front and raise
    # ValueError instead of letting `.get()` raise AttributeError.
    if not isinstance(data, dict):
        raise ValueError(
            f"{entry.path}: strategy 'json-singleton' requires a JSON object, "
            f"got {type(data).__name__}"
        )
    fields = {
        spec.name: _stringify(data.get(spec.column or spec.name))
        for spec in entry.fields
    }
    return [ImportRow(content=json.dumps(data, ensure_ascii=False), file_path=None,
                       fields=fields, created_at=created_at)]


STRATEGY_IMPORTERS = {
    "whole-file": import_whole_file,
    "delimited-sections": import_delimited_sections,
    "csv-rows": import_csv_rows,
    "json-array-rows": import_json_array_rows,
    "json-singleton": import_json_singleton,
}


def import_entry(project_root: Path, entry: ManifestEntry) -> list[ImportRow]:
    importer = STRATEGY_IMPORTERS[entry.db_strategy()]
    return importer(project_root, entry)


def count_source_rows(project_root: Path, entry: ManifestEntry) -> int:
    """Independently re-derives how many rows the source file *should* produce, for
    init_service's entry-count parity check (spec §7.1 step 4) — a comparison target
    computed fresh from the file, not a re-read of import_entry's own accumulated
    result, so it can catch a run where fewer rows landed in the DB than the source
    actually contains."""
    strategy = entry.db_strategy()
    path = project_root / entry.path
    if strategy in ("whole-file", "json-singleton"):
        return 1
    if strategy == "delimited-sections":
        text = path.read_text(encoding="utf-8")
        pattern = re.compile(entry.delimiter or _DEFAULT_SECTION_DELIMITER)
        starts = [m.start() for m in pattern.finditer(text)]
        return len(starts) if starts else 1
    if strategy == "csv-rows":
        with path.open(newline="", encoding="utf-8") as f:
            return sum(1 for _ in csv.DictReader(f))
    if strategy == "json-array-rows":
        data = json.loads(path.read_text(encoding="utf-8"))
        return len(data)
    raise ValueError(f"unknown import strategy {strategy!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_importers.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/importers.py tests/pdata/test_importers.py
git commit -m "feat(pdata-init): add whole-file/delimited-sections/csv/json import strategies"
```

---

## Task 5: `lib/pdata/backup.py` — pre-cutover tar.gz snapshot, plus rehearsal backup-dir isolation

**Files:**
- Create: `src/cc_session_tools/lib/pdata/backup.py`
- Modify: `src/cc_session_tools/lib/pdata/init_paths.py` (adds `REHEARSAL_BACKUP_DIRNAME` and
  `backup_dir_override()` — deferred from Task 1 because it needs this task's
  `backup.BACKUP_DIR_ENV` to exist first)
- Test: `tests/pdata/test_backup.py`
- Modify: `tests/pdata/test_init_paths.py` (adds `backup_dir_override()` tests)

- [ ] **Step 1: Write the failing test**

```python
# tests/pdata/test_backup.py
from __future__ import annotations

import tarfile

from cc_session_tools.lib.pdata import backup


def test_backup_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv(backup.BACKUP_DIR_ENV, str(tmp_path / "custom"))
    assert backup.backup_dir() == tmp_path / "custom"


def test_create_backup_writes_tar_containing_project_files(monkeypatch, tmp_path):
    monkeypatch.setenv(backup.BACKUP_DIR_ENV, str(tmp_path / "backups"))
    project_root = tmp_path / "demo"
    project_root.mkdir()
    (project_root / "ideas.csv").write_text("idea\nfirst\n")

    tar_path = backup.create_backup(project="demo", project_root=project_root)

    assert tar_path.exists()
    assert tar_path.parent == tmp_path / "backups"
    with tarfile.open(tar_path, "r:gz") as tar:
        names = tar.getnames()
        assert "demo/ideas.csv" in names


def test_create_backup_is_outside_project_root(monkeypatch, tmp_path):
    monkeypatch.setenv(backup.BACKUP_DIR_ENV, str(tmp_path / "backups"))
    project_root = tmp_path / "demo"
    project_root.mkdir()
    tar_path = backup.create_backup(project="demo", project_root=project_root)
    assert project_root not in tar_path.parents
```

```python
# append to tests/pdata/test_init_paths.py — deferred from Task 1, see that task's
# note; needs backup.BACKUP_DIR_ENV, which doesn't exist until this task.
from cc_session_tools.lib.pdata import backup


def test_backup_dir_override_noop_without_rehearse(monkeypatch):
    monkeypatch.delenv(backup.BACKUP_DIR_ENV, raising=False)
    with init_paths.backup_dir_override(None):
        assert backup.BACKUP_DIR_ENV not in os.environ


def test_backup_dir_override_redirects_and_restores(monkeypatch, tmp_path):
    monkeypatch.setenv(backup.BACKUP_DIR_ENV, "/original/backups")
    rehearse_path = tmp_path / "rehearsal"
    with init_paths.backup_dir_override(rehearse_path):
        assert os.environ[backup.BACKUP_DIR_ENV] == str(
            rehearse_path / init_paths.REHEARSAL_BACKUP_DIRNAME
        )
    assert os.environ[backup.BACKUP_DIR_ENV] == "/original/backups"


def test_backup_dir_override_clears_when_previously_unset(monkeypatch, tmp_path):
    monkeypatch.delenv(backup.BACKUP_DIR_ENV, raising=False)
    rehearse_path = tmp_path / "rehearsal"
    with init_paths.backup_dir_override(rehearse_path):
        assert backup.BACKUP_DIR_ENV in os.environ
    assert backup.BACKUP_DIR_ENV not in os.environ


def test_excluded_dir_names_includes_rehearsal_backup_dirname():
    """The classifier (Task 3) walks project_root via EXCLUDED_DIR_NAMES alone —
    without this, a rehearsal's own backup tarball directory would be walked into
    and its contents proposed as project files on a reclassification pass."""
    assert init_paths.REHEARSAL_BACKUP_DIRNAME in init_paths.EXCLUDED_DIR_NAMES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pdata/test_backup.py tests/pdata/test_init_paths.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cc_session_tools.lib.pdata.backup'` for the
first file; `AttributeError: module ... has no attribute 'backup_dir_override'` for the second.

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/pdata/backup.py
"""Pre-cutover backup snapshot for `ccst pdata init` (spec §7.1 step 5)."""
from __future__ import annotations

import os
import tarfile
import time
from pathlib import Path

from cc_session_tools.lib import paths

BACKUP_DIR_ENV = "CCST_PDATA_BACKUP_DIR"


def backup_dir() -> Path:
    override = os.environ.get(BACKUP_DIR_ENV)
    return Path(override).expanduser() if override else paths.data_home() / "pdata-backups"


def create_backup(*, project: str, project_root: Path) -> Path:
    """tar.gz snapshot of project_root as it stands right now, written outside
    project_root (spec §7.1 step 5 — "stored outside the project folder, before
    touching any original file"). One backup per --write invocation; never
    overwritten or extended incrementally, matching the big-bang migration
    philosophy (spec goals)."""
    target_dir = backup_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    tar_path = target_dir / f"{project}-{int(time.time())}.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(project_root, arcname=project)
    return tar_path
```

```python
# modify the top-level imports in src/cc_session_tools/lib/pdata/init_paths.py —
# backup.py has no import of its own on init_paths, so this is not a cycle:

from cc_session_tools.lib.pdata import backup, store
```

```python
# modify EXCLUDED_DIR_NAMES in src/cc_session_tools/lib/pdata/init_paths.py — the
# classifier (Task 3) must never walk into a rehearsal's own backup tarball
# directory and propose it as a project file, exactly as it already skips
# REHEARSAL_DB_DIRNAME:

REHEARSAL_BACKUP_DIRNAME = ".ccst-pdata-rehearsal-backups"

EXCLUDED_DIR_NAMES = frozenset({
    ".git", ".claude", "cc-sessions", MIGRATED_ARCHIVE_DIRNAME, REHEARSAL_DB_DIRNAME,
    REHEARSAL_BACKUP_DIRNAME,
})
```

```python
# append to src/cc_session_tools/lib/pdata/init_paths.py:


@contextmanager
def backup_dir_override(rehearse: Path | None) -> Iterator[None]:
    """Redirect backup.create_backup() to a self-contained rehearsal directory
    instead of the real backup_dir() (paths.data_home()/"pdata-backups" or
    whatever CCST_PDATA_BACKUP_DIR is already set to), by reusing this plan's own
    backup.BACKUP_DIR_ENV seam — mirrors project_db_dir_override's shape exactly.
    Without this, a rehearsed --write would still deposit a
    <project>-<epoch>.tar.gz into the same directory a genuine production
    migration's backup would use, indistinguishable by filename (plan Decision
    3). No-op when rehearse is None."""
    if rehearse is None:
        yield
        return
    override_dir = rehearse / REHEARSAL_BACKUP_DIRNAME
    previous = os.environ.get(backup.BACKUP_DIR_ENV)
    os.environ[backup.BACKUP_DIR_ENV] = str(override_dir)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(backup.BACKUP_DIR_ENV, None)
        else:
            os.environ[backup.BACKUP_DIR_ENV] = previous
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_backup.py tests/pdata/test_init_paths.py -v`
Expected: PASS (3 tests in test_backup.py; 12 tests in test_init_paths.py)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/backup.py src/cc_session_tools/lib/pdata/init_paths.py tests/pdata/test_backup.py tests/pdata/test_init_paths.py
git commit -m "feat(pdata-init): add pre-cutover tar.gz backup snapshot and rehearsal backup-dir isolation"
```

---

## Task 6: `lib/pdata/cutover.py` — archive migrated-source files

**Files:**
- Create: `src/cc_session_tools/lib/pdata/cutover.py`
- Test: `tests/pdata/test_cutover.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/pdata/test_cutover.py
from __future__ import annotations

from cc_session_tools.lib.pdata import cutover
from cc_session_tools.lib.pdata.init_paths import (
    MIGRATED_ARCHIVE_DIRNAME,
    MIGRATED_MANIFEST_FILENAME,
)
from cc_session_tools.lib.pdata.manifest import ManifestEntry


def test_archive_entries_moves_files_and_writes_manifest_log(tmp_path):
    (tmp_path / "ideas.csv").write_text("idea\nfirst\n")
    entry = ManifestEntry(path="ideas.csv", classification="db-owned",
                           record_group="ideas", strategy="csv-rows")

    cutover.archive_entries(project_root=tmp_path, entries=[entry])

    assert not (tmp_path / "ideas.csv").exists()
    archived = tmp_path / MIGRATED_ARCHIVE_DIRNAME / "ideas.csv"
    assert archived.exists()
    assert archived.read_text() == "idea\nfirst\n"

    log_path = tmp_path / MIGRATED_ARCHIVE_DIRNAME / MIGRATED_MANIFEST_FILENAME
    assert log_path.exists()
    assert "ideas.csv" in log_path.read_text()
    assert "ideas" in log_path.read_text()  # record_group name recorded


def test_archive_entries_preserves_relative_directory_structure(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "log.csv").write_text("x\n1\n")
    entry = ManifestEntry(path="sub/log.csv", classification="db-owned",
                           record_group="sublog", strategy="csv-rows")

    cutover.archive_entries(project_root=tmp_path, entries=[entry])

    assert (tmp_path / MIGRATED_ARCHIVE_DIRNAME / "sub" / "log.csv").exists()


def test_archive_entries_noop_for_empty_list(tmp_path):
    cutover.archive_entries(project_root=tmp_path, entries=[])
    assert not (tmp_path / MIGRATED_ARCHIVE_DIRNAME).exists()


def test_archive_entries_appends_across_calls(tmp_path):
    (tmp_path / "a.csv").write_text("x\n1\n")
    (tmp_path / "b.csv").write_text("x\n1\n")
    entry_a = ManifestEntry(path="a.csv", classification="db-owned",
                             record_group="a", strategy="csv-rows")
    entry_b = ManifestEntry(path="b.csv", classification="db-owned",
                             record_group="b", strategy="csv-rows")

    cutover.archive_entries(project_root=tmp_path, entries=[entry_a])
    cutover.archive_entries(project_root=tmp_path, entries=[entry_b])

    log_path = tmp_path / MIGRATED_ARCHIVE_DIRNAME / MIGRATED_MANIFEST_FILENAME
    text = log_path.read_text()
    assert "a.csv" in text and "b.csv" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pdata/test_cutover.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cc_session_tools.lib.pdata.cutover'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/pdata/cutover.py
"""Cutover: archive migrated-source originals, never delete them (spec §7.1 steps 6-7)."""
from __future__ import annotations

import time
from pathlib import Path

from cc_session_tools.lib.pdata.init_paths import (
    MIGRATED_ARCHIVE_DIRNAME,
    MIGRATED_MANIFEST_FILENAME,
)
from cc_session_tools.lib.pdata.manifest import ManifestEntry


def archive_entries(*, project_root: Path, entries: list[ManifestEntry]) -> None:
    """Move every db-owned entry's source file into project_root/.pdata-migrated/,
    preserving its relative path, and append one line per entry to MANIFEST.md.
    Never deletes — cutover only relocates within project_root (spec §7.1 step 6);
    deleting the archive is a manual, human-directed action (step 7)."""
    if not entries:
        return
    archive_root = project_root / MIGRATED_ARCHIVE_DIRNAME
    archive_root.mkdir(parents=True, exist_ok=True)
    manifest_path = archive_root / MIGRATED_MANIFEST_FILENAME
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with manifest_path.open("a", encoding="utf-8") as log:
        for entry in entries:
            source = project_root / entry.path
            destination = archive_root / entry.path
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.rename(destination)
            log.write(
                f"- {now} — {entry.path} — migrated source, superseded by ccst pdata "
                f"(record_group={entry.db_group()})\n"
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_cutover.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/cutover.py tests/pdata/test_cutover.py
git commit -m "feat(pdata-init): add cutover archiving of migrated-source files"
```

---

## Task 7: `lib/pdata/init_service.py` — `dry_run()`

**Files:**
- Create: `src/cc_session_tools/lib/pdata/init_service.py`
- Test: `tests/pdata/test_init_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/pdata/test_init_service.py
from __future__ import annotations

from cc_session_tools.lib.pdata import init_paths, init_service


def test_dry_run_empty_project_reports_no_files_and_creates_db(monkeypatch, tmp_path):
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))

    result = init_service.dry_run(project="biz")

    assert "no files found" in result.report
    assert result.manifest.entries == []
    assert (tmp_path / "dbs" / "biz.db").exists()


def test_dry_run_reports_classified_entries(monkeypatch, tmp_path):
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "CLAUDE.md").write_text("# demo\n")
    (project_dir / "ideas.csv").write_text("idea\nfirst\n")

    result = init_service.dry_run(project="demo")

    assert "[folder-owned] CLAUDE.md" in result.report
    assert "[db-owned]     ideas.csv -> group=ideas strategy=csv-rows" in result.report
    assert result.proposal_path == project_dir / init_paths.PROPOSAL_FILENAME
    assert result.proposal_path.exists()


def test_dry_run_second_call_preserves_hand_edited_proposal(monkeypatch, tmp_path):
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "notes.md").write_text("## one\nbody\n")

    from cc_session_tools.lib.pdata import manifest

    first = init_service.dry_run(project="demo")
    edited = manifest.load(first.proposal_path)
    edited.entries[0].classification = "db-owned"
    edited.entries[0].record_group = "notes"
    edited.entries[0].strategy = "delimited-sections"
    edited.entries[0].reviewed = True
    manifest.save(edited, first.proposal_path)

    second = init_service.dry_run(project="demo")
    assert second.manifest.entries[0].classification == "db-owned"
    assert second.manifest.entries[0].reviewed is True


def test_dry_run_disambiguates_against_existing_live_record_group(monkeypatch, tmp_path):
    """A record_group can already have live rows — from an earlier ccst pdata init
    run, or from an unrelated mechanism entirely (e.g. Plan A's service.add_record
    used directly, or a different plan's own writes) — even before this project's
    first classification pass for a *new* file proposing that same name. The
    fresh pass must never silently propose merging into it."""
    from cc_session_tools.lib.pdata import service

    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "notes.csv").write_text("x\n1\n")

    service.add_record(
        project="demo", record_group="notes", content="pre-existing",
        file_path=None, fields={},
    )

    result = init_service.dry_run(project="demo")

    assert result.manifest.entries[0].record_group != "notes"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pdata/test_init_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cc_session_tools.lib.pdata.init_service'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/pdata/init_service.py
"""Orchestration for `ccst pdata init` (spec §7): dry-run classification (steps
0-2) and the write/verify/backup/cutover phase (Task 9 adds write()). Every DB
write goes through Plan A's service.py — this module owns no SQL of its own.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cc_session_tools.lib.pdata import init_paths, manifest, repository, service
from cc_session_tools.lib.pdata.manifest import Manifest


@dataclass
class DryRunResult:
    manifest: Manifest
    report: str
    proposal_path: Path


def dry_run(*, project: str, rehearse: Path | None = None) -> DryRunResult:
    project_root = init_paths.resolve_project_root(project, rehearse=rehearse)
    with init_paths.project_db_dir_override(rehearse):
        # repository.connect() runs the base-schema DDL (CREATE TABLE IF NOT
        # EXISTS) on every call — this is what "safe to run against an empty
        # folder... also how a genuinely new project gets its .db" (spec §5) means.
        repository.connect(project).close()
        # The project's already-live record_groups (from a prior ccst pdata init
        # run, from Plan A's service.add_record used directly, or from an
        # unrelated mechanism like Plan C's session-output groups) — threaded
        # through to the classifier so a first-ever/forced-reclassification pass
        # never silently proposes merging a new file into one of them (see
        # classify._disambiguate_record_groups).
        existing_record_groups = frozenset(
            str(group["record_group"]) for group in service.schema_list(project=project)
        )
    proposal_path = project_root / init_paths.PROPOSAL_FILENAME
    m = manifest.load_or_create(
        project_root, project, proposal_path,
        existing_record_groups=existing_record_groups,
    )
    return DryRunResult(manifest=m, report=_render_report(m), proposal_path=proposal_path)


def _render_report(m: Manifest) -> str:
    if not m.entries:
        return f"ccst pdata init — {m.project}: no files found, empty base schema created."
    lines = [f"ccst pdata init — {m.project}: {len(m.entries)} file(s) classified"]
    for e in m.entries:
        if e.classification == "folder-owned":
            lines.append(f"  [folder-owned] {e.path}")
        else:
            field_names = [f.name for f in e.fields]
            lines.append(
                f"  [db-owned]     {e.path} -> group={e.record_group} "
                f"strategy={e.strategy} fields={field_names}"
            )
    lines.append(
        "Review/override entries in the proposal file listed below before running --write."
    )
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_init_service.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/init_service.py tests/pdata/test_init_service.py
git commit -m "feat(pdata-init): add dry_run() classification orchestration"
```

---

## Task 8: CLI wiring — `ccst pdata init` (dry-run only)

**Files:**
- Modify: `src/cc_session_tools/cli/ccst.py`
- Test: `tests/test_ccst_pdata_init_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ccst_pdata_init_cli.py
from __future__ import annotations

import json
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
    env["CCST_PROJECTS_ROOT"] = str(tmp_path / "projects")
    return env


def test_pdata_init_dry_run_empty_project(base_env, tmp_path):
    (tmp_path / "projects" / "biz").mkdir(parents=True)
    r = _run(base_env, "pdata", "init", "--project", "biz")
    assert r.returncode == 0, r.stderr
    assert "no files found" in r.stdout


def test_pdata_init_dry_run_classifies_and_writes_proposal(base_env, tmp_path):
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "CLAUDE.md").write_text("# demo\n")
    (project_dir / "ideas.csv").write_text("idea,priority\nfirst,1\n")

    r = _run(base_env, "pdata", "init", "--project", "demo")
    assert r.returncode == 0, r.stderr
    assert "[folder-owned] CLAUDE.md" in r.stdout
    assert "group=ideas strategy=csv-rows" in r.stdout
    assert (project_dir / ".ccst-pdata-proposal.json").exists()


def test_pdata_init_rejects_bad_project_name(base_env):
    r = _run(base_env, "pdata", "init", "--project", "../escape")
    assert r.returncode == 2
    assert "project" in r.stderr


def test_pdata_init_rejects_malformed_proposal_file(base_env, tmp_path):
    """A hand-edited .ccst-pdata-proposal.json missing a required key must produce
    the documented exit-2 validation error, not an uncaught KeyError traceback."""
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / ".ccst-pdata-proposal.json").write_text(
        json.dumps({"entries": [{"path": "x", "classification": "folder-owned"}]})
    )  # missing the required "project" key

    r = _run(base_env, "pdata", "init", "--project", "demo")
    assert r.returncode == 2
    assert "malformed manifest" in r.stderr
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ccst_pdata_init_cli.py -v`
Expected: FAIL with an argparse "invalid choice: 'init'" error (exit 2) — `pdata` has no
`init` verb yet.

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/cc_session_tools/cli/ccst.py — new section after the existing
# "---------- pdata ----------" handlers from Plan A:

def _cmd_pdata_init(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import init_service

    rehearse = Path(args.rehearse) if args.rehearse else None

    if not args.write:
        try:
            result = init_service.dry_run(project=args.project, rehearse=rehearse)
        except ValueError as exc:
            print(f"ccst pdata: {exc}", file=sys.stderr)
            return 2
        print(result.report)
        print(f"\nProposal: {result.proposal_path}")
        return 0

    try:
        result = init_service.write(project=args.project, rehearse=rehearse)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ccst pdata: {exc}", file=sys.stderr)
        return 2

    if result.failure is not None:
        print("ccst pdata init: verification failed, nothing was cut over:", file=sys.stderr)
        for reason in result.failure.reasons:
            print(f"  - {reason}", file=sys.stderr)
        return 1

    print(
        f"Wrote {len(result.created_record_ids)} record(s) across "
        f"{len(result.entries_written)} file(s)."
    )
    print(f"Backup: {result.backup_path}")
    for path in result.entries_written:
        print(f"  cut over: {path}")
    print()
    print(result.report)  # spec §7.1 step 4's human-readable diff report, for review
    return 0
```

```python
# add to pdata_sub in _build_parser(), after Plan A's existing pdata verbs:

    pdata_init_parser = pdata_sub.add_parser(
        "init", help="Initialize/migrate a project's data store (spec §7)"
    )
    pdata_init_parser.add_argument("--project", required=True, metavar="NAME")
    pdata_init_parser.add_argument(
        "--rehearse", default=None, metavar="PATH",
        help="Run against a copy at PATH instead of the live project (spec §7.1 step 0)",
    )
    pdata_init_parser.add_argument(
        "--write", action="store_true",
        help="Perform the write/verify/backup/cutover phase (default: dry-run only)",
    )
```

```python
# add to main() dispatch in src/cc_session_tools/cli/ccst.py, inside the
# `if args.noun == "pdata":` block Plan A already added:

        if args.verb == "init":
            sys.exit(_cmd_pdata_init(args))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ccst_pdata_init_cli.py -v`
Expected: PASS (4 tests) — `--write` isn't exercised by these tests yet; that arrives in Task 10
alongside `init_service.write()` (Task 9).

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/ccst.py tests/test_ccst_pdata_init_cli.py
git commit -m "feat(pdata-init): wire ccst pdata init dry-run CLI verb"
```

---

## Task 9: `lib/pdata/init_service.py` — `write()`

**Files:**
- Modify: `src/cc_session_tools/lib/pdata/init_service.py`
- Modify: `tests/pdata/test_init_service.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/pdata/test_init_service.py

def test_write_without_prior_dry_run_raises(monkeypatch, tmp_path):
    import pytest

    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    (tmp_path / "projects" / "demo").mkdir(parents=True)

    with pytest.raises(FileNotFoundError, match="proposal"):
        init_service.write(project="demo")


def test_write_imports_csv_rows_and_cuts_over(monkeypatch, tmp_path):
    from cc_session_tools.lib.pdata import service

    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv("CCST_PDATA_BACKUP_DIR", str(tmp_path / "backups"))
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "ideas.csv").write_text("idea\nfirst\nsecond\n")

    init_service.dry_run(project="demo")
    result = init_service.write(project="demo")

    assert result.failure is None
    assert len(result.created_record_ids) == 2
    assert result.entries_written == ["ideas.csv"]
    assert result.backup_path is not None and result.backup_path.exists()
    assert not (project_dir / "ideas.csv").exists()
    assert (project_dir / init_paths.MIGRATED_ARCHIVE_DIRNAME / "ideas.csv").exists()
    assert "ideas.csv: 2 row(s)" in result.report

    records = service.list_records(project="demo", record_group="ideas")
    assert {r.content for r in records} == {'{"idea": "first"}', '{"idea": "second"}'}


def test_write_aborts_and_soft_deletes_on_absolute_file_path(monkeypatch, tmp_path):
    from cc_session_tools.lib.pdata import manifest, service

    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv("CCST_PDATA_BACKUP_DIR", str(tmp_path / "backups"))
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "docs.csv").write_text("doc_path,note\n/etc/passwd,bad\n")

    dry = init_service.dry_run(project="demo")
    edited = manifest.load(dry.proposal_path)
    edited.entries[0].file_path_column = "doc_path"
    manifest.save(edited, dry.proposal_path)

    result = init_service.write(project="demo")

    assert result.failure is not None
    assert result.entries_written == []
    assert result.backup_path is None
    # nothing cut over, original file untouched
    assert (project_dir / "docs.csv").exists()
    assert not (project_dir / init_paths.MIGRATED_ARCHIVE_DIRNAME).exists()
    # no live rows left over from the aborted attempt
    assert service.list_records(project="demo", record_group="docs") == []


def test_write_verification_catches_unresolved_file_path_and_soft_deletes(monkeypatch, tmp_path):
    from cc_session_tools.lib.pdata import manifest, service

    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv("CCST_PDATA_BACKUP_DIR", str(tmp_path / "backups"))
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "docs.csv").write_text("doc_path,note\nmissing/does-not-exist.pdf,bad\n")

    dry = init_service.dry_run(project="demo")
    edited = manifest.load(dry.proposal_path)
    edited.entries[0].file_path_column = "doc_path"
    manifest.save(edited, dry.proposal_path)

    result = init_service.write(project="demo")

    assert result.failure is not None
    assert any("does not resolve" in reason for reason in result.failure.reasons)
    assert not (project_dir / init_paths.MIGRATED_ARCHIVE_DIRNAME).exists()
    assert service.list_records(project="demo", record_group="docs", include_deleted=True)[0].deleted_at is not None


def test_write_rehearse_leaves_real_project_db_and_backup_dir_untouched(monkeypatch, tmp_path):
    """Covers all three of --rehearse's isolation seams: project files, the .db
    (via CCST_PROJECT_DB_DIR), and the backup tarball (via CCST_PDATA_BACKUP_DIR).
    A real production backup dir is set here specifically so a bug that skips the
    backup-dir redirection would deposit a real tar.gz into it and fail this
    test, rather than the test silently writing into whatever default
    paths.data_home() resolves to on the machine running it."""
    import shutil

    from cc_session_tools.lib.pdata import service

    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    real_backup_dir = tmp_path / "real-backups"
    monkeypatch.setenv("CCST_PDATA_BACKUP_DIR", str(real_backup_dir))
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "ideas.csv").write_text("idea\nfirst\n")
    rehearsal_dir = tmp_path / "rehearsal-demo"
    shutil.copytree(project_dir, rehearsal_dir)

    init_service.dry_run(project="demo", rehearse=rehearsal_dir)
    result = init_service.write(project="demo", rehearse=rehearsal_dir)

    assert result.failure is None
    # real project folder still has its original file...
    assert (project_dir / "ideas.csv").exists()
    # ...and the real (non-rehearsal) db has no rows from the rehearsal run...
    assert service.list_records(project="demo", record_group="ideas") == []
    # ...and the rehearsal's backup tarball landed inside the rehearsal sandbox,
    # never in the real CCST_PDATA_BACKUP_DIR a genuine migration would use.
    assert result.backup_path is not None
    assert rehearsal_dir in result.backup_path.parents
    assert not real_backup_dir.exists() or not any(real_backup_dir.iterdir())


def test_write_aborts_and_soft_deletes_on_oversized_csv_field(monkeypatch, tmp_path):
    """csv.Error (e.g. a field exceeding csv.field_size_limit()) must be caught by the
    same abort-and-soft-delete path as ValueError/OSError, not crash write() with an
    unhandled exception mid-run."""
    import csv

    from cc_session_tools.lib.pdata import service

    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv("CCST_PDATA_BACKUP_DIR", str(tmp_path / "backups"))
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "ideas.csv").write_text("idea\nfirst\n" + ("x" * 200_000) + "\n")

    old_limit = csv.field_size_limit()
    csv.field_size_limit(100_000)
    try:
        init_service.dry_run(project="demo")
        result = init_service.write(project="demo")
    finally:
        csv.field_size_limit(old_limit)

    assert result.failure is not None
    assert service.list_records(project="demo", record_group="ideas") == []


def test_write_reports_rollback_failure_without_crashing(monkeypatch, tmp_path):
    """A RecordNotFoundError/VersionConflictError raised mid-rollback (both plain
    Exception subclasses, not ValueError/OSError) must be collected into the
    returned WriteFailure, not propagate and abort the rollback loop partway.

    This must reuse a fixture where at least one row is actually inserted before
    the run fails, or created_ids stays empty and the rollback loop (and this
    test's mocked delete_record) never runs at all. An absolute/'..'
    file_path_column value (as in test_write_aborts_and_soft_deletes_on_absolute_
    file_path) is the wrong fixture for that reason: add_record's
    _validate_relative_file_path() raises before repository.connect() is ever
    called, so no row lands and created_ids is empty. The unresolved-but-relative
    file_path fixture below inserts the row successfully and only fails later, in
    _verify() — exactly like test_write_verification_catches_unresolved_file_path_
    and_soft_deletes — which is what gives rollback something to actually do."""
    from cc_session_tools.lib.pdata import manifest, service

    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv("CCST_PDATA_BACKUP_DIR", str(tmp_path / "backups"))
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "docs.csv").write_text(
        "doc_path,note\nmissing/does-not-exist.pdf,bad\n"
    )

    dry = init_service.dry_run(project="demo")
    edited = manifest.load(dry.proposal_path)
    edited.entries[0].file_path_column = "doc_path"
    manifest.save(edited, dry.proposal_path)

    def _flaky_delete_record(**kwargs):
        raise service.RecordNotFoundError(kwargs["record_id"])

    monkeypatch.setattr(service, "delete_record", _flaky_delete_record)
    result = init_service.write(project="demo")

    assert result.failure is not None
    assert any("does not resolve" in reason for reason in result.failure.reasons)
    assert any("rollback failed" in reason for reason in result.failure.reasons)


def test_write_rejects_conflicting_field_sql_types_across_entries(monkeypatch, tmp_path):
    """Two manifest entries feeding the same record_group with the same field name
    but a different sql_type must be rejected before any DDL/import runs — Plan
    A's schema_add_field silently no-ops on an already-existing column, which
    would otherwise drop the second entry's type with no error or warning."""
    import pytest

    from cc_session_tools.lib.pdata import manifest

    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "a.csv").write_text("count\n1\n")
    (project_dir / "b.csv").write_text("count\nx\n")

    dry = init_service.dry_run(project="demo")
    edited = manifest.load(dry.proposal_path)
    for entry in edited.entries:
        entry.record_group = "shared"
    edited.entries[0].fields[0].sql_type = "INTEGER"
    edited.entries[1].fields[0].sql_type = "TEXT"
    manifest.save(edited, dry.proposal_path)

    with pytest.raises(ValueError, match="conflicting sql_type"):
        init_service.write(project="demo")


def test_write_aborts_and_soft_deletes_on_manifest_strategy_shape_mismatch(monkeypatch, tmp_path):
    """A hand-edited manifest entry (pm-project-init Step 4) can assign a strategy
    that doesn't match the file's actual JSON shape — importers.py raises
    ValueError for this (see test_importers.py), and that ValueError must hit the
    same abort-and-soft-delete path as any other per-entry failure, not crash
    write() with an unhandled AttributeError/TypeError."""
    from cc_session_tools.lib.pdata import manifest, service

    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv("CCST_PDATA_BACKUP_DIR", str(tmp_path / "backups"))
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "chars.json").write_text('{"name": "solo"}')  # a JSON object

    dry = init_service.dry_run(project="demo")
    edited = manifest.load(dry.proposal_path)
    # force a strategy that doesn't match this file's actual (object) shape
    edited.entries[0].strategy = "json-array-rows"
    manifest.save(edited, dry.proposal_path)

    result = init_service.write(project="demo")

    assert result.failure is not None
    assert any("json-array-rows" in reason for reason in result.failure.reasons)
    assert service.list_records(project="demo", record_group="chars") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pdata/test_init_service.py -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'write'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/cc_session_tools/lib/pdata/init_service.py

import csv

from cc_session_tools.lib.pdata import backup, cutover
from cc_session_tools.lib.pdata.importers import ImportRow, count_source_rows, import_entry
from cc_session_tools.lib.pdata.manifest import ManifestEntry

# `service` is already imported at module level (Task 7's dry_run() uses it too).


@dataclass
class WriteFailure:
    reasons: list[str]


@dataclass
class WriteResult:
    created_record_ids: list[int]
    entries_written: list[str]
    backup_path: Path | None
    failure: WriteFailure | None
    report: str = ""


def _validate_no_conflicting_field_types(m: Manifest) -> None:
    """Two manifest entries can legitimately feed the same record_group (nothing
    in this plan's manifest/write() design forbids it, and Plan D's own
    row-count-parity fixture relies on exactly this) — but Plan A's
    schema_add_field/add_extension_column silently no-ops when a field name
    already has a column, so two entries proposing the same field name with a
    *different* sql_type would otherwise have the second entry's type silently
    dropped, with no error, no warning, and no mention in the diff report, and its
    rows coerced/stored under the first entry's column type. Catch the conflict
    here, before any DDL or row import runs (a validation error, exit 2 — spec
    Decision 10 — not a verification failure with partially-inserted rows to roll
    back), so an incompatible pair of entries is rejected up front instead of
    silently corrupting one side's data."""
    seen: dict[tuple[str, str], str] = {}
    for entry in m.entries:
        if entry.classification != "db-owned":
            continue
        for spec in entry.fields:
            key = (entry.db_group(), spec.name)
            prior_type = seen.get(key)
            if prior_type is not None and prior_type != spec.sql_type:
                raise ValueError(
                    f"conflicting sql_type for field {spec.name!r} in record_group "
                    f"{entry.db_group()!r}: {prior_type!r} (from an earlier entry) "
                    f"vs {spec.sql_type!r} (from {entry.path!r}) — align both "
                    f"entries' field sql_type in the proposal before running --write"
                )
            seen[key] = spec.sql_type


def write(*, project: str, rehearse: Path | None = None) -> WriteResult:
    project_root = init_paths.resolve_project_root(project, rehearse=rehearse)
    proposal_path = project_root / init_paths.PROPOSAL_FILENAME
    if not proposal_path.exists():
        raise FileNotFoundError(
            f"no classification proposal found at {proposal_path} — run "
            f"'ccst pdata init --project {project}' (add --rehearse if rehearsing) first"
        )
    m = manifest.load(proposal_path)
    _validate_no_conflicting_field_types(m)

    created_ids: list[int] = []
    reasons: list[str] = []
    written_entries: list[ManifestEntry] = []
    # (record_id, ImportRow) pairs per entry — kept (not just the id) so _verify can
    # spot-check DB content against what was actually imported, and so the
    # human-readable diff report (spec §7.1 step 4) has real content to show.
    entry_rows: dict[str, list[tuple[int, ImportRow]]] = {}

    # Both rehearsal-isolation seams are entered together for the whole
    # write/verify/backup phase: project_db_dir_override redirects the .db (Plan
    # A's CCST_PROJECT_DB_DIR seam), backup_dir_override redirects where
    # backup.create_backup() below writes its tar.gz (this plan's own
    # CCST_PDATA_BACKUP_DIR seam). Without the second seam, a rehearsed --write
    # would still deposit a real <project>-<epoch>.tar.gz into the production
    # backup directory — indistinguishable by filename from a genuine migration's
    # backup (plan Decision 3). Both are no-ops when rehearse is None.
    with (
        init_paths.project_db_dir_override(rehearse),
        init_paths.backup_dir_override(rehearse),
    ):
        for entry in m.entries:
            if entry.classification != "db-owned":
                continue
            try:
                for spec in entry.fields:
                    service.schema_add_field(
                        project=project, record_group=entry.db_group(),
                        field_name=spec.name, sql_type=spec.sql_type,
                        description=spec.description, default=spec.default,
                    )
                rows_for_entry: list[tuple[int, ImportRow]] = []
                for row in import_entry(project_root, entry):
                    record = service.add_record(
                        project=project, record_group=entry.db_group(),
                        content=row.content, file_path=row.file_path,
                        fields=row.fields, created_at=row.created_at,
                    )
                    created_ids.append(record.id)
                    rows_for_entry.append((record.id, row))
                entry_rows[entry.path] = rows_for_entry
                written_entries.append(entry)
            except (ValueError, OSError, csv.Error) as exc:
                reasons.append(f"{entry.path}: {exc}")

        reasons.extend(
            _verify(project=project, project_root=project_root,
                     written_entries=written_entries, entry_rows=entry_rows)
        )

        if reasons:
            # Soft-delete every row inserted this run (spec §4.5) — no hard delete,
            # full auditability, and nothing proceeds to backup/cutover (plan
            # Decision 8). Every id here was just inserted in this single-threaded
            # run, so its version is always 1 — but each delete_record call is still
            # wrapped individually: service.RecordNotFoundError/VersionConflictError
            # are plain Exception subclasses (not ValueError/OSError), so an
            # unwrapped raise here would abort the loop mid-way and leave some
            # just-inserted rows soft-deleted and others still live. Any rollback
            # failure is reported alongside the original failure reasons rather
            # than raised, since the caller still needs a WriteResult back, not a
            # crash, to know backup/cutover did not run.
            rollback_failures: list[str] = []
            for record_id in created_ids:
                try:
                    service.delete_record(project=project, record_id=record_id, expected_version=1)
                except (service.RecordNotFoundError, service.VersionConflictError) as exc:
                    rollback_failures.append(f"record {record_id}: rollback failed: {exc}")
            return WriteResult(created_record_ids=[], entries_written=[],
                                backup_path=None,
                                failure=WriteFailure(reasons=reasons + rollback_failures))

        # Still inside both overrides: a rehearsed run's backup must land in the
        # rehearsal sandbox (backup_dir_override), never in the real backup dir.
        backup_path = backup.create_backup(project=project, project_root=project_root)

    cutover.archive_entries(project_root=project_root, entries=written_entries)
    return WriteResult(
        created_record_ids=created_ids,
        entries_written=[e.path for e in written_entries],
        backup_path=backup_path, failure=None,
        report=_render_diff_report(written_entries=written_entries, entry_rows=entry_rows),
    )


def _verify(
    *, project: str, project_root: Path, written_entries: list[ManifestEntry],
    entry_rows: dict[str, list[tuple[int, ImportRow]]],
) -> list[str]:
    """Spec §7.1 step 4: entry-count parity (DB rows vs. an independent re-count of
    the source file), a content spot-check (DB content vs. what was actually passed
    to add_record), and file_path resolution — all three must hold for every
    newly-inserted row before backup/cutover proceeds."""
    reasons: list[str] = []
    for entry in written_entries:
        rows = entry_rows[entry.path]
        expected_count = count_source_rows(project_root, entry)
        if len(rows) != expected_count:
            reasons.append(
                f"{entry.path}: imported {len(rows)} row(s) but re-counting the source "
                f"gives {expected_count} — entry-count parity check failed"
            )
        for record_id, import_row in rows:
            record = service.get_record(project=project, record_id=record_id)
            assert record is not None, (
                f"record {record_id} inserted this run but missing during verification"
            )
            if record.content != import_row.content:
                reasons.append(
                    f"{entry.path}: record {record_id} content does not match what "
                    f"was imported — content spot-check failed"
                )
            if record.file_path:
                target = project_root / record.file_path
                if not target.exists():
                    reasons.append(
                        f"{entry.path}: record {record_id} file_path "
                        f"{record.file_path!r} does not resolve under {project_root}"
                    )
    return reasons


def _render_diff_report(
    *, written_entries: list[ManifestEntry], entry_rows: dict[str, list[tuple[int, ImportRow]]],
) -> str:
    """Spec §7.1 step 4's human-readable diff report — old content vs. what landed
    in the DB — for Chris to review. Printed by the CLI as part of a successful
    `--write`'s output (Task 10), immediately after verification passed and before
    the process would otherwise exit; there is no separate interactive confirmation
    gate ahead of backup/cutover (`--write` stays the single atomic operation
    Decision 8 describes), so this report is the review artifact for that already-
    completed run, not a pre-cutover prompt — the human review gate the spec
    protects against a wrong classification lives at step 2 (proposal review) and
    step 0 (--rehearse), both of which run before --write is ever invoked."""
    lines = ["ccst pdata init --write — verification diff report:"]
    for entry in written_entries:
        rows = entry_rows[entry.path]
        lines.append(f"  {entry.path}: {len(rows)} row(s) -> group={entry.db_group()}")
        for record_id, row in rows[:3]:
            preview = row.content[:80].replace("\n", " ")
            lines.append(f"    id={record_id} content={preview!r}")
        if len(rows) > 3:
            lines.append(f"    ... and {len(rows) - 3} more row(s)")
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_init_service.py -v`
Expected: PASS (13 tests total in the file)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/init_service.py tests/pdata/test_init_service.py
git commit -m "feat(pdata-init): add write() with verify/backup/cutover and abort-and-soft-delete"
```

---

## Task 10: CLI wiring — `ccst pdata init --write` and `--rehearse` end-to-end

**Files:**
- Modify: `tests/test_ccst_pdata_init_cli.py`

No production code changes — Task 8 already wired `--write`/`--rehearse` through to
`init_service.write()`; this task is CLI-level (subprocess) test coverage proving the whole
procedure works end-to-end through the actual `ccst` entry point, not just via direct Python
calls into `init_service`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_ccst_pdata_init_cli.py

import shutil


def test_pdata_init_write_end_to_end_imports_and_cuts_over(base_env, tmp_path):
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "ideas.csv").write_text("idea\nfirst\nsecond\n")
    base_env["CCST_PDATA_BACKUP_DIR"] = str(tmp_path / "backups")

    r_dry = _run(base_env, "pdata", "init", "--project", "demo")
    assert r_dry.returncode == 0, r_dry.stderr

    r_write = _run(base_env, "pdata", "init", "--project", "demo", "--write")
    assert r_write.returncode == 0, r_write.stderr
    assert "Wrote 2 record(s)" in r_write.stdout

    assert not (project_dir / "ideas.csv").exists()
    assert (project_dir / ".pdata-migrated" / "ideas.csv").exists()

    r_list = _run(base_env, "pdata", "list", "--project", "demo", "--group", "ideas",
                  "--format", "json")
    assert r_list.returncode == 0, r_list.stderr
    assert "first" in r_list.stdout and "second" in r_list.stdout


def test_pdata_init_write_without_prior_dry_run_errors(base_env, tmp_path):
    (tmp_path / "projects" / "demo").mkdir(parents=True)
    r = _run(base_env, "pdata", "init", "--project", "demo", "--write")
    assert r.returncode == 2
    assert "proposal" in r.stderr


def test_pdata_init_write_aborts_on_bad_file_path_without_cutover(base_env, tmp_path):
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "docs.csv").write_text("doc_path,note\n/etc/passwd,bad\n")
    base_env["CCST_PDATA_BACKUP_DIR"] = str(tmp_path / "backups")

    _run(base_env, "pdata", "init", "--project", "demo")
    proposal_path = project_dir / ".ccst-pdata-proposal.json"
    data = json.loads(proposal_path.read_text())
    data["entries"][0]["file_path_column"] = "doc_path"
    proposal_path.write_text(json.dumps(data))

    r_write = _run(base_env, "pdata", "init", "--project", "demo", "--write")
    assert r_write.returncode == 1
    assert "verification failed" in r_write.stderr.lower()
    assert (project_dir / "docs.csv").exists()
    assert not (project_dir / ".pdata-migrated").exists()


def test_pdata_init_rehearse_does_not_touch_real_project(base_env, tmp_path):
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "ideas.csv").write_text("idea\nfirst\n")
    rehearsal_dir = tmp_path / "rehearsal-demo"
    shutil.copytree(project_dir, rehearsal_dir)
    real_backup_dir = tmp_path / "backups"
    base_env["CCST_PDATA_BACKUP_DIR"] = str(real_backup_dir)

    _run(base_env, "pdata", "init", "--project", "demo", "--rehearse", str(rehearsal_dir))
    r_write = _run(base_env, "pdata", "init", "--project", "demo",
                    "--rehearse", str(rehearsal_dir), "--write")
    assert r_write.returncode == 0, r_write.stderr

    assert (project_dir / "ideas.csv").exists()
    r_list_real = _run(base_env, "pdata", "list", "--project", "demo", "--group", "ideas")
    assert r_list_real.returncode == 0
    assert "first" not in r_list_real.stdout
    # the rehearsal's backup tarball must never land in the real backup dir
    assert not real_backup_dir.exists() or not any(real_backup_dir.iterdir())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ccst_pdata_init_cli.py -v`
Expected: PASS already for most, but run it to confirm — if any fail, Task 9's `write()` has a
bug reachable only through the real CLI process boundary (e.g. an env var not propagating); fix
`init_service.py`/`ccst.py` before proceeding, since Task 8/9 alone don't guarantee correctness
under a real subprocess.

- [ ] **Step 3: (n/a — no production code changes; this task is CLI-level test coverage only)**

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ccst_pdata_init_cli.py -v`
Expected: PASS (8 tests total in the file)

- [ ] **Step 5: Commit**

```bash
git add tests/test_ccst_pdata_init_cli.py
git commit -m "test(pdata-init): cover ccst pdata init --write/--rehearse end-to-end via CLI"
```

---

## Task 11: `ccst doctor` — `check_pending_pdata_migration`

**Files:**
- Modify: `src/cc_session_tools/lib/doctor.py`
- Modify: `tests/test_ccst_doctor.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_ccst_doctor.py

from cc_session_tools.lib.doctor import check_pending_pdata_migration


def test_check_pending_pdata_migration_ok_when_projects_root_missing(tmp_path):
    results = check_pending_pdata_migration(tmp_path / "does-not-exist")
    assert len(results) == 1
    assert results[0].status == Status.OK


def test_check_pending_pdata_migration_ok_when_no_projects_have_archive(tmp_path):
    (tmp_path / "demo").mkdir()
    results = check_pending_pdata_migration(tmp_path)
    assert len(results) == 1
    assert results[0].status == Status.OK


def test_check_pending_pdata_migration_warns_for_undeleted_archive(tmp_path):
    project_dir = tmp_path / "demo"
    archive_dir = project_dir / ".pdata-migrated"
    archive_dir.mkdir(parents=True)
    (archive_dir / "ideas.csv").write_text("idea\nfirst\n")
    (archive_dir / "MANIFEST.md").write_text("- migrated ideas.csv\n")

    results = check_pending_pdata_migration(tmp_path)

    assert len(results) == 1
    assert results[0].status == Status.WARN
    assert "demo" in results[0].name
    assert "1 archived" in results[0].reason


def test_check_pending_pdata_migration_ignores_manifest_file_itself(tmp_path):
    project_dir = tmp_path / "demo"
    archive_dir = project_dir / ".pdata-migrated"
    archive_dir.mkdir(parents=True)
    (archive_dir / "MANIFEST.md").write_text("nothing archived yet\n")

    results = check_pending_pdata_migration(tmp_path)
    assert results[0].status == Status.OK


def test_check_pending_pdata_migration_reports_one_result_per_pending_project(tmp_path):
    for name in ("demo", "other"):
        archive_dir = tmp_path / name / ".pdata-migrated"
        archive_dir.mkdir(parents=True)
        (archive_dir / "x.csv").write_text("x\n")

    results = check_pending_pdata_migration(tmp_path)
    assert {r.status for r in results} == {Status.WARN}
    assert {r.name for r in results} == {"pdata-init:pending:demo", "pdata-init:pending:other"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ccst_doctor.py -k pending_pdata -v`
Expected: FAIL with `ImportError: cannot import name 'check_pending_pdata_migration'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/cc_session_tools/lib/doctor.py, near check_pending_data_store_migration:

from cc_session_tools.lib.pdata.init_paths import (
    MIGRATED_ARCHIVE_DIRNAME,
    MIGRATED_MANIFEST_FILENAME,
)


def check_pending_pdata_migration(projects_root: Path) -> list[CheckResult]:
    """Warn about ccst pdata init runs whose archived-but-undeleted migrated-source
    originals (spec §7.1 step 7) are still sitting under a project's
    .pdata-migrated/ directory. Mirrors check_pending_data_store_migration()'s
    pattern but is WARN-only: unlike the CCST-infra migration that check covers,
    there is no version upgrade forcing every project through ccst pdata init, so
    a project with no .pdata-migrated/ directory at all is a completely normal,
    unremarkable state — nothing to FAIL on. Reuses init_paths.py's own
    MIGRATED_ARCHIVE_DIRNAME/MIGRATED_MANIFEST_FILENAME constants (the same ones
    cutover.py writes to) rather than re-typing the literal strings, so the two
    can never drift apart."""
    if not projects_root.is_dir():
        return [CheckResult(
            name="pdata-init:pending", status=Status.OK,
            reason=f"{projects_root} does not exist — nothing to check",
        )]

    pending: list[tuple[str, Path, int]] = []
    for project_dir in sorted(p for p in projects_root.iterdir() if p.is_dir()):
        archive_dir = project_dir / MIGRATED_ARCHIVE_DIRNAME
        if not archive_dir.is_dir():
            continue
        remaining = [
            p for p in archive_dir.rglob("*")
            if p.is_file() and p.name != MIGRATED_MANIFEST_FILENAME
        ]
        if remaining:
            pending.append((project_dir.name, archive_dir, len(remaining)))

    if not pending:
        return [CheckResult(
            name="pdata-init:pending", status=Status.OK,
            reason="no archived-but-undeleted migration sources found",
        )]

    return [
        CheckResult(
            name=f"pdata-init:pending:{project_name}",
            status=Status.WARN,
            reason=(
                f"{count} archived migrated-source file(s) remain at {archive_dir} — "
                "safe to remove once verified (ccst pdata init never deletes "
                "automatically, spec §7.1 step 7; manual delete only)"
            ),
        )
        for project_name, archive_dir, count in pending
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ccst_doctor.py -k pending_pdata -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/doctor.py tests/test_ccst_doctor.py
git commit -m "feat(pdata-init): add ccst doctor check for undeleted migrated-source originals"
```

---

## Task 12: Wire the new doctor check into `run_all_checks` and `ccst doctor`

**Files:**
- Modify: `src/cc_session_tools/lib/doctor.py`
- Modify: `src/cc_session_tools/cli/ccst.py`
- Modify: `tests/test_ccst_doctor.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_ccst_doctor.py

def test_run_all_checks_includes_pdata_migration_check_when_projects_root_given(tmp_path):
    results = run_all_checks(
        installed_version="1.2.0",
        settings_path=tmp_path / "settings.json",
        bundle_path=tmp_path / "bundle.json",
        skills_source_dir=None,
        skills_target_dir=tmp_path / "skills",
        env={},
        skip_pypi=True,
        projects_root=tmp_path / "cc",
    )
    assert any(r.name.startswith("pdata-init:pending") for r in results)


def test_run_all_checks_skips_pdata_migration_check_when_projects_root_omitted(tmp_path):
    results = run_all_checks(
        installed_version="1.2.0",
        settings_path=tmp_path / "settings.json",
        bundle_path=tmp_path / "bundle.json",
        skills_source_dir=None,
        skills_target_dir=tmp_path / "skills",
        env={},
        skip_pypi=True,
    )
    assert not any(r.name.startswith("pdata-init:pending") for r in results)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ccst_doctor.py -k run_all_checks_includes_pdata -v`
Expected: FAIL — `run_all_checks()` raises `TypeError: unexpected keyword argument 'projects_root'`

- [ ] **Step 3: Write minimal implementation**

```python
# modify run_all_checks() signature and body in src/cc_session_tools/lib/doctor.py:

def run_all_checks(
    *,
    installed_version: str,
    settings_path: Path,
    bundle_path: Path,
    skills_source_dir: Path | None,
    skills_target_dir: Path,
    env: dict[str, str | None],
    skip_pypi: bool = False,
    store_paths: dict[str, Path] | None = None,
    legacy_migration_paths: LegacyMigrationPaths | None = None,
    projects_root: Path | None = None,
) -> list[CheckResult]:
    # ... existing body unchanged up to the legacy-migration block ...

    # Pending legacy-data migration
    if legacy_migration_paths is not None:
        results.extend(check_pending_data_store_migration(legacy_migration_paths))

    # Pending ccst pdata init cutover (spec §7.1 step 7)
    if projects_root is not None:
        results.extend(check_pending_pdata_migration(projects_root))

    # PyPI version check
    if not skip_pypi:
        results.append(check_pypi_version(installed_version))

    return results
```

```python
# add to src/cc_session_tools/cli/ccst.py, inside _cmd_doctor(), alongside the
# existing legacy_migration_paths construction:

    from cc_session_tools.lib.pdata.init_paths import default_projects_root

    results = run_all_checks(
        installed_version=__version__,
        settings_path=settings_path,
        bundle_path=bundle_path,
        skills_source_dir=skills_source_dir,
        skills_target_dir=skills_target_dir,
        env=env_vars,
        skip_pypi=args.no_pypi,
        store_paths=store_paths,
        legacy_migration_paths=legacy_migration_paths,
        projects_root=default_projects_root(),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ccst_doctor.py -v`
Expected: PASS, full file green

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/doctor.py src/cc_session_tools/cli/ccst.py tests/test_ccst_doctor.py
git commit -m "feat(pdata-init): wire pending-pdata-migration check into ccst doctor"
```

---

## Task 13: `pm-project-init` skill

**Files:**
- Create: `skills/pm-project-init/SKILL.md`

Not a TDD task — this is a documentation/skill-authoring deliverable. Steps are write, self-check
against `superpowers:writing-skills`' good-practice checklist, commit.

- [ ] **Step 1: Write the skill file**

```markdown
---
name: pm-project-init
description: Use when initializing a brand-new project's ccst pdata store, or migrating an existing ~/cc/<project>'s informally-accumulated flat files into it - drives `ccst pdata init` and applies judgement reviewing its classification report (spec §3's folder-owned/db-owned framework), since the tool's own defaults are deliberately conservative and need a human decision on every ambiguous file. Triggers - "migrate <project> to pdata", "run pdata init for <project>", "set up the data store for <project>", "/pm-project-init". Do NOT use for ccst pdata verify, per-record-group schema design (pm-pdata-schema-design), or version-conflict reconciliation (pm-pdata-conflict-resolution) - those are separate skills.
---

# pm-project-init

Drives `ccst pdata init` (spec §7) end to end and applies the judgement the tool itself
deliberately does not: `ccst pdata init`'s automatic classifier only proposes CSV/JSON files as
db-owned (their structure is genuinely machine-inferable); every markdown/text file defaults to
folder-owned, because guessing whether a specific project's `STATUS.md` is a log, a versioned
plan doc, or a stacked-snapshot journal is exactly the kind of per-project call this skill exists
to make - not something baked into shared tooling.

This skill's job is to **read and interpret** `ccst pdata init`'s output and drive its flags. It
never re-implements the classifier, the importer, or the verification logic itself - if the
report looks wrong, that's a bug in `ccst pdata init`, not something to work around by hand.

## 1. Rehearse first for a project's first-ever migration

For a project's very first `ccst pdata init` run - especially a high-stakes one (e.g. `pbt`,
`maxella`) - copy the project directory elsewhere first and rehearse against the copy:

```sh
cp -r ~/cc/<project> /tmp/rehearsal-<project>
ccst pdata init --project <project> --rehearse /tmp/rehearsal-<project>
```

Rehearsal is optional once the mechanism is trusted for later, lower-stakes projects, or for a
project that's already been migrated once and is just picking up a few newly-added files.

## 2. Run the dry-run classification pass

```sh
ccst pdata init --project <project> [--rehearse <path>]
```

This prints a report and writes (or, on a second run, simply re-displays) a proposal file at
`<project-root>/.ccst-pdata-proposal.json`. Read both.

## 3. Review every `[folder-owned]` entry that is a markdown/text file

This is the primary judgement point. For each such entry, apply spec §3's classification
framework:

- **Folder-owned, correctly defaulted:** a versioned plan document, a closed/frozen narrative
  report, a README, a principles doc. Leave it alone.
- **Should be db-owned:** an incremental note log, a current-state file edited in place, a
  stacked-dated-snapshot journal, a reference/lookup table. Flip it.

If this project already has a compiled inventory analysis (e.g. a
`per-project-data-store-inventory.md`-style document from an earlier planning session), use its
proposed `record_group`/extension-column/strategy recommendations as the starting point for the
override rather than re-deriving them from scratch.

`ccst pdata init`'s classifier auto-disambiguates two auto-classified files that would otherwise
collide on the same `record_group` (e.g. two files both named `notes.csv` in different
subdirectories) — but this only runs once, at classification time. A hand-edited override (Step 4)
that renames a `record_group` to match an existing one is **not** re-checked. Before approving the
proposal, scan it yourself for two entries sharing a `record_group` that you did not deliberately
intend to merge.

## 4. Hand-edit the proposal to encode overrides

Edit `.ccst-pdata-proposal.json` directly. For an entry that should become db-owned:

```json
{
  "path": "planning/decisions.md",
  "classification": "db-owned",
  "reviewed": true,
  "record_group": "decisions",
  "strategy": "delimited-sections",
  "delimiter": "(?m)^## D-\\d+",
  "fields": []
}
```

Strategy choices: `whole-file` (one row, whole file), `delimited-sections` (one row per
heading-delimited section - covers both append-only logs and stacked-snapshot journals),
`csv-rows`, `json-array-rows`, `json-singleton`. Set `"reviewed": true` on entries you've made a
deliberate decision about - it has no effect on the tool's own behaviour (the proposal file is
never regenerated once it exists, reviewed or not) but keeps a human-readable record of which
entries were actually looked at versus left at their untouched default.

Never delete the proposal file to "start over" without good reason - doing so discards every
override made so far. If new files appear in the project after the first dry run, add entries for
them by hand rather than deleting and reclassifying everything.

## 5. Get explicit approval before `--write`

Summarise the finished proposal in plain language for Chris - which files become which
`record_group`s, roughly how many rows each will produce, which files stay folder-owned - and get
explicit approval. **Never invoke `--write` without it.**

## 6. Run the write phase

```sh
ccst pdata init --project <project> [--rehearse <path>] --write
```

- **Exit 0:** everything imported, verified, backed up, and cut over. Report the backup tar path
  and the list of cut-over files to Chris.
- **Exit 1:** verification failed. Nothing was cut over and no live rows were left behind (they
  were soft-deleted). Read the printed reasons - usually a bad `file_path_column`, a wrong
  `content_column`, or a header that needed a different field name - fix the proposal entry, and
  re-run `--write`.
- **Exit 2:** a CLI/validation error (e.g. no proposal file yet - re-run the dry-run first).

## 7. After a successful cutover

Archived originals live at `<project-root>/.pdata-migrated/` and are **never auto-deleted** (spec
§7.1 step 7). `ccst doctor` will keep WARNing about them until Chris explicitly deletes them
himself - that WARN is expected and not urgent. Do not delete the archive without his explicit
instruction.

## 8. Rollback, if something's found wrong post-cutover

There is no `--rollback` flag. Recover by hand:

1. Restore the backup tar (`result.backup_path`, printed on a successful `--write`) over the
   project folder: `tar -xzf <backup>.tar.gz -C <parent-of-project-root>`.
2. Undo the DB side with Plan A's already-shipped `ccst pdata delete --project <name> --id <id>
   --version <n>` for the specific rows that shouldn't have landed, or - for a wholesale bad
   run - delete the project's `.db` file directly and re-run `ccst pdata init` from scratch.
3. Fix whatever was wrong in the proposal (classification, strategy, field mapping) before
   re-attempting.

## Never do without explicit Chris approval

- Invoke `--write` on an unreviewed proposal.
- Delete anything under `.pdata-migrated/`.
- Delete a project's `.db` file as part of a rollback.
```

- [ ] **Step 2: Self-check against skill-writing conventions**

Compare against an existing skill in this repo (e.g. `skills/manage-recurring-cc-jobs-using-ccsched/SKILL.md`)
for frontmatter shape and section style; confirm the `description` field states triggers and
exclusions in one paragraph, matching that precedent.

- [ ] **Step 3: Commit**

```bash
git add skills/pm-project-init/SKILL.md
git commit -m "docs(pdata-init): add pm-project-init skill"
```

---

## Task 14: Full suite + mypy --strict + version bump + CHANGELOG

**Files:**
- Modify: `pyproject.toml`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS, 0 failures — confirms no cross-task regressions against Plan A's own suite (this
plan makes zero changes to Plan A's shipped files, per Decision 3/8, so a regression here would
indicate an unintended interaction, e.g. an env var leaking between tests).

- [ ] **Step 2: Run `mypy --strict` on the new/modified code**

Run: `uv run mypy --strict src/cc_session_tools/lib/pdata/ src/cc_session_tools/lib/doctor.py src/cc_session_tools/cli/ccst.py`
Expected: no errors — fix any type errors surfaced here using precise types rather than casts,
per this repo's coding standards. Pay particular attention to `manifest.py`'s `load()` (JSON
parsing boundary) and `init_service.py`'s use of `entry.db_group()`/`entry.db_strategy()`.

- [ ] **Step 3: Bump the version (minor, per the Versioning section above)**

Read the current `version` out of `pyproject.toml` and bump its minor component by one — do not
hardcode `1.2.0`: if only Plan A has landed the current value is `1.1.0` and this task sets
`1.2.0`; if Plan C (`pm-update-central-files`) already landed first, the current value is
`1.2.0` and this task sets `1.3.0`.

```toml
# pyproject.toml
[project]
name = "cc-session-tools"
version = "1.2.0"  # or "1.3.0" if Plan C already bumped it — see above, don't hardcode
```

- [ ] **Step 4: Add the CHANGELOG entry**

```markdown
# CHANGELOG.md — insert under "## [Unreleased]", following whatever entries already exist
# there (e.g. Plan A's ccst pdata core entry) at the time this task actually runs.

### Added

- **`ccst pdata init` — unified per-project data-store init/migration.** New
  `--project <name> [--rehearse <path>] [--write]` verb (spec §7): a dry-run pass classifies
  every file in a project as folder-owned or db-owned (CSV/JSON get an automatic proposal; every
  other file defaults to folder-owned, pending human review), writes a hand-editable
  classification proposal, and — once approved and re-run with `--write` — imports the approved
  entries into that project's `ccst pdata` store, verifies the result, takes a full pre-cutover
  backup, and archives (never deletes) the original source files. A verification failure aborts
  before any file is touched, with every row inserted during that run soft-deleted. `--rehearse
  <path>` runs the whole procedure against a copy with zero effect on the real project or its
  `.db`. `ccst doctor` gains a check that WARNs about archived-but-undeleted migrated-source
  files (manual-delete-only, per spec). Ships with the `pm-project-init` skill, which drives the
  tool and applies the judgement its deliberately conservative classifier defers to a human. This
  is Plan B of the per-project data-store feature, built directly on Plan A's `ccst pdata`
  schema/CLI — `ccst pdata verify`, the `pm-pdata-schema-design`/`pm-pdata-conflict-resolution`
  skills, and any actual per-project migration content are deferred to later work (see
  `docs/superpowers/plans/2026-07-30-ccst-pdata-init-migration.md`'s Scope section).
```

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "chore(pdata-init): bump to 1.2.0 for ccst pdata init/migration"
```

---

## Post-plan note for whoever picks up the real per-project migrations

This plan deliberately classifies and imports only synthetic fixture projects in its own tests —
it never decides what any of the 10 real `~/cc/<project>` directories' files should become. That
operational work is driven by the `pm-project-init` skill (Task 13), informed by
`2026-07-29-per-project-data-store-inventory.md`'s per-project proposals, one project at a time,
starting with a rehearsed run for the highest-stakes projects (`maxella`, `pbt`) per spec §7.1
step 0.

    assert "project" in r.stderr
</result>