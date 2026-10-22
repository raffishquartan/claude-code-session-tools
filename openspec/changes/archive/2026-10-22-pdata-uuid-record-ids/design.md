# Design

## Context

See proposal.md for the motivation. Facts about the current code that shape the approach:

- `records.id` is `INTEGER PRIMARY KEY` (a rowid; no AUTOINCREMENT), `repository.py:31-40`.
  `insert_base_record` returns `cursor.lastrowid`.
- Integer ids are also keys in `ext_<group>.record_id` (PK and FK, `repository.py:144`) and in
  `pdata_verify_watermark.record_id` (`verify.py`). `pdata_verify_runs.id` is an unrelated local
  rowid. `record_group_fields` and `pdata_meta` (the vector clock, keyed by machine) hold no record
  ids. No `content` or extension field stores another record's id in this repo's code.
- Integer typing is spread over argparse (`get/update/delete/restore --id` are `type=int`;
  `resolve --choice` parses `int(id)`), the service layer (`record_id: int`), `resolve.py` sorting
  and diffing, `init_service` rollback lists, and output formatting. `ORDER BY id` is used in
  `list_base_records`, `query_records`, resolve diffing and per-table dump ordering.
- The dump (`dump.py`) is a SQL text of every table, `latest.sql` plus `latest.sha256`, with 24
  archives. It has no format or schema marker. `rehydrate` replaces the whole database by replaying
  the dump into a temp file, so it adopts whatever schema the dump holds. 3.x code therefore cannot
  detect a UUID dump.
- Fork detection is the vector clock (`vector_clock.compare`). The "id collision" class lives in
  `resolve._classify`: same integer id on both sides with different `created_at`.
- The precedent migration is `ccst sessions migrate-uuid` (3.0.0): dry run by default, `--write`,
  `db.backup_to` into `migration-backups/`, rebuild in one `BEGIN IMMEDIATE`, a `migrations` ledger
  marker, a lock probe for running sessions, doctor check `migration-to-3.0.0:sessions-uuid`, and
  `refuse_if_sessions_db_pre_uuid`. `lib/occupancy.py` offers a real `claude`-process check per
  project. pdata has no schema-migration framework (only `CREATE ... IF NOT EXISTS`).

## Goals / Non-Goals

**Goals:**
- No two independently created records ever share an id; fork resolution then only ever sees real
  conflicts.
- A converged multi-machine installation converts with every machine ending on identical ids, and
  an unconverted or half-upgraded machine fails closed (refuses) rather than corrupting data.
- Reuse the 3.0.0 migration patterns and the existing doctor and hook plumbing.

**Non-Goals:**
- Repairing today's forks; they must be resolved with 3.x tooling before migrating.
- Merging data from machines that have diverged format 3 lineages (documented manual re-entry).
- Automatic "union" resolution of one-sided additions in `resolve` (a follow-up; see Open Questions).
- Changing the vector-clock algorithm or the `version` column semantics.

## Decisions

### D1. Id type: UUIDv4 as TEXT; ordering by `created_at, id`
New records get `uuid.uuid4()` in canonical lowercase hyphenated form, stored as `TEXT PRIMARY KEY`.
Alternative UUIDv7 (time-ordered): rejected because Python 3.12/3.13 stdlib lacks it, the project
orders by `created_at` anyway, and ids are never used as an ordering key once `ORDER BY` is
`created_at, id`. UUIDv5 is used only where determinism is required (D2, D3). Nothing depends on
small integers except the CLI `--id` type and `resolve --choice` parsing, both changed.

### D2. Migration mapping: UUIDv5(namespace, `"<project>:<old integer id>"`)
A fixed product-defined namespace UUID. Determinism is only safe because migration requires a
converged state (D4): in a converged state the integer-to-record map is identical on every machine,
so each machine independently computes the same UUIDs. Integer ids that collided between machines
were different records; they are exactly the unresolved-fork case, which D4 refuses, so the mapping
never merges unrelated records. The record group is not part of the key (the integer id is unique
in the table and groups can be renamed).

### D3. Deterministic ids for records unique by meaning
`session-output-watermark`: UUIDv5(namespace, `"<project>:session-output-watermark"`). Registered
session-output records: UUIDv5(namespace, `"<project>:session-output:<file_path>"`). This addresses
the originating failure directly: two machines backfilling the same files produce the same records,
not duplicates, and the watermark can no longer exist twice (today `limit=1 ORDER BY id` would pick
nondeterministically under random UUIDs). A same-id divergence is an edit conflict that `resolve`
can settle.
Migrated rows are mapped by D2, not D3: a migrated watermark or session-output row carries
uuid5(`<project>:<old integer id>`), and the reconcile code finds existing rows by the D3 id
or, failing that, by `file_path` (watermark: the oldest watermark row), so no duplicate appears.

Why v5 here and v4 elsewhere: v4 is random, so it is right for new records (two machines can never
pick the same id) but two machines migrating the same old record would pick different ids and the
migrated databases would differ on every row, which `rehydrate` would then see as a total fork. v5
is a hash of its input, so identical input gives identical output on every machine with no
coordination. The same property is what D3 uses for records that are unique by meaning.

### D4. Migration preconditions: converged, quiet, unlocked
Per project, `--write` requires: database is format 3; local equals the published format 3 dump
(vector clocks equal and no unpublished change, as `sync-check` reports "unchanged"); no `claude`
process occupies the project (`lib/occupancy.py`); a throwaway `BEGIN IMMEDIATE` succeeds. This is
what makes D2 safe: a machine with extra local rows could otherwise reuse an integer another machine
also used, and uuid5 would silently equate two unrelated records.

### D5. Format marker and dump location
Format is recorded in the database via the existing `migrations` ledger (`db.MIGRATIONS_DDL`,
marker `pdata-uuid-ids`) plus a schema probe (`records.id` declared TEXT), and in the dump as a
`-- pdata_format=4` header line (absent means 3, the 3.x integer-id format; formats are named for the ccst major version that introduced them, so the directory `v4/` and `pdata_format=4` agree). Format 4 dumps go to `.pdata-db-dump/v4/`. Why a
new location: 3.x code has no format check and `rehydrate` replaces the whole database, so a UUID
dump at the old path would be loaded by a not-yet-upgraded machine and break it. With a separate
path, a 3.x machine keeps working on the frozen format 3 dump (it is simply isolated until it is
upgraded). Alternatives: (a) same path with a header marker only: rejected, unsafe for 3.x;
(b) same path with a deliberately invalid checksum so 3.x refuses: rejected, the 3.x publish
behaviour on an invalid dump is not guaranteed and refusal text would be misleading.

### D6. Join sequence and the "adopt" path
Sequence: (1) on every machine on 3.x, run `ccst pdata sync-check --all-projects` until each project
is unchanged, resolve any forks with 3.x tooling, stop writing pdata; (2) upgrade one machine to
4.0.0 and run `migrate-uuid --write`: it backs up, rewrites, verifies, records the marker, publishes
the format 4 dump whose header carries the format 3 vector it came from; (3) upgrade each other
machine and run `migrate-uuid --write`: because a format 4 dump exists, it checks that its local
format 3 state matches the dump's recorded vector and then ends with the dump's data (by D2 the two
are identical). A machine that upgrades before step 2 finds a format 3 database: record verbs refuse,
sync-check reports "migration pending" and neither publishes nor rehydrates. A machine may also
upgrade days after step 2, provided it made no pdata writes on 3.x in between: "no claude session
running" is not the same as "no writes", because scheduled jobs on 3.x (`pdata-sync-hourly`,
the session-output reconcile job) keep writing and publishing format 3 data. The runbook therefore
tells the user to disable pdata scheduled jobs on any machine that will not be migrated the same
day (`ccsched disable`) and re-enable them after it migrates. A machine that did write on 3.x after
step 2 has its `migrate-uuid` refuse (the recorded-vector check above) and those changes must be
re-entered by hand; changes that are only session-output rows are re-derived by the reconcile job
under their deterministic ids.

### D6c. Lineage vector storage, and the unpublished-changes dead end
The format 3 vector clock that a format 4 database was migrated from is stored in the database
(table `pdata_migration_origin`, one row per machine), and a one-row table `pdata_migrated_from`
records that the database was migrated at all, because that vector may be empty and an empty vector
must still read as a lineage. Both are ordinary table data, so they replicate with every dump and
rehydrate, and every publish copies them into the dump header (`-- migrated_from_format=3` and the
`-- migrated_from_format3_vector:` lines); the header is what a joining machine reads before it
touches its own data. Format 4 code never
writes a format 3 dump, so a format 3 database with unpublished changes cannot publish them once ccst is
4.0.0. `migrate-uuid` therefore refuses that state with a message that gives the recovery: install
3.16.2 (`uv tool install cc-session-tools==3.16.2`), run `ccst pdata sync-check` until it reports
"unchanged", then upgrade again; the runbook makes this step 0 ("on every machine, while still on
3.x, before upgrading anything").

### D6a. Making the required migration impossible to miss
Upgrading is `uv tool install cc-session-tools --upgrade`, which cannot print anything about data.
So the notice is layered, each layer independent: (1) `install-everything.sh` ends with a framed
banner, and when any pdata project is unmigrated it lists the projects and the exact commands to
run (`ccst pdata migrate-uuid --all-projects`, then `--write`) and says to do this before starting
any `claude` session; (2) the format gate (D7) makes every record verb, scheduled job and hook
refuse with the same message, so nothing silently misbehaves; (3) the `pending-migration`
SessionStart hook injects the message into the next session; (4) `ccst doctor` shows FAIL
`migration-to-4.0.0:pdata-uuid` per project. There is no automatic migration, because it needs
every machine converged first (D4).

### D6b. The runbook is a deliverable, written for a terminal
The migration happens in the window where ccst is upgraded but the databases are not, so no
`claude` session can run on that machine. The runbook (`docs/pdata-uuid-migration-runbook.md`,
committed with the change) is therefore a plain-terminal, copy-paste checklist the user repeats once
per computer, with a first-machine section and a later-machine section, expected output per step and
a troubleshooting table. Its commands are limited to the verbs the format gate exempts (D7) plus
`ccsched`, and the install banner (D6a) names its path.

### D7. Unmigrated databases fail closed
A format check on every record-touching verb (mirrors `refuse_if_sessions_db_pre_uuid`). Allowed on
format 3: `migrate-uuid`, `sync-check` (status only), `verify`, `doctor`.

### D8. Fork classification and `resolve`
Remove the `created_at`-based integer-collision branch; keep edit-conflict, delete-versus-update and
group-mismatch. Update error text and the `pm-pdata-resolve-conflicts` skill. `resolve` stays
all-or-nothing: with UUIDs, additions on both sides appear as one-sided records that need an explicit
`local`/`dump` choice per record; auto-union is deferred (Open Questions).

### D9. Tables rewritten by the migration
`records.id` (to TEXT PRIMARY KEY), every `ext_<group>.record_id` (TEXT, FK), and
`pdata_verify_watermark.record_id`. `pdata_verify_runs` is unrelated and unchanged. The rewrite uses
the 3.0.0 pattern: create new table, insert-select with the mapping, drop, rename, reindex, inside
one `BEGIN IMMEDIATE` with foreign keys off, then integrity verification (`row-count-parity` still
works because it compares counts, not ids).

## Risks / Trade-offs

- [A converged state cannot be fully proven from one machine] -> The check uses this machine's
  view of the dump; the runbook requires every machine to run `sync-check` and report "unchanged"
  before step 2, and D6 refuses a late joiner whose history is not in the migrated lineage.
- [A 3.x machine left running after step 2 diverges silently] -> Its changes are not lost
  (frozen format 3 dump keeps them) but are not synced; runbook says to upgrade all machines the
  same day; `migrate-uuid` on that machine explains how to re-enter changes.
- [Cross-references to ids in user `content` or extension fields] -> Not visible to ccst; dry run
  lists extension and content fields whose names suggest ids (`*_id`, `id`, `ref`) so the user can
  audit before writing.
- [Whole-database rehydrate replaces the schema] -> Format check happens before replay (D5).
- [Test churn: ~40 lines hard-code integer ids; id-collision tests are rewritten] -> Tasks group
  tests with each code change.
- [Majors are disruptive] -> 4.0.0, with the format gate and doctor check making failure obvious.

## Migration Plan

1. Ship 4.0.0 (CHANGELOG, version bump). No behaviour change for projects until `migrate-uuid`.
2. Follow the runbook (`docs/pdata-uuid-migration-runbook.md`): quiesce and converge, migrate one
   machine, publish, migrate the rest, run `ccst doctor`.
3. Rollback on one machine: install 3.x, restore the named backup over the project's database file;
   format 3 dump and archives were never modified so 3.x continues to sync from them. Rolling back
   after other machines have written format 4 data discards those writes.
4. The format 3 dump stays in place indefinitely; its archives (24 deep) keep loading under 3.x.

## Open Questions

- Should `resolve` offer an automatic union for records present on only one side (safe now that
  ids are unique)? Deferred; it does not change the specs here.
- Registered session-output records take `created_at` from file mtime, which can differ per machine
  for the same file. With deterministic ids this surfaces as an edit conflict rather than a
  duplicate. Whether to normalise `created_at` for these rows can be decided during implementation.
