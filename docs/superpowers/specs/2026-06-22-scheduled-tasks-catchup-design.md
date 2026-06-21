# Scheduled-tasks catch-up for a frequently-off laptop

- **Status:** Design — approved for spec-review
- **Date:** 2026-06-22
- **Owner:** raffishquartan
- **Target repo:** `claude-code-session-tools` (CCST)
- **Relates to:** consumes nothing; is the substrate that
  `2026-06-20-inter-session-messaging-design.md` deferred (its §3, §20). Messaging's
  Telegram push was the original motivating consumer but is **explicitly dropped** from
  scope here (see §3).

## 1. Problem

Periodic jobs (Tesco-shop checks, calendar syncs, and similar) need to run on a fixed
cadence, but this laptop is frequently off, asleep, or offline. Plain cron assumes the
machine is on at the scheduled instant:

- A job scheduled for 09:00 simply never fires if the laptop was off at 09:00.
- Nothing notices the miss, so no catch-up ever happens.

A mechanism is needed that, whenever the machine *is* in use, determines which scheduled
runs were missed while it was off and back-fills them — running once-late, or coalescing
multiple missed runs into one, depending on each job's declared semantics.

## 2. Goals

1. Jobs run on a declared cadence **without a live timer that can be missed**.
2. Missed runs are **detected and back-filled** the next time the machine is in use.
3. Coalescing (N misses → 1 run, vs each-missed-instant-individually) is a **declared
   per-job property**, not a global default.
4. The catch-up sweep **never blocks or noticeably slows** session start.
5. The user can see — **without prompting** — when each job last ran, what was missed, what
   was back-filled, and what failed.
6. New jobs can be **registered into a general registry**; the substrate is not hard-wired
   to today's handful of jobs.
7. Ships inside CCST with the same packaging/installer/upgrade story as existing tooling.

## 3. Non-goals (this design)

- **Telegram phone-push and any away-from-laptop delivery.** Push delivery runs *from this
  laptop*; if the laptop is off or asleep, nothing on it runs — not Claude Code, not a WSL
  timer, not a Windows scheduled task. "Reach my phone while the laptop is off" is
  physically impossible without an always-on external host (cloud cron / VPS / phone-side
  agent), which is a separate project. Telegram push is therefore dropped from this design.
- **Any OS-level timer** — cron daemon, `systemd` timers, or a Windows Task Scheduler
  artefact. See §4 for why the trigger is Claude Code SessionStart only.
- Cross-machine sync. Single local machine.
- Driving full Claude sessions as jobs. A job is a **command** (argv), not a conversation.

## 4. Architecture overview

The trigger is **Claude Code `SessionStart` only**. This is deliberate, and it dissolves the
original problem rather than fighting it:

- Under WSL2 the distro (and any `systemd`/cron inside it) shuts down entirely when Windows
  sleeps or shuts down, and does not auto-restart on Windows boot. An in-WSL timer would
  itself be missed during exactly the downtime we care about. The only reliable "the machine
  is in use again" signal is the user starting Claude Code.
- Once the trigger is SessionStart, there is **no live timer to miss**. The scheduler
  becomes a **reconciler**: on each session start, for each job, compare its declared cadence
  against its last successful run and back-fill what is owed. Miss-detection is arithmetic on
  `last_success` vs `cadence` — no persisted timer, no OS scheduling facility.

Seven pieces, all shipped inside CCST, mirroring the messaging design's layout:

| Piece | What it is | Installed by |
|-------|-----------|--------------|
| **Registry** | `~/.claude/cc-scheduler/jobs.toml` — hand-curatable job definitions | lazily created; seeded empty by installer |
| **State** | `~/.claude/cc-scheduler/state.json` — per-job `last_success` / `last_attempt` | created lazily by the sweep |
| **Ledger** | the **existing** `~/.claude/hooks/fires.jsonl` telemetry stream | already present |
| **Reconcile sweep** | `src/cccs_hooks/catchup.py`, run via `ccst hooks run catchup` | `ccst hooks install` |
| **`ccsched` CLI** | new `[project.scripts]` entry point; management surface | installer step 1 (`uv tool install`) |
| **Logic lib** | `src/cc_session_tools/lib/scheduler/` — parsing, due-computation, runner | (part of the package) |
| **Skill** | `manage-recurring-cc-jobs-using-ccsched` | `ccst skills install` (symlink) |

## 5. Runtime data locations

All runtime data lives under the user's home, never in the repo:

```
~/.claude/cc-scheduler/
├── jobs.toml          # registry (hand-edited and/or `ccsched add`)
├── state.json         # per-job last_success / last_attempt / consecutive_failures
└── .sweep.lock        # O_EXCL sweep lock (transient)
~/.claude/hooks/
└── fires.jsonl        # shared telemetry ledger (reused, not new)
```

The repo ships only code and the hook-bundle entry (in `config/`). The registry is seeded
empty; jobs are added by the user via the skill / CLI.

## 6. Registry and job record

`jobs.toml` is an array of `[[job]]` tables. TOML is chosen over JSON/JSONL because this is a
**hand-curated config document** mutated in place (not an append-only stream), so comments
and clean per-job tables matter.

```toml
[[job]]
id = "tesco-shop-check"        # unique kebab id; primary key
cadence = "daily@09:00"        # see §7
coalesce = "one"               # "one" | "each"  (see §8)
command = ["ccst", "hooks", "run", "check-tesco-due"]  # any argv
surface = true                 # inject this job's result into the SessionStart digest
enabled = true                 # disabled jobs are skipped but kept
catchup_window = "7d"          # misses older than this are dropped (logged, not run)
timeout = "60s"                # per-run kill
```

Defaults when a field is omitted: `coalesce = "one"`, `surface = true`, `enabled = true`,
`catchup_window = "7d"`, `timeout = "60s"`.

## 7. Cadence grammar

Declarative presets. The `@HH:MM` forms are interpreted in **local wall-clock** (the user
expects "9am local"); all stored timestamps are UTC.

| Form | Meaning |
|------|---------|
| `every:<dur>` | fixed interval since `last_success` (e.g. `every:6h`, `every:30m`) |
| `daily@HH:MM` | once per local day at `HH:MM` |
| `weekly:<dow>@HH:MM` | once per week (`dow` ∈ mon..sun) |
| `monthly:<dom>@HH:MM` | once per month on day-of-month `dom` |

`<dur>` is an integer + unit suffix (`s`/`m`/`h`/`d`). A raw `cron:"<expr>"` escape hatch is
**deferred** (it would add a cron-parsing dependency); the four forms above cover every job
in scope and the grammar can be extended later without changing stored data.

## 8. Coalescing (declared per job)

- **`one`** — N missed instants collapse to a single catch-up run. The recommended default;
  fits jobs that only need current state once (Tesco check, calendar sync). After running,
  `last_success` advances to `now`.
- **`each`** — every missed instant runs individually (rare; for jobs that must emit one
  artefact per period). Subject to the per-sweep cap in §9.3; `last_success` advances one
  instant at a time so undrained instants run on later sweeps.

## 9. Reconcile algorithm

The sweep (`ccst hooks run catchup`, also `ccsched sweep`) does, per enabled job, in
registry order:

1. **Compute owed.** `owed` = the set of scheduled instants in `(last_success, now]` per the
   job's cadence. If the job has never run, the baseline is its registration time. Instants
   older than `now − catchup_window` are dropped and logged as `skip_expired` (visible, never
   silent).
2. **Nothing owed →** record nothing, move on.
3. **`coalesce: one` and owed ≥ 1 →** run the command **once**.
4. **`coalesce: each` →** run once per owed instant, up to the per-sweep cap (§9.3);
   remaining instants are logged `defer` and drained on later sweeps.
5. **On success:** advance `last_success` (to `now` for `one`; to the satisfied instant for
   `each`), reset `consecutive_failures`, append `run`/`backfill` ledger events, and collect
   the job's stdout for the digest if `surface`.
6. **On failure:** append a `fail` event, **do not** advance `last_success` (so it retries on
   the next sweep), increment `consecutive_failures` (surfaced in the digest). At most **one
   attempt per job per sweep** — no in-sweep retry hammering.

### 9.1 SessionStart must stay fast

The whole sweep is **time-boxed** (default ~10s, configurable). When the budget is exhausted,
remaining jobs are left untouched (their state is unchanged, so they are simply picked up on
the next sweep) — nothing is marked run or failed.

### 9.2 Sequential execution

Jobs run **sequentially** within the budget — deterministic, audit-friendly, and trivially
correct. Parallelism is YAGNI for the cadences in scope.

### 9.3 Per-sweep back-fill cap

`coalesce: each` after a long outage is capped (default 5 runs/sweep/job). Overflow is logged
(`"12 owed, ran 5, 7 deferred"`) and drained over subsequent sweeps. No silent truncation.

## 10. Concurrency and idempotency

- **Sweep lock.** A single `O_EXCL` lock at `~/.claude/cc-scheduler/.sweep.lock` guards the
  whole sweep. If two sessions start near-simultaneously, the first acquires the lock and
  runs; the second sees `EEXIST` and skips its sweep entirely (the next session start, or the
  optional UserPromptSubmit secondary trigger in §13, covers it). The lock stores the holder
  pid + start time so a stale lock (holder dead) can be reclaimed.
- **State writes** use the existing `write_json_atomic`-style `.tmp`-swap.
- **Ledger appends** use the existing telemetry `O_APPEND` path (atomic for small lines).
- **Idempotency is a job contract.** Coalescing reduces re-runs, but the scheduler cannot
  make a non-idempotent job safe; the skill (§12) reminds the author that a registered job
  must be safe to run late and safe to coalesce.

## 11. Observability

The "thick" model: a durable ledger plus a no-prompt surface.

- **Digest** — after the sweep, the SessionStart hook emits a compact
  `hookSpecificOutput.additionalContext` block, one line per job that *did* something:
  - `✓ ran tesco-shop-check (1d overdue)`
  - `✗ calendar-sync failed (2nd consecutive) — see fires.jsonl`
  - `⏳ job foo: 7 backfills deferred`

  Jobs that succeeded with `surface: false` are omitted to avoid clutter. **Failures always
  surface**, regardless of `surface`.
- **Ledger** — every sweep action is one JSONL line in `fires.jsonl`:
  `{ts, hook: "catchup", job_id, event, owed, ran, exit_code, duration_ms, error}` where
  `event ∈ {run, backfill, skip_expired, defer, fail}`. `ccsched status` reads it for the
  richer on-demand view; existing telemetry rotation/trim applies unchanged.

## 12. `ccsched` CLI and the management skill

New entry point:

```toml
ccsched = "cc_session_tools.cli.ccsched:main"
```

Logic lives in `src/cc_session_tools/lib/scheduler/` (cadence parse, due-computation,
coalescing, runner, state I/O, digest formatting). The CLI is a thin argparse layer matching
`ccd.py` conventions (`_build_parser()`, `main(argv=None) -> int`, `--version`).

| Command | Purpose |
|---------|---------|
| `ccsched add` | Register a job. Flags: `--id`, `--cadence`, `--coalesce`, `--command …` (argv), `--surface/--no-surface`, `--catchup-window`, `--timeout`. |
| `ccsched list` | Table: id, cadence, coalesce, enabled, last_success, next_due. |
| `ccsched edit <id>` | Modify fields of an existing job. |
| `ccsched enable <id>` / `disable <id>` | Toggle without removing. |
| `ccsched remove <id>` | Delete a job from the registry. |
| `ccsched run <id> [--force]` | Run one job now (records to ledger). |
| `ccsched status [<id>]` | Recent ledger entries / last outcomes. |
| `ccsched sweep` | Run the reconcile sweep manually (same path as the hook). |

Validation lives at the CLI/schema boundary (per coding standards): unique kebab id,
parseable cadence, non-empty argv, `coalesce ∈ {one, each}`, positive durations. Internals
trust validated input.

The single skill **`manage-recurring-cc-jobs-using-ccsched`** (`skills/…`, deployed by
`ccst skills install`) guides Claude to translate a natural-language request ("run X every
week") into a validated `ccsched add`, remind the author of the idempotency contract, and —
critically — **disambiguate the three schedulers**:

- local reconcile-on-session-start job → this / `ccsched`;
- remote cloud cron agent → built-in `/schedule`;
- poll within one live session → `/loop`.

No other skills are needed: the sweep and digest are pure automation, and existing job-skills
(`check-tesco-shop-due`, `sync-personal-blocked-time-to-work-calendar`) are not rewritten —
they become registry entries whose `command` points at their existing entrypoints.

## 13. Delivery hook

One `SessionStart` hook, registered in `config/hooks-bundle.json` and merged via
`merge_hook_settings` (dedup by event + matcher + command, matching the existing convention):

```
SessionStart → ccst hooks run catchup
```

`catchup` is added to `HOOK_VERBS`, dispatching to `cccs_hooks.catchup`, which reads session
context from stdin JSON, runs the sweep via `lib/scheduler/`, and emits the digest. An
**optional** throttled `UserPromptSubmit` secondary trigger (for sub-daily cadences during a
long session, reusing the messaging cursor idea) is **off by default** and can be enabled per
install; v1 ships SessionStart only.

## 14. CCST packaging, installer, docs, upgrade

- **pyproject.toml:** add the `ccsched` script; add scheduler test paths to
  `[tool.pytest.ini_options].testpaths`.
- **config/hooks-bundle.json:** add the SessionStart `catchup` entry.
- **ccst.py:** add `catchup` to `HOOK_VERBS`.
- **install-everything.sh:** no new top-level step — the registry is lazy-created and seeded
  empty; steps 1–3 pick up the new CLI, skill, and hook idempotently, so **existing users get
  the capability by re-running the installer with `--upgrade`**.
- **README.md / CHANGELOG.md:** a new "## Scheduled-task catch-up" section (registry,
  `ccsched`, the reconcile model, the digest) and `### Added` entries for `ccsched`, the hook,
  and the skill.

## 15. Error handling

- The sweep **never blocks a session**: any hook-level failure degrades to an empty
  `additionalContext` (the session proceeds), logged to telemetry — never `except: pass`.
- A **per-job** failure is isolated: it is recorded and surfaced but never aborts the sweep or
  affects sibling jobs.
- An **unparseable `jobs.toml`** surfaces a clear digest warning ("cc-scheduler: jobs.toml
  failed to parse — no jobs ran") and runs nothing; it does not crash the session.
- Validation errors at the CLI boundary return a structured non-zero exit; no silent success.

## 16. Testing strategy

Matches the existing pytest + subprocess + `tmp_path` convention; never touches real
`~/.claude/`.

- **lib unit tests:** cadence parse (every form + invalids), due-computation (misses across
  day/week/month boundaries, `catchup_window` expiry, DST transition behaviour), coalesce
  `one` vs `each`, state round-trip, sweep-lock race (two concurrent sweeps → exactly one
  runs), per-sweep cap.
- **CLI tests (subprocess):** each subcommand happy path + each validation branch (dup id,
  bad cadence, empty command, unknown id, bad coalesce).
- **Hook tests:** synthetic stdin JSON → assert the `additionalContext` digest and the
  resulting `state.json` + ledger writes; failure path (job exits non-zero → `fail` event +
  surfaced); budget/cap path (many owed → `defer` recorded); unparseable registry path.
- Every `try/except` in a handler gets a failure-path test (coding standard).

## 17. Open implementation details (resolved during writing-plans, no user input needed)

1. Exact `additionalContext` digest wording/format and overdue-duration phrasing.
2. Stale-lock reclamation policy (pid-liveness check vs age threshold).
3. Whether `next_due` in `ccsched list` is computed or cached.
4. DST edge-case policy for the wall-clock cadences (skip-once vs run-once on the
   ambiguous/again hour).
5. Duration/`catchup_window` parser location (shared helper in `lib/scheduler/`).

## 18. Deferred / spun out

- **`cron:"<expr>"` cadence** — deferred to avoid a cron-parsing dependency (§7).
- **`UserPromptSubmit` secondary trigger** — built but off by default (§13).
- **Telegram phone-push / away-from-laptop delivery** — out of scope; needs an always-on
  external host (§3).
- **Migrating existing always-fire SessionStart job-hooks** (Tesco, calendar) into cadenced
  registry jobs — a documented follow-up the user performs via the skill, not part of this
  build.
