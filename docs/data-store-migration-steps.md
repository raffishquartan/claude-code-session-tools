# Data-store migration steps

> Step-by-step instructions for migrating this machine from the pre-0.19.0 flat-file/JSONL/TOML
> data stores to the SQLite (WAL mode) stores under `~/.local/share/claude/`. Companion to
> `docs/data-store-migration-backups.md` (the post-migration audit checklist) and
> `docs/superpowers/plans/2026-07-13-data-store-uplift-*.md` (the full design/implementation
> record). Written 2026-07-17 after `f/claude-data-store-uplift` (78 commits over `main`) passed
> its final review — see that review's findings folded in below.

**Do not run any step below until you've read "Findings from testing" at the bottom** — it may
change what you run first.

## What's changing on disk

| Subsystem | Old location(s) | New location |
|---|---|---|
| `ccmsg` | `~/.claude/cc-messages/` | `~/.local/share/claude/ccmsg.db` |
| `ccsched` | `~/.claude/cc-scheduler/` (`jobs.toml`, `state.json`, `.cursors/`, `.reconcile.*.ts`) | `~/.local/share/claude/ccsched.db` |
| session tags / activity / doctor-mutes | `~/.cache/claude/session-tags/*.tag`, `~/.claude/projects/**/.last-opened`/`.last-active`, `~/.claude/cc-doctor-mutes.json` | `~/.local/share/claude/sessions.db` |
| telemetry | `~/.cache/claude/logs/fires.jsonl` (+ `.1`/`.2`/`.3` rotation) | `~/.local/share/claude/telemetry.db` |
| command cache | `~/.cache/claude/logs/command-cache.db` | `~/.local/share/claude/command-cache.db` (path move only, same SQLite format) |
| claude-flags cache | wherever `claude_flags.py`'s old `_CACHE_DIR` pointed | `~/.local/share/claude/claude-flags.json` (path move + atomic-write fix, still flat JSON) |

Every migration script is **non-destructive by construction**: write the new store → verify
(row-count match + spot-check) → tar.gz-backup the old files to
`~/.local/share/claude/migration-backups/` → only then delete the old files. If verification
fails at any point, the script aborts and the old files are untouched.

## Step 0 — stop the clock on the old stores

Nothing here requires you to close Claude Code, but the four flat-file stores with pre-existing
data (`ccmsg`, `ccsched`, sessions, telemetry) are actively written to by any Claude Code session
that's currently open. **Close other Claude Code sessions/windows before migrating** so nothing
writes to a flat file mid-migration (a message sent or job that fires between the migration
script's read and its old-file delete would be silently lost — the script can't see writes that
happen after it already read the source).

## Step 1 — full pre-migration backup (belt-and-suspenders, independent of each script's own backup)

Each migration script takes its own backup before deleting anything (see the table above), but
run the standalone snapshot script first anyway — it captures everything in one place, before any
migration script has touched anything, in case you want to inspect or fully roll back later
without hunting through four separate `migration-backups/*.tar.gz` files:

```sh
bash scripts/backup_pre_migration.sh
```

Writes one timestamped tarball to `~/claude-data-store-migration-backup-<timestamp>.tar.gz`
containing byte-for-byte copies of every old-location file listed in the table above (whichever
of them actually exist on this machine). Read-only — touches nothing under `~/.claude/`,
`~/.cache/claude/`, or `~/.local/share/claude/`. Safe to run any number of times.

## Step 2 — dry-run every migration script

```sh
cd ~/repos/claude-code-session-tools
python3 scripts/migrate_ccmsg_to_db.py --dry-run
python3 scripts/migrate_fires_jsonl_to_telemetry_db.py --dry-run
uv run python -m cc_session_tools.cli.migrate_ccsched --dry-run
uv run python -m cc_session_tools.cli.migrate_sessions_db --dry-run
```

Each prints what it would do (row counts, files it would touch) without writing anything. Read
the output — in particular check the malformed-line/skipped-row counts the telemetry and ccmsg
scripts report; a nonzero count isn't necessarily a problem (both scripts treat this as
observability data, not silently-dropped content) but is worth a glance before proceeding.

## Step 3 — run each migration for real, one at a time

```sh
python3 scripts/migrate_ccmsg_to_db.py
python3 scripts/migrate_fires_jsonl_to_telemetry_db.py
uv run python -m cc_session_tools.cli.migrate_ccsched
uv run python -m cc_session_tools.cli.migrate_sessions_db
```

(`ccsched` and `sessions` are also reachable as `ccst migrate ccsched` / `ccst sessions migrate`
once you've reinstalled per Step 4 below — but reinstalling first would mean the *hooks* start
writing to the new stores before you've migrated the *old* data into them, so migrate first with
the module invocations above, using the still-installed 0.18.0 binary.)

Order doesn't matter between the four — each is independent. If any one reports a mismatch and
aborts, stop and read its output before re-running; do not pass `--force` on a first attempt (see
"Findings from testing" below for what `--force` actually does and when it's safe).

Command-cache and claude-flags need **no migration script** — they're path moves only, picked up
automatically the first time the new binary runs (Step 4).

## Step 4 — reinstall the CLI tools

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

## Step 5 — verify

```sh
ccl --limit 5              # most-recent-5 sessions, reading sessions.db
ccmsg list                 # reads ccmsg.db
ccsched list                # reads ccsched.db
ccst telemetry query --limit 5   # reads telemetry.db
```

All four should return data that looks like your pre-migration history, not empty results.

## Step 6 — audit and retain backups

Follow `docs/data-store-migration-backups.md`'s checklist. Keep both the standalone Step 1
tarball and each script's own `migration-backups/*.tar.gz` for 30 days, then delete.

---

## Findings from testing (folded in 2026-07-17)

- A stray, test-polluted `telemetry.db` (71 synthetic rows, session ids `test-session-1`/`-2`,
  left over from an earlier phase's manual smoke test that didn't override `CCST_DATA_HOME`) was
  found at the real `~/.local/share/claude/telemetry.db` path and has been cleared using the
  telemetry migration script's own documented recovery procedure before this doc was written.
  If you ever run a manual smoke test of these tools yourself, always set `CCST_DATA_HOME` to a
  scratch directory first — see the new `scripts/backup_pre_migration.sh` for the pattern, and
  note this is now flagged as a process gap in the exhaustive review (see the review write-up).
