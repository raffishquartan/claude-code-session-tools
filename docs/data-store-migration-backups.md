# Data-store migration backups — audit checklist

> Manual checklist, not automated. Each of Phases 2, 3, 4, and 5 (the phases with pre-existing
> flat-file data) takes its own `tar czf` backup of the pre-migration tree before deleting old
> files, per `2026-07-13-data-store-uplift-00-overview.md`'s binding decision 4. This document is
> how a human confirms, after the fact, that every migration actually ran and verified — not a
> substitute for reading each phase's own migration-script output at the time it ran.

## Expected backup files

All under `~/.local/share/claude/migration-backups/`:

| Phase | Subsystem | Expected filename pattern | Source tree backed up |
|---|---|---|---|
| 2 | `ccmsg` | `ccmsg-<date>.tar.gz` | `~/.claude/cc-messages/` |
| 3 | `ccsched` | `ccsched-<date>.tar.gz` | `~/.claude/cc-scheduler/` (`jobs.toml`, `state.json`, `.cursors/`, `.reconcile.*.ts`) |
| 4 | `sessions.db` | `sessions-<date>.tar.gz` | `~/.cache/claude/session-tags/*.tag`, `~/.claude/projects/**/.last-active`, `.last-opened` |
| 5 | `telemetry.db` | `telemetry-<date>.tar.gz` | `~/.cache/claude/logs/fires.jsonl` and rotated slots |

Phase 6 (`command-cache.db`, `claude-flags.json`) is a path move only — no data transformation, no
backup script, per §8.1 of the design spec ("path move only").

## Audit steps (run once, after all of Phases 2-6 have landed and been used at least once)

- [ ] `ls -la ~/.local/share/claude/migration-backups/` — confirm all four files above exist.
- [ ] For each, confirm the corresponding phase's own migration script printed a verification
      success message when it ran (row counts matched, spot-check passed) — check that phase's own
      terminal output/log if still available, or re-run the migration script's `--verify-only`
      mode (if it has one) against the new `.db` file and the still-present old flat files.
- [ ] Confirm the old flat-file trees listed in the table above are gone (each migration script
      only deletes them after its own backup+verify steps pass) — if any are still present, the
      corresponding migration did not complete; do not delete this checklist's backups until it
      has.

## Retention window

**Keep for 30 days after the audit above passes, then safe to delete manually.**

30 days is long enough to catch a subtle post-migration correctness bug in real day-to-day use
(a message or job whose old-format edge case the migration script's row-count/spot-check pass
missed) while the pre-migration data is still one `tar xzf` away, without accumulating
multi-gigabyte archives indefinitely. This is a manual step, not an automated retention policy —
automated pruning of these backups is explicitly out of scope for this migration (source spec
§10).

```bash
# after the retention window and the audit above both pass:
rm ~/.local/share/claude/migration-backups/*.tar.gz
```
