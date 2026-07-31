# ccst pdata — core schema + CLI (Plan A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `ccst pdata` — the shared, project-aware SQLite CLI for per-project "content"
data (correspondence, facts, decisions, timelines, current-state, etc.) — covering the base
schema, the extension-table mechanism, the schema registry, and every `records`/`schema`
subcommand: `add`, `get`, `list`, `query`, `update`, `delete`, `restore`, `schema list`,
`schema show`, `schema add-field`.

**Architecture:** One SQLite `.db` file per project at
`~/.local/share/claude/project-db/<project>.db` (overridable via `CCST_PROJECT_DB_DIR`, the
usual one-env-var-per-subsystem test seam), opened through the existing
`cc_session_tools.lib.db.connect()` helper (WAL + busy-timeout + `CREATE ... IF NOT EXISTS`
DDL). Business logic lives in a new `src/cc_session_tools/lib/pdata/` package split by
responsibility (`store` = paths, `naming` = validators/transforms, `repository` = all SQL,
`service` = orchestration/validation, `formatting` = table/json/csv/diff rendering) — mirroring
`lib/messaging/`'s `store.py`/`repository.py`/`service.py` split. The CLI surface is a new
`pdata` noun added directly to the existing `src/cc_session_tools/cli/ccst.py` (argparse wiring
+ thin handlers that lazily import `lib.pdata.service`), matching this file's own established
convention for every other noun (`sessions`, `migrate`, `telemetry`, `gc`, ...) rather than
inventing a second standalone CLI module — `ccst.py` is already the single home for "argparse
wiring across every ccst noun"; `pdata` does not get special treatment.

**Tech Stack:** Python 3.11+, stdlib `sqlite3` via `lib/db.py`, `argparse`, `pytest` (subprocess
CLI tests matching `tests/messaging/test_ccmsg_cli.py` / `tests/scheduler/test_ccsched_cli.py`).

---

## Scope

**In scope** (spec `2026-07-26-per-project-data-store-spec.md` §4, §5 minus "Project
lifecycle", §6):

- §4.1 storage location, §4.2 base `records` table (naming validator, relative `file_path`,
  epoch timestamps, one-`file_path`-per-row), §4.3 extension tables (hyphen→underscore
  transform, the three content-modelling shapes — documentary naming only, no new mechanism),
  §4.4 schema registry (`record_group_fields`), §4.5 soft delete (`deleted_at`).
- §5 **Records**: `add`, `get`, `list`, `query`, `update`, `delete`, `restore`.
- §5 **Schema discovery and evolution**: `schema list`, `schema show`, `schema add-field`.
- §6 conflict-handling contract (`update`'s optimistic-concurrency version check + current-vs-
  attempted diff output), implemented as part of `update`/`delete`, not a separate subsystem.

**Explicitly out of scope** (deferred to later plans, per the spec and the dispatching prompt —
not designed or implemented here):

- `ccst pdata init` / migration (spec §7), `pm-project-init` skill.
- `pm-update-central-files` rename (spec §8).
- `pm-pdata-schema-design` / `pm-pdata-conflict-resolution` skills (spec §8.1).
- `ccst pdata verify` + its `ccsched` job (spec §8.2).
- `ccst pdata export` (spec §5 "Project lifecycle").
- Any actual per-project migration/cutover work (the inventory doc is input to a later plan,
  not this one).
- The backlog in spec §9 (backup mechanism for per-project `.db` files, common-store
  `created_at`/`updated_at` columns, a web UI, etc.).
- `ccst doctor` checks for pdata (no on-disk format this plan produces is ever unreadable by
  older code — see Versioning below — so the "major bump needs a FAIL doctor check" rule in
  this repo's `CLAUDE.md` does not apply here).

## Versioning (read before Task 17)

This repo's `CLAUDE.md` version policy triggers a **major** bump only for a change that
"relocates, reformats, or otherwise makes existing on-disk data unreadable by old code paths
until a migration step runs." This plan creates a **brand-new** per-project `.db` file family
that does not exist today and touches zero existing on-disk data — nothing a prior version of
`ccst` wrote becomes unreadable. That makes this plan, in isolation, a **minor** bump (new CLI
subcommands, additive), not the major `v2.0.0` the parent spec's header targets for the feature
as a whole. The `v2.0.0` bump belongs to whichever later plan actually performs the breaking
part: relocating/reformatting each project's existing flat files during `ccst pdata init`
migration (spec §7), and the `pm-`-prefixed skill renames (spec §8). Task 17 bumps
`pyproject.toml` from `1.0.0` to `1.1.0` and adds a `CHANGELOG.md [Unreleased]` entry — do not
bump to `2.0.0` in this plan.

## Necessary implementation decisions beyond the spec's literal text

The spec (read in full before writing code — this section is deltas, not a substitute) leaves a
few things implicit that a working implementation must pin down. These are binding for this
plan:

1. **Project name is a filesystem path component.** `--project <name>` is interpolated into
   `project-db/<name>.db`. `lib/pdata/store.py` rejects any name containing `/`, or equal to
   `.`/`..`, or empty — a boundary validation the spec doesn't spell out but that path-traversal
   safety requires.
2. **Extension column names, types, and DEFAULT literals cannot be parameterized in DDL.**
   SQLite has no bound-parameter support for identifiers or column types in `CREATE
   TABLE`/`ALTER TABLE ADD COLUMN`, **and** (confirmed by direct execution against SQLite
   3.45 — `ALTER TABLE t ADD COLUMN x INTEGER DEFAULT ?` raises `sqlite3.OperationalError: near
   "?": syntax error`) no bound-parameter support for the `DEFAULT` clause's literal either.
   `lib/pdata/naming.py` therefore validates field names against `^[a-z][a-z0-9_]*$` (Task 2/3),
   `lib/pdata/repository.py` validates the type token against a fixed whitelist (`TEXT`,
   `INTEGER`, `REAL`, `BLOB`, case-insensitive), and any `--default` value is rendered into a
   literal by `_render_default_literal` (Task 7) before being interpolated: single-quote-escaped
   for `TEXT`, re-serialized from a parsed `int`/`float` (never the raw string) for
   `INTEGER`/`REAL`, and rejected outright for `BLOB` (no default support — omit `--default`).
   Every interpolated identifier (table and column names) is additionally double-quoted as
   defense in depth, matching the existing `"schema"` quoting precedent in
   `lib/messaging/repository.py`. Extension field names are also rejected if they collide with a
   base `records` column name or `record_id` (the `_RESERVED_FIELD_NAMES` check in
   `validate_field_name`, Task 2/3) — a colliding field would silently overwrite the base
   column's value once `get`/`list`/`query` flatten base + extension fields into one dict.
3. **One-to-one base/extension row invariant, backfilled on first creation.** The spec says
   `add` "Inserts a base row, plus an extension-table row in the *same transaction* if the group
   has declared fields" without stating whether an extension row is created when the group has
   an extension table but this particular `add` call passes no `--field`. This plan always
   creates the extension row (with NULLs for any unset column) whenever `ext_<group>` exists for
   the group, regardless of whether `--field` was passed on that specific call. This keeps the
   invariant "every `records` row in a group with an extension table has exactly one
   `ext_<group>` row" — which is what lets `update --field` always resolve to a plain `UPDATE ...
   WHERE record_id=?` instead of needing `INSERT ... ON CONFLICT` upsert logic, and what lets
   `query`'s auto-`LEFT JOIN` behave predictably (a row is either fully joined or the group
   genuinely has no extension table — never "joined but the specific row happens to have no
   extension row"). **The invariant must hold for rows that existed before the group's first
   extension field was ever added, too**: `ensure_extension_table` (Task 7) backfills an
   `ext_<group>` row for every pre-existing `records` row in that group at the moment the
   extension table is first created — otherwise `update --field` on one of those pre-existing
   rows would resolve its `UPDATE ext_<group> ... WHERE record_id=?` against a row that was
   never inserted, matching zero rows and silently discarding the field write (a direct G1
   violation). `update_extension_row` (Task 15) additionally asserts its own `UPDATE` affected
   exactly one row, turning any future regression of this invariant into a loud failure instead
   of a silent no-op, per this repo's "trust the contract, but throw loudly if it's ever violated"
   coding standard.
4. **`--where` op whitelist.** Spec §5 shows `--where "<field> <op> <value>"` without listing
   valid `<op>` tokens. This plan supports `= != < > <= >= LIKE` (case-insensitive keyword,
   values always bound as parameters, never string-interpolated).
5. **`--file` must be relative and non-escaping.** Spec §4.2 says `file_path` is resolved
   relative to the project's data directory and specifically warns against baking in an
   absolute, machine-specific path. The CLI boundary (`add`/`update`) rejects an absolute
   `--file` value with a validation error rather than silently accepting and storing it, and
   also rejects any `..` path-traversal segment — mirroring Decision 1's project-name
   path-safety check — since Plan B later resolves `file_path` against the project root
   (`project_root / record.file_path`), which a relative-but-escaping value like
   `../../etc/passwd` would otherwise pass straight through.
6. **Exit codes.** `2` for a CLI/validation error (bad `record_group`, unregistered field, bad
   `--where` syntax, absolute `--file`), `1` for "not found", `3` for a version conflict on
   `update`/`delete` — `3` mirrors the existing `AlreadyClaimedError` → exit `3` convention in
   `ccmsg claim`/`ccmsg archive` (`src/cc_session_tools/cli/ccmsg.py`), reused here so any future
   skill/script can distinguish "conflict, needs human reconciliation" from an ordinary error by
   exit code alone.
7. **Every `fields`-accepting parameter takes `collections.abc.Mapping[str, ...]`, not
   `dict[str, ...]`.** `dict` is invariant in its value type, so a `dict[str, str]` argument
   (e.g. what the CLI's `--field k=v` parsing always produces) cannot be passed to a parameter
   declared `dict[str, object]` without a `mypy --strict` error, even though it's obviously safe
   at runtime — `str` values are valid `object` values. `Mapping` is covariant in its value type,
   so a `Mapping[str, object]`-typed parameter accepts `dict[str, str]`, `dict[str, int]`, or any
   other `dict[str, X]` argument directly.

   This plan's two entry points, `add_record` and `update_record`, are both fed exclusively by
   the CLI's `--field name=value` parser (`_parse_field_assignment`), which only ever produces
   `str` values — so both are typed `Mapping[str, str]`, the precise type for their one real
   caller, not the broader `Mapping[str, object]`. The covariance payoff shows up one layer
   down, in the functions that accept that same fields mapping as a *narrower* argument into a
   *broader*-typed parameter: `repository.insert_extension_row` and
   `repository.update_extension_row` are typed `Mapping[str, object]` so they can take either
   entry point's `Mapping[str, str]` directly, and `VersionConflictError.__init__` /
   `formatting.render_conflict_diff` are typed `Mapping[str, object]` because the dicts they
   actually receive (the merged current/attempted rows built in `update_record`/`delete_record`)
   mix `str` field values with a non-`str` `id: int` and `file_path: str | None` — a genuinely
   heterogeneous mapping, unlike `add_record`/`update_record`'s own `fields` parameter.
   `Record.fields` itself stays a concrete `dict[str, object]` — it's a stored/owned attribute
   the code builds and mutates (`record.fields = {...}`), not a read-only input parameter, so
   the covariance concern doesn't apply to it.

## File structure

```
src/cc_session_tools/lib/pdata/
  __init__.py          (empty, package marker)
  store.py             project_db_dir() / db_path(project) — path resolution only
  naming.py            record_group validator, ext-table-name transform, field-name validator
  repository.py        all SQL: schema DDL, connect(), records + record_group_fields CRUD,
                        extension-table DDL ops, PRAGMA table_info introspection
  service.py           Record/ConflictError dataclasses, validation + orchestration on top of
                        repository (field resolution, join-and-flatten, diff building)
  formatting.py         table/json/csv renderers + human-readable conflict diff renderer

src/cc_session_tools/cli/ccst.py   (modified — new "pdata" noun: argparse + _cmd_pdata_* handlers)

tests/pdata/
  __init__.py
  test_store.py
  test_naming.py
  test_repository.py
  test_service.py
  test_formatting.py

tests/test_ccst_pdata_cli.py       (subprocess CLI tests, one file per
                                     tests/test_ccst_sessions_cli.py precedent)

pyproject.toml   (modified — version bump, Task 17)
CHANGELOG.md     (modified — [Unreleased] entry, Task 17)
```

`ccst.py` grows substantially (~10 new subcommands). This matches the file's own existing
convention (every noun's argparse + thin handler lives here already); splitting it is out of
scope for this plan per the "don't unilaterally restructure" coding-standards rule.

---

## Task 1: `lib/pdata/store.py` — path resolution

**Files:**
- Create: `src/cc_session_tools/lib/pdata/__init__.py`
- Create: `src/cc_session_tools/lib/pdata/store.py`
- Test: `tests/pdata/__init__.py`
- Test: `tests/pdata/test_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/pdata/__init__.py
```
(empty file)

```python
# tests/pdata/test_store.py
from __future__ import annotations

import pytest

from cc_session_tools.lib.pdata import store


def test_db_path_default_location(monkeypatch, tmp_path):
    monkeypatch.delenv(store.PROJECT_DB_DIR_ENV, raising=False)
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path))
    assert store.db_path("pbt") == tmp_path / "project-db" / "pbt.db"


def test_db_path_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv(store.PROJECT_DB_DIR_ENV, str(tmp_path / "custom"))
    assert store.db_path("pbt") == tmp_path / "custom" / "pbt.db"


@pytest.mark.parametrize("bad_name", ["", ".", "..", "a/b", "../escape", "/abs"])
def test_db_path_rejects_unsafe_project_names(bad_name):
    with pytest.raises(ValueError, match="project"):
        store.db_path(bad_name)


def test_db_path_accepts_normal_project_names():
    # Must not raise for the real project names this system deals with.
    for name in ("pbt", "maxella", "deauppet", "oneshot", "claude", "home"):
        store.db_path(name)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/chris/repos/claude-code-session-tools/.worktrees/pdata-core && uv run pytest tests/pdata/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cc_session_tools.lib.pdata'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/pdata/__init__.py
```
(empty file — package marker, matches `lib/messaging/__init__.py` / `lib/scheduler/__init__.py`)

```python
# src/cc_session_tools/lib/pdata/store.py
"""Per-project data-store path resolution: ~/.local/share/claude/project-db/<project>.db.

One SQLite .db per project (spec §4.1) — distinct from this repo's own one-file-per-subsystem
CCST-infra stores (ccmsg.db, ccsched.db, ...), which live flat in data_home() itself.
"""
from __future__ import annotations

import os
from pathlib import Path

from cc_session_tools.lib import paths

PROJECT_DB_DIR_ENV = "CCST_PROJECT_DB_DIR"


def project_db_dir() -> Path:
    """Directory holding every project's <project>.db. CCST_PROJECT_DB_DIR overrides the
    default paths.data_home() / "project-db" (tests redirect via the env var)."""
    override = os.environ.get(PROJECT_DB_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return paths.data_home() / "project-db"


def validate_project_name(project: str) -> None:
    """Reject a project name that isn't safe to use as a single filesystem path component.

    project is interpolated directly into a file path (project-db/<project>.db) — this is a
    path-traversal boundary check, not a spec-mandated naming convention.
    """
    if not project or project in (".", "..") or "/" in project or "\\" in project:
        raise ValueError(f"invalid project name: {project!r}")


def db_path(project: str) -> Path:
    validate_project_name(project)
    return project_db_dir() / f"{project}.db"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_store.py -v`
Expected: PASS (9 collected items: 4 test functions, one of which —
`test_db_path_rejects_unsafe_project_names` — is parametrized over 6 cases)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/__init__.py src/cc_session_tools/lib/pdata/store.py tests/pdata/__init__.py tests/pdata/test_store.py
git commit -m "feat(pdata): add per-project db path resolution"
```

---

## Task 2: `lib/pdata/naming.py` — `record_group` naming validator

**Files:**
- Create: `src/cc_session_tools/lib/pdata/naming.py`
- Test: `tests/pdata/test_naming.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/pdata/test_naming.py
from __future__ import annotations

import pytest

from cc_session_tools.lib.pdata import naming


@pytest.mark.parametrize("name", ["ccst-ideas", "filings", "session-output", "a", "a1-b2-c3"])
def test_validate_record_group_accepts_valid_names(name):
    naming.validate_record_group(name)  # must not raise


@pytest.mark.parametrize(
    "name",
    ["", "CCST-Ideas", "ccst_ideas", "ccst ideas", "-leading", "trailing-", "double--hyphen",
     "has.dot", "1-2-3-"],
)
def test_validate_record_group_rejects_invalid_names(name):
    with pytest.raises(ValueError, match="record_group"):
        naming.validate_record_group(name)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pdata/test_naming.py -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'validate_record_group'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/pdata/naming.py
"""record_group naming convention and the derived SQL-identifier transforms (spec §4.2/§4.3).

record_group is a caller-facing name (e.g. 'key-events'); ext_<record_group> is never typed by
a caller directly — its underscore form is purely an internal table-naming detail.
"""
from __future__ import annotations

import re

_RECORD_GROUP_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_FIELD_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# The base `records` table's fixed column set (spec §4.2). Public — this is the single source
# of truth for "what is a base column"; repository.py's schema_show_columns (Task 9) and its
# query-builder's base-vs-extension field resolution (Task 14) both import this rather than
# each maintaining their own copy, so the two can't silently drift apart.
BASE_RECORD_COLUMNS: tuple[str, ...] = (
    "id", "record_group", "content", "file_path",
    "created_at", "updated_at", "version", "deleted_at",
)


def validate_record_group(record_group: str) -> None:
    """Raise ValueError unless record_group is lowercase letters/digits/hyphens only, with no
    leading/trailing/doubled hyphen (spec §4.2's ^[a-z0-9]+(-[a-z0-9]+)*$)."""
    if not _RECORD_GROUP_RE.match(record_group):
        raise ValueError(
            f"invalid record_group {record_group!r}: must match "
            f"^[a-z0-9]+(-[a-z0-9]+)*$ (lowercase letters, digits, single hyphens only)"
        )


def extension_table_name(record_group: str) -> str:
    """ext_<record_group> with every hyphen replaced by an underscore (spec §4.3 bug fix) —
    the only place this transform happens; callers never type the underscore form."""
    validate_record_group(record_group)
    return "ext_" + record_group.replace("-", "_")


_RESERVED_FIELD_NAMES = frozenset(BASE_RECORD_COLUMNS) | {"record_id"}


def validate_field_name(field_name: str) -> None:
    """Raise ValueError unless field_name is safe to interpolate as a SQL identifier
    (extension-table column names cannot be bound parameters — see plan Decision 2) and
    doesn't collide with a base records column or the ext table's own record_id PK — a
    colliding extension field would silently overwrite the base column's value once
    get/list/query flatten base + extension fields into one dict."""
    if not _FIELD_NAME_RE.match(field_name):
        raise ValueError(
            f"invalid field name {field_name!r}: must match ^[a-z][a-z0-9_]*$"
        )
    if field_name in _RESERVED_FIELD_NAMES:
        raise ValueError(
            f"invalid field name {field_name!r}: collides with a base records column"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_naming.py -v`
Expected: PASS (2 parametrized tests, 5 + 9 cases)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/naming.py tests/pdata/test_naming.py
git commit -m "feat(pdata): add record_group naming validator"
```

---

## Task 3: `naming.py` — extension-table transform + field-name validator tests

**Files:**
- Modify: `tests/pdata/test_naming.py`

(The functions were written in Task 2 alongside the validator since they live in the same
small file and share the same regex-module shape; this task is pure test coverage for the two
functions not yet exercised.)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/pdata/test_naming.py

def test_extension_table_name_transforms_hyphens_to_underscores():
    assert naming.extension_table_name("key-events") == "ext_key_events"
    assert naming.extension_table_name("filings") == "ext_filings"
    assert naming.extension_table_name("a-b-c") == "ext_a_b_c"


def test_extension_table_name_rejects_invalid_record_group():
    with pytest.raises(ValueError, match="record_group"):
        naming.extension_table_name("Not_Valid")


@pytest.mark.parametrize("name", ["sender", "sent_at", "is_read", "a1", "a_1_b"])
def test_validate_field_name_accepts_valid_names(name):
    naming.validate_field_name(name)  # must not raise


@pytest.mark.parametrize("name", ["", "Sender", "1abc", "sent-at", "sent at", "sent.at"])
def test_validate_field_name_rejects_invalid_names(name):
    with pytest.raises(ValueError, match="field name"):
        naming.validate_field_name(name)


@pytest.mark.parametrize(
    "name",
    ["id", "record_group", "content", "file_path", "created_at", "updated_at",
     "version", "deleted_at", "record_id"],
)
def test_validate_field_name_rejects_reserved_base_column_names(name):
    with pytest.raises(ValueError, match="collides"):
        naming.validate_field_name(name)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pdata/test_naming.py -v`
Expected: PASS already (implementation from Task 2 covers this) — confirm no failures, i.e.
this step is a no-op verification, not a real red step. Since Task 2 already implemented both
functions, skip straight to Step 4.

- [ ] **Step 3: (n/a — implementation already exists from Task 2)**

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_naming.py -v`
Expected: PASS (7 tests total in the file)

- [ ] **Step 5: Commit**

```bash
git add tests/pdata/test_naming.py
git commit -m "test(pdata): cover extension_table_name and validate_field_name"
```

---

## Task 4: `lib/pdata/repository.py` — base schema DDL + `connect()`

**Files:**
- Create: `src/cc_session_tools/lib/pdata/repository.py`
- Test: `tests/pdata/test_repository.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/pdata/test_repository.py
from __future__ import annotations

from cc_session_tools.lib.pdata import repository


def test_connect_creates_records_and_record_group_fields_tables(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        tables = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "records" in tables
        assert "record_group_fields" in tables

        record_cols = {r["name"] for r in conn.execute('PRAGMA table_info("records")')}
        assert record_cols == {
            "id", "record_group", "content", "file_path",
            "created_at", "updated_at", "version", "deleted_at",
        }

        field_cols = {r["name"] for r in conn.execute('PRAGMA table_info("record_group_fields")')}
        assert field_cols == {"record_group", "field_name", "description", "added_at"}
    finally:
        conn.close()


def test_connect_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    repository.connect("testproj").close()
    conn = repository.connect("testproj")  # must not raise on re-run
    conn.close()


def test_connect_rejects_unsafe_project_name(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    import pytest
    with pytest.raises(ValueError, match="project"):
        repository.connect("../escape")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pdata/test_repository.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cc_session_tools.lib.pdata.repository'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/pdata/repository.py
"""SQLite data-access layer for per-project data stores (spec §4).

The single home of all SQL for the base records/record_group_fields tables and every
ext_<record_group> extension table. Callers go through service.py for validation; this module
trusts its inputs are already validated (record_group/field-name charset, project name).
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from cc_session_tools.lib import db
from cc_session_tools.lib.pdata import store

_BASE_DDL = """
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY,
    record_group TEXT NOT NULL,
    content TEXT NOT NULL,
    file_path TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    deleted_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_records_group ON records(record_group);
CREATE INDEX IF NOT EXISTS idx_records_updated ON records(updated_at);

CREATE TABLE IF NOT EXISTS record_group_fields (
    record_group TEXT NOT NULL,
    field_name TEXT NOT NULL,
    description TEXT,
    added_at INTEGER NOT NULL,
    PRIMARY KEY (record_group, field_name)
);
"""


def connect(project: str) -> sqlite3.Connection:
    """Open <project>.db through the shared helper, in explicit-transaction mode.

    isolation_level=None turns off sqlite3's implicit BEGIN so callers issue their own
    BEGIN IMMEDIATE for multi-statement writes (see _immediate), matching
    lib/messaging/repository.py's connect()."""
    conn = db.connect(store.db_path(project), ddl=_BASE_DDL)
    conn.isolation_level = None
    return conn


@contextmanager
def _immediate(conn: sqlite3.Connection) -> Iterator[None]:
    """Run the body inside a BEGIN IMMEDIATE / COMMIT, rolling back on error."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_repository.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/repository.py tests/pdata/test_repository.py
git commit -m "feat(pdata): add base records/record_group_fields schema + connect()"
```

---

## Task 5: `repository.py` — base-row insert + get-by-id

**Files:**
- Modify: `src/cc_session_tools/lib/pdata/repository.py`
- Modify: `tests/pdata/test_repository.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/pdata/test_repository.py

def test_insert_base_record_then_get_by_id(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        record_id = repository.insert_base_record(
            conn, record_group="ccst-ideas", content="an idea", file_path=None,
            created_at=1000, updated_at=1000,
        )
        assert record_id == 1
        row = repository.get_base_record(conn, record_id)
        assert row is not None
        assert row["record_group"] == "ccst-ideas"
        assert row["content"] == "an idea"
        assert row["file_path"] is None
        assert row["created_at"] == 1000
        assert row["updated_at"] == 1000
        assert row["version"] == 1
        assert row["deleted_at"] is None
    finally:
        conn.close()


def test_get_base_record_returns_none_for_missing_id(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        assert repository.get_base_record(conn, 999) is None
    finally:
        conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pdata/test_repository.py -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'insert_base_record'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/cc_session_tools/lib/pdata/repository.py

def insert_base_record(
    conn: sqlite3.Connection,
    *,
    record_group: str,
    content: str,
    file_path: str | None,
    created_at: int,
    updated_at: int,
) -> int:
    """Insert one records row (caller already validated record_group). Returns the new id.
    Caller owns the transaction (wrap in _immediate if this isn't the only statement)."""
    cur = conn.execute(
        "INSERT INTO records (record_group, content, file_path, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (record_group, content, file_path, created_at, updated_at),
    )
    assert cur.lastrowid is not None  # sqlite3 always sets this after a successful INSERT
    return cur.lastrowid


def get_base_record(conn: sqlite3.Connection, record_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_repository.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/repository.py tests/pdata/test_repository.py
git commit -m "feat(pdata): add base-row insert and get-by-id"
```

---

## Task 6: `service.py` — `Record` dataclass + `add_record` (content-only) + CLI `ccst pdata add`

**Files:**
- Create: `src/cc_session_tools/lib/pdata/service.py`
- Modify: `src/cc_session_tools/cli/ccst.py`
- Test: `tests/pdata/test_service.py`
- Test: `tests/test_ccst_pdata_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/pdata/test_service.py
from __future__ import annotations

import pytest

from cc_session_tools.lib.pdata import service


def test_add_record_content_only(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    record = service.add_record(
        project="testproj", record_group="ccst-ideas", content="an idea",
        file_path=None, fields={}, created_at=1000,
    )
    assert record.id == 1
    assert record.record_group == "ccst-ideas"
    assert record.content == "an idea"
    assert record.fields == {}


def test_add_record_rejects_invalid_record_group(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="record_group"):
        service.add_record(
            project="testproj", record_group="Not Valid", content="x",
            file_path=None, fields={}, created_at=1000,
        )


def test_add_record_rejects_absolute_file_path(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="relative"):
        service.add_record(
            project="testproj", record_group="filings", content="x",
            file_path="/etc/passwd", fields={}, created_at=1000,
        )


def test_add_record_rejects_path_traversal_file_path(monkeypatch, tmp_path):
    """Regression test: a relative-but-escaping --file (no leading '/') must be rejected too,
    since Plan B later resolves file_path against the project root."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="\\.\\."):
        service.add_record(
            project="testproj", record_group="filings", content="x",
            file_path="../../etc/passwd", fields={}, created_at=1000,
        )
```

```python
# tests/test_ccst_pdata_cli.py
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


def test_pdata_add_content_only(base_env):
    r = _run(base_env, "pdata", "add", "--project", "testproj",
              "--group", "ccst-ideas", "--content", "an idea")
    assert r.returncode == 0, r.stderr
    assert "1" in r.stdout


def test_pdata_add_rejects_invalid_group(base_env):
    r = _run(base_env, "pdata", "add", "--project", "testproj",
              "--group", "Not Valid", "--content", "an idea")
    assert r.returncode == 2
    assert "record_group" in r.stderr


def test_pdata_add_accepts_created_at_flag(base_env):
    """CLI-level regression test for spec §5's `--created-at <epoch>` flag: confirms argparse
    actually accepts it and forwards it to service.add_record without erroring, not just that
    the flag is reachable via the Python API. (Task 12's `ccst pdata get` later adds an
    end-to-end check that the value is actually persisted.)"""
    r = _run(base_env, "pdata", "add", "--project", "testproj",
              "--group", "ccst-ideas", "--content", "an old idea",
              "--created-at", "1000")
    assert r.returncode == 0, r.stderr
    assert "1" in r.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pdata/test_service.py tests/test_ccst_pdata_cli.py -v`
Expected: FAIL — `ModuleNotFoundError` for `service`, and the CLI test fails because `ccst`
has no `pdata` noun yet (`argparse` error, exit 2 from "invalid choice").

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/pdata/service.py
"""Business logic for ccst pdata: validation, orchestration, and join-and-flatten on top of
repository.py's raw SQL. The CLI layer (ccst.py) stays a thin argparse wrapper around this
module, matching lib/messaging/service.py's split."""
from __future__ import annotations

import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass, field

from cc_session_tools.lib.pdata import naming, repository


@dataclass
class Record:
    id: int
    record_group: str
    content: str
    file_path: str | None
    created_at: int
    updated_at: int
    version: int
    deleted_at: int | None
    fields: dict[str, object] = field(default_factory=dict)


def _validate_relative_file_path(file_path: str | None) -> None:
    """Boundary check mirroring Decision 1's project-name path-traversal guard: file_path is
    later resolved against the project root (project_root / record.file_path, per spec §4.2 —
    see Plan B), so a relative-but-escaping value like '../../etc/passwd' must be rejected here
    too, not just a leading '/'. Splitting on '/' (not os.sep) is deliberate: file_path is a
    stored, portable identifier, not a native OS path, so it always uses '/' regardless of the
    host platform."""
    if file_path is None:
        return
    if file_path.startswith("/"):
        raise ValueError(
            f"--file must be relative to the project root, got absolute path: {file_path!r}"
        )
    if any(segment == ".." for segment in file_path.split("/")):
        raise ValueError(
            f"--file must not contain '..' path-traversal segments: {file_path!r}"
        )


def _row_to_record(row: sqlite3.Row) -> Record:
    return Record(
        id=row["id"],
        record_group=row["record_group"],
        content=row["content"],
        file_path=row["file_path"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=row["version"],
        deleted_at=row["deleted_at"],
        fields={},
    )


def add_record(
    *,
    project: str,
    record_group: str,
    content: str,
    file_path: str | None,
    fields: Mapping[str, str],
    created_at: int | None = None,
) -> Record:
    naming.validate_record_group(record_group)
    _validate_relative_file_path(file_path)
    ts = created_at if created_at is not None else int(time.time())

    conn = repository.connect(project)
    try:
        with repository._immediate(conn):
            record_id = repository.insert_base_record(
                conn, record_group=record_group, content=content, file_path=file_path,
                created_at=ts, updated_at=ts,
            )
        row = repository.get_base_record(conn, record_id)
        assert row is not None  # just inserted in this same connection
    finally:
        conn.close()
    return _row_to_record(row)
```

```python
# add to src/cc_session_tools/cli/ccst.py — new "---------- pdata ----------" section,
# placed after the existing "---------- gc report ----------" section (before "---------- hooks
# run ----------" or anywhere consistent — exact position doesn't matter, keep new-noun
# sections grouped together for readability).

# ---------- pdata ----------


def _cmd_pdata_add(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import service

    try:
        record = service.add_record(
            project=args.project,
            record_group=args.group,
            content=args.content,
            file_path=args.file,
            fields={},
            created_at=args.created_at,
        )
    except ValueError as exc:
        print(f"ccst pdata: {exc}", file=sys.stderr)
        return 2
    print(record.id)
    return 0
```

```python
# add to _build_parser() in src/cc_session_tools/cli/ccst.py, after the "gc" section:

    # ---- pdata ----
    pdata_parser = sub.add_parser("pdata", help="Per-project SQLite data store commands")
    pdata_sub = pdata_parser.add_subparsers(dest="verb", metavar="<verb>")
    pdata_sub.required = True

    pdata_add_parser = pdata_sub.add_parser("add", help="Insert a new record")
    pdata_add_parser.add_argument("--project", required=True, metavar="NAME")
    pdata_add_parser.add_argument("--group", required=True, metavar="RECORD_GROUP")
    pdata_add_parser.add_argument("--content", required=True)
    pdata_add_parser.add_argument("--file", default=None, metavar="PATH",
                                   help="Relative sibling/source file path")
    pdata_add_parser.add_argument(
        "--created-at", type=int, default=None, metavar="EPOCH",
        help="Unix epoch seconds to backdate created_at/updated_at to (default: now); "
             "see spec §5.",
    )
```

```python
# add to main() dispatch in src/cc_session_tools/cli/ccst.py, after the "gc" block:

    if args.noun == "pdata":
        if args.verb == "add":
            sys.exit(_cmd_pdata_add(args))
```

Also update the module docstring at the top of `ccst.py` to list the new noun (append after the
existing `gc report` line, matching the docstring's existing style):

```
  pdata add                      Insert a new record into a project's SQLite data store (see
                                 ccst pdata --help for the full records/schema subcommand set).
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_service.py tests/test_ccst_pdata_cli.py -v`
Expected: PASS (3 + 2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/service.py src/cc_session_tools/cli/ccst.py tests/pdata/test_service.py tests/test_ccst_pdata_cli.py
git commit -m "feat(pdata): add ccst pdata add (content-only)"
```

---

## Task 7: `repository.py` — extension-table DDL ops + `record_group_fields` upsert

**Files:**
- Modify: `src/cc_session_tools/lib/pdata/repository.py`
- Modify: `tests/pdata/test_repository.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/pdata/test_repository.py

def test_ensure_extension_table_creates_table_with_record_id_pk(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            repository.ensure_extension_table(conn, "key-events")
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "ext_key_events" in tables
        cols = {r["name"] for r in conn.execute('PRAGMA table_info("ext_key_events")')}
        assert cols == {"record_id"}
    finally:
        conn.close()


def test_ensure_extension_table_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            repository.ensure_extension_table(conn, "key-events")
        with repository._immediate(conn):
            repository.ensure_extension_table(conn, "key-events")  # must not raise
    finally:
        conn.close()


def test_add_extension_column_creates_typed_column(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            repository.add_extension_column(conn, "key-events", "sender", "TEXT", default=None)
        cols = {r["name"]: r["type"] for r in conn.execute('PRAGMA table_info("ext_key_events")')}
        assert cols["sender"] == "TEXT"
    finally:
        conn.close()


def test_add_extension_column_with_default(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            repository.add_extension_column(conn, "key-events", "is_read", "INTEGER", default=0)
            repository.insert_base_record(
                conn, record_group="key-events", content="x", file_path=None,
                created_at=1, updated_at=1,
            )
            conn.execute(
                'INSERT INTO "ext_key_events" (record_id) VALUES (1)'
            )
        row = conn.execute('SELECT is_read FROM "ext_key_events" WHERE record_id=1').fetchone()
        assert row["is_read"] == 0
    finally:
        conn.close()


def test_add_extension_column_is_idempotent_noop_if_column_exists(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            repository.add_extension_column(conn, "key-events", "sender", "TEXT", default=None)
        with repository._immediate(conn):
            repository.add_extension_column(conn, "key-events", "sender", "TEXT", default=None)
        cols = [r["name"] for r in conn.execute('PRAGMA table_info("ext_key_events")')]
        assert cols.count("sender") == 1
    finally:
        conn.close()


def test_add_extension_column_rejects_bad_type(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    import pytest
    conn = repository.connect("testproj")
    try:
        with pytest.raises(ValueError, match="type"):
            with repository._immediate(conn):
                repository.add_extension_column(conn, "key-events", "x", "DROP TABLE records", default=None)
    finally:
        conn.close()


def test_add_extension_column_with_text_default_escapes_quotes(monkeypatch, tmp_path):
    """Regression test for the DDL-DEFAULT bound-parameter bug: ALTER TABLE ADD COLUMN ...
    DEFAULT ? is not valid SQLite (confirmed: raises OperationalError), so the default must be
    embedded as an escaped literal instead."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            repository.add_extension_column(
                conn, "key-events", "note", "TEXT", default="it's fine",
            )
            record_id = repository.insert_base_record(
                conn, record_group="key-events", content="x", file_path=None,
                created_at=1, updated_at=1,
            )
            repository.insert_extension_row(conn, "key-events", record_id, {})
        row = conn.execute(
            'SELECT note FROM "ext_key_events" WHERE record_id=?', (record_id,)
        ).fetchone()
        assert row["note"] == "it's fine"
    finally:
        conn.close()


def test_add_extension_column_with_invalid_integer_default_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    import pytest
    conn = repository.connect("testproj")
    try:
        with pytest.raises(ValueError, match="INTEGER"):
            with repository._immediate(conn):
                repository.add_extension_column(
                    conn, "key-events", "count", "INTEGER", default="not-a-number",
                )
    finally:
        conn.close()


def test_ensure_extension_table_backfills_rows_that_predate_it(monkeypatch, tmp_path):
    """Regression test for the missing-backfill bug: a record_group that already had rows
    before its first schema add-field call must still get an ext row for each of those rows —
    otherwise a later `update --field` on one of them silently updates zero rows."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            pre_existing_id = repository.insert_base_record(
                conn, record_group="notes", content="already here", file_path=None,
                created_at=1, updated_at=1,
            )
        with repository._immediate(conn):
            repository.add_extension_column(conn, "notes", "priority", "INTEGER", default=None)
        ext_row = repository.get_extension_row(conn, "notes", pre_existing_id)
        assert ext_row is not None
        assert ext_row["priority"] is None

        # And update_extension_row (Task 15) must be able to find that backfilled row —
        # verified here directly against the raw UPDATE, since update_extension_row itself
        # isn't defined until Task 15.
        cur = conn.execute(
            'UPDATE "ext_notes" SET priority=? WHERE record_id=?', (5, pre_existing_id),
        )
        assert cur.rowcount == 1
    finally:
        conn.close()


def test_upsert_field_description(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            repository.upsert_field_description(
                conn, record_group="key-events", field_name="sender",
                description="who sent it", added_at=1000,
            )
        row = conn.execute(
            "SELECT * FROM record_group_fields WHERE record_group=? AND field_name=?",
            ("key-events", "sender"),
        ).fetchone()
        assert row["description"] == "who sent it"
        assert row["added_at"] == 1000

        # Re-run with a new description — must overwrite, not duplicate (idempotent upsert).
        with repository._immediate(conn):
            repository.upsert_field_description(
                conn, record_group="key-events", field_name="sender",
                description="updated", added_at=1000,
            )
        rows = conn.execute(
            "SELECT * FROM record_group_fields WHERE record_group=? AND field_name=?",
            ("key-events", "sender"),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["description"] == "updated"
    finally:
        conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pdata/test_repository.py -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'ensure_extension_table'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/cc_session_tools/lib/pdata/repository.py, at module level near the top:

_ALLOWED_COLUMN_TYPES = frozenset({"TEXT", "INTEGER", "REAL", "BLOB"})


def _normalize_column_type(sql_type: str) -> str:
    """Whitelist-validate a column type token before it is interpolated into DDL (identifiers
    and types cannot be bound parameters in SQLite DDL — see plan Decision 2)."""
    normalized = sql_type.strip().upper()
    if normalized not in _ALLOWED_COLUMN_TYPES:
        raise ValueError(
            f"invalid column type {sql_type!r}: must be one of "
            f"{', '.join(sorted(_ALLOWED_COLUMN_TYPES))}"
        )
    return normalized
```

```python
# add to src/cc_session_tools/lib/pdata/repository.py:

from cc_session_tools.lib.pdata import naming  # add to existing import block


def ensure_extension_table(conn: sqlite3.Connection, record_group: str) -> None:
    """CREATE ext_<group> (record_id INTEGER PRIMARY KEY REFERENCES records(id)) if it doesn't
    exist yet — record_group is validated by naming.extension_table_name(). Caller owns the
    transaction.

    On the table's *first* creation only, backfills an ext row for every records row already
    in this group. Without this, a group that already had rows before its first
    `schema add-field` call would leave those rows without an ext row forever — breaking the
    one-to-one base/extension row invariant (plan Decision 3) for exactly the rows that existed
    first, and making `update --field` on any of them silently affect zero rows (a G1 silent-
    data-loss path). Checking existence explicitly first (rather than `CREATE TABLE IF NOT
    EXISTS` + unconditional backfill) is what makes the backfill run exactly once."""
    if extension_table_exists(conn, record_group):
        return
    table = naming.extension_table_name(record_group)
    conn.execute(
        f'CREATE TABLE "{table}" (record_id INTEGER PRIMARY KEY REFERENCES records(id))'
    )
    conn.execute(
        f'INSERT INTO "{table}" (record_id) SELECT id FROM records WHERE record_group=?',
        (record_group,),
    )


def extension_table_exists(conn: sqlite3.Connection, record_group: str) -> bool:
    table = naming.extension_table_name(record_group)
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def list_extension_columns(conn: sqlite3.Connection, record_group: str) -> list[str]:
    """Live extension column names for record_group, excluding the record_id PK. Returns []
    if the extension table doesn't exist."""
    if not extension_table_exists(conn, record_group):
        return []
    table = naming.extension_table_name(record_group)
    return [
        r["name"] for r in conn.execute(f'PRAGMA table_info("{table}")')
        if r["name"] != "record_id"
    ]


def add_extension_column(
    conn: sqlite3.Connection,
    record_group: str,
    field_name: str,
    sql_type: str,
    *,
    default: object | None,
) -> None:
    """Idempotent: creates ext_<group> if missing (backfilling existing rows — see
    ensure_extension_table), then ADD COLUMN if field_name isn't already a column (no-op if it
    already exists — spec §5's schema add-field idempotency). Caller owns the transaction."""
    naming.validate_field_name(field_name)
    normalized_type = _normalize_column_type(sql_type)
    ensure_extension_table(conn, record_group)
    table = naming.extension_table_name(record_group)
    existing = set(list_extension_columns(conn, record_group))
    if field_name in existing:
        return
    if default is None:
        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{field_name}" {normalized_type}')
    else:
        literal = _render_default_literal(default, normalized_type)
        conn.execute(
            f'ALTER TABLE "{table}" ADD COLUMN "{field_name}" {normalized_type} '
            f'DEFAULT {literal}'
        )


def _render_default_literal(value: object, normalized_type: str) -> str:
    """Render a DEFAULT clause literal for ALTER TABLE ADD COLUMN.

    SQLite does not accept a bound parameter in a DDL DEFAULT clause (confirmed: `ALTER TABLE t
    ADD COLUMN x INTEGER DEFAULT ?` raises `sqlite3.OperationalError: near "?": syntax error` —
    this is a hard SQLite grammar constraint, not a driver limitation). The literal must
    therefore be embedded directly in the SQL string. This stays injection-safe because
    normalized_type is already whitelist-checked (_normalize_column_type) and every branch
    below re-serializes value from a parsed Python value rather than ever passing the raw
    input string through unescaped:
    - TEXT: single-quote the string, doubling any embedded single quotes (SQL's own escape).
    - INTEGER/REAL: parse to a Python int/float first (raises ValueError on anything that
      isn't a valid number) and embed the *canonical* re-serialization, never the raw string.
    - BLOB: rejected — there's no safe, simple literal syntax to accept an arbitrary
      caller-supplied blob default, and no spec requirement to support one; omit --default for
      a BLOB field.
    """
    if normalized_type == "TEXT":
        return "'" + str(value).replace("'", "''") + "'"
    if normalized_type == "INTEGER":
        try:
            return str(int(str(value)))
        except ValueError as exc:
            raise ValueError(f"--default {value!r} is not a valid INTEGER") from exc
    if normalized_type == "REAL":
        try:
            return repr(float(str(value)))
        except ValueError as exc:
            raise ValueError(f"--default {value!r} is not a valid REAL") from exc
    raise ValueError(f"column defaults are not supported for type {normalized_type}")


def upsert_field_description(
    conn: sqlite3.Connection, *, record_group: str, field_name: str,
    description: str | None, added_at: int,
) -> None:
    """Idempotent write to record_group_fields (spec §4.4) — overwrites description/added_at
    on re-run rather than duplicating the (record_group, field_name) row."""
    conn.execute(
        "INSERT INTO record_group_fields (record_group, field_name, description, added_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(record_group, field_name) "
        "DO UPDATE SET description=excluded.description, added_at=excluded.added_at",
        (record_group, field_name, description, added_at),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_repository.py -v`
Expected: PASS (15 tests total in the file)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/repository.py tests/pdata/test_repository.py
git commit -m "feat(pdata): add extension-table DDL ops and field-description upsert"
```

---

## Task 8: `service.py` — `schema_add_field` + CLI `ccst pdata schema add-field`

**Files:**
- Modify: `src/cc_session_tools/lib/pdata/service.py`
- Modify: `src/cc_session_tools/cli/ccst.py`
- Modify: `tests/pdata/test_service.py`
- Modify: `tests/test_ccst_pdata_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/pdata/test_service.py

def test_schema_add_field_creates_column_and_description(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    service.schema_add_field(
        project="testproj", record_group="key-events", field_name="sender",
        sql_type="TEXT", description="who sent it", default=None,
    )
    from cc_session_tools.lib.pdata import repository
    conn = repository.connect("testproj")
    try:
        cols = repository.list_extension_columns(conn, "key-events")
        assert "sender" in cols
        row = conn.execute(
            "SELECT description FROM record_group_fields WHERE record_group=? AND field_name=?",
            ("key-events", "sender"),
        ).fetchone()
        assert row["description"] == "who sent it"
    finally:
        conn.close()


def test_schema_add_field_without_description_leaves_it_blank(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    service.schema_add_field(
        project="testproj", record_group="key-events", field_name="sender",
        sql_type="TEXT", description=None, default=None,
    )
    from cc_session_tools.lib.pdata import repository
    conn = repository.connect("testproj")
    try:
        row = conn.execute(
            "SELECT description FROM record_group_fields WHERE record_group=? AND field_name=?",
            ("key-events", "sender"),
        ).fetchone()
        assert row is None  # no --description given -> no row written at all
    finally:
        conn.close()


def test_schema_add_field_rejects_invalid_record_group(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="record_group"):
        service.schema_add_field(
            project="testproj", record_group="Bad Group", field_name="x",
            sql_type="TEXT", description=None, default=None,
        )


def test_schema_add_field_rerun_updates_description_without_duplicating_column(
    monkeypatch, tmp_path,
):
    """Regression test for the re-run-to-edit-description path (spec §10's open question about
    a possible edit-description command): calling schema_add_field again for a column that
    already exists must fall through add_extension_column's early-return and still reach
    upsert_field_description, overwriting the description rather than erroring or duplicating
    the (record_group, field_name) row."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    service.schema_add_field(
        project="testproj", record_group="key-events", field_name="sender",
        sql_type="TEXT", description="who sent it", default=None,
    )
    service.schema_add_field(
        project="testproj", record_group="key-events", field_name="sender",
        sql_type="TEXT", description="updated description", default=None,
    )
    from cc_session_tools.lib.pdata import repository
    conn = repository.connect("testproj")
    try:
        cols = repository.list_extension_columns(conn, "key-events")
        assert cols.count("sender") == 1
        rows = conn.execute(
            "SELECT description FROM record_group_fields WHERE record_group=? AND field_name=?",
            ("key-events", "sender"),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["description"] == "updated description"
    finally:
        conn.close()
```

```python
# append to tests/test_ccst_pdata_cli.py

def test_pdata_schema_add_field(base_env):
    r = _run(
        base_env, "pdata", "schema", "add-field", "--project", "testproj",
        "--group", "key-events", "--field", "sender:TEXT", "--description", "who sent it",
    )
    assert r.returncode == 0, r.stderr


def test_pdata_schema_add_field_rejects_bad_field_spec(base_env):
    r = _run(
        base_env, "pdata", "schema", "add-field", "--project", "testproj",
        "--group", "key-events", "--field", "not-a-valid-spec",
    )
    assert r.returncode == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pdata/test_service.py tests/test_ccst_pdata_cli.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'schema_add_field'`; CLI test
fails with "invalid choice: 'schema'".

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/cc_session_tools/lib/pdata/service.py (uses the module's existing `import time`;
# no new import needed):


def schema_add_field(
    *,
    project: str,
    record_group: str,
    field_name: str,
    sql_type: str,
    description: str | None,
    default: object | None,
) -> None:
    naming.validate_record_group(record_group)
    naming.validate_field_name(field_name)
    now = int(time.time())
    conn = repository.connect(project)
    try:
        with repository._immediate(conn):
            repository.add_extension_column(
                conn, record_group, field_name, sql_type, default=default,
            )
            if description is not None:
                repository.upsert_field_description(
                    conn, record_group=record_group, field_name=field_name,
                    description=description, added_at=now,
                )
    finally:
        conn.close()
```

```python
# add to src/cc_session_tools/cli/ccst.py:

def _parse_field_spec(raw: str) -> tuple[str, str]:
    """Parse "name:TYPE" into (name, TYPE). Raises ValueError on malformed input."""
    if ":" not in raw:
        raise ValueError(f"malformed --field spec (want name:TYPE): {raw!r}")
    name, sql_type = raw.split(":", 1)
    if not name or not sql_type:
        raise ValueError(f"malformed --field spec (want name:TYPE): {raw!r}")
    return name, sql_type


def _cmd_pdata_schema_add_field(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import service

    try:
        field_name, sql_type = _parse_field_spec(args.field)
        service.schema_add_field(
            project=args.project,
            record_group=args.group,
            field_name=field_name,
            sql_type=sql_type,
            description=args.description,
            default=args.default,
        )
    except ValueError as exc:
        print(f"ccst pdata: {exc}", file=sys.stderr)
        return 2
    print(f"added field {field_name!r} ({sql_type}) to {args.group!r}")
    return 0
```

```python
# add to _build_parser(), inside the "---- pdata ----" section, after pdata_add_parser:

    pdata_schema_parser = pdata_sub.add_parser("schema", help="Schema discovery and evolution")
    pdata_schema_sub = pdata_schema_parser.add_subparsers(dest="subverb", metavar="<subverb>")
    pdata_schema_sub.required = True

    pdata_schema_add_field_parser = pdata_schema_sub.add_parser(
        "add-field", help="Add/describe an extension-table field (idempotent)",
    )
    pdata_schema_add_field_parser.add_argument("--project", required=True, metavar="NAME")
    pdata_schema_add_field_parser.add_argument("--group", required=True, metavar="RECORD_GROUP")
    pdata_schema_add_field_parser.add_argument(
        "--field", required=True, metavar="NAME:TYPE",
        help="e.g. sender:TEXT — TYPE is one of TEXT, INTEGER, REAL, BLOB",
    )
    pdata_schema_add_field_parser.add_argument("--description", default=None, metavar="TEXT")
    pdata_schema_add_field_parser.add_argument("--default", default=None, metavar="VALUE")
```

```python
# add to main() dispatch, inside the "if args.noun == 'pdata':" block:

        if args.verb == "schema":
            if args.subverb == "add-field":
                sys.exit(_cmd_pdata_schema_add_field(args))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_service.py tests/test_ccst_pdata_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/service.py src/cc_session_tools/cli/ccst.py tests/pdata/test_service.py tests/test_ccst_pdata_cli.py
git commit -m "feat(pdata): add ccst pdata schema add-field"
```

---

## Task 9: `schema list` / `schema show`

**Files:**
- Modify: `src/cc_session_tools/lib/pdata/repository.py`
- Modify: `src/cc_session_tools/lib/pdata/service.py`
- Modify: `src/cc_session_tools/cli/ccst.py`
- Modify: `tests/pdata/test_repository.py`
- Modify: `tests/pdata/test_service.py`
- Modify: `tests/test_ccst_pdata_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/pdata/test_repository.py

def test_list_record_groups_returns_counts_and_ext_flag(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            repository.insert_base_record(
                conn, record_group="ccst-ideas", content="a", file_path=None,
                created_at=1, updated_at=1,
            )
            repository.insert_base_record(
                conn, record_group="ccst-ideas", content="b", file_path=None,
                created_at=2, updated_at=5,
            )
            repository.insert_base_record(
                conn, record_group="filings", content="c", file_path=None,
                created_at=3, updated_at=3,
            )
            repository.add_extension_column(conn, "filings", "doc_type", "TEXT", default=None)
        groups = {g["record_group"]: g for g in repository.list_record_groups(conn)}
        assert groups["ccst-ideas"]["row_count"] == 2
        assert groups["ccst-ideas"]["has_extension_table"] is False
        assert groups["ccst-ideas"]["max_updated_at"] == 5
        assert groups["filings"]["row_count"] == 1
        assert groups["filings"]["has_extension_table"] is True
    finally:
        conn.close()


def test_list_record_groups_excludes_soft_deleted_from_row_count(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            rid = repository.insert_base_record(
                conn, record_group="notes", content="a", file_path=None,
                created_at=1, updated_at=1,
            )
            conn.execute("UPDATE records SET deleted_at=? WHERE id=?", (2, rid))
        groups = {g["record_group"]: g for g in repository.list_record_groups(conn)}
        assert groups["notes"]["row_count"] == 0
    finally:
        conn.close()
```

```python
# append to tests/pdata/test_service.py

def test_schema_list_and_show(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    service.add_record(
        project="testproj", record_group="filings", content="x", file_path=None,
        fields={}, created_at=1000,
    )
    service.schema_add_field(
        project="testproj", record_group="filings", field_name="doc_type",
        sql_type="TEXT", description="kind of document", default=None,
    )

    groups = service.schema_list(project="testproj")
    names = {g["record_group"] for g in groups}
    assert "filings" in names

    columns = service.schema_show(project="testproj", record_group="filings")
    base_names = {c["name"] for c in columns if c["source"] == "base"}
    ext_names = {c["name"]: c for c in columns if c["source"] == "extension"}
    assert base_names == {"id", "record_group", "content", "file_path",
                           "created_at", "updated_at", "version", "deleted_at"}
    assert ext_names["doc_type"]["type"] == "TEXT"
    assert ext_names["doc_type"]["description"] == "kind of document"


def test_schema_show_field_without_description_shows_blank(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    service.schema_add_field(
        project="testproj", record_group="filings", field_name="doc_type",
        sql_type="TEXT", description=None, default=None,
    )
    columns = service.schema_show(project="testproj", record_group="filings")
    ext = next(c for c in columns if c["name"] == "doc_type")
    assert ext["description"] is None
```

```python
# append to tests/test_ccst_pdata_cli.py

def test_pdata_schema_list_and_show(base_env):
    _run(base_env, "pdata", "add", "--project", "testproj", "--group", "filings",
         "--content", "x")
    r_list = _run(base_env, "pdata", "schema", "list", "--project", "testproj")
    assert r_list.returncode == 0
    assert "filings" in r_list.stdout

    r_show = _run(base_env, "pdata", "schema", "show", "--project", "testproj",
                    "--group", "filings")
    assert r_show.returncode == 0
    assert "content" in r_show.stdout


def test_pdata_schema_list_rejects_bad_project_name(base_env):
    r = _run(base_env, "pdata", "schema", "list", "--project", "../escape")
    assert r.returncode == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pdata/test_repository.py tests/pdata/test_service.py tests/test_ccst_pdata_cli.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'list_record_groups'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/cc_session_tools/lib/pdata/repository.py:

def list_record_groups(conn: sqlite3.Connection) -> list[dict[str, object]]:
    """Every distinct record_group in this project's DB: row count (active rows only, per
    spec §4.5 default), whether it has an extension table, most recent updated_at.

    row_count uses COUNT(*) FILTER (WHERE deleted_at IS NULL), not a WHERE clause on the query
    itself — a WHERE deleted_at IS NULL placed before GROUP BY would drop a record_group whose
    only rows are all soft-deleted from the result set entirely (there being no non-deleted row
    left to GROUP BY), which would make it silently vanish from `schema list` instead of showing
    row_count=0. The FILTER form still groups every record_group that has any row at all — active
    or soft-deleted — and only restricts what gets counted."""
    rows = conn.execute(
        "SELECT record_group, "
        "COUNT(*) FILTER (WHERE deleted_at IS NULL) AS row_count, "
        "MAX(updated_at) AS max_updated_at "
        "FROM records GROUP BY record_group ORDER BY record_group"
    ).fetchall()
    return [
        {
            "record_group": r["record_group"],
            "row_count": r["row_count"],
            "max_updated_at": r["max_updated_at"],
            "has_extension_table": extension_table_exists(conn, r["record_group"]),
        }
        for r in rows
    ]


def show_schema_columns(conn: sqlite3.Connection, record_group: str) -> list[dict[str, object]]:
    """Base columns (fixed) + live extension columns (name/type from PRAGMA table_info,
    description from record_group_fields if set) for one record_group."""
    columns: list[dict[str, object]] = [
        {"source": "base", "name": name, "type": None, "description": None, "added_at": None}
        for name in naming.BASE_RECORD_COLUMNS
    ]
    if not extension_table_exists(conn, record_group):
        return columns

    table = naming.extension_table_name(record_group)
    descriptions = {
        r["field_name"]: (r["description"], r["added_at"])
        for r in conn.execute(
            "SELECT field_name, description, added_at FROM record_group_fields "
            "WHERE record_group=?",
            (record_group,),
        ).fetchall()
    }
    for r in conn.execute(f'PRAGMA table_info("{table}")'):
        if r["name"] == "record_id":
            continue
        description, added_at = descriptions.get(r["name"], (None, None))
        columns.append({
            "source": "extension", "name": r["name"], "type": r["type"],
            "description": description, "added_at": added_at,
        })
    return columns
```

```python
# add to src/cc_session_tools/lib/pdata/service.py:

def schema_list(*, project: str) -> list[dict[str, object]]:
    conn = repository.connect(project)
    try:
        return repository.list_record_groups(conn)
    finally:
        conn.close()


def schema_show(*, project: str, record_group: str) -> list[dict[str, object]]:
    naming.validate_record_group(record_group)
    conn = repository.connect(project)
    try:
        return repository.show_schema_columns(conn, record_group)
    finally:
        conn.close()
```

```python
# add to src/cc_session_tools/cli/ccst.py — requires `from typing import cast` added to the
# existing `from typing import Any` import line (`from typing import Any, cast`):

def _cmd_pdata_schema_list(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import service

    try:
        groups = service.schema_list(project=args.project)
    except ValueError as exc:
        print(f"ccst pdata: {exc}", file=sys.stderr)
        return 2
    if not groups:
        print(f"No record_groups found in project {args.project!r}.")
        return 0
    name_w = max(len(str(g["record_group"])) for g in groups)
    for g in groups:
        ext = "yes" if g["has_extension_table"] else "no"
        max_updated_at = g["max_updated_at"]
        updated = _fmt_ts(cast(float, max_updated_at)) if max_updated_at else "(never)"
        print(f"{str(g['record_group']):<{name_w}}  rows={g['row_count']:<6} "
              f"ext={ext:<3} updated={updated}")
    return 0


def _cmd_pdata_schema_show(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import service

    try:
        columns = service.schema_show(project=args.project, record_group=args.group)
    except ValueError as exc:
        print(f"ccst pdata: {exc}", file=sys.stderr)
        return 2
    for c in columns:
        type_label = c["type"] or ""
        desc = c["description"] or ""
        print(f"{c['source']:<9} {c['name']:<20} {type_label:<10} {desc}")
    return 0
```

```python
# add to _build_parser(), inside pdata_schema_sub:

    pdata_schema_list_parser = pdata_schema_sub.add_parser(
        "list", help="List every record_group and whether it has an extension table",
    )
    pdata_schema_list_parser.add_argument("--project", required=True, metavar="NAME")

    pdata_schema_show_parser = pdata_schema_sub.add_parser(
        "show", help="Show base + extension columns for one record_group",
    )
    pdata_schema_show_parser.add_argument("--project", required=True, metavar="NAME")
    pdata_schema_show_parser.add_argument("--group", required=True, metavar="RECORD_GROUP")
```

```python
# add to main() dispatch, inside "if args.verb == 'schema':":

            if args.subverb == "list":
                sys.exit(_cmd_pdata_schema_list(args))
            if args.subverb == "show":
                sys.exit(_cmd_pdata_schema_show(args))
```

`_fmt_ts` already exists in `ccst.py` (used by `_cmd_sessions_list`) — reuse it, no new helper
needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_repository.py tests/pdata/test_service.py tests/test_ccst_pdata_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/repository.py src/cc_session_tools/lib/pdata/service.py src/cc_session_tools/cli/ccst.py tests/pdata/test_repository.py tests/pdata/test_service.py tests/test_ccst_pdata_cli.py
git commit -m "feat(pdata): add ccst pdata schema list/show"
```

---

## Task 10: `add` — field routing (`--field k=v`, reject unregistered, 1:1 extension row)

**Files:**
- Modify: `src/cc_session_tools/lib/pdata/repository.py`
- Modify: `src/cc_session_tools/lib/pdata/service.py`
- Modify: `src/cc_session_tools/cli/ccst.py`
- Modify: `tests/pdata/test_repository.py`
- Modify: `tests/pdata/test_service.py`
- Modify: `tests/test_ccst_pdata_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/pdata/test_repository.py

def test_insert_extension_row(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            repository.add_extension_column(conn, "key-events", "sender", "TEXT", default=None)
            record_id = repository.insert_base_record(
                conn, record_group="key-events", content="x", file_path=None,
                created_at=1, updated_at=1,
            )
            repository.insert_extension_row(
                conn, "key-events", record_id, {"sender": "alice"},
            )
        row = conn.execute(
            'SELECT * FROM "ext_key_events" WHERE record_id=?', (record_id,)
        ).fetchone()
        assert row["sender"] == "alice"
    finally:
        conn.close()


def test_insert_extension_row_with_no_fields_still_creates_row(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            repository.add_extension_column(conn, "key-events", "sender", "TEXT", default=None)
            record_id = repository.insert_base_record(
                conn, record_group="key-events", content="x", file_path=None,
                created_at=1, updated_at=1,
            )
            repository.insert_extension_row(conn, "key-events", record_id, {})
        row = conn.execute(
            'SELECT * FROM "ext_key_events" WHERE record_id=?', (record_id,)
        ).fetchone()
        assert row is not None
        assert row["sender"] is None
    finally:
        conn.close()
```

```python
# append to tests/pdata/test_service.py

def test_add_record_routes_field_to_extension_table(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    service.schema_add_field(
        project="testproj", record_group="key-events", field_name="sender",
        sql_type="TEXT", description=None, default=None,
    )
    record = service.add_record(
        project="testproj", record_group="key-events", content="an event",
        file_path=None, fields={"sender": "alice"}, created_at=1000,
    )
    assert record.fields == {"sender": "alice"}


def test_add_record_rejects_unregistered_field(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="unregistered field"):
        service.add_record(
            project="testproj", record_group="key-events", content="an event",
            file_path=None, fields={"nope": "x"}, created_at=1000,
        )


def test_add_record_with_no_fields_and_no_extension_table_stays_base_only(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    record = service.add_record(
        project="testproj", record_group="ccst-ideas", content="an idea",
        file_path=None, fields={}, created_at=1000,
    )
    assert record.fields == {}
```

```python
# append to tests/test_ccst_pdata_cli.py

def test_pdata_add_with_field_routes_to_extension_table(base_env):
    _run(base_env, "pdata", "schema", "add-field", "--project", "testproj",
         "--group", "key-events", "--field", "sender:TEXT")
    r = _run(base_env, "pdata", "add", "--project", "testproj", "--group", "key-events",
              "--content", "an event", "--field", "sender=alice")
    assert r.returncode == 0, r.stderr


def test_pdata_add_rejects_unregistered_field(base_env):
    r = _run(base_env, "pdata", "add", "--project", "testproj", "--group", "key-events",
              "--content", "an event", "--field", "nope=x")
    assert r.returncode == 2
    assert "unregistered" in r.stderr
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pdata/test_repository.py tests/pdata/test_service.py tests/test_ccst_pdata_cli.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'insert_extension_row'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/cc_session_tools/lib/pdata/repository.py:

def insert_extension_row(
    conn: sqlite3.Connection, record_group: str, record_id: int, fields: Mapping[str, object],
) -> None:
    """INSERT INTO ext_<group> (record_id, <given fields>) VALUES (...). Always creates a row
    (even with fields={}) so the group's base/ext rows stay 1:1 whenever ext_<group> exists —
    see plan Decision 3. Caller must have already validated every key in fields is a live
    column (service.py's job, not this layer's)."""
    table = naming.extension_table_name(record_group)
    columns = ["record_id", *fields.keys()]
    placeholders = ", ".join("?" for _ in columns)
    quoted_columns = ", ".join(f'"{c}"' for c in columns)
    conn.execute(
        f'INSERT INTO "{table}" ({quoted_columns}) VALUES ({placeholders})',
        (record_id, *fields.values()),
    )


def get_extension_row(
    conn: sqlite3.Connection, record_group: str, record_id: int,
) -> sqlite3.Row | None:
    if not extension_table_exists(conn, record_group):
        return None
    table = naming.extension_table_name(record_group)
    return conn.execute(
        f'SELECT * FROM "{table}" WHERE record_id=?', (record_id,)
    ).fetchone()
```

```python
# modify add_record() in src/cc_session_tools/lib/pdata/service.py:

def add_record(
    *,
    project: str,
    record_group: str,
    content: str,
    file_path: str | None,
    fields: Mapping[str, str],
    created_at: int | None = None,
) -> Record:
    naming.validate_record_group(record_group)
    _validate_relative_file_path(file_path)
    ts = created_at if created_at is not None else int(time.time())

    conn = repository.connect(project)
    try:
        with repository._immediate(conn):
            live_columns = set(repository.list_extension_columns(conn, record_group))
            unregistered = set(fields) - live_columns
            if unregistered:
                raise ValueError(
                    f"unregistered field(s) for group {record_group!r}: "
                    f"{sorted(unregistered)} — run 'ccst pdata schema add-field' first"
                )
            record_id = repository.insert_base_record(
                conn, record_group=record_group, content=content, file_path=file_path,
                created_at=ts, updated_at=ts,
            )
            if repository.extension_table_exists(conn, record_group):
                repository.insert_extension_row(conn, record_group, record_id, fields)
        row = repository.get_base_record(conn, record_id)
        assert row is not None
        ext_row = repository.get_extension_row(conn, record_group, record_id)
        record = _row_to_record(row)
        if ext_row is not None:
            record.fields = {k: ext_row[k] for k in ext_row.keys() if k != "record_id"}
    finally:
        conn.close()
    return record
```

Note: raising `ValueError` inside the `with repository._immediate(conn):` block is safe — the
`_immediate` context manager's `except BaseException` catches it, issues `ROLLBACK`, and
re-raises, so a rejected `add` never leaves a partial base row committed.

```python
# modify _cmd_pdata_add() and the pdata_add_parser in src/cc_session_tools/cli/ccst.py:

def _parse_field_assignment(raw: str) -> tuple[str, str]:
    """Parse "k=v" into (k, v). Raises ValueError on malformed input."""
    if "=" not in raw:
        raise ValueError(f"malformed --field assignment (want name=value): {raw!r}")
    name, value = raw.split("=", 1)
    if not name:
        raise ValueError(f"malformed --field assignment (want name=value): {raw!r}")
    return name, value


def _cmd_pdata_add(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import service

    try:
        fields = dict(_parse_field_assignment(raw) for raw in (args.field or []))
        record = service.add_record(
            project=args.project,
            record_group=args.group,
            content=args.content,
            file_path=args.file,
            fields=fields,
            created_at=args.created_at,
        )
    except ValueError as exc:
        print(f"ccst pdata: {exc}", file=sys.stderr)
        return 2
    print(record.id)
    return 0
```

```python
# add to pdata_add_parser in _build_parser():

    pdata_add_parser.add_argument(
        "--field", action="append", default=[], metavar="NAME=VALUE",
        help="Extension field assignment; may repeat. Field must already be registered via "
             "'ccst pdata schema add-field'.",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_repository.py tests/pdata/test_service.py tests/test_ccst_pdata_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/repository.py src/cc_session_tools/lib/pdata/service.py src/cc_session_tools/cli/ccst.py tests/pdata/test_repository.py tests/pdata/test_service.py tests/test_ccst_pdata_cli.py
git commit -m "feat(pdata): route ccst pdata add --field into the extension table"
```

---

## Task 11: `lib/pdata/formatting.py` — table/json/csv renderers

**Files:**
- Create: `src/cc_session_tools/lib/pdata/formatting.py`
- Test: `tests/pdata/test_formatting.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/pdata/test_formatting.py
from __future__ import annotations

import json

from cc_session_tools.lib.pdata import formatting

_ROWS = [
    {"id": 1, "content": "first", "sender": "alice"},
    {"id": 2, "content": "second", "sender": None},
]


def test_render_table_includes_headers_and_values():
    out = formatting.render(_ROWS, fmt="table")
    assert "id" in out and "content" in out and "sender" in out
    assert "first" in out
    assert "alice" in out


def test_render_json_round_trips():
    out = formatting.render(_ROWS, fmt="json")
    parsed = json.loads(out)
    assert parsed == _ROWS


def test_render_csv_has_header_and_rows():
    out = formatting.render(_ROWS, fmt="csv")
    lines = out.strip().splitlines()
    assert lines[0] == "id,content,sender"
    assert lines[1] == "1,first,alice"
    assert lines[2] == "2,second,"


def test_render_empty_list_table():
    assert "No rows" in formatting.render([], fmt="table")


def test_render_empty_list_json():
    assert json.loads(formatting.render([], fmt="json")) == []


def test_render_unknown_format_raises():
    import pytest
    with pytest.raises(ValueError, match="format"):
        formatting.render(_ROWS, fmt="xml")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pdata/test_formatting.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cc_session_tools.lib.pdata.formatting'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/pdata/formatting.py
"""Output rendering for ccst pdata list/query/get: table, json, csv — plus a human-readable
current-vs-attempted conflict diff for update (spec §6.2)."""
from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping

_FORMATS = ("table", "json", "csv")


def render(rows: list[dict[str, object]], *, fmt: str) -> str:
    if fmt not in _FORMATS:
        raise ValueError(f"invalid format {fmt!r}: must be one of {', '.join(_FORMATS)}")
    if fmt == "json":
        return json.dumps(rows)
    if not rows:
        return "No rows." if fmt == "table" else ""
    if fmt == "csv":
        return _render_csv(rows)
    return _render_table(rows)


def _render_csv(rows: list[dict[str, object]]) -> str:
    fieldnames = list(rows[0].keys())
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: ("" if v is None else v) for k, v in row.items()})
    return buf.getvalue()


def _render_table(rows: list[dict[str, object]]) -> str:
    fieldnames = list(rows[0].keys())
    str_rows = [
        {k: ("" if row.get(k) is None else str(row.get(k))) for k in fieldnames}
        for row in rows
    ]
    widths = {k: max(len(k), max(len(r[k]) for r in str_rows)) for k in fieldnames}
    header = "  ".join(k.ljust(widths[k]) for k in fieldnames)
    sep = "  ".join("-" * widths[k] for k in fieldnames)
    lines = [header, sep]
    lines += ["  ".join(r[k].ljust(widths[k]) for k in fieldnames) for r in str_rows]
    return "\n".join(lines)


def render_conflict_diff(
    current: Mapping[str, object], attempted: Mapping[str, object], *, fmt: str,
) -> str:
    """current-vs-attempted diff for an update()/delete() version conflict (spec §6.2)."""
    if fmt == "json":
        return json.dumps({"current": current, "attempted": attempted})
    lines = [f"version conflict on record {current.get('id')}:"]
    all_keys = sorted(set(current) | set(attempted))
    for key in all_keys:
        cur_val = current.get(key)
        att_val = attempted.get(key)
        if key in attempted and cur_val != att_val:
            lines.append(f"  {key}: current={cur_val!r} attempted={att_val!r}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_formatting.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/formatting.py tests/pdata/test_formatting.py
git commit -m "feat(pdata): add table/json/csv output renderers"
```

---

## Task 12: `get` — join-and-flatten for a single known id + CLI `ccst pdata get`

**Files:**
- Modify: `src/cc_session_tools/lib/pdata/service.py`
- Modify: `src/cc_session_tools/cli/ccst.py`
- Modify: `tests/pdata/test_service.py`
- Modify: `tests/test_ccst_pdata_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/pdata/test_service.py

def test_get_record_flattens_extension_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    service.schema_add_field(
        project="testproj", record_group="key-events", field_name="sender",
        sql_type="TEXT", description=None, default=None,
    )
    created = service.add_record(
        project="testproj", record_group="key-events", content="an event",
        file_path=None, fields={"sender": "alice"}, created_at=1000,
    )
    fetched = service.get_record(project="testproj", record_id=created.id)
    assert fetched is not None
    assert fetched.content == "an event"
    assert fetched.fields == {"sender": "alice"}


def test_get_record_returns_none_for_missing_id(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    assert service.get_record(project="testproj", record_id=999) is None
```

`get_record`'s soft-delete-exclusion behavior is intentionally **not** tested here — it depends
on `service.delete_record`, which doesn't exist until Task 16. That test
(`test_get_record_excludes_soft_deleted_by_default`) is added in Task 16 instead, once
`delete_record` exists, so this task's own test suite is fully green at the end of this task
with no forward reference to later work.

```python
# append to tests/test_ccst_pdata_cli.py

def test_pdata_get_shows_flattened_fields(base_env):
    _run(base_env, "pdata", "schema", "add-field", "--project", "testproj",
         "--group", "key-events", "--field", "sender:TEXT")
    r_add = _run(base_env, "pdata", "add", "--project", "testproj", "--group", "key-events",
                  "--content", "an event", "--field", "sender=alice")
    record_id = r_add.stdout.strip()
    r_get = _run(base_env, "pdata", "get", "--project", "testproj", "--id", record_id)
    assert r_get.returncode == 0
    assert "alice" in r_get.stdout


def test_pdata_get_missing_id_errors(base_env):
    r = _run(base_env, "pdata", "get", "--project", "testproj", "--id", "999")
    assert r.returncode == 1


def test_pdata_get_rejects_bad_project_name(base_env):
    r = _run(base_env, "pdata", "get", "--project", "../escape", "--id", "1")
    assert r.returncode == 2


def test_pdata_add_created_at_flag_is_persisted(base_env):
    """End-to-end check (deferred from Task 6, which has no `get` yet) that `ccst pdata add
    --created-at` actually lands the given epoch in storage rather than silently falling back
    to 'now'."""
    r_add = _run(base_env, "pdata", "add", "--project", "testproj", "--group", "ccst-ideas",
                  "--content", "an old idea", "--created-at", "1000")
    record_id = r_add.stdout.strip()
    r_get = _run(base_env, "pdata", "get", "--project", "testproj", "--id", record_id)
    assert r_get.returncode == 0
    assert "1000" in r_get.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pdata/test_service.py tests/test_ccst_pdata_cli.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'get_record'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/cc_session_tools/lib/pdata/service.py:

def get_record(
    *, project: str, record_id: int, include_deleted: bool = False,
) -> Record | None:
    conn = repository.connect(project)
    try:
        row = repository.get_base_record(conn, record_id)
        if row is None:
            return None
        if row["deleted_at"] is not None and not include_deleted:
            return None
        record = _row_to_record(row)
        ext_row = repository.get_extension_row(conn, record.record_group, record_id)
        if ext_row is not None:
            record.fields = {k: ext_row[k] for k in ext_row.keys() if k != "record_id"}
        return record
    finally:
        conn.close()


def record_to_dict(record: Record) -> dict[str, object]:
    """Flatten a Record into one dict (base columns + extension fields merged) for CLI
    rendering. The single home of this shape — every ccst.py handler that prints a Record
    (get/list/query, and the current side of an update/delete conflict) calls this instead of
    each re-deriving its own flatten, per this repo's "one helper per shared shape" coding
    standard."""
    from dataclasses import asdict
    d = asdict(record)
    fields = d.pop("fields")
    d.update(fields)
    return d
```

```python
# add to src/cc_session_tools/cli/ccst.py:

def _cmd_pdata_get(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import formatting, service

    try:
        record = service.get_record(
            project=args.project, record_id=args.id, include_deleted=args.include_deleted,
        )
    except ValueError as exc:
        print(f"ccst pdata: {exc}", file=sys.stderr)
        return 2
    if record is None:
        print(f"ccst pdata: record not found: {args.id}", file=sys.stderr)
        return 1
    print(formatting.render([service.record_to_dict(record)], fmt="table"))
    return 0
```

```python
# add to pdata_sub in _build_parser():

    pdata_get_parser = pdata_sub.add_parser("get", help="Fetch a single record by id")
    pdata_get_parser.add_argument("--project", required=True, metavar="NAME")
    pdata_get_parser.add_argument("--id", required=True, type=int)
    pdata_get_parser.add_argument("--include-deleted", action="store_true")
```

```python
# add to main() dispatch, inside "if args.noun == 'pdata':":

        if args.verb == "get":
            sys.exit(_cmd_pdata_get(args))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_service.py tests/test_ccst_pdata_cli.py -v`
Expected: PASS (the soft-delete test is deferred to Task 16, per the Step 1 correction above)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/service.py src/cc_session_tools/cli/ccst.py tests/pdata/test_service.py tests/test_ccst_pdata_cli.py
git commit -m "feat(pdata): add ccst pdata get with join-and-flatten"
```

---

## Task 13: `list` — filters + join-and-flatten + CLI `ccst pdata list`

**Files:**
- Modify: `src/cc_session_tools/lib/pdata/repository.py`
- Modify: `src/cc_session_tools/lib/pdata/service.py`
- Modify: `src/cc_session_tools/cli/ccst.py`
- Modify: `tests/pdata/test_repository.py`
- Modify: `tests/pdata/test_service.py`
- Modify: `tests/test_ccst_pdata_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/pdata/test_repository.py

def test_list_base_records_filters_by_group_since_until(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            repository.insert_base_record(conn, record_group="a", content="1",
                                           file_path=None, created_at=100, updated_at=100)
            repository.insert_base_record(conn, record_group="a", content="2",
                                           file_path=None, created_at=200, updated_at=200)
            repository.insert_base_record(conn, record_group="b", content="3",
                                           file_path=None, created_at=150, updated_at=150)
        rows = repository.list_base_records(conn, record_group="a", since=None, until=None,
                                             limit=None, include_deleted=False)
        assert [r["content"] for r in rows] == ["1", "2"]

        rows = repository.list_base_records(conn, record_group="a", since=150, until=None,
                                             limit=None, include_deleted=False)
        assert [r["content"] for r in rows] == ["2"]

        rows = repository.list_base_records(conn, record_group="a", since=None, until=150,
                                             limit=None, include_deleted=False)
        assert [r["content"] for r in rows] == ["1"]

        rows = repository.list_base_records(conn, record_group="a", since=None, until=None,
                                             limit=1, include_deleted=False)
        assert len(rows) == 1
    finally:
        conn.close()


def test_list_base_records_excludes_deleted_unless_asked(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            rid = repository.insert_base_record(conn, record_group="a", content="1",
                                                 file_path=None, created_at=1, updated_at=1)
            conn.execute("UPDATE records SET deleted_at=? WHERE id=?", (2, rid))
        assert repository.list_base_records(
            conn, record_group="a", since=None, until=None, limit=None, include_deleted=False,
        ) == []
        assert len(repository.list_base_records(
            conn, record_group="a", since=None, until=None, limit=None, include_deleted=True,
        )) == 1
    finally:
        conn.close()
```

```python
# append to tests/pdata/test_service.py

def test_list_records_flattens_extension_fields_for_every_row(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    service.schema_add_field(
        project="testproj", record_group="key-events", field_name="sender",
        sql_type="TEXT", description=None, default=None,
    )
    service.add_record(project="testproj", record_group="key-events", content="e1",
                        file_path=None, fields={"sender": "alice"}, created_at=1000)
    service.add_record(project="testproj", record_group="key-events", content="e2",
                        file_path=None, fields={"sender": "bob"}, created_at=2000)
    rows = service.list_records(project="testproj", record_group="key-events")
    assert [r.fields["sender"] for r in rows] == ["alice", "bob"]
```

```python
# append to tests/test_ccst_pdata_cli.py

def test_pdata_list_json_format(base_env):
    _run(base_env, "pdata", "add", "--project", "testproj", "--group", "ccst-ideas",
         "--content", "idea one")
    r = _run(base_env, "pdata", "list", "--project", "testproj", "--group", "ccst-ideas",
              "--format", "json")
    assert r.returncode == 0
    import json
    parsed = json.loads(r.stdout)
    assert parsed[0]["content"] == "idea one"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pdata/test_repository.py tests/pdata/test_service.py tests/test_ccst_pdata_cli.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'list_base_records'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/cc_session_tools/lib/pdata/repository.py:

def list_base_records(
    conn: sqlite3.Connection,
    *,
    record_group: str,
    since: int | None,
    until: int | None,
    limit: int | None,
    include_deleted: bool,
) -> list[sqlite3.Row]:
    clauses = ["record_group=?"]
    params: list[object] = [record_group]
    if not include_deleted:
        clauses.append("deleted_at IS NULL")
    if since is not None:
        clauses.append("updated_at >= ?")
        params.append(since)
    if until is not None:
        clauses.append("updated_at <= ?")
        params.append(until)
    where = " AND ".join(clauses)
    sql = f"SELECT * FROM records WHERE {where} ORDER BY id"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()
```

```python
# add to src/cc_session_tools/lib/pdata/service.py:

def list_records(
    *,
    project: str,
    record_group: str,
    since: int | None = None,
    until: int | None = None,
    limit: int | None = None,
    include_deleted: bool = False,
) -> list[Record]:
    naming.validate_record_group(record_group)
    conn = repository.connect(project)
    try:
        rows = repository.list_base_records(
            conn, record_group=record_group, since=since, until=until,
            limit=limit, include_deleted=include_deleted,
        )
        has_ext = repository.extension_table_exists(conn, record_group)
        records = []
        for row in rows:
            record = _row_to_record(row)
            if has_ext:
                ext_row = repository.get_extension_row(conn, record_group, row["id"])
                if ext_row is not None:
                    record.fields = {
                        k: ext_row[k] for k in ext_row.keys() if k != "record_id"
                    }
            records.append(record)
        return records
    finally:
        conn.close()
```

```python
# add to src/cc_session_tools/cli/ccst.py:

def _cmd_pdata_list(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import formatting, service

    try:
        records = service.list_records(
            project=args.project, record_group=args.group,
            since=args.since, until=args.until, limit=args.limit,
            include_deleted=args.include_deleted,
        )
    except ValueError as exc:
        print(f"ccst pdata: {exc}", file=sys.stderr)
        return 2
    print(formatting.render([service.record_to_dict(r) for r in records], fmt=args.format))
    return 0
```

```python
# add to pdata_sub in _build_parser():

    pdata_list_parser = pdata_sub.add_parser("list", help="List records in one record_group")
    pdata_list_parser.add_argument("--project", required=True, metavar="NAME")
    pdata_list_parser.add_argument("--group", required=True, metavar="RECORD_GROUP")
    pdata_list_parser.add_argument("--since", type=int, default=None, metavar="EPOCH")
    pdata_list_parser.add_argument("--until", type=int, default=None, metavar="EPOCH")
    pdata_list_parser.add_argument("--limit", type=int, default=None, metavar="N")
    pdata_list_parser.add_argument("--include-deleted", action="store_true")
    pdata_list_parser.add_argument(
        "--format", choices=("table", "json", "csv"), default="table",
    )
```

```python
# add to main() dispatch:

        if args.verb == "list":
            sys.exit(_cmd_pdata_list(args))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_repository.py tests/pdata/test_service.py tests/test_ccst_pdata_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/repository.py src/cc_session_tools/lib/pdata/service.py src/cc_session_tools/cli/ccst.py tests/pdata/test_repository.py tests/pdata/test_service.py tests/test_ccst_pdata_cli.py
git commit -m "feat(pdata): add ccst pdata list with join-and-flatten"
```

---

## Task 14: `query` — `--where` parsing + auto-join + CLI `ccst pdata query`

**Files:**
- Modify: `src/cc_session_tools/lib/pdata/repository.py`
- Modify: `src/cc_session_tools/lib/pdata/service.py`
- Modify: `src/cc_session_tools/cli/ccst.py`
- Modify: `tests/pdata/test_repository.py`
- Modify: `tests/pdata/test_service.py`
- Modify: `tests/test_ccst_pdata_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/pdata/test_repository.py

def test_query_records_filters_on_base_and_extension_columns(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            repository.add_extension_column(conn, "key-events", "sent_at", "INTEGER", default=None)
            r1 = repository.insert_base_record(conn, record_group="key-events", content="a",
                                                file_path=None, created_at=1, updated_at=1)
            repository.insert_extension_row(conn, "key-events", r1, {"sent_at": 100})
            r2 = repository.insert_base_record(conn, record_group="key-events", content="b",
                                                file_path=None, created_at=2, updated_at=2)
            repository.insert_extension_row(conn, "key-events", r2, {"sent_at": 200})

        rows = repository.query_records(
            conn, record_group="key-events",
            conditions=[("sent_at", ">", "150")], limit=None,
        )
        assert [r["content"] for r in rows] == ["b"]

        rows = repository.query_records(
            conn, record_group="key-events",
            conditions=[("content", "=", "a")], limit=None,
        )
        assert [r["content"] for r in rows] == ["a"]
    finally:
        conn.close()


def test_query_records_rejects_unknown_field(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    import pytest
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            repository.insert_base_record(conn, record_group="key-events", content="a",
                                           file_path=None, created_at=1, updated_at=1)
        with pytest.raises(ValueError, match="unknown field"):
            repository.query_records(
                conn, record_group="key-events",
                conditions=[("nope", "=", "x")], limit=None,
            )
    finally:
        conn.close()
```

```python
# append to tests/pdata/test_service.py

def test_query_records_service_layer(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    service.schema_add_field(
        project="testproj", record_group="key-events", field_name="sent_at",
        sql_type="INTEGER", description=None, default=None,
    )
    # fields values are always strings (matching every --field k=v caller — the CLI can only
    # ever produce strings, and add_record's fields: Mapping[str, str] signature reflects
    # that). SQLite's own column affinity converts a bound TEXT '100' into the stored/compared
    # INTEGER 100 for an INTEGER-affinity column — confirmed by direct execution — so this
    # doesn't need Python-side int parsing anywhere in this plan.
    service.add_record(project="testproj", record_group="key-events", content="a",
                        file_path=None, fields={"sent_at": "100"}, created_at=1)
    service.add_record(project="testproj", record_group="key-events", content="b",
                        file_path=None, fields={"sent_at": "200"}, created_at=2)
    rows = service.query_records(
        project="testproj", record_group="key-events", where=["sent_at > 150"],
    )
    assert [r.content for r in rows] == ["b"]


def test_query_records_rejects_bad_where_syntax(monkeypatch, tmp_path):
    # Must be too few whitespace-separated tokens to match _WHERE_CLAUSE_RE at all (a single
    # word, with no op/value) so this actually exercises the "malformed clause" branch — not
    # the "invalid operator" branch already covered by test_query_records_rejects_bad_where_operator.
    # ("not a valid clause" would parse as field="not", op="A", value="valid clause" and raise
    # the invalid-operator error instead, since "A" is not a valid op — that string looked like
    # a syntax failure but wasn't one.)
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="malformed"):
        service.query_records(
            project="testproj", record_group="key-events", where=["singleword"],
        )


def test_query_records_rejects_bad_where_operator(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="operator"):
        service.query_records(
            project="testproj", record_group="key-events", where=["content ~= x"],
        )

# Note: query's --include-deleted default-exclusion behaviour is regression-tested in Task 16
# (test_query_records_excludes_soft_deleted_by_default / test_pdata_query_include_deleted),
# not here — it depends on service.delete_record, which doesn't exist until Task 16, matching
# this plan's existing precedent of deferring a test to the task that provides its dependency
# (see test_get_record_excludes_soft_deleted_by_default's "moved here from Task 12" note).
```

```python
# append to tests/test_ccst_pdata_cli.py

def test_pdata_query_with_where(base_env):
    _run(base_env, "pdata", "schema", "add-field", "--project", "testproj",
         "--group", "key-events", "--field", "sent_at:INTEGER")
    _run(base_env, "pdata", "add", "--project", "testproj", "--group", "key-events",
         "--content", "a", "--field", "sent_at=100")
    _run(base_env, "pdata", "add", "--project", "testproj", "--group", "key-events",
         "--content", "b", "--field", "sent_at=200")
    r = _run(base_env, "pdata", "query", "--project", "testproj", "--group", "key-events",
              "--where", "sent_at > 150", "--format", "json")
    assert r.returncode == 0, r.stderr
    import json
    parsed = json.loads(r.stdout)
    assert [row["content"] for row in parsed] == ["b"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pdata/test_repository.py tests/pdata/test_service.py tests/test_ccst_pdata_cli.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'query_records'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/cc_session_tools/lib/pdata/repository.py:

_WHERE_OPS = frozenset({"=", "!=", "<", ">", "<=", ">=", "LIKE"})
# Deliberately a subset of naming.BASE_RECORD_COLUMNS, not the whole set: id/record_group are
# already fixed by the surrounding SELECT/WHERE record_group=?, and version/deleted_at are
# concurrency/soft-delete internals a --where filter has no legitimate reason to target
# (deleted rows are already excluded by the r.deleted_at IS NULL clause below).
_BASE_QUERYABLE_COLUMNS = frozenset({"content", "file_path", "created_at", "updated_at"})


def query_records(
    conn: sqlite3.Connection,
    *,
    record_group: str,
    conditions: list[tuple[str, str, str]],
    limit: int | None,
    include_deleted: bool = False,
) -> list[sqlite3.Row]:
    """conditions is a list of (field, op, value) already syntax-checked by
    service._parse_where_clause; op is guaranteed in _WHERE_OPS. field is resolved against
    base columns first, then live extension columns — auto-LEFT-JOINing ext_<group> so a
    caller never writes a JOIN or names the table (spec §5).

    include_deleted mirrors list_base_records' flag (spec §4.5: "list/query/get exclude
    soft-deleted rows by default; --include-deleted shows them") — query is not exempt from
    that default just because it filters on --where instead of --since/--until."""
    has_ext = extension_table_exists(conn, record_group)
    ext_columns = set(list_extension_columns(conn, record_group)) if has_ext else set()
    table = naming.extension_table_name(record_group) if has_ext else None

    clauses = ["r.record_group=?"]
    if not include_deleted:
        clauses.append("r.deleted_at IS NULL")
    params: list[object] = [record_group]
    for field_name, op, value in conditions:
        if op not in _WHERE_OPS:
            raise ValueError(f"invalid operator {op!r}")
        if field_name in _BASE_QUERYABLE_COLUMNS:
            clauses.append(f'r."{field_name}" {op} ?')
        elif field_name in ext_columns:
            clauses.append(f'e."{field_name}" {op} ?')
        else:
            raise ValueError(
                f"unknown field {field_name!r} for group {record_group!r} "
                f"(not a base column or a registered extension field)"
            )
        params.append(value)

    join = f'LEFT JOIN "{table}" e ON e.record_id = r.id' if table else ""
    sql = f"SELECT r.* FROM records r {join} WHERE {' AND '.join(clauses)} ORDER BY r.id"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()
```

```python
# add to src/cc_session_tools/lib/pdata/service.py:

import re as _re

# op is deliberately \S+ here (not the literal alternation of valid ops) — matching only a
# literal alternation would make an invalid op (e.g. "~=") fail to match the whole regex at all,
# so the caller falls into the generic "malformed clause" branch and the dedicated "invalid
# operator" error below becomes unreachable dead code. Capturing any non-space token as op and
# checking membership afterward is what makes both error messages actually reachable.
_WHERE_CLAUSE_RE = _re.compile(
    r"^(?P<field>\S+)\s+(?P<op>\S+)\s+(?P<value>.+)$",
)


def _parse_where_clause(raw: str) -> tuple[str, str, str]:
    match = _WHERE_CLAUSE_RE.match(raw.strip())
    if not match:
        raise ValueError(
            f"malformed --where clause (want '<field> <op> <value>'): {raw!r}"
        )
    field_name = match.group("field")
    op = match.group("op").upper()
    value = match.group("value")
    if op not in repository._WHERE_OPS:
        raise ValueError(f"invalid --where operator {op!r}: {raw!r}")
    return field_name, op, value


def query_records(
    *, project: str, record_group: str, where: list[str], limit: int | None = None,
    include_deleted: bool = False,
) -> list[Record]:
    naming.validate_record_group(record_group)
    conditions = [_parse_where_clause(clause) for clause in where]
    conn = repository.connect(project)
    try:
        rows = repository.query_records(
            conn, record_group=record_group, conditions=conditions, limit=limit,
            include_deleted=include_deleted,
        )
        has_ext = repository.extension_table_exists(conn, record_group)
        records = []
        for row in rows:
            record = _row_to_record(row)
            if has_ext:
                ext_row = repository.get_extension_row(conn, record_group, row["id"])
                if ext_row is not None:
                    record.fields = {
                        k: ext_row[k] for k in ext_row.keys() if k != "record_id"
                    }
            records.append(record)
        return records
    finally:
        conn.close()
```

```python
# add to src/cc_session_tools/cli/ccst.py:

def _cmd_pdata_query(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import formatting, service

    try:
        records = service.query_records(
            project=args.project, record_group=args.group,
            where=args.where or [], limit=args.limit,
            include_deleted=args.include_deleted,
        )
    except ValueError as exc:
        print(f"ccst pdata: {exc}", file=sys.stderr)
        return 2
    print(formatting.render([service.record_to_dict(r) for r in records], fmt=args.format))
    return 0
```

```python
# add to pdata_sub in _build_parser():

    pdata_query_parser = pdata_sub.add_parser(
        "query", help="Query records with structured --where filters",
    )
    pdata_query_parser.add_argument("--project", required=True, metavar="NAME")
    pdata_query_parser.add_argument("--group", required=True, metavar="RECORD_GROUP")
    pdata_query_parser.add_argument(
        "--where", action="append", default=[], metavar="'<field> <op> <value>'",
        help="May repeat; clauses are ANDed. op is one of = != < > <= >= LIKE.",
    )
    pdata_query_parser.add_argument("--limit", type=int, default=None, metavar="N")
    pdata_query_parser.add_argument("--include-deleted", action="store_true")
    pdata_query_parser.add_argument(
        "--format", choices=("table", "json", "csv"), default="table",
    )
```

```python
# add to main() dispatch:

        if args.verb == "query":
            sys.exit(_cmd_pdata_query(args))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_repository.py tests/pdata/test_service.py tests/test_ccst_pdata_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/repository.py src/cc_session_tools/lib/pdata/service.py src/cc_session_tools/cli/ccst.py tests/pdata/test_repository.py tests/pdata/test_service.py tests/test_ccst_pdata_cli.py
git commit -m "feat(pdata): add ccst pdata query with auto-join --where filters"
```

---

## Task 15: `update` — optimistic concurrency + current-vs-attempted diff

**Files:**
- Modify: `src/cc_session_tools/lib/pdata/repository.py`
- Modify: `src/cc_session_tools/lib/pdata/service.py`
- Modify: `src/cc_session_tools/cli/ccst.py`
- Modify: `tests/pdata/test_repository.py`
- Modify: `tests/pdata/test_service.py`
- Modify: `tests/test_ccst_pdata_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/pdata/test_repository.py

def test_update_base_record_succeeds_with_correct_version(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            rid = repository.insert_base_record(conn, record_group="a", content="old",
                                                 file_path=None, created_at=1, updated_at=1)
        with repository._immediate(conn):
            updated = repository.update_base_record(
                conn, record_id=rid, expected_version=1, content="new",
                file_path=None, updated_at=2,
            )
        assert updated is True
        row = repository.get_base_record(conn, rid)
        assert row["content"] == "new"
        assert row["version"] == 2
        assert row["updated_at"] == 2
    finally:
        conn.close()


def test_update_base_record_returns_false_on_version_mismatch(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            rid = repository.insert_base_record(conn, record_group="a", content="old",
                                                 file_path=None, created_at=1, updated_at=1)
        with repository._immediate(conn):
            updated = repository.update_base_record(
                conn, record_id=rid, expected_version=99, content="new",
                file_path=None, updated_at=2,
            )
        assert updated is False
        row = repository.get_base_record(conn, rid)
        assert row["content"] == "old"  # untouched
        assert row["version"] == 1
    finally:
        conn.close()


def test_update_base_record_with_content_none_preserves_existing_file_path(monkeypatch, tmp_path):
    """Regression test: omitting --content/--file (passing None) must leave the existing
    column value untouched, not overwrite it with NULL — a content-only or file-only update is
    the normal case per spec §4.2's content+file_path record shape."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            rid = repository.insert_base_record(
                conn, record_group="a", content="old", file_path="a/original.md",
                created_at=1, updated_at=1,
            )
        with repository._immediate(conn):
            updated = repository.update_base_record(
                conn, record_id=rid, expected_version=1, content=None,
                file_path=None, updated_at=2,
            )
        assert updated is True
        row = repository.get_base_record(conn, rid)
        assert row["content"] == "old"
        assert row["file_path"] == "a/original.md"
        assert row["version"] == 2
    finally:
        conn.close()


def test_update_extension_row(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            repository.add_extension_column(conn, "key-events", "sender", "TEXT", default=None)
            rid = repository.insert_base_record(conn, record_group="key-events", content="x",
                                                 file_path=None, created_at=1, updated_at=1)
            repository.insert_extension_row(conn, "key-events", rid, {"sender": "alice"})
        with repository._immediate(conn):
            repository.update_extension_row(conn, "key-events", rid, {"sender": "bob"})
        row = repository.get_extension_row(conn, "key-events", rid)
        assert row["sender"] == "bob"
    finally:
        conn.close()
```

```python
# append to tests/pdata/test_service.py

def test_update_record_happy_path(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    service.schema_add_field(
        project="testproj", record_group="key-events", field_name="sender",
        sql_type="TEXT", description=None, default=None,
    )
    created = service.add_record(
        project="testproj", record_group="key-events", content="old",
        file_path=None, fields={"sender": "alice"}, created_at=1,
    )
    updated = service.update_record(
        project="testproj", record_id=created.id, expected_version=1,
        content="new", file_path=None, fields={"sender": "bob"}, updated_at=2,
    )
    assert updated.content == "new"
    assert updated.version == 2
    assert updated.fields["sender"] == "bob"


def test_update_record_conflict_raises_with_current_and_attempted(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    created = service.add_record(
        project="testproj", record_group="notes", content="old",
        file_path=None, fields={}, created_at=1,
    )
    with pytest.raises(service.VersionConflictError) as exc_info:
        service.update_record(
            project="testproj", record_id=created.id, expected_version=99,
            content="new", file_path=None, fields={}, updated_at=2,
        )
    assert exc_info.value.current["content"] == "old"
    assert exc_info.value.attempted["content"] == "new"


def test_update_record_missing_id_raises_not_found(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    with pytest.raises(service.RecordNotFoundError):
        service.update_record(
            project="testproj", record_id=999, expected_version=1,
            content="new", file_path=None, fields={}, updated_at=2,
        )


def test_update_record_omitting_file_preserves_existing_file_path(monkeypatch, tmp_path):
    """Regression test: a content-only update (--file omitted, i.e. file_path=None) must not
    silently null out a previously-set file_path."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    created = service.add_record(
        project="testproj", record_group="filings", content="old",
        file_path="filings/original.md", fields={}, created_at=1,
    )
    updated = service.update_record(
        project="testproj", record_id=created.id, expected_version=1,
        content="new", file_path=None, fields={}, updated_at=2,
    )
    assert updated.content == "new"
    assert updated.file_path == "filings/original.md"


def test_update_record_omitting_content_updates_only_file_path(monkeypatch, tmp_path):
    """Regression test: --content is optional per spec §5 — a file-only (or field-only) update
    must not require resending the existing content."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    created = service.add_record(
        project="testproj", record_group="filings", content="original content",
        file_path="filings/old.md", fields={}, created_at=1,
    )
    updated = service.update_record(
        project="testproj", record_id=created.id, expected_version=1,
        content=None, file_path="filings/new.md", fields={}, updated_at=2,
    )
    assert updated.content == "original content"
    assert updated.file_path == "filings/new.md"


def test_update_record_rejects_empty_update(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    created = service.add_record(
        project="testproj", record_group="notes", content="x",
        file_path=None, fields={}, created_at=1,
    )
    with pytest.raises(ValueError, match="at least one"):
        service.update_record(
            project="testproj", record_id=created.id, expected_version=1,
            content=None, file_path=None, fields={}, updated_at=2,
        )
```

```python
# append to tests/test_ccst_pdata_cli.py

def test_pdata_update_happy_path(base_env):
    r_add = _run(base_env, "pdata", "add", "--project", "testproj", "--group", "notes",
                  "--content", "old")
    record_id = r_add.stdout.strip()
    r_update = _run(base_env, "pdata", "update", "--project", "testproj", "--id", record_id,
                      "--version", "1", "--content", "new")
    assert r_update.returncode == 0, r_update.stderr
    r_get = _run(base_env, "pdata", "get", "--project", "testproj", "--id", record_id)
    assert "new" in r_get.stdout


def test_pdata_update_version_conflict_exits_3(base_env):
    r_add = _run(base_env, "pdata", "add", "--project", "testproj", "--group", "notes",
                  "--content", "old")
    record_id = r_add.stdout.strip()
    r_update = _run(base_env, "pdata", "update", "--project", "testproj", "--id", record_id,
                      "--version", "99", "--content", "new")
    assert r_update.returncode == 3
    assert "current" in r_update.stdout.lower() or "current" in r_update.stderr.lower()


def test_pdata_update_without_content_preserves_existing_content(base_env):
    """Regression test: --content is optional (spec §5) — a --file-only update must not require
    resending --content, and must not overwrite content as a side effect."""
    r_add = _run(base_env, "pdata", "add", "--project", "testproj", "--group", "filings",
                  "--content", "original", "--file", "filings/old.md")
    record_id = r_add.stdout.strip()
    r_update = _run(base_env, "pdata", "update", "--project", "testproj", "--id", record_id,
                      "--version", "1", "--file", "filings/new.md")
    assert r_update.returncode == 0, r_update.stderr
    r_get = _run(base_env, "pdata", "get", "--project", "testproj", "--id", record_id)
    assert "original" in r_get.stdout
    assert "filings/new.md" in r_get.stdout


def test_pdata_update_without_file_preserves_existing_file_path(base_env):
    """Regression test: a content-only update (--file omitted) must not silently null out a
    previously-set file_path."""
    r_add = _run(base_env, "pdata", "add", "--project", "testproj", "--group", "filings",
                  "--content", "old", "--file", "filings/keep.md")
    record_id = r_add.stdout.strip()
    r_update = _run(base_env, "pdata", "update", "--project", "testproj", "--id", record_id,
                      "--version", "1", "--content", "new")
    assert r_update.returncode == 0, r_update.stderr
    r_get = _run(base_env, "pdata", "get", "--project", "testproj", "--id", record_id)
    assert "filings/keep.md" in r_get.stdout


def test_pdata_update_rejects_empty_update(base_env):
    r_add = _run(base_env, "pdata", "add", "--project", "testproj", "--group", "notes",
                  "--content", "x")
    record_id = r_add.stdout.strip()
    r_update = _run(base_env, "pdata", "update", "--project", "testproj", "--id", record_id,
                      "--version", "1")
    assert r_update.returncode == 2
    assert "at least one" in r_update.stderr.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pdata/test_repository.py tests/pdata/test_service.py tests/test_ccst_pdata_cli.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'update_base_record'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/cc_session_tools/lib/pdata/repository.py:

def update_base_record(
    conn: sqlite3.Connection,
    *,
    record_id: int,
    expected_version: int,
    content: str | None,
    file_path: str | None,
    updated_at: int,
) -> bool:
    """UPDATE ... WHERE id=? AND version=?, bumping version by 1 (spec §6.2). Returns True iff
    exactly one row was updated; False means the version didn't match (someone else's write
    landed first) — caller (service.py) resolves the id-not-found-vs-conflict distinction by
    checking whether the row exists at all.

    content/file_path are each Optional per spec §5's `[--content "..."]  [--file <path>]` —
    both are optional on update, meaning "leave this field unchanged", not "clear it to NULL".
    COALESCE(?, content)/COALESCE(?, file_path) is what implements that: passing None reuses the
    existing on-disk value instead of overwriting it. Without the COALESCE, a content-only update
    (the common case — see spec §4.2's content+file_path record shape) would silently null out
    file_path on every call that omits --file, a G1 silent-data-loss bug."""
    cur = conn.execute(
        "UPDATE records SET content=COALESCE(?, content), file_path=COALESCE(?, file_path), "
        "updated_at=?, version=version+1 "
        "WHERE id=? AND version=? AND deleted_at IS NULL",
        (content, file_path, updated_at, record_id, expected_version),
    )
    return cur.rowcount == 1


def update_extension_row(
    conn: sqlite3.Connection, record_group: str, record_id: int, fields: Mapping[str, object],
) -> None:
    """Raises AssertionError if no ext_<group> row exists for record_id. This should be
    unreachable: ensure_extension_table backfills every pre-existing row when the extension
    table is first created (Task 7), and add_record/insert_extension_row create one for every
    new row from then on (Task 10) — so every records row in a group with an extension table
    has exactly one ext_<group> row (plan Decision 3). Asserting here turns any future
    regression of that invariant into a loud failure instead of a silent no-op that discards the
    field write (the bug this repository originally shipped with, caught in plan review before
    implementation — see plan Decision 3)."""
    if not fields:
        return
    table = naming.extension_table_name(record_group)
    assignments = ", ".join(f'"{k}"=?' for k in fields)
    cur = conn.execute(
        f'UPDATE "{table}" SET {assignments} WHERE record_id=?',
        (*fields.values(), record_id),
    )
    if cur.rowcount == 0:
        raise AssertionError(
            f"invariant violation: no {table} row for record_id={record_id} despite the "
            f"extension table existing — the base/extension 1:1 row invariant was broken "
            f"upstream (see plan Decision 3)"
        )
```

```python
# add to src/cc_session_tools/lib/pdata/service.py — note: record_to_dict() already exists
# from Task 12; this task's VersionConflictError.current/.attempted are built with that same
# function (not a second copy) so the CLI's conflict-diff dict and its ordinary get/list/query
# dicts always have the same shape.

class RecordNotFoundError(Exception):
    """Raised when a record id resolves to no row (or no active row)."""


class VersionConflictError(Exception):
    """Raised on an update()/delete() optimistic-concurrency conflict (spec §6.2). Carries the
    current on-disk row and what the caller attempted, both flattened dicts, for the CLI to
    render as a diff."""

    def __init__(self, current: Mapping[str, object], attempted: Mapping[str, object]):
        super().__init__(f"version conflict on record {current.get('id')}")
        self.current = current
        self.attempted = attempted


def update_record(
    *,
    project: str,
    record_id: int,
    expected_version: int,
    content: str | None,
    file_path: str | None,
    fields: Mapping[str, str],
    updated_at: int | None = None,
) -> Record:
    """content and file_path are each optional (spec §5's `[--content "..."]  [--file <path>]`)
    — omitting one (passing None) leaves that column unchanged; it does not clear it. At least
    one of content, file_path, or fields must be given, or this is a no-op update request that
    only bumps version/updated_at for nothing (this repo's coding standard: reject inputs that
    ask the system to do nothing)."""
    if content is None and file_path is None and not fields:
        raise ValueError(
            "ccst pdata update requires at least one of --content, --file, or --field"
        )
    _validate_relative_file_path(file_path)
    ts = updated_at if updated_at is not None else int(time.time())

    conn = repository.connect(project)
    try:
        existing = repository.get_base_record(conn, record_id)
        if existing is None or existing["deleted_at"] is not None:
            raise RecordNotFoundError(record_id)
        record_group = existing["record_group"]

        live_columns = set(repository.list_extension_columns(conn, record_group))
        unregistered = set(fields) - live_columns
        if unregistered:
            raise ValueError(
                f"unregistered field(s) for group {record_group!r}: "
                f"{sorted(unregistered)} — run 'ccst pdata schema add-field' first"
            )

        with repository._immediate(conn):
            ok = repository.update_base_record(
                conn, record_id=record_id, expected_version=expected_version,
                content=content, file_path=file_path, updated_at=ts,
            )
            if ok and fields:
                repository.update_extension_row(conn, record_group, record_id, fields)

        if not ok:
            current_row = repository.get_base_record(conn, record_id)
            assert current_row is not None
            # The existence/soft-delete check above ran before this _immediate block acquired
            # its write lock, so a concurrent soft-delete can land in that narrow window: the
            # UPDATE's own `AND deleted_at IS NULL` clause then affects 0 rows for a reason that
            # isn't actually a version mismatch. Re-check deleted_at here (now inside the lock,
            # so this read is race-free) and report the accurate error rather than always
            # assuming a conflict.
            if current_row["deleted_at"] is not None:
                raise RecordNotFoundError(record_id)
            current = record_to_dict(_row_to_record(current_row))
            ext_row = repository.get_extension_row(conn, record_group, record_id)
            if ext_row is not None:
                current.update({k: ext_row[k] for k in ext_row.keys() if k != "record_id"})
            # A None content/file_path means "unchanged" (see the docstring above) — reflect
            # what would actually have landed on disk in the conflict diff, not a misleading
            # literal None, by falling back to the pre-update existing value for display.
            attempted = {
                "id": record_id,
                "content": content if content is not None else existing["content"],
                "file_path": file_path if file_path is not None else existing["file_path"],
                **fields,
            }
            raise VersionConflictError(current=current, attempted=attempted)

        updated_row = repository.get_base_record(conn, record_id)
        assert updated_row is not None
        record = _row_to_record(updated_row)
        ext_row = repository.get_extension_row(conn, record_group, record_id)
        if ext_row is not None:
            record.fields = {k: ext_row[k] for k in ext_row.keys() if k != "record_id"}
        return record
    finally:
        conn.close()
```

```python
# add to src/cc_session_tools/cli/ccst.py:

def _cmd_pdata_update(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import formatting, service

    try:
        fields = dict(_parse_field_assignment(raw) for raw in (args.field or []))
        record = service.update_record(
            project=args.project, record_id=args.id, expected_version=args.version,
            content=args.content, file_path=args.file, fields=fields,
        )
    except service.RecordNotFoundError:
        print(f"ccst pdata: record not found: {args.id}", file=sys.stderr)
        return 1
    except service.VersionConflictError as exc:
        print(formatting.render_conflict_diff(exc.current, exc.attempted, fmt=args.format))
        return 3
    except ValueError as exc:
        print(f"ccst pdata: {exc}", file=sys.stderr)
        return 2
    print(f"updated record {record.id} (version {record.version})")
    return 0
```

```python
# add to pdata_sub in _build_parser():

    pdata_update_parser = pdata_sub.add_parser("update", help="Update a record (version-checked)")
    pdata_update_parser.add_argument("--project", required=True, metavar="NAME")
    pdata_update_parser.add_argument("--id", required=True, type=int)
    pdata_update_parser.add_argument("--version", required=True, type=int, dest="version",
                                      metavar="EXPECTED_VERSION")
    pdata_update_parser.add_argument(
        "--content", default=None,
        help="New content. Omit to leave content unchanged (at least one of --content, "
             "--file, --field is required)",
    )
    pdata_update_parser.add_argument(
        "--file", default=None, metavar="PATH",
        help="New relative file path. Omit to leave the existing file_path unchanged.",
    )
    pdata_update_parser.add_argument("--field", action="append", default=[], metavar="NAME=VALUE")
    pdata_update_parser.add_argument(
        "--format", choices=("table", "json"), default="table",
        help="Format used only for the conflict diff on a version mismatch",
    )
```

```python
# add to main() dispatch:

        if args.verb == "update":
            sys.exit(_cmd_pdata_update(args))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_repository.py tests/pdata/test_service.py tests/test_ccst_pdata_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/repository.py src/cc_session_tools/lib/pdata/service.py src/cc_session_tools/cli/ccst.py tests/pdata/test_repository.py tests/pdata/test_service.py tests/test_ccst_pdata_cli.py
git commit -m "feat(pdata): add ccst pdata update with optimistic-concurrency conflict diff"
```

---

## Task 16: `delete` / `restore` — soft delete

**Files:**
- Modify: `src/cc_session_tools/lib/pdata/repository.py`
- Modify: `src/cc_session_tools/lib/pdata/service.py`
- Modify: `src/cc_session_tools/cli/ccst.py`
- Modify: `tests/pdata/test_repository.py`
- Modify: `tests/pdata/test_service.py`
- Modify: `tests/test_ccst_pdata_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/pdata/test_repository.py

def test_soft_delete_sets_deleted_at_with_version_check(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            rid = repository.insert_base_record(conn, record_group="a", content="x",
                                                 file_path=None, created_at=1, updated_at=1)
        with repository._immediate(conn):
            ok = repository.soft_delete(conn, record_id=rid, expected_version=1, deleted_at=2)
        assert ok is True
        row = repository.get_base_record(conn, rid)
        assert row["deleted_at"] == 2
        assert row["version"] == 2
    finally:
        conn.close()


def test_soft_delete_returns_false_on_version_mismatch(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            rid = repository.insert_base_record(conn, record_group="a", content="x",
                                                 file_path=None, created_at=1, updated_at=1)
        with repository._immediate(conn):
            ok = repository.soft_delete(conn, record_id=rid, expected_version=99, deleted_at=2)
        assert ok is False
        row = repository.get_base_record(conn, rid)
        assert row["deleted_at"] is None
    finally:
        conn.close()


def test_restore_clears_deleted_at(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            rid = repository.insert_base_record(conn, record_group="a", content="x",
                                                 file_path=None, created_at=1, updated_at=1)
        with repository._immediate(conn):
            repository.soft_delete(conn, record_id=rid, expected_version=1, deleted_at=2)
        with repository._immediate(conn):
            repository.restore(conn, record_id=rid, restored_at=3)
        row = repository.get_base_record(conn, rid)
        assert row["deleted_at"] is None
        assert row["version"] == 3
    finally:
        conn.close()
```

```python
# append to tests/pdata/test_service.py

def test_get_record_excludes_soft_deleted_by_default(monkeypatch, tmp_path):
    """Moved here from Task 12: depends on delete_record, added in this task."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    created = service.add_record(
        project="testproj", record_group="notes", content="x", file_path=None,
        fields={}, created_at=1000,
    )
    service.delete_record(project="testproj", record_id=created.id, expected_version=1)
    assert service.get_record(project="testproj", record_id=created.id) is None
    assert service.get_record(
        project="testproj", record_id=created.id, include_deleted=True,
    ) is not None


def test_query_records_excludes_soft_deleted_by_default(monkeypatch, tmp_path):
    """Moved here from Task 14: depends on delete_record, added in this task. Regression test
    for query's --include-deleted default-exclude contract (spec §4.5: 'list/query/get exclude
    soft-deleted rows by default; --include-deleted shows them') — query is not exempt from
    that default just because it filters on --where instead of --since/--until."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    created = service.add_record(
        project="testproj", record_group="notes", content="x", file_path=None,
        fields={}, created_at=1,
    )
    service.delete_record(project="testproj", record_id=created.id, expected_version=1)

    visible = service.query_records(
        project="testproj", record_group="notes", where=["content = x"],
    )
    assert visible == []

    visible_with_deleted = service.query_records(
        project="testproj", record_group="notes", where=["content = x"],
        include_deleted=True,
    )
    assert [r.id for r in visible_with_deleted] == [created.id]


def test_delete_record_conflict(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    created = service.add_record(
        project="testproj", record_group="notes", content="x", file_path=None,
        fields={}, created_at=1000,
    )
    with pytest.raises(service.VersionConflictError):
        service.delete_record(project="testproj", record_id=created.id, expected_version=99)


def test_restore_record_makes_it_visible_again(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    created = service.add_record(
        project="testproj", record_group="notes", content="x", file_path=None,
        fields={}, created_at=1000,
    )
    service.delete_record(project="testproj", record_id=created.id, expected_version=1)
    service.restore_record(project="testproj", record_id=created.id)
    assert service.get_record(project="testproj", record_id=created.id) is not None
```

```python
# append to tests/test_ccst_pdata_cli.py

def test_pdata_delete_then_restore(base_env):
    r_add = _run(base_env, "pdata", "add", "--project", "testproj", "--group", "notes",
                  "--content", "x")
    record_id = r_add.stdout.strip()

    r_del = _run(base_env, "pdata", "delete", "--project", "testproj", "--id", record_id,
                  "--version", "1")
    assert r_del.returncode == 0, r_del.stderr

    r_get = _run(base_env, "pdata", "get", "--project", "testproj", "--id", record_id)
    assert r_get.returncode == 1

    r_restore = _run(base_env, "pdata", "restore", "--project", "testproj", "--id", record_id)
    assert r_restore.returncode == 0, r_restore.stderr

    r_get2 = _run(base_env, "pdata", "get", "--project", "testproj", "--id", record_id)
    assert r_get2.returncode == 0


def test_pdata_delete_version_conflict_exits_3(base_env):
    r_add = _run(base_env, "pdata", "add", "--project", "testproj", "--group", "notes",
                  "--content", "x")
    record_id = r_add.stdout.strip()
    r_del = _run(base_env, "pdata", "delete", "--project", "testproj", "--id", record_id,
                  "--version", "99")
    assert r_del.returncode == 3


def test_pdata_delete_rejects_bad_project_name(base_env):
    r = _run(base_env, "pdata", "delete", "--project", "../escape", "--id", "1",
              "--version", "1")
    assert r.returncode == 2


def test_pdata_restore_rejects_bad_project_name(base_env):
    r = _run(base_env, "pdata", "restore", "--project", "../escape", "--id", "1")
    assert r.returncode == 2


def test_pdata_query_include_deleted(base_env):
    """Moved here from Task 14: depends on `pdata delete`, added in this task."""
    r_add = _run(base_env, "pdata", "add", "--project", "testproj", "--group", "notes",
                  "--content", "gone")
    record_id = r_add.stdout.strip()
    _run(base_env, "pdata", "delete", "--project", "testproj", "--id", record_id,
         "--version", "1")

    import json
    r_default = _run(base_env, "pdata", "query", "--project", "testproj", "--group", "notes",
                       "--where", "content = gone", "--format", "json")
    assert json.loads(r_default.stdout) == []

    r_included = _run(base_env, "pdata", "query", "--project", "testproj", "--group", "notes",
                        "--where", "content = gone", "--include-deleted", "--format", "json")
    assert [row["content"] for row in json.loads(r_included.stdout)] == ["gone"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pdata/test_repository.py tests/pdata/test_service.py tests/test_ccst_pdata_cli.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'soft_delete'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/cc_session_tools/lib/pdata/repository.py:

def soft_delete(
    conn: sqlite3.Connection, *, record_id: int, expected_version: int, deleted_at: int,
) -> bool:
    """Same version-checked contract as update_base_record (spec §4.5/§6.2). Returns True iff
    the row was found, not already deleted, and had the expected version."""
    cur = conn.execute(
        "UPDATE records SET deleted_at=?, version=version+1 "
        "WHERE id=? AND version=? AND deleted_at IS NULL",
        (deleted_at, record_id, expected_version),
    )
    return cur.rowcount == 1


def restore(conn: sqlite3.Connection, *, record_id: int, restored_at: int) -> bool:
    """Clears deleted_at. No version check on restore (spec doesn't require one for restore —
    only delete/update are version-gated); bumps version so a concurrent restore+edit still
    shows up in the version history. Returns True iff a soft-deleted row was found."""
    cur = conn.execute(
        "UPDATE records SET deleted_at=NULL, updated_at=?, version=version+1 "
        "WHERE id=? AND deleted_at IS NOT NULL",
        (restored_at, record_id),
    )
    return cur.rowcount == 1
```

```python
# add to src/cc_session_tools/lib/pdata/service.py:

def delete_record(
    *, project: str, record_id: int, expected_version: int, deleted_at: int | None = None,
) -> None:
    ts = deleted_at if deleted_at is not None else int(time.time())
    conn = repository.connect(project)
    try:
        existing = repository.get_base_record(conn, record_id)
        if existing is None or existing["deleted_at"] is not None:
            raise RecordNotFoundError(record_id)

        with repository._immediate(conn):
            ok = repository.soft_delete(
                conn, record_id=record_id, expected_version=expected_version, deleted_at=ts,
            )
        if not ok:
            current_row = repository.get_base_record(conn, record_id)
            assert current_row is not None
            # Same race as update_record: the existence check above ran before this _immediate
            # block took its write lock, so a concurrent soft-delete in that window makes
            # soft_delete's own `AND deleted_at IS NULL` clause affect 0 rows. Re-check
            # deleted_at (race-free now that we hold the lock) so an already-deleted record
            # reports RecordNotFoundError rather than a misleading version conflict.
            if current_row["deleted_at"] is not None:
                raise RecordNotFoundError(record_id)
            current = record_to_dict(_row_to_record(current_row))
            attempted = {"id": record_id, "deleted_at": ts}
            raise VersionConflictError(current=current, attempted=attempted)
    finally:
        conn.close()


def restore_record(*, project: str, record_id: int, restored_at: int | None = None) -> None:
    ts = restored_at if restored_at is not None else int(time.time())
    conn = repository.connect(project)
    try:
        existing = repository.get_base_record(conn, record_id)
        if existing is None or existing["deleted_at"] is None:
            raise RecordNotFoundError(record_id)
        with repository._immediate(conn):
            repository.restore(conn, record_id=record_id, restored_at=ts)
    finally:
        conn.close()
```

```python
# add to src/cc_session_tools/cli/ccst.py:

def _cmd_pdata_delete(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import formatting, service

    try:
        service.delete_record(
            project=args.project, record_id=args.id, expected_version=args.version,
        )
    except service.RecordNotFoundError:
        print(f"ccst pdata: record not found: {args.id}", file=sys.stderr)
        return 1
    except service.VersionConflictError as exc:
        print(formatting.render_conflict_diff(exc.current, exc.attempted, fmt="table"))
        return 3
    except ValueError as exc:
        print(f"ccst pdata: {exc}", file=sys.stderr)
        return 2
    print(f"deleted record {args.id}")
    return 0


def _cmd_pdata_restore(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import service

    try:
        service.restore_record(project=args.project, record_id=args.id)
    except service.RecordNotFoundError:
        print(f"ccst pdata: record not found (or not deleted): {args.id}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"ccst pdata: {exc}", file=sys.stderr)
        return 2
    print(f"restored record {args.id}")
    return 0
```

```python
# add to pdata_sub in _build_parser():

    pdata_delete_parser = pdata_sub.add_parser("delete", help="Soft-delete a record (version-checked)")
    pdata_delete_parser.add_argument("--project", required=True, metavar="NAME")
    pdata_delete_parser.add_argument("--id", required=True, type=int)
    pdata_delete_parser.add_argument("--version", required=True, type=int,
                                      metavar="EXPECTED_VERSION")

    pdata_restore_parser = pdata_sub.add_parser("restore", help="Clear a soft-delete")
    pdata_restore_parser.add_argument("--project", required=True, metavar="NAME")
    pdata_restore_parser.add_argument("--id", required=True, type=int)
```

```python
# add to main() dispatch:

        if args.verb == "delete":
            sys.exit(_cmd_pdata_delete(args))
        if args.verb == "restore":
            sys.exit(_cmd_pdata_restore(args))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_repository.py tests/pdata/test_service.py tests/test_ccst_pdata_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/repository.py src/cc_session_tools/lib/pdata/service.py src/cc_session_tools/cli/ccst.py tests/pdata/test_repository.py tests/pdata/test_service.py tests/test_ccst_pdata_cli.py
git commit -m "feat(pdata): add ccst pdata delete/restore soft-delete"
```

---

## Task 17: Full suite + version bump + CHANGELOG

**Files:**
- Modify: `pyproject.toml`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS, 0 failures. This is the check step, not a red step — everything through Task 16
should already be green; this confirms no cross-task regressions (e.g. `ccst.py`'s module
docstring/help text, or another noun's dispatch block, accidentally broken by the pdata
additions).

- [ ] **Step 2: Run `mypy --strict` on the new package**

Run: `uv run mypy --strict src/cc_session_tools/lib/pdata/ src/cc_session_tools/cli/ccst.py`
Expected: no errors (per this repo's CLAUDE.md, `strict: true`/`mypy --strict` is a build-failure
gate, not optional — fix any type errors surfaced here before proceeding, using precise types
rather than casts or `Any`).

- [ ] **Step 3: Bump the version (minor, per the Versioning section above)**

```toml
# pyproject.toml
[project]
name = "cc-session-tools"
version = "1.1.0"
```

- [ ] **Step 4: Add the CHANGELOG entry**

```markdown
# CHANGELOG.md — insert under "## [Unreleased]", above the existing "### Fixed" section (or
# retitle [Unreleased] to "## [1.1.0]" per this repo's existing release-cut convention if this
# is being cut as part of a release rather than staged — follow whatever the repo's current
# [Unreleased] state is at the time this task actually runs).

### Added

- **`ccst pdata` — per-project SQLite data store CLI.** New `records`/`schema` subcommands
  (`add`, `get`, `list`, `query`, `update`, `delete`, `restore`, `schema list`, `schema show`,
  `schema add-field`) operate on one SQLite `.db` per project under
  `~/.local/share/claude/project-db/<project>.db`. Every record lives in a `record_group`
  (validated lowercase-hyphenated name); an optional per-group extension table gives structured
  fields real typed/indexed columns without a CCST source change (`schema add-field`).
  `update`/`delete` use optimistic concurrency (`--version`) and surface a current-vs-attempted
  diff on conflict instead of silently overwriting or retrying. This is Plan A of the
  per-project data-store feature — `ccst pdata init`/migration, the `pm-`-prefixed skills, and
  `ccst pdata verify`/`export` are deferred to later plans (see
  `docs/superpowers/plans/2026-07-30-ccst-pdata-core.md`'s Scope section).
```

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "chore(pdata): bump to 1.1.0 for ccst pdata core"
```

---

## Post-plan note for whoever picks up Plan B (migration/init)

This plan deliberately leaves `ccst pdata init` unimplemented. Before starting that plan, re-read
this plan's "Necessary implementation decisions beyond the spec's literal text" section — in
particular the 1:1 base/extension-row invariant (Decision 3) and the project-name path-safety
check (Decision 1), both of which the migration/init logic must preserve.
