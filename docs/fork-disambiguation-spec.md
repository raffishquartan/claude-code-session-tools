# Spec + impl plan: `ccl`/`ccs` must list forked sessions separately

Task #111. Chosen approach: full fix (make `sessions.db` uuid-aware), not the lightweight
read-the-transcripts-directly option.

## Root cause (confirmed by reading `src/cc_session_tools/lib/sessions_db.py` in full)

The `sessions` table's primary key is `(project_dir, basename)`:

```sql
CREATE TABLE IF NOT EXISTS sessions (
    project_dir   TEXT NOT NULL,
    basename      TEXT NOT NULL,
    start_date    TEXT NOT NULL,
    last_opened   REAL,
    last_active   REAL,
    discovered_at TEXT NOT NULL,
    PRIMARY KEY (project_dir, basename)
);
```

`basename` is the `cc-sessions/<tag>/` directory name - not a session uuid. Ctrl-L-Ctrl-L forking
produces a second live session (new uuid, new `.jsonl` transcript under
`~/.claude/projects/<encoded-cwd>/`) that keeps the *same* tag/basename. Every write path
(`ensure_session_row`, `touch_last_opened`, `touch_last_active`) upserts on
`ON CONFLICT(project_dir, basename) DO UPDATE ...` - so the second fork's SessionStart/Stop hook
just overwrites the first fork's timestamps in the same row. There is structurally no way for two
forks to coexist as separate rows today. `ccs.py`'s list-mode (`_collect_session_rows` →
`sessions_db.list_sessions`) can therefore never show more than one entry per tag, regardless of
how many live transcripts share it. This is a data-model gap, not a display bug.

`session_tags` (separate table, PK on `uuid`) is already uuid-keyed and CAN represent multiple
uuids sharing one tag - it's only the `sessions` table (the one `ccs`/`ccl` actually list from)
that's the bottleneck.

## Target schema

```sql
CREATE TABLE IF NOT EXISTS sessions (
    project_dir   TEXT NOT NULL,
    basename      TEXT NOT NULL,
    uuid          TEXT NOT NULL,
    start_date    TEXT NOT NULL,
    last_opened   REAL,
    last_active   REAL,
    discovered_at TEXT NOT NULL,
    PRIMARY KEY (project_dir, basename, uuid)
);
CREATE INDEX IF NOT EXISTS idx_sessions_basename    ON sessions(basename);
CREATE INDEX IF NOT EXISTS idx_sessions_start_date  ON sessions(start_date);
CREATE INDEX IF NOT EXISTS idx_sessions_last_active ON sessions(last_active);
CREATE INDEX IF NOT EXISTS idx_sessions_last_opened ON sessions(last_opened);
-- new: fast "how many uuids share this (project_dir, basename)" lookup for ccl's fork detection
CREATE INDEX IF NOT EXISTS idx_sessions_proj_basename ON sessions(project_dir, basename);
```

`SessionRow` gains a `uuid: str` field.

## Write-path changes

`ensure_session_row`, `touch_last_opened`, `touch_last_active` each gain a required `uuid: str`
keyword-only parameter, threaded into the INSERT/ON CONFLICT clause (now conflicting on the
3-column PK, so two forks' upserts land in two distinct rows instead of clobbering each other).

**Before implementing:** audit every call site -
`grep -rn "ensure_session_row\|touch_last_opened\|touch_last_active" src/` - each caller (the
SessionStart/Stop hooks, `ccd.py`'s safety-net call mentioned in `ensure_session_row`'s docstring)
already receives `session_id` in its hook stdin payload (confirmed pattern: both `catchup.py` and
`messaging_deliver.py` read `data.get("session_id", ...)` from stdin) - it's a threading exercise,
not a missing-data problem. Do this audit first; do not guess the call sites from this doc alone.

## Read-path / `ccs.py` changes

- `row_by_basename = {row.basename: row for row in session_rows}` (ccs.py, list mode) must become
  `rows_by_basename: dict[str, list[SessionRow]] = defaultdict(list)` - one basename can now map
  to N rows.
- The `sessions: list[tuple[Path, Path]]` construction must expand to one display entry per
  `(basename, uuid)` pair when N > 1, not one per basename.
- Per-uuid disambiguation info: resolve each row's own transcript file directly -
  `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl` (the standard transcript path pattern seen
  throughout this project) - and use *that* file's size (`_format_size`, already exists at
  ccs.py:449) and mtime for display, not the shared `cc-sessions/<basename>/` directory stat.
  This is the actual "transcript size / timestamp" info Chris asked for - it must come from the
  uuid-specific file, not the tag-level directory, or forks would still look identical.
- Display format for a forked tag (exact wording is an implementation-time judgement call, but the
  content must include): the shared tag, then per-fork uuid (or a short prefix), transcript size,
  and last-active/last-opened timestamp for each fork, clearly distinguishing them.
- `find_exact(basename)` naturally returns multiple rows now (it already returns `list[SessionRow]`
  - no signature change, just more realistic multi-row results going forward).

## Migration

Extend the existing `cli/migrate_sessions_db.py` (already the established pattern for this file,
per its own module - reuse it, don't build a parallel migration path).

**Hardest open decision - resolve this first, before writing any migration code:** existing
`sessions` rows have no uuid. For each `(project_dir, basename)` row, the migration must find
*which* uuid(s) it corresponds to by scanning `~/.claude/projects/<encoded-project_dir>/*.jsonl`
for transcripts whose tag (via `session_tag()` / `session_tag_from_relpath()` in
`lib/sessions.py` - read those functions' exact contract before relying on this, not confirmed in
this investigation) matches the row's `basename`. Two outcomes:
- Exactly one matching transcript found → backfill `uuid` directly, trivial case.
- Zero or >1 matches found → do NOT guess. Flag the row (e.g. a `_migration_ambiguous` sidecar
  table, or a synthetic placeholder uuid like `"unknown"` that a doctor check can find and report)
  rather than silently picking one and losing the others - this is precisely the "silent loss"
  failure mode Chris is trying to eliminate by asking for this feature in the first place.

## `ccst doctor` guard (required by this repo's version policy)

Per `.claude/CLAUDE.md`'s "Version policy": any change making existing on-disk data unreadable by
old code until migrated is a **major** bump, and must ship a `ccst doctor` check that FAILs (not
WARNs) when the migration hasn't run. Follow the existing pattern already visible in this
project's own `ccst doctor --drift` output (`"migration-to-1.0.0:sessions ... old files remain"`)
- add an equivalent `migration-to-<new-major>:sessions-uuid` check.

## Test plan (TDD - write these first, per this repo's established convention)

1. Schema: a fresh DB has the 3-column PK; inserting the same `(project_dir, basename)` with two
   different uuids produces two rows, not one overwrite.
2. `touch_last_opened`/`touch_last_active`/`ensure_session_row`: each requires uuid; two calls with
   different uuids for the same basename never clobber each other.
3. `list_sessions`: returns both rows for a forked basename; `find_exact` likewise.
4. `ccs.py` list mode: a fixture with two `SessionRow`s sharing a basename produces two printed
   entries, each with its own size/timestamp sourced from its own transcript file (not the shared
   directory).
5. Migration: unambiguous single-transcript case backfills correctly; ambiguous case is flagged,
   not silently resolved; re-running the migration is idempotent.
6. `ccst doctor`: fails (not warns) pre-migration, passes post-migration.

## Suggested order of work

1. Resolve the "hardest open decision" above (read `lib/sessions.py`'s tag-resolution functions
   properly - this investigation did not confirm their exact contract).
2. Schema + write-path changes + their tests (steps 1-2 of the test plan).
3. Migration script + its tests (step 5).
4. `ccst doctor` guard + its test (step 6).
5. `ccs.py` display changes + their tests (step 4).
6. Full suite green, version bump (major), changelog entry.

## Why this wasn't implemented in this session

This turn's investigation confirmed the root cause and designed the fix, but did not implement it.
Reasons: (a) this session's context is already very large (the context-window warnings were
silenced via `/context-override` a few turns ago specifically because of it, not because the
underlying pressure went away); (b) this is a real schema migration under this repo's own
version-policy rules (major bump, mandatory doctor guard) - exactly the kind of multi-step work
this project's own conventions say should go through TDD/a written plan before code, not be
freehanded under a loaded context. Recommend starting a fresh session (new context) with this file
as the starting prompt, per the "one session per phase, self-contained handoff prompt" pattern.
