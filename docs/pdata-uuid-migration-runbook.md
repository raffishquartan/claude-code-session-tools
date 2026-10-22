# pdata UUID migration runbook (ccst 3.x to 4.0.0)

A copy-paste checklist for moving every machine's `pdata` databases from format 3 (integer record
ids, ccst 3.x) to format 4 (UUID record ids, ccst 4.0.0). Follow it from a plain terminal, once
per computer. No `claude` session is needed, or wanted: until a machine is migrated, its `pdata`
data refuses to sync or change, and Claude Code sessions in `pdata` projects cannot use it.

Every command here is either `ccsched`, one of the `ccst pdata` verbs that still run on an
unmigrated database (`migrate-uuid`, `migration-notice`, `sync-check`, `verify`), `ccst doctor`,
`ccst --version`, or a file move. `ccst pdata rehydrate` appears only in the recovery recipes,
where the database it acts on is already format 4 or absent.

Default locations used below (adjust if you set `CCST_DATA_HOME`, `CCST_PROJECT_DB_DIR` or
`CCST_PROJECTS_ROOT`):

| What | Where |
|---|---|
| A project's database | `~/.local/share/claude/project-db/<project>.db` |
| Pre-migration backups | `~/.local/share/claude/project-db/migration-backups/pdata-<project>-pre-4.0.0-<UTC stamp>.db` |
| Format 3 dump (3.x, frozen after migration) | `~/cc/<project>/.pdata-db-dump/latest.sql` |
| Format 4 dump (4.0.0) | `~/cc/<project>/.pdata-db-dump/v4/latest.sql` |

## 1. What you will see, and why

- 4.0.0 changes `pdata` record ids from integers to UUIDs, so two machines can never hand out the
  same id for different records. Existing ids are converted deterministically: integer id `N` in
  project `p` becomes the UUIDv5 of `p:N`, so every machine that migrates the same data gets the
  same UUIDs.
- After upgrading, and until `ccst pdata migrate-uuid --write` has run on that machine:
  - record commands (`ccst pdata add/get/list/query/update/delete/...`) refuse with a message
    naming `ccst pdata migrate-uuid` (exit 1);
  - `ccst pdata sync-check` reports `migration pending` and neither loads nor publishes anything
    (exit 0); the `pdata-sync` hook and the pdata scheduled jobs do the same or refuse;
  - `ccst doctor` shows `[FAIL] migration-to-4.0.0:pdata-uuid:<project>`;
  - a `claude` session start shows the same through the `pending-migration` hook;
  - `install-everything.sh` ends with a framed `pdata MIGRATION REQUIRED` banner.
- Migration is only safe from a state where every machine already holds the same format 3 data
  (converged). 4.0.0 never writes a format 3 dump, so anything a machine has not published by the
  time it upgrades can only be published by going back to 3.x (section 6, "Unpublished changes").
  That is why step 0 happens on 3.x, on every machine, before anything is upgraded.
- The format 4 dump lives in `.pdata-db-dump/v4/`. A machine still on 3.x never reads it, and the
  format 3 dump and its archives are never modified, which is what makes rollback possible.

## 2. Machine order

| Machine | When | What it does |
|---|---|---|
| Every machine | Step 0, all on the same day, before any upgrade | Disable the pdata jobs, converge on 3.x, stop writing pdata |
| First machine | Any converged machine; pick the one you use most | Rewrites its databases to UUIDs and publishes the format 4 dump |
| Each later machine | After the first machine's `v4/latest.sql` has arrived through your file sync; same day if you can | Checks it holds the exact format 3 state the first machine migrated from, then adopts the format 4 dump |

The rule for later machines: one may migrate days later, as long as it made no `pdata` writes on
3.x in the meantime. Its pdata scheduled jobs stay disabled from step 0 until it migrates (they
write and publish without you), and do not start `claude` sessions in `pdata` projects on it until
then. If it did write, its `migrate-uuid` refuses with a lineage mismatch (section 6).

A project that exists on only one machine just takes the first-machine path on that machine.

## 3. Step 0 - on every machine, while still on 3.x, BEFORE upgrading anything

Do 0.1 to 0.3 on every machine, then repeat 0.4 round all machines until it settles.

0.1. Close every `claude` session on this machine. Their SessionStart/SessionEnd sync hooks write
     and publish.

0.2. Check the installed version:

```sh
ccst --version
```

Expected: `ccst 3.<something>` (for example `ccst 3.16.2`). If it already says `ccst 4.0.0`, this
machine was upgraded early: go to section 6, "Unpublished changes", before continuing.

0.3. Disable the pdata scheduled jobs so nothing writes behind your back:

```sh
ccsched list
ccsched disable pdata-sync-hourly
ccsched disable pm-session-output-reconcile
ccsched disable pdata-verify-all
```

Expected: `ccsched list` shows the three jobs; each `disable` prints `disabled <job>` (exit 0).
Afterwards `ccsched list` shows `false` in the `enabled` column for all three. If a job is not
listed, this machine never registered it; skip that line.

0.4. Publish and pull until every machine agrees:

```sh
ccst pdata sync-check --all-projects
```

Expected when settled (3.x prints one summary line, exit 0):

```
ccst pdata sync-check --all-projects: fast-forwarded 0, published 0, unchanged N, conflict 0, deferred 0, occupied 0, errors 0 of N project(s)
```

Run it on each machine in turn, waiting for your file sync (OneDrive or similar) to deliver each
project's `.pdata-db-dump/` to the other machines between runs. Repeat the round until one full
round has every machine printing `fast-forwarded 0, published 0, ... conflict 0, ... errors 0`.

- `conflict 1` or more: a fork. Run `ccst pdata sync-check --project <project>` to see which,
  then resolve it with the 3.x tooling - `ccst pdata resolve --project <project>` to see the
  differences, then `ccst pdata resolve --project <project> --choice <ID>=local --choice <ID>=dump ...`
  with one `--choice` per differing record (the `pm-pdata-resolve-conflicts` skill walks through this; a `claude` session is fine
  here because you are still on 3.x - close it afterwards). Then continue the rounds.
- `occupied 1` or more: a `claude` session is still open in that project. Close it and re-run.
- `deferred 1` or more: another process held the database. Re-run.
- `errors 1` or more: read the per-project message printed above the summary and fix that
  project first.

0.5. From now until this machine is migrated, do not change `pdata` data on it.

## 4. First machine

Do this on the machine you picked to go first, after step 0 has settled everywhere.

4.1. Upgrade. Either:

```sh
uv tool install cc-session-tools --upgrade
```

or, from a clone of this repository, `bash install-everything.sh --upgrade` (it ends with the
`pdata MIGRATION REQUIRED` banner listing your projects).

4.2. Check the version:

```sh
ccst --version
```

Expected: `ccst 4.0.0`. The first `ccst` command after an upgrade may also print
`ccst: install config is out of sync ... syncing`; that is ccst updating its own config and is
unrelated to pdata.

4.3. See what doctor says:

```sh
ccst doctor
```

Expected: one `[FAIL] migration-to-4.0.0:pdata-uuid:<project>` per project, saying the database is
format 3 and naming `ccst pdata migrate-uuid --write --project <project>` and this runbook. Doctor
exits 1 while anything is WARN or FAIL; that is expected here.

4.4. Confirm the format 3 state is still converged:

```sh
ccst pdata sync-check --all-projects
```

Expected (exit 0), with N your number of projects:

```
ccst pdata sync-check --all-projects: fast-forwarded 0, published 0, unchanged 0, conflict 0, deferred 0, occupied 0, migration-pending N, errors 0 of N project(s)
```

Per project, `ccst pdata sync-check --project <project>` must say
`migration pending (format 3 dump: unchanged)`. Any other value in the brackets
(`unpublished_changes`, `behind`, `fork`, `not_published`, `checksum_invalid`) means step 0 did not
finish for that project: see section 6.

4.5. Dry run (changes nothing):

```sh
ccst pdata migrate-uuid --all-projects
```

Expected for each project (exit 0):

```
<project>: dry run - nothing was changed
  database: ~/.local/share/claude/project-db/<project>.db
  plan: rewrite every id to uuid5("<project>:<integer id>"), back up, verify, record the marker, publish the format 4 dump
  records per group (including soft-deleted):
    <group>: <rows> (<n> soft-deleted)
  extension tables:
    ext_<group>: <rows> row(s)
  verify watermark rows: <n>
  format 3 vector: <machine>=<revision>, ...
  preconditions:
    [ok] database is format 3
    [ok] converged with the published format 3 dump
    [ok] no `claude` session is running in ~/cc/<project>
    [ok] the database is not locked
  every precondition holds: re-run with --write to migrate
```

Any `[NO]` line is followed by its remedy and makes the command exit 1; see section 6. If the
output lists "fields whose names suggest they hold record ids", note them: ccst cannot see or
rewrite ids stored inside your own fields or JSON content, so check those values by hand after
the migration.

4.6. Migrate:

```sh
ccst pdata migrate-uuid --all-projects --write
```

Expected for each project (exit 0):

```
<project>: migrated to format 4
  backup: ~/.local/share/claude/project-db/migration-backups/pdata-<project>-pre-4.0.0-<UTC stamp>.db
  rewrote <n> record(s), <n> extension table(s) and <n> verify watermark row(s) to UUID ids; verify passed
  recorded the pdata-uuid-ids marker
  published ~/cc/<project>/.pdata-db-dump/v4/latest.sql (machine_id=<this machine>)
```

Write down the backup paths: they are what rollback restores. A project with no database prints
`nothing to migrate`; running the command again prints `already migrated` (both exit 0).

4.7. Verify:

```sh
ccst pdata verify --all-projects
```

Expected: `ccst pdata verify --all-projects: OK (N project(s), 0 issue(s))` (exit 0).

4.8. Sync is back:

```sh
ccst pdata sync-check --all-projects
```

Expected: `... unchanged N, conflict 0, deferred 0, occupied 0, migration-pending 0, errors 0 of N project(s)` (exit 0).

4.9. Doctor:

```sh
ccst doctor --all
```

Expected: `[OK  ] migration-to-4.0.0:pdata-uuid:<project>  database is format 4 (UUID record ids)`
for every project (OK rows only appear with `--all`). Other, unrelated WARN rows may still make
doctor exit 1.

4.10. Re-enable the pdata jobs:

```sh
ccsched enable pdata-sync-hourly
ccsched enable pm-session-output-reconcile
ccsched enable pdata-verify-all
```

Expected: `enabled <job>` for each (exit 0). `claude` sessions can be used again on this machine.

## 5. Each later machine

5.1. Wait until your file sync has delivered the first machine's format 4 dump for every project
this machine shares with it:

```sh
ls ~/cc/<project>/.pdata-db-dump/v4/
```

Expected: `latest.sql` and `latest.sha256` are present. Do not continue before they are: a
machine that finds no format 4 dump takes the first-machine path, and two machines that both do
that end up in a fork.

5.2. Upgrade and check the version: as 4.1 and 4.2.

5.3. Doctor: as 4.3 (FAIL per project, expected).

5.4. Status:

```sh
ccst pdata sync-check --project <project>
```

Expected (exit 0):

```
ccst pdata sync-check: <project>: migration pending (format 3 dump: unchanged) - the database is format 3 but the published dump is format 4; nothing was loaded or published (this is not a fork). The format 3 side must be migrated. ...
```

5.5. Dry run:

```sh
ccst pdata migrate-uuid --all-projects
```

Expected for each shared project (exit 0) - check that the plan line says **adopt**:

```
<project>: dry run - nothing was changed
  database: ~/.local/share/claude/project-db/<project>.db
  plan: adopt the published format 4 dump (another machine migrated first); this database is backed up, then replaced by the dump's data
  ...
  preconditions:
    [ok] database is format 3
    [ok] this database is the format 3 state the format 4 dump came from
    [ok] the format 4 dump holds every record of this database
    [ok] no `claude` session is running in ~/cc/<project>
    [ok] the database is not locked
  every precondition holds: re-run with --write to migrate
```

If the plan line says "rewrite every id" for a project the first machine also has, its format 4
dump has not arrived yet: stop and go back to 5.1.

5.6. Migrate:

```sh
ccst pdata migrate-uuid --all-projects --write
```

Expected for each shared project (exit 0):

```
<project>: migrated to format 4 by adopting the published format 4 dump
  backup: ~/.local/share/claude/project-db/migration-backups/pdata-<project>-pre-4.0.0-<UTC stamp>.db
  adopted ~/cc/<project>/.pdata-db-dump/v4/latest.sql
```

5.7. Verify, sync, doctor and re-enable the jobs: as 4.7, 4.8, 4.9 and 4.10.

## 6. Troubleshooting

Each entry is keyed by the text `migrate-uuid` prints. Every refusal changes nothing, so it is
always safe to fix the cause and re-run. Exit statuses: 0 ready / migrated / adopted / already
migrated / nothing to migrate; 1 refused (a precondition does not hold); 2 failed after the
backup (the original database was restored or left in place; the message names the backup) or an
error.

| Message | Cause | What to do |
|---|---|---|
| `[NO] converged with the published format 3 dump` ... `unpublished changes` (or `never been published`) | This machine has format 3 changes no dump holds. 4.0.0 cannot publish format 3. | Unpublished changes recipe below. |
| `[NO] converged ...` ... `the published format 3 dump is ahead` | Another machine published format 3 changes this one has not loaded. | Unpublished changes recipe below (3.x `sync-check` fast-forwards). |
| `[NO] converged ...` ... `(fork)` | This machine and the format 3 dump both changed. | Install 3.16.2 as in the recipe below, resolve with `ccst pdata resolve --project <project>` (step 0.4), `sync-check` until unchanged, upgrade again, re-run. |
| `[NO] converged ...` ... `fails its checksum` | The format 3 dump is damaged or was caught mid-sync. | Wait for the file sync to finish and re-run; if it persists, on 3.16.2 on the machine with good data run `ccst pdata dump --project <project> --force`. |
| `` [NO] no `claude` session is running in ... `` | A `claude` process is working in that project. | Close every `claude` session in the project and re-run from a plain terminal. |
| `[NO] the database is not locked` | Another process has the database open for writing. | Close `claude` sessions, check `ccsched list` shows the pdata jobs disabled, wait a minute and re-run. |
| `[NO] this database is the format 3 state the format 4 dump came from` (lineage mismatch), or `[NO] the format 4 dump holds every record of this database` | This machine wrote format 3 data after the first machine migrated, so its changes are not in the migrated lineage. | Lineage mismatch recipe below. |
| `<project>: FAILED` ... `migration failed: ...; the original database was restored from the backup ...` (exit 2) | Something failed after the backup. Nothing is lost. | Read the error, fix it and re-run. Keep the named backup. |
| `<project>: FAILED` ... `migration failed: ...; the original database is unchanged; the backup is ...` (exit 2) | Something failed before the rewrite was committed. Nothing is lost. | Read the error, fix it and re-run. Keep the named backup. |
| `<project>: FAILED` ... `restoring the backup also failed ...` (exit 2) | The rewrite failed after it was committed, and putting the backup back failed too. | With no `ccst` or `claude` process running, copy the named backup over the database and remove any `-wal`/`-shm` files beside it (as in rollback, section 7), then re-run. |
| `<project>: FAILED` ... `the steps finished before the failure are kept ...` (exit 2) | Finishing an earlier run failed part way. | Read the error, fix it and re-run the command it names. |
| `<project>: this machine has no database for it and the project's only published dump is format 3 ...`, or sync reports `there is no database on this machine` | This machine has never had the project's database, and no machine has migrated the project yet. Nothing is created here, so the format 3 data can still be migrated. | Migrate the project on a machine that holds its format 3 database; this machine then loads the format 4 dump on its next sync. |
| `<project>: the database was converted to format 4 by `ccst pdata migrate-uuid`, but that run did not finish ...` (from a record verb, `dump`, `sync-check` or the sync hook) | An earlier `--write` converted the database and stopped before verifying it. Nothing reads, publishes or syncs it until it is finished. | Re-run `ccst pdata migrate-uuid --write --project <project>` from a plain terminal. |
| `[FAIL] migration-to-4.0.0:pdata-uuid:<project>` ... `marker is missing` | An earlier `--write` stopped before finishing. | Re-run `ccst pdata migrate-uuid --write --project <project>`; it finishes the remaining steps (`finished an earlier migration`). |
| `[FAIL] migration-to-4.0.0:pdata-uuid:<project>` ... `marker is recorded but the database is still format 3` | A pre-migration backup was copied back over a migrated database. | Decide which data you want: to stay on 4.0.0, follow section 5 for that project; to stay on 3.x, follow rollback (section 7). |

### Unpublished changes: back to 3.x, publish, upgrade again

For a machine already on 4.0.0 whose format 3 database has changes nobody else has:

```sh
uv tool install cc-session-tools==3.16.2
ccst --version                                    # ccst 3.16.2
ccst pdata sync-check --all-projects              # repeat (step 0.4) until every project is unchanged
uv tool install cc-session-tools --upgrade
ccst --version                                    # ccst 4.0.0
ccst pdata migrate-uuid --all-projects            # the dry run should now show [ok] converged
```

If another machine has already migrated, the published changes from this machine are not in its
format 4 dump: this then becomes a lineage mismatch on this machine (next recipe).

### Lineage mismatch: adopt the format 4 data and re-enter the difference

The first machine migrated from a format 3 state this machine has moved past. Its format 3
database still holds the extra changes; keep it to copy them from.

```sh
mkdir -p ~/pdata-pre-4.0.0-aside
mv ~/.local/share/claude/project-db/<project>.db ~/pdata-pre-4.0.0-aside/
# and, if they exist:
mv ~/.local/share/claude/project-db/<project>.db-wal ~/pdata-pre-4.0.0-aside/
mv ~/.local/share/claude/project-db/<project>.db-shm ~/pdata-pre-4.0.0-aside/
ccst pdata rehydrate --project <project>          # loads the format 4 dump: fast-forwarded ...
ccst pdata migrate-uuid --project <project>       # already migrated
ccst pdata verify --project <project>
```

Then re-enter the records that only the old database has (compare with `sqlite3` or any SQLite
browser on the file in `~/pdata-pre-4.0.0-aside/`). Records of the session-output index need no
re-entry: the `pm-session-output-reconcile` job re-derives them once re-enabled.

## 7. Rollback (one machine)

Rollback returns one machine to 3.x and its pre-migration database. The format 3 dump and its
archives (the last 24) were never modified, so 3.x keeps syncing from them for as long as they are
kept. Anything written in format 4 after the migration - on this machine or on others that read
its format 4 dump - is not in the backup and is lost to this machine.

```sh
ccsched disable pdata-sync-hourly
ccsched disable pm-session-output-reconcile
ccsched disable pdata-verify-all
# close every claude session, then:
uv tool install cc-session-tools==3.16.2
mkdir -p ~/pdata-4.0.0-aside
mv ~/.local/share/claude/project-db/<project>.db ~/pdata-4.0.0-aside/
# and, if they exist, the <project>.db-wal and <project>.db-shm files beside it
cp ~/.local/share/claude/project-db/migration-backups/pdata-<project>-pre-4.0.0-<UTC stamp>.db \
   ~/.local/share/claude/project-db/<project>.db
ccst --version                                    # ccst 3.16.2
ccst pdata sync-check --all-projects              # unchanged, as at the end of step 0
ccsched enable pdata-sync-hourly
ccsched enable pm-session-output-reconcile
ccsched enable pdata-verify-all
```

Repeat the `mv` and `cp` lines for each project. Every machine that shares a project must be on
the same format: roll back all of them or none.

## 8. Final checklist (per machine)

- [ ] Step 0 done here: pdata jobs disabled, and a full `sync-check` round settled on 3.x
- [ ] `ccst --version` prints `ccst 4.0.0`
- [ ] Later machines only: `.pdata-db-dump/v4/latest.sql` present for every shared project, and
      the dry run's plan says "adopt"
- [ ] `ccst pdata migrate-uuid --all-projects --write` printed `migrated to format 4` (or `already
      migrated` / `nothing to migrate`) for every project, exit 0
- [ ] Backup paths written down
- [ ] `ccst pdata verify --all-projects` prints `OK`
- [ ] `ccst pdata sync-check --all-projects` shows `migration-pending 0` and `errors 0`
- [ ] `ccst doctor --all` shows `[OK  ] migration-to-4.0.0:pdata-uuid:<project>` for every project
- [ ] `ccsched enable` run for `pdata-sync-hourly`, `pm-session-output-reconcile` and
      `pdata-verify-all`
- [ ] Any "fields whose names suggest they hold record ids" from the dry run checked by hand
