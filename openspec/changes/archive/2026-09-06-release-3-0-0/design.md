## Context

See `proposal.md` - Why for the motivating bug. The rest of this section is the code-level state
this design builds on, confirmed by direct reading during this session (not re-derived from the
gap-review file's citations, though they match):

- Current `sessions` table DDL (`src/cc_session_tools/lib/sessions_db.py:37-45`):
  `PRIMARY KEY (project_dir, basename)`, with an `updated_at TEXT` column already present
  (added via `_BACKFILL_COLUMNS`, `sessions_db.py:87-93` - the original
  `docs/fork-disambiguation-spec.md`'s own DDL quotes omit this column; it must not be dropped).
- `db.py` already provides everything a one-shot migration needs generically: `MIGRATIONS_DDL`
  (a `migrations(name, applied_at)` table, already appended to `sessions.db`'s DDL),
  `record_migration()` (`db.py:111-116`), `backup_to()` (`db.py:176-201`, safe against concurrent
  writers via SQLite's own backup API), and `checkpoint()` (`db.py:171-173`). No new generic
  infrastructure is needed - this migration is the first to combine them with a PK rebuild.
- `install_sync.is_auto_sync_exempt()` (`install_sync.py:139-172`) exempts by top-level noun
  (`install-everything`, `doctor`, `repair`, `migrate`) or verb (`install`, `uninstall`).
  **Confirmed by reading `ccst.py`'s argparse wiring**: `ccst sessions migrate`/`ccst sessions
  list` have noun=`"sessions"`, verb=`"migrate"`/`"list"` - `"sessions"` is not in
  `_EXEMPT_NOUNS` today, so *every* `ccst sessions ...` subcommand (including the existing
  `sessions migrate`) currently runs `ensure_synced()` first. This is a real, pre-existing gap,
  not hypothetical - see Decision 8.
- `_check_db_not_locked()` (`lib/pdata/init_service.py:187-236`) is the concurrency-guard
  precedent: a throwaway `BEGIN IMMEDIATE`/`ROLLBACK` on `busy_timeout=0`, raising a clear error
  if another connection holds the write lock. Reused verbatim in shape for `sessions.db`.
- `verify._check_manifest_missing_with_evidence()` (`lib/pdata/verify.py:97-136`) is the
  detection precedent: distinguishes "genuinely never happened" from "happened, evidence now
  missing" using a signal that can only exist if the flow actually ran - not a row count.
  `doctor.check_pending_data_store_migration()` (`doctor.py:568-670`) is a different shape (keys
  on legacy files, `doctor.py:601-635`) that does not transfer here, confirmed by reading it in
  full: this migration has no legacy source outside `sessions.db` itself, so a copy would
  evaluate every machine as `OK` unconditionally.
- `claude_code_usage.session_names.load_jsonl_titles()` (`src/claude_code_usage/session_names.py:73-90`)
  already returns `{session_uuid: full_basename}` from each transcript's `custom-title` record -
  this is the backfill source (Decision 5).
- Real call sites for `ensure_session_row`/`touch_last_opened`/`touch_last_active`, confirmed by
  `grep -rn` (not the original spec's 2-site guess): `src/hooks/session_tag.py:120`,
  `src/hooks/after_response.py:36`, `cli/migrate_sessions_db.py:106/108/110`, `cli/ccd.py:201`,
  `skills/move-session/scripts/move_session.py:579/589/591`, plus test fixtures. Additional
  uuid-aware sites beyond these three functions: `skills/move-session/scripts/move_session.py:576-592`,
  `sessions_db.delete_session_row()` (`sessions_db.py:399-413`), `cli/ccr.py:118-129`
  (`find_exact`/disambiguation), `lib/sessions_repair.py:34-36,103-106`, `cli/ccst.py:1950-1983`
  (`_cmd_sessions_list`), `lib/sessions.py:59-78` (`find_matching_sessions`),
  `lib/session_gc.py:167-181`.

## Goals / Non-Goals

**Goals:**
- Every gap in `fork-disambig-migration-gap-review.md`'s numbered list (20 items) is resolved by
  an explicit decision below, or the decision states why it does not apply.
- A migration that is safe to run against a live, multi-project, multi-year-old installation:
  backed up, concurrency-guarded, idempotent, and leaves no phantom-fork state on ambiguous input.
- `ccst doctor` correctly reports FAIL only for "upgraded but not migrated", never for a fresh
  install, and never silently reports OK for "migrated then corrupted".

**Non-Goals:**
- Cross-machine coordination or syncing `sessions.db` itself - it stays machine-local by existing
  design (Decision 11); each machine's operator runs the migration independently.
- A GUI or interactive wizard for resolving ambiguous rows - `ccst repair sessions --uuid` is a
  scriptable CLI verb, matching the existing `_cmd_repair_sessions` shape.
- Changing `session_tags`' schema - it is already uuid-keyed and unaffected.

## Decisions

Numbered to match the gap-review file's "Gap list" (section 5) exactly, so every gap has a
traceable resolution. All 20 are addressed; none are deferred to Open Questions.

**1-2. Detection: two-signal design.**
Add `SESSIONS_UUID_MIGRATION = "sessions-uuid-pk"` next to `LEGACY_FLAT_FILE_MIGRATION`
(`sessions_db.py`). Add `sessions_schema_is_uuid_keyed(conn) -> bool`, reading
`PRAGMA table_info(sessions)` and checking the `pk` ordinal column (index 5) for
`project_dir=1, basename=2, uuid=3` (all three present and in that order - not merely "a uuid
column exists"). New doctor check `migration-to-3.0.0:sessions-uuid`, following the four-state
table from the gap review:

| PK is 3-col | marker present | status | reason text |
|---|---|---|---|
| no | no | FAIL | "sessions.db has not been migrated to the fork-aware schema; run `ccst sessions migrate-uuid` from a plain terminal with no other `claude` session running" |
| no | yes | FAIL | "sessions.db's migration marker is present but the schema is not 3-column-keyed - the schema was reverted or the marker was copied from another file; back up the current file and re-run `ccst sessions migrate-uuid`" |
| yes | no | OK | fresh install (3.0.0's own DDL) - see below |
| yes | yes | OK | migrated |

Modeled on `verify._check_manifest_missing_with_evidence()`, not
`check_pending_data_store_migration()` - the latter's legacy-file-count signal does not exist
for this migration (confirmed above), so it is not used at all here, only the two-signal
approach. **Fresh-install handling**: the DDL creation path (`sessions_db.connect()`, when it
runs `CREATE TABLE` for a file that did not exist before the call) records
`SESSIONS_UUID_MIGRATION` immediately after creating the table, in the same connection/transaction
as the DDL - so a brand-new file is never in the "3-col PK, no marker" (WARN/ambiguous) state at
all; it is created already fully in the "OK" state. This is simpler than the gap review's
suggested WARN row for that state, and removes it as a reachable outcome entirely for anything
created by 3.0.0+ code.

**3-4. Migration is a full table rebuild; DDL must keep `updated_at`.**
The migration performs, inside one transaction with `PRAGMA foreign_keys=OFF` for the
transaction's duration:
1. `db.checkpoint()` (WAL truncate) before starting.
2. `db.backup_to()` the live file to
   `~/.local/share/claude/migration-backups/sessions-pre-3.0.0-<UTC timestamp>.db` (matching the
   existing `migration-backups/` convention from `migrate_sessions_db.py:280`).
3. `CREATE TABLE sessions_new (project_dir TEXT NOT NULL, basename TEXT NOT NULL, uuid TEXT NOT
   NULL, start_date TEXT NOT NULL, last_opened REAL, last_active REAL, discovered_at TEXT NOT
   NULL, updated_at TEXT, PRIMARY KEY (project_dir, basename, uuid))` - `updated_at` included,
   fixing the spec's DDL bug.
4. `INSERT INTO sessions_new SELECT project_dir, basename, <resolved uuid>, start_date,
   last_opened, last_active, discovered_at, updated_at FROM sessions` - one row per resolved
   uuid; ambiguous rows go to a sidecar instead (Decision 7).
5. `DROP TABLE sessions`; `ALTER TABLE sessions_new RENAME TO sessions`; recreate all five
   indexes from the target DDL (`idx_sessions_basename`, `idx_sessions_start_date`,
   `idx_sessions_last_active`, `idx_sessions_last_opened`, new `idx_sessions_proj_basename`).
6. `db.record_migration(conn, SESSIONS_UUID_MIGRATION, applied_at=<now>)`, commit.
7. `db.checkpoint()` after.
A test asserts `updated_at` values survive the rebuild unchanged (gap 4).
`db.add_missing_columns()` is explicitly not used for this migration - it cannot alter a PK, and
the module docstring for the new migration function states this so a future editor does not
"simplify" it into a no-op.

**5. Delete the wrong `session_tag_from_relpath()` citation.**
It lives at `lib/pdata/session_output.py:95` and is unrelated to transcripts; not referenced
anywhere in this design or the implementation.

**6. Backfill source: `load_jsonl_titles()`, not a tag-join.**
The migration scans each project's `~/.claude/projects/<encoded-project_dir>/*.jsonl` via
`load_jsonl_titles()`, keyed by `custom_title == basename`. For a `(project_dir, basename)` row:
zero matching uuids -> ambiguous (Decision 7); exactly one -> backfilled directly; more than one
-> ambiguous, and (per the gap review's measured motivating case) is very likely a genuine fork -
each matching uuid becomes its own new row rather than being flagged as one ambiguous row, since
a fork's whole point is to have multiple rows. The `session_tags` label-join is not used at all -
measured 6x worse on live data and cited only as background.

**7. Placeholder reconciliation, not a bare `"unknown"` uuid.**
Rows the backfill cannot resolve (zero transcripts found) go into a new sidecar table:
```sql
CREATE TABLE IF NOT EXISTS sessions_migration_ambiguous (
    project_dir TEXT NOT NULL,
    basename    TEXT NOT NULL,
    reason      TEXT NOT NULL,      -- "no-transcript-found"
    start_date  TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    flagged_at  TEXT NOT NULL,
    PRIMARY KEY (project_dir, basename)
);
```
These rows are *not* copied into `sessions` at all during the rebuild (so no placeholder uuid
value ever occupies the new PK) - they exist only in the sidecar until reconciled. Reconciliation
happens automatically on the next real write: `ensure_session_row`/`touch_last_opened` (now
uuid-aware) check the sidecar first; if a `(project_dir, basename)` row exists there, they
`DELETE` it and proceed with a normal insert into `sessions` using the real uuid - so the session
is simply "discovered late" rather than ever appearing as a duplicate fork. `ccst repair sessions
--uuid` (Decision new-verb below) lists and can force-delete stale sidecar rows (e.g. for
sessions whose transcripts were later GC'd and will never resolve).

**8. Pre-migration backup.** Covered in Decision 3-4 step 2.

**9. Concurrency guard.**
New `_check_sessions_db_not_locked()` in `cli/migrate_sessions_db.py`, structurally identical to
`init_service._check_db_not_locked()`: throwaway connection, `busy_timeout=0`, `BEGIN
IMMEDIATE`/`ROLLBACK`, raising with "another process appears to be using sessions.db right now -
close any running `claude` sessions, wait for it to finish, or confirm it isn't stuck, then
re-run" if the probe fails to acquire the lock. Called before step 1 of the rebuild.

**10. Dry-run and confirmation gate.**
`ccst sessions migrate-uuid --dry-run` (default when neither `--dry-run` nor `--write` is passed,
matching `init_service.dry_run()`'s "show first" precedent) prints, per project: resolved count,
ambiguous count and their basenames, and the backup path that would be written. `--write` performs
the concurrency check, backup, and rebuild. No separate interactive confirmation prompt beyond
requiring the explicit `--write` flag - this matches this repo's other one-shot migration verbs
(`ccst migrate ccsched/ccmsg/telemetry --dry-run`/no-flag-writes pattern) rather than inventing a
new interactive-confirmation UX for just this one command.

**11. Call-site audit - full list, with the 3 semantic decisions made explicitly:**
| Site | Resolution |
|---|---|
| `move_session.py:576-592` (`sync_sessions_table`) | **Correction made during implementation** (openspec-apply-change, task 4.5): moves only the **one** row matching the uuid `discover_session_jsonl()` actually resolved for this invocation, via a new `delete_session_row_exact(project_dir, basename, uuid)` - not every fork sharing the basename. `move_session.py` operates on exactly one resolved transcript per run; a sibling fork's transcript is untouched by the move, so re-keying its `sessions` row too would point it at a project_dir its real `.jsonl` never moved to. The originally-stated "moves all forks together" design was wrong for this call site specifically - it assumed move_session.py moved the whole fork set, which it does not. |
| `sessions_db.delete_session_row()` | Deletes **all** rows for `(project_dir, basename)` - correct here because `delete-sessions` deletes the whole shared `cc-sessions/<basename>/` directory and every fork's transcript with it, unlike a move. `delete_session_row_exact()` (new, single-uuid) is the one `move_session.py` uses instead. |
| `cli/ccr.py:118-129` / `lib/sessions.py:59-78` (`find_matching_sessions`) | **Correction made during implementation** (task 4.4): reading `ccr.py` in full showed it already has a *file-based* fork-disambiguation mechanism independent of sessions_db rows - `find_all_jsonls_for_session()` + `_pick_duplicate_transcript()` (`ccr.py:50-79`, `108-256`) - which fires once a single basename is chosen and correctly disambiguates by reading the actual transcript files (size/date per candidate), already exercised by this repo's existing ccr tests. The bug that actually existed was upstream of that: `find_matching_sessions` returned one `SessionMatch` per sessions.db row, so a forked basename produced two *indistinguishable* entries (identical basename and project_dir) in `ccr`'s fragment-match picker/list - a confusing double-disambiguation UX, not a resume-the-wrong-thing bug. Fixed by deduplicating `find_matching_sessions` to one `SessionMatch` per `(project_dir, basename)`, leaving the existing file-based mechanism as the sole (and sufficient) disambiguation step. `find_exact()`'s exact-match fast path (`ccr.py:118-129`) needed no change: it already takes the first matching row via `break`, which is correct here since every fork of a basename shares the same `project_dir`/`session_dir`. |
| `lib/sessions_repair.py:34-36,103-106` | Dedupe key becomes `(project_dir, basename, uuid)`, matching the new PK - stops treating two real forks as duplicates of each other. |
| `cli/ccst.py:1950-1983` (`sessions list`) | Adds a `uuid` field to both the table and `--json` output; two forks print as two distinguishable lines instead of two identical-looking ones. |
| `lib/sessions.py:59-78` (`find_matching_sessions`) | Returns one `SessionMatch` per row (already does, no signature change) - now naturally surfaces both forks to `ccr`/`ccs` instead of being unreachable in practice. |
| `lib/session_gc.py:167-181` | Left as-is for this change - GC'ing `sessions` rows (vs. only `session_tags`) is a separate policy decision with its own risk profile (a GC'd `sessions` row is a bigger behavior change than a GC'd tag cache entry) and is out of scope here; the stale comment at `session_gc.py:168-169` is updated to note the table is now uuid-keyed but GC behavior is unchanged, rather than silently left describing the pre-migration schema. |

**12. Display list vs. filesystem work list.**
`ccs.py`'s filesystem work list (content search, `--order-by update`'s rglob,
`ccs.py:1153-1155`) continues to iterate one entry per **basename** (unchanged - two forks still
share one `cc-sessions/<basename>/` directory, so scanning it once is correct and sufficient).
Only the **display** construction changes: a new `rows_by_basename: dict[str, list[SessionRow]]`
built once, consumed at render time to expand each basename into N display lines (one per fork)
without duplicating the underlying filesystem-list entries that feed search/ordering. All three
existing `row_by_basename` consumers (`ccs.py:1156`, `1242`, `1328-1332`) are updated to use the
list-valued map and iterate its values where a display line is produced, but the single
filesystem-list entry per basename is untouched.

**13. Fork whose transcript no longer exists.**
If a fork's `<uuid>.jsonl` is missing at display time (GC'd, or deleted by the `delete-sessions`/
`clean-hook-sessions` skills), the display shows that fork's row with its last known
last-active/last-opened timestamp and `(transcript missing)` in place of a size - it is not
hidden, since the row itself still exists and hiding it would silently under-count forks again.
`session_gc.py`'s "NOT the `sessions` table" comment (line 168-169) is corrected to state the
table is uuid-keyed as of 3.0.0 but is still excluded from GC (per Decision 11's `session_gc.py`
row - a deliberate scope boundary, not an oversight).

**14. Migration verb: its own, not folded into `ccst sessions migrate`.**
New verb `ccst sessions migrate-uuid` (dry-run by default, `--write` to execute), separate from
the existing `ccst sessions migrate` (which migrates flat tag-cache/activity-sentinel/mutes files
into `sessions.db` - a different one-shot migration with an unrelated source and an existing,
accurate docstring that should not be overloaded with new behavior). **Not** added to `ccst
migrate all` - that command's docstring and its four registered migrations (`ccsched`, `ccmsg`,
`telemetry`, and `sessions`-meaning-the-flat-file-import) all describe "flat-file legacy source ->
new store" migrations; this migration has no flat-file source and is a schema rebuild of a store
that already exists, a different enough shape that lumping it into `migrate all`'s single summary
line would blur what actually ran. `ccst doctor`'s FAIL text names the exact command
(`ccst sessions migrate-uuid --write`) directly, so discoverability does not depend on `migrate
all`.

**15. FAIL-remediation text.** Given verbatim in the table under Decision 1-2, to the
`doctor.py:645-650` standard: names the check, states the constraint ("from a plain terminal with
no other `claude` session running" - the concurrency guard in Decision 9 is what makes this
necessary, mirroring the existing 1.0.0 check's "not inside a Claude Code session" wording for the
same underlying reason: a live hook write racing the rebuild), and the exact command.

**16. `install_sync` exemption.**
Add `"sessions"` to `install_sync._EXEMPT_NOUNS`. This fixes a real, pre-existing gap (confirmed
above: `ccst sessions migrate`/`ccst sessions list` are not currently exempt, even though `doctor`
and `repair` already are) and is consistent with per-noun exemption granularity already used for
those two - `ccst sessions migrate-uuid` inherits the exemption automatically since it is a verb
under the same noun. `ensure_synced()`'s own write (a version stamp in `install_sync`, an
unrelated table) does not touch the `sessions` table's schema either way, so nothing about the
rebuild itself depends on this - it only prevents an unrelated auto-sync apply from running ahead
of a migration command the user is actively trying to invoke.

**17. CHANGELOG.** A `[3.0.0]` entry is written to the 1.0.0 entry's bar as part of this change
(not deferred) - see Process step 6 in the task prompt; covers the breaking PK change, the
`ccst sessions migrate-uuid --write` path, the backup location as the rollback path, and the
version-skew note from Decision 19.

**18. Cross-machine independence.** Stated directly: `sessions.db` is machine-local
(`paths.data_home()` is a hardcoded POSIX path, `paths.py:18-23`, never synced - confirmed
against the multi-laptop sync spec's explicit non-goals list). Each machine runs `ccst sessions
migrate-uuid --write` independently; backfill results differ per machine because each has its own
local transcript set. No coordination exists or is needed. Documented in the CHANGELOG and in
`ccst doctor`'s FAIL text implicitly (the command has no machine-selection flag - it only ever
acts on the local file).

**19. Version-skew behavior.** Documented explicitly (not just implied): a migrated `sessions.db`
is unsupported by <3.0.0 CCST - its `ON CONFLICT(project_dir, basename)` raises
`sqlite3.OperationalError` against the 3-column PK (verified in the gap review against SQLite
3.51.0), and `hooks/session_tag.py`'s current `except sqlite3.Error` swallows it to stderr only,
so a downgraded or mixed-version machine silently stops recording `last_opened`/`last_active`.
Rollback path is restoring the Decision 3-4 backup file. This repo's supported-platform statement
already limits itself to Linux/macOS - no separate cross-OS work is implied here. `PRAGMA
user_version` is **not** bumped for this - it would only be a forward signal no current code
checks, adding a new mechanism for a benefit no reader consumes yet; the marker
(`SESSIONS_UUID_MIGRATION`) is the actual signal that matters and already exists for this purpose.

**20. Filesystem assumption.** Stated once in the CHANGELOG and in `migrate_sessions_db.py`'s new
function's docstring: the migration assumes `sessions.db` is on a native Linux/macOS filesystem,
matching this repo's existing Linux/macOS-only support statement (`README.md:1086`); a `/mnt/c`
9p-mounted WSL2 path is not specifically guarded against beyond the existing WAL/locking caveats
already documented in `lib/pdata/backup.py:17`.

## Risks / Trade-offs

- **[Risk]** A rebuild interrupted mid-transaction (process killed, machine sleeps) could leave
  `sessions_new` present alongside a dropped-then-not-yet-renamed `sessions`. → **Mitigation**:
  the entire rebuild (steps 3-6 of Decision 3-4) runs inside one SQLite transaction; SQLite's
  atomic commit means an interruption before `COMMIT` leaves the original `sessions` table
  fully intact on the next open - the "half-renamed" state is not reachable because `DROP`/
  `RENAME`/`record_migration` are never individually committed until all of them succeed.
- **[Risk]** A project with an unusually large number of transcripts could make the dry-run scan
  slow enough to feel unresponsive. → **Mitigation**: `load_jsonl_titles()` already exists and is
  already used at this scale elsewhere (`lib/sessions.py:110-124`); no new scanning approach is
  introduced, so this migration inherits whatever performance profile that function already has
  in production use.
- **[Trade-off]** Ambiguous rows are excluded from `sessions` entirely until reconciled (Decision
  7), rather than kept visible-but-marked in the main table. This means a truly-never-reconciled
  ambiguous session becomes invisible to `ccl`/`ccs` rather than showing up oddly. Accepted
  because the alternative (a placeholder value in the PK) is the exact phantom-fork bug this
  design exists to avoid, and `ccst repair sessions --uuid --list` (or equivalent) remains the way
  to audit what is sitting unreconciled.
- **[Trade-off]** `session_gc.py` is deliberately left untouched (Decision 11). This means a
  `sessions` row for a session whose transcript is long gone can persist indefinitely post-3.0.0,
  same as it can today. Accepted as an explicit non-goal (see Goals/Non-Goals) rather than
  quietly expanding this change's scope into GC policy.

## Migration Plan

Deploy: ship as part of the normal `uv tool install --reinstall` flow. No automatic execution -
`ccst doctor` FAILs and tells the operator the exact command (Decision 15); this is a deliberate,
manual, one-time step per machine, matching every other one-shot migration in this repo.

Rollback: restore the Decision 3-4 backup file over the live `sessions.db` (with no `claude`
session running, per the same concurrency concern as the forward migration) and reinstall a
<3.0.0 build. Documented in the CHANGELOG per Decision 17.

Sequencing across the two steps this task's process defines: schema + write-path changes and
their tests land first (spec requirements 1-4), then the migration script and its tests
(requirement 5-8), then the `ccst doctor` guard and `ccst repair sessions --uuid` (requirement
5, 9), then the `ccs.py`/`ccr.py`/`ccst sessions list` read-path/display changes and their tests
(requirements 2-3) - matching the original spec's suggested order, since nothing above changes
that ordering's rationale (schema must exist before anything reads/writes uuid-aware rows).
