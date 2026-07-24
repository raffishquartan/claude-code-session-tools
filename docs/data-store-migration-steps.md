# Data-store migration steps

> Step-by-step instructions for migrating this machine from the pre-0.19.0 flat-file/JSONL/TOML
> data stores to the SQLite (WAL mode) stores under `~/.local/share/claude/`. Companion to
> `docs/data-store-migration-backups.md` (the post-migration audit checklist) and
> `docs/superpowers/plans/2026-07-13-data-store-uplift-*.md` (the full design/implementation
> record). Written 2026-07-17, revised the same day after an exhaustive cross-phase code review
> and a real-data migration test (both against `f/claude-data-store-uplift`, 80 commits over
> `main`) — every finding from both is folded into the steps below, not left as a separate list.

## What's changing on disk

| Subsystem | Old location(s) | New location |
|---|---|---|
| `ccmsg` | `~/.claude/cc-messages/` | `~/.local/share/claude/ccmsg.db` |
| `ccsched` | `~/.claude/cc-scheduler/` (`jobs.toml`, `state.json`, `.cursors/`, `.reconcile.*.ts`) | `~/.local/share/claude/ccsched.db` |
| session tags / doctor-mutes | `~/.cache/claude/session-tags/*.tag`, `~/.claude/cc-doctor-mutes.json` | `~/.local/share/claude/sessions.db` |
| session activity sentinels | `<project-root>/cc-sessions/<basename>/.last-opened`/`.last-active` (one pair per session, scattered across every project) | `~/.local/share/claude/sessions.db` — **mtimes copied in, originals left in place, never deleted** (see Step 3) |
| telemetry | `~/.cache/claude/logs/fires.jsonl` (+ `.1`/`.2`/`.3` rotation) | `~/.local/share/claude/telemetry.db` |
| command cache | `~/.cache/claude/logs/command-cache.db` | `~/.local/share/claude/command-cache.db` (path move only, same SQLite format) |
| claude-flags cache | `~/.cache/cc-session-tools/claude-flags.json` | `~/.local/share/claude/claude-flags.json` (path move + atomic-write fix, still flat JSON) |

Every migration script is **non-destructive by construction**: write the new store → verify
(row-count match + spot-check) → tar.gz-backup the old files to
`~/.local/share/claude/migration-backups/` → only then delete the old files. If verification
fails at any point, the script aborts and the old files are untouched. The one exception is the
activity sentinels row above — those are copied, never deleted, by design (harmless once
`sessions.db` is authoritative), so there's nothing to back up or restore for them.

## Script paths (quick reference)

All commands below assume `cd ~/repos/claude-code-session-tools` first; full absolute paths
given here so this section stands alone if you ever need it without that context:

| Step | What it does | Full path |
|---|---|---|
| 2 | standalone pre-migration backup | `/home/chris/repos/claude-code-session-tools/scripts/backup_pre_migration.sh` |
| 3/4 | migrate `ccmsg` | `/home/chris/repos/claude-code-session-tools/scripts/migrate_ccmsg_to_db.py` |
| 3/4 | migrate telemetry | `/home/chris/repos/claude-code-session-tools/scripts/migrate_fires_jsonl_to_telemetry_db.py` |
| 3/4 | migrate `ccsched` | `/home/chris/repos/claude-code-session-tools/src/cc_session_tools/cli/migrate_ccsched.py` (run as a module: `uv run python -m cc_session_tools.cli.migrate_ccsched`, not invoked by file path directly) |
| 3/4 | migrate sessions (tags/activity/mutes) | `/home/chris/repos/claude-code-session-tools/src/cc_session_tools/cli/migrate_sessions_db.py` (run as a module: `uv run python -m cc_session_tools.cli.migrate_sessions_db`, not invoked by file path directly) |

The `ccmsg`/telemetry scripts live under `scripts/` and are run directly by file path; the
`ccsched`/sessions ones live under `src/cc_session_tools/cli/` and are proper CLI modules (also
reachable as `ccst migrate ccsched` / `ccst sessions migrate` once you've reinstalled — see
Step 5) — this split is why the commands in Steps 3-4 below don't all look the same shape.

## Step 0 — run everything from a plain terminal, not from inside Claude Code

**Important, found during real-data testing:** the `bash-hard-deny` PreToolUse hook statically
scans any script named on a `python`/`python3` command line, including with `--dry-run`, and
blocks it if the script's source contains a destructive-file-operation token
(`.unlink(`/`shutil.rmtree(`/etc.). Three of the four migration scripts legitimately call these
(on their own already-backed-up-and-verified old files) as their last step, so **every command in
Steps 2 and 3 below will be blocked if you ask a Claude Code session to run them** — there is no
bypass env var for this particular check. Run them from a regular terminal window instead. This
also sidesteps Step 0's original close-other-sessions concern for the migration commands
themselves (though you should still close other sessions before migrating — see below).

## Step 1 — stop the clock on the old stores

The four flat-file stores with pre-existing data (`ccmsg`, `ccsched`, sessions, telemetry) are
actively written to by any Claude Code session that's currently open. **Close other Claude Code
sessions/windows before migrating** so nothing writes to a flat file mid-migration (a message sent
or job that fires between the migration script's read and its old-file delete would be silently
lost — the script can't see writes that happen after it already read the source).

## Step 2 — full pre-migration backup (belt-and-suspenders, independent of each script's own backup)

Each migration script takes its own backup before deleting anything (see the table above), but
run the standalone snapshot script first anyway — it captures everything in one place, before any
migration script has touched anything, in case you want to inspect or fully roll back later
without hunting through four separate `migration-backups/*.tar.gz` files:

```sh
bash scripts/backup_pre_migration.sh
```

Writes one timestamped tarball to `~/claude-data-store-migration-backup-<timestamp>.tar.gz`
containing byte-for-byte copies of every old-location file listed in the table above (whichever
of them actually exist on this machine — the activity sentinels are deliberately excluded, per the
table's note). Read-only — touches nothing under `~/.claude/`, `~/.cache/claude/`, or
`~/.local/share/claude/`. Safe to run any number of times.

## Step 3 — dry-run every migration script

```sh
cd ~/repos/claude-code-session-tools
python3 scripts/migrate_ccmsg_to_db.py --dry-run
python3 scripts/migrate_fires_jsonl_to_telemetry_db.py --dry-run
uv run python -m cc_session_tools.cli.migrate_ccsched --dry-run
uv run python -m cc_session_tools.cli.migrate_sessions_db --dry-run
```

Each prints what it would do (row counts, files it would touch) without writing anything —
confirmed by direct testing that every writer path in all four scripts is gated behind the
`--dry-run` check before any database connection is even opened. Read the output — in particular
check the malformed/skipped-row counts the telemetry and ccmsg scripts report; a nonzero count
isn't necessarily a problem (both scripts treat this as observability data, not silently-dropped
content — one real-world example seen during testing: a single 2290-byte run of NUL bytes from a
torn write, correctly skipped and counted) but is worth a glance before proceeding.

## Step 4 — run each migration for real, one at a time

```sh
python3 scripts/migrate_ccmsg_to_db.py
python3 scripts/migrate_fires_jsonl_to_telemetry_db.py
uv run python -m cc_session_tools.cli.migrate_ccsched
uv run python -m cc_session_tools.cli.migrate_sessions_db
```

(`ccsched` and `sessions` are also reachable as `ccst migrate ccsched` / `ccst sessions migrate`
once you've reinstalled per Step 5 below — but reinstalling first would mean the *hooks* start
writing to the new stores before you've migrated the *old* data into them, so migrate first with
the module invocations above, using the still-installed 0.18.0 binary.)

Order doesn't matter between the four — each is independent, and each script's own dry-run/verify
step is unaffected by whether the others have run yet. If any one reports a mismatch and aborts
(exit 2), **stop and read its stderr output before re-running** — do not reach for `--force` as a
first response. The four scripts are not equally safe to re-run:

- **ccmsg, ccsched, sessions** are all safely re-runnable as-is (their own verify step tolerates
  a DB that already has some or all of the data — this is what lets you re-run after an aborted
  attempt, or double-check by running a second time, without `--force`).
- **telemetry is the one exception.** Its catch-up-event ids must land at `id == N` for the
  N-th row (so the pre-existing per-session cursor offsets keep meaning the same thing after
  migration), which forbids `INSERT OR IGNORE`-style dedup. A second run against a dest DB that
  already has rows requires `--force`, and `--force` **appends a full duplicate copy** rather than
  deduplicating. If the telemetry migration aborts partway (e.g. killed after writing but before
  its own backup+delete step), do NOT just re-run it — follow the recovery procedure printed in
  its own `--help` / docstring (`scripts/migrate_fires_jsonl_to_telemetry_db.py`), which resets
  the destination tables and their id sequences before a clean re-run.

Expect the sessions migration to take a few seconds even with a large tag corpus (tens of
thousands of `.tag` files) — this was a real ~30-rows/sec bottleneck found and fixed during
testing (now ~8500+ rows/sec), but if you're running an older checkout that predates that fix, a
large corpus taking several minutes is a known slow-not-hung condition, not a bug.

Command-cache and claude-flags need **no migration script** — they're path moves only, picked up
automatically the first time the new binary runs (Step 5). The old files at their pre-0.19.0
locations are not touched or deleted by anything; delete them yourself once you've confirmed the
new binary is working, if you want to reclaim the space.

## Step 5 — reinstall the CLI tools

```sh
uv tool install --reinstall ~/repos/claude-code-session-tools
```

`--reinstall` is required — without it `uv` sees the version number changed but may still skip
rebuilding in some cache states; this repo's own `.claude/CLAUDE.md` calls this out explicitly.
This rebuilds the wheel from `f/claude-data-store-uplift` (or `main`, once merged) and updates all
six shims (`ccd`, `ccr`, `ccs`, `claude-code-usage`, `ccst`, `ccmsg`).

Confirm:

```sh
ccst --version   # should print 0.19.0
ccst doctor      # data-store checks should now show OK, not WARN, for the four migrated stores
```

If you migrated the four stores one at a time rather than all in one sitting, `ccst doctor` and
`ccst gc report` both degrade cleanly against a mix of migrated/not-yet-migrated stores (WARN, not
crash, for anything not yet migrated) — verified directly. The one thing to know about migrating
incrementally: the scheduled-job catch-up digest can misbehave transiently if `ccsched` and
`telemetry` aren't migrated in the same sitting, since the surfacing cursor lives in one store's
DB and the rows it indexes live in the other's. It self-heals (no crash, no data loss — a stale or
default cursor just means the next digest is unusually large or empty once), but migrating all
four together in one sitting avoids the blip entirely.

## Step 6 — verify

```sh
ccl --limit 5                    # most-recent-5 sessions, reading sessions.db
ccmsg list                       # reads ccmsg.db
ccsched list                     # reads ccsched.db
ccst telemetry query --limit 5   # reads telemetry.db
```

All four should return data that looks like your pre-migration history, not empty results.

## Step 7 — audit and retain backups

Follow `docs/data-store-migration-backups.md`'s checklist. Keep both the standalone Step 2
tarball and each script's own `migration-backups/*.tar.gz` for 30 days, then delete.

## Rollback — unwinding the migration

Only the flat-file *reads* stop once you reinstall 0.19.0 — nothing old is deleted until each
migration script's own verify+backup steps pass, and even then the new `.db` files never
overwrite the old ones in place. So rolling back is: restore the old flat files, then reinstall
the old binary.

**1. Restore the old flat files from the Step 2 tarball** (or, per-store, from
`~/.local/share/claude/migration-backups/*.tar.gz` if you'd rather restore one store at a time):

```sh
mkdir -p /tmp/ccst-restore
tar xzf ~/claude-data-store-migration-backup-<timestamp>.tar.gz -C /tmp/ccst-restore

cp -a /tmp/ccst-restore/ccmsg/cc-messages ~/.claude/
cp -a /tmp/ccst-restore/ccsched/cc-scheduler ~/.claude/
cp -a /tmp/ccst-restore/sessions/session-tags ~/.cache/claude/
[ -e /tmp/ccst-restore/sessions/cc-doctor-mutes.json ] && \
  cp -a /tmp/ccst-restore/sessions/cc-doctor-mutes.json ~/.claude/
cp -a /tmp/ccst-restore/telemetry/fires.jsonl* ~/.cache/claude/logs/
cp -a /tmp/ccst-restore/command-cache/command-cache.db ~/.cache/claude/logs/
mkdir -p ~/.cache/cc-session-tools
cp -a /tmp/ccst-restore/claude-flags/claude-flags.json ~/.cache/cc-session-tools/
```

(`cp -a` rather than `mv`/overwrite-in-place, so if any of these already exist — e.g. you
migrated, used the new tools for a while, then decided to roll back — you keep whatever
accumulated there since migrating rather than silently losing it. Check for conflicts first with
`diff -r` if that matters to you.)

**2. Reinstall the pre-migration binary:**

```sh
cd ~/repos/claude-code-session-tools
git checkout main   # or the pre-migration commit if main has since been fast-forwarded past it
uv tool install --reinstall ~/repos/claude-code-session-tools
ccst --version   # should print 0.18.0 again
```

**3. Optional cleanup** — the new `.db` files are harmless left in place (0.18.0 never reads
`~/.local/share/claude/`), but if you want a clean rollback, delete them yourself from a plain
terminal (not from inside a Claude Code session — this is a plain `rm`, no script involved, but
keep the same discipline): `~/.local/share/claude/{ccmsg,ccsched,sessions,telemetry,command-cache}.db`
and `~/.local/share/claude/claude-flags.json`.

---

## Summary of what testing found and fixed before this doc was finalized (2026-07-17)

- **Stray test-polluted `telemetry.db` at the real path**, cleared. An earlier phase's manual
  smoke test wrote 71 synthetic rows (session ids `test-session-1`/`-2`) to the real
  `~/.local/share/claude/telemetry.db` because it didn't override `CCST_DATA_HOME`. Cleared using
  the telemetry migration script's own documented recovery procedure. If you ever smoke-test
  these tools by hand, always set `CCST_DATA_HOME` to a scratch directory first.
- **`migrate_ccmsg_to_db.py`'s verify step used exact row-count equality**, which broke
  re-runnability (a re-run after success, or after an interrupted delete, would false-abort).
  Fixed to `>=`, matching the other three scripts.
- **`sessions.db` bulk writes were ~30 rows/sec** (a fresh connection + full DDL re-run per tag),
  which would have made a real ~24k-tag migration take ~13 minutes and look hung on a first run.
  Fixed to reuse one connection across the whole migration (~280x faster, ~8500+ rows/sec).
- **`migrate_ccmsg_to_db.py`'s final summary and verify step both used cursor *file* count where
  they meant cursor *row* count** (one file can hold several partition entries) — fixed to report
  and verify the real row count.
- **This doc's own predecessor, `docs/data-store-migration-backups.md`, had a wrong path/claim**
  for the sessions activity sentinels (said `~/.claude/projects/**/`, implied they're backed up
  and deleted) — corrected; see that file's own change note.
- **Real-data test scale exercised, no correctness bugs found**: 34 messages + 9,760 cursor files
  (ccmsg); 37,353 telemetry log lines / 36,887 events + 465 catchup rows spanning May–July 2026
  (telemetry); 9 jobs + 7,595 cursors + 7,599 throttles (ccsched); 4,037+ tags and 20 session
  directories sampled (sessions) — all against read-only copies of this machine's real data, in an
  isolated scratch sandbox, never touching the real paths.
