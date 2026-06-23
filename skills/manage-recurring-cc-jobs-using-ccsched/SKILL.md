---
name: manage-recurring-cc-jobs-using-ccsched
description: Use when the user wants a local command to run on a recurring cadence on this laptop and to be caught up after the machine has been off - "run X every day", "check Tesco every morning", "weekly calendar sync", "schedule a local job", "add a recurring job", "/manage-recurring-cc-jobs-using-ccsched". Translates the request into a validated `ccsched add`. Do NOT use for cloud cron agents (that is `/schedule`) or for polling within one live session (that is `/loop`).
---

# Manage recurring CC jobs with ccsched

`ccsched` registers a local job that is **reconciled on Claude Code session
start**: if the laptop was off when a run was due, the next session backfills it.
There is no live timer to miss.

## First: which scheduler?

Disambiguate before doing anything:

1. **`ccsched`** (this skill) - a local command (argv) that should run on a
   cadence and be **caught up** after the laptop was off. Trigger: a periodic
   local task on this machine (Tesco check, calendar sync).
2. **`/schedule`** - a **cloud cron** agent that runs on Anthropic's
   infrastructure regardless of whether the laptop is on. Use when the run must
   happen at a wall-clock instant even with the laptop off.
3. **`/loop`** - poll/repeat **within one live session**. Use for "keep checking
   every 5 minutes while I work".

If the user wants away-from-laptop delivery (e.g. a phone push), `ccsched` cannot
do it - that needs an always-on host. Say so.

## Translate the request into `ccsched add`

Map the natural-language cadence to the grammar:

| User says | `--cadence` |
|-----------|-------------|
| every 6 hours | `every:6h` |
| every two weeks | `every:2w` |
| every other Friday from 9 Jan 2026 (custody changeover) | `every:2w@from=2026-01-09` |
| every morning at 9 | `daily@09:00` |
| Mondays at 7:30 | `weekly:mon@07:30` |
| the 1st of each month | `monthly:1@09:00` |
| the last day of each month | `monthly:31@09:00` (clamps to the real last day) |
| the 3rd Thursday each month | `monthly:thu#3@09:00` |
| the last Friday each month | `monthly:fri#last@18:00` |

Cadence forms at a glance: `every:<dur>` (units `s/m/h/d/w`), anchored `every:<dur>@from=YYYY-MM-DD` (drift-free, fixed grid — use for fortnightly-from-a-date), `daily@HH:MM`, `weekly:<dow>@HH:MM`, `monthly:<dom>@HH:MM` (dom > days-in-month clamps to the last day), `monthly:<dow>#<n>@HH:MM` (n ∈ 1..5 or `last`; a `#5` request is skipped in months with only four of that weekday).

Then:

```sh
ccsched add --id <kebab-id> --cadence <cadence> \
  --coalesce one \
  --catchup-window 7d --timeout 60s \
  --command <argv...>
```

- `--id` must be unique kebab-case.
- `--command` takes the **whole argv** after it (e.g. `--command ccst hooks run check-tesco-due`).
- `--coalesce one` (default) collapses N missed runs into one - right for
  "current state" jobs. Use `--coalesce each` only when every missed period must
  produce its own artefact.

## The idempotency contract

A registered job **must be safe to run late and safe to coalesce**. The scheduler
reduces re-runs but cannot make a non-idempotent command safe. Before adding a
job, confirm the command does the right thing when run once, late, after several
missed days.

## Migrating an existing always-fire SessionStart hook

If a job currently fires as a plain SessionStart hook (e.g. the Tesco or
calendar checks) and you add a `ccsched` entry for it, it will run **twice** until
the old hook is removed. Migrate in this order: (1) add the registry entry,
(2) then remove the old SessionStart hook. Never the reverse.

## Inspecting

- `ccsched list` - cadence, enabled, last_success, next_due.
- `ccsched status [<id>]` - recent ledger outcomes from fires.jsonl.
- `ccsched run <id> --force` - run now (for testing the command).
- `ccsched sweep` - run the reconcile sweep manually.
