# Scheduled-tasks catch-up for a frequently-off laptop

- **Status:** Design — approved for spec-review (rev 2: detached execution + richer cadences)
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
multiple missed runs into one, depending on each job's declared semantics — **without
slowing down session start**.

## 2. Goals

1. Jobs run on a declared cadence **without a live timer that can be missed**.
2. Missed runs are **detected and back-filled** the next time the machine is in use.
3. Coalescing (N misses → 1 run, vs each-missed-instant-individually) is a **declared
   per-job property**, not a global default.
4. The catch-up sweep **never blocks or noticeably slows** session start — *regardless of how
   slow or numerous the jobs are*.
5. The user can see — **without prompting** — when each job last ran, what was missed, what
   was back-filled, and what failed.
6. New jobs can be **registered into a general registry**; the substrate is not hard-wired
   to today's handful of jobs.
7. The cadence grammar covers the user's real schedules, including **fortnightly cadences
   anchored to a fixed date** (the Max/Ella custody changeover) and **nth-weekday-of-month**
   ("3rd Thursday"), which cron cannot express.
8. Ships inside CCST with the same packaging/installer/upgrade story as existing tooling.

## 3. Non-goals (this design)

- **Telegram phone-push and any away-from-laptop delivery.** Push delivery runs *from this
  laptop*; if the laptop is off or asleep, nothing on it runs. "Reach my phone while the
  laptop is off" needs an always-on external host (cloud cron / VPS / phone agent), a
  separate project. Dropped from this design.
- **Any OS-level timer** — cron daemon, `systemd` timers, or a Windows Task Scheduler
  artefact. See §4 for why the trigger is Claude Code SessionStart only.
- **Driving full Claude sessions as jobs.** A job is a **command** (argv) run as a
  subprocess, not an interactive tool-using Claude conversation. Recurring work that needs
  Claude's judgment is split: the deterministic *check* is a job that surfaces a nudge; the
  agentic *do* stays interactive (or uses the built-in `/schedule` cloud-agent path). E.g.
  `check-tesco-shop-due` (a command) is a job; `do-tesco-shop` (a conversation) is not.
- Cross-machine sync. Single local machine.

## 4. Architecture overview

The trigger is **Claude Code `SessionStart` (and `UserPromptSubmit`) only**. This is
deliberate, and it dissolves the original problem rather than fighting it:

- Under WSL2 the distro (and any `systemd`/cron inside it) shuts down entirely when Windows
  sleeps or shuts down, and does not auto-restart on Windows boot. An in-WSL timer would
  itself be missed during exactly the downtime we care about. The only reliable "the machine
  is in use again" signal is the user starting / interacting with Claude Code.
- Once the trigger is session activity, there is **no live timer to miss**. The scheduler is
  a **reconciler**: on session activity, for each job, compare its declared cadence against
  its last successful run and back-fill what is owed. Miss-detection is arithmetic on
  `last_success` vs `cadence`.

**Execution is detached, off the session's critical path.** The SessionStart hook does only
the cheap part — reconcile (what is owed?) and **launch owed jobs as detached background
worker processes** — then returns instantly with a digest of *previously completed* runs read
from the ledger. Jobs then run in the background no matter how slow they are; each worker
records its own outcome to the ledger and state. Subsequent session activity (the next
`UserPromptSubmit` or `SessionStart`) **reaps** those outcomes and surfaces them. For a
*catch-up* scheduler this async semantics is correct, not a compromise: the jobs are late by
definition, so surfacing the result seconds later (often the same session) or next session is
exactly the intent. This makes goal 4 absolute — the per-job timeout kills a *background*
process, never the session.

Eight pieces, all shipped inside CCST, mirroring the messaging design's layout:

| Piece | What it is | Installed by |
|-------|-----------|--------------|
| **Registry** | `~/.claude/cc-scheduler/jobs.toml` — hand-curatable job definitions | lazily created; seeded empty |
| **State** | `~/.claude/cc-scheduler/state.json` — per-job timing + in-flight tracking | created lazily |
| **Cursors** | `~/.claude/cc-scheduler/.cursors/<session-uuid>.json` — per-session surfacing high-water mark | created lazily |
| **Ledger** | the **existing** `~/.claude/hooks/fires.jsonl` telemetry stream | already present |
| **Reconcile/surface hook** | `src/cccs_hooks/catchup.py`, run via `ccst hooks run catchup` | `ccst hooks install` |
| **Detached worker** | `ccsched _run-job <id>` — the background process that actually runs a job | (part of the CLI) |
| **`ccsched` CLI** | new `[project.scripts]` entry point; management surface | installer step 1 |
| **Logic lib + Skill** | `src/cc_session_tools/lib/scheduler/` + skill `manage-recurring-cc-jobs-using-ccsched` | package / `ccst skills install` |

## 5. Runtime data locations

All runtime data lives under the user's home, never in the repo. The root is overridable via
`CC_SCHEDULER_DIR` (so tests never touch real `~/.claude/`); the ledger reuses the telemetry
`CCCS_HOOKS_DIR` override.

```
~/.claude/cc-scheduler/
├── jobs.toml                 # registry (hand-edited and/or `ccsched add`)
├── state.json                # per-job: registered_at / last_success / last_attempt /
│                             #          consecutive_failures / in_flight
├── .cursors/<uuid>.json      # per-session last-surfaced ledger position
├── .run.<job-id>.lock        # O_EXCL per-job in-flight lock (transient)
└── (no global sweep lock — see §10)
~/.claude/hooks/
└── fires.jsonl               # shared telemetry ledger (reused, not new)
```

## 6. Registry and job record

`jobs.toml` is an array of `[[job]]` tables. TOML is chosen over JSON/JSONL because this is a
**hand-curated config document** mutated in place (not an append-only stream), so comments
and clean per-job tables matter.

```toml
[[job]]
id = "maxella-fortnightly-reminder"   # unique kebab id; primary key
cadence = "every:2w@from=2026-01-09"  # see §7
coalesce = "one"                      # "one" | "each"  (see §8)
command = ["ccst", "hooks", "run", "..."]   # any argv, run detached
surface = true                        # include this job's completed runs in the digest
enabled = true                        # disabled jobs are skipped but kept
catchup_window = "7d"                 # misses older than this are dropped (logged, not run)
timeout = "120s"                      # kills the detached worker process, not the session
```

Defaults when omitted: `coalesce = "one"`, `surface = true`, `enabled = true`,
`catchup_window = "7d"`, `timeout = "120s"`. (The timeout default can now safely exceed any
session-hook budget because workers run detached — §4.)

## 7. Cadence grammar

Declarative presets, deliberately *more* expressive than cron (which cannot express
fortnightly or nth-weekday — §9 of the prior discussion). The `@HH:MM` forms are interpreted
in **local wall-clock**; all stored timestamps are UTC. `<dur>` is an integer + unit suffix
(`s`/`m`/`h`/`d`/`w`).

| Form | Meaning |
|------|---------|
| `every:<dur>` | fixed interval since `last_success` (drifts if a run is late); e.g. `every:6h` |
| `every:<dur>@from=<YYYY-MM-DD>` | **anchored** interval — instants at `from, from+dur, from+2·dur, …`; drift-free. `every:2w@from=2026-01-09` = fortnightly from 9 Jan 2026 (the custody cadence) |
| `daily@HH:MM` | once per local day |
| `weekly:<dow>@HH:MM` | once per week (`dow` ∈ mon..sun) |
| `monthly:<dom>@HH:MM` | once per month on day-of-month `dom` |
| `monthly:<dow>#<n>@HH:MM` | **nth weekday of month**; `n` ∈ 1..5 or `last`. `monthly:thu#3@09:00` = 3rd Thursday; `monthly:fri#last@18:00` = last Friday |

A raw `cron:"<expr>"` escape hatch remains **deferred** — it would add a cron-parsing
dependency *and* still could not express the two forms above, so it buys nothing here. The
grammar can be extended later without changing stored data.

## 8. Coalescing (declared per job)

- **`one`** — N missed instants collapse to a single catch-up run (recommended default; fits
  jobs that only need current state once). After a successful run, `last_success` advances to
  `now`; a coalesced run is recorded with event `backfill`.
- **`each`** — every missed instant runs individually (rare; for jobs that must emit one
  artefact per period). Subject to the per-launch cap in §9.3; `last_success` advances one
  instant at a time so undrained instants run on later sweeps.

## 9. Reconcile, launch, and reap

Two phases, both invoked from the same hook (`ccst hooks run catchup`, also `ccsched sweep`),
which receives session context (uuid, cwd) on stdin:

### 9.1 Reconcile + launch (cheap; bounded; never blocks)

Per enabled job, in registry order:

1. **Skip if in-flight.** If `state.in_flight` is set and its worker is alive, the job is
   already running — record nothing, move on (prevents overlapping runs).
2. **Compute owed.** `owed` = scheduled instants in `(last_success, now]` per cadence. If the
   job has never run, the baseline is `registered_at` (stamped by `ccsched add`; a job
   hand-added to `jobs.toml` with no state entry is stamped `registered_at = now` on first
   sight, so it never retroactively back-fills from epoch). Instants older than
   `now − catchup_window` are dropped and logged `skip_expired` (visible, never silent).
3. **Nothing owed →** move on.
4. **Launch detached.** Spawn `ccsched _run-job <id> --instants <k>` as a detached background
   process (`k` = 1 for `coalesce: one`; `min(owed, cap)` for `each`). Record the spawn
   intent (the worker stamps `in_flight` itself, §9.4). Do **not** wait for it.

This phase touches only small files and spawns processes — it is trivially within any
session-hook budget no matter how many/slow the jobs. A soft cap on *launches per sweep*
(default 20) bounds pathological fan-out; overflow is logged `defer` and launched next sweep.

### 9.2 The detached worker (`ccsched _run-job <id> --instants k`)

1. **Acquire the per-job in-flight lock** (`O_EXCL` on `.run.<id>.lock`). If it already
   exists and its holder is alive, exit immediately (another worker won the race). Stale lock
   (holder dead) is reclaimed.
2. Stamp `state.in_flight = {pid, started_at, instants: k}` (atomic write).
3. Run `command` with the job's `timeout` (kills the subprocess on overrun). For
   `coalesce: each`, run up to `k` times sequentially.
4. **On success:** advance `last_success` (to `now` for `one`; per satisfied instant for
   `each`), reset `consecutive_failures`, append `run`/`backfill` ledger events (capturing
   stdout for the digest if `surface`).
5. **On failure / timeout:** append a `fail` event, **do not** advance `last_success`,
   increment `consecutive_failures`.
6. **Always:** clear `in_flight` and release the lock (even on crash — a dead holder's lock
   is reclaimable).

### 9.3 Surface (reap)

The hook also emits a digest of ledger entries newer than this session's cursor
(`.cursors/<uuid>.json`), then advances the cursor. This runs on every SessionStart and
UserPromptSubmit, so a job launched at session start whose worker finishes a few seconds later
is surfaced at the user's next prompt — same session. The per-`each` launch cap (default 5)
bounds back-fill volume after a long outage; overflow drains over later sweeps and is logged.

## 10. Concurrency and idempotency

- **No global sweep lock.** Two sessions reconciling at once is safe: both may try to launch
  the same owed job, but the **per-job `O_EXCL` in-flight lock** (§9.2 step 1) guarantees
  exactly one worker proceeds; the loser exits at once.
- **State writes** use the existing `write_json_atomic`-style `.tmp`-swap.
- **Ledger appends** use the existing telemetry `O_APPEND` path.
- **Idempotency is a job contract.** Coalescing reduces re-runs but cannot make a
  non-idempotent job safe; the skill (§12) reminds the author that a registered job must be
  safe to run late and to coalesce.

## 11. Observability

The "thick" model: a durable ledger plus a no-prompt surface.

- **Digest** — the hook emits a compact `hookSpecificOutput.additionalContext` block, one
  line per *completed* run newer than the session cursor:
  - `✓ ran tesco-shop-check (1d overdue)`
  - `✗ calendar-sync failed (2nd consecutive) — see fires.jsonl`
  - `⏳ job foo: 7 backfills deferred`
  - `▶ launched calendar-sync (running in background)` — optional, for just-launched jobs

  Completed runs with `surface: false` are omitted; **failures always surface**.
- **Ledger** — every action is one JSONL line in `fires.jsonl`:
  `{ts, hook: "catchup", job_id, event, owed, ran, exit_code, duration_ms, error}` where
  `event ∈ {launch, run, backfill, skip_expired, defer, fail}`. `ccsched status` reads it;
  existing telemetry rotation/trim applies unchanged.

## 12. `ccsched` CLI and the management skill

New entry point `ccsched = "cc_session_tools.cli.ccsched:main"`. Logic in
`src/cc_session_tools/lib/scheduler/` (cadence parse, due-computation, coalescing, state I/O,
locking, worker, digest). The CLI is a thin argparse layer matching `ccd.py` conventions.

| Command | Purpose |
|---------|---------|
| `ccsched add` | Register a job. Flags: `--id`, `--cadence`, `--coalesce`, `--command …`, `--surface/--no-surface`, `--catchup-window`, `--timeout`. |
| `ccsched list` | Table: id, cadence, coalesce, enabled, last_success, next_due (computed on the fly from cadence + last_success, not cached), in-flight. |
| `ccsched edit <id>` / `enable <id>` / `disable <id>` / `remove <id>` | Manage existing jobs. |
| `ccsched run <id> [--force]` | Run one job now, **synchronously**, in the foreground (manual/debug). |
| `ccsched status [<id>]` | Recent ledger entries / last outcomes / in-flight. |
| `ccsched sweep` | Run the reconcile+launch+surface phase manually (same path as the hook). |
| `ccsched _run-job <id> --instants k` | Internal detached worker (§9.2); not for direct use. |

Validation at the CLI/schema boundary: unique kebab id, parseable cadence (incl. anchored
`from=` and `#n` forms), non-empty argv, `coalesce ∈ {one, each}`, positive durations.
Internals trust validated input.

The single skill **`manage-recurring-cc-jobs-using-ccsched`** (`skills/…`, deployed by
`ccst skills install`) guides Claude to translate a natural-language request ("remind me every
other Friday", "3rd Thursday each month") into a validated `ccsched add` with the right
cadence form, remind the author of the idempotency contract, and — critically —
**disambiguate the three schedulers**: local reconcile job → `ccsched`; remote cloud cron
agent → built-in `/schedule`; poll within one live session → `/loop`.

No other skills are needed: reconcile/launch/surface are pure automation, and existing
job-skills (`check-tesco-shop-due`, `sync-personal-blocked-time-to-work-calendar`) are not
rewritten — they become registry entries whose `command` points at their existing entrypoints.

## 13. Delivery hooks

Two hooks, both `ccst hooks run catchup` (the module distinguishes by the hook event on
stdin), registered in `config/hooks-bundle.json` and merged via `merge_hook_settings` (dedup
by event + matcher + command):

- **`SessionStart`** — reconcile + launch owed jobs detached + surface completed-since-cursor.
- **`UserPromptSubmit`** — surface completed-since-cursor (reap); also a *throttled* reconcile
  (so sub-daily cadences fire during a long session without re-reconciling on every keypress).

`catchup` is added to `HOOK_VERBS`, dispatching to `cccs_hooks.catchup`.

## 14. CCST packaging, installer, docs, upgrade

- **pyproject.toml:** add the `ccsched` script; add scheduler test paths.
- **config/hooks-bundle.json:** add the SessionStart and UserPromptSubmit `catchup` entries.
- **ccst.py:** add `catchup` to `HOOK_VERBS` / `HOOK_DESCRIPTIONS`.
- **install-everything.sh:** no new top-level step — registry is lazy-created/seeded empty;
  existing steps pick up the new CLI, skill, and hooks idempotently, so **existing users get
  the capability by re-running the installer with `--upgrade`**.
- **README.md / CHANGELOG.md:** a "## Scheduled-task catch-up" section and `### Added`
  entries; version bump 0.13.0 → 0.14.0 (messaging shipped 0.13.0).

## 15. Error handling

- The hook **never blocks a session**: any hook-level failure degrades to empty
  `additionalContext` (the session proceeds), logged to telemetry via the env-honouring
  ledger path — never `except: pass`.
- A **detached worker** owns its own error reporting: command failure/timeout → `fail` event
  + state update; a worker crash leaves a stale in-flight lock that the next reconcile
  reclaims (and logs).
- A **per-job** failure is isolated — never affects sibling jobs or the sweep.
- An **unparseable `jobs.toml`** surfaces a clear digest warning and runs nothing; no crash.
- Validation errors at the CLI boundary return a structured non-zero exit; no silent success.

## 16. Testing strategy

Matches the existing pytest + subprocess + `tmp_path` convention; `CC_SCHEDULER_DIR` and
`CCCS_HOOKS_DIR` redirect all I/O so tests never touch real `~/.claude/`.

- **lib unit (pure, injected `now`):** cadence parse — every form incl. `every:Nw@from=`
  anchored intervals and `monthly:<dow>#n`/`#last` nth-weekday, plus invalids; due-computation
  (misses across day/week/month boundaries, anchored-interval drift-freeness, nth-weekday
  resolution incl. months with only 4 of a weekday for `#5`, `catchup_window` expiry, DST);
  coalesce `one` vs `each`; state round-trip; per-job in-flight lock race (two workers → one
  proceeds); launch cap.
- **CLI:** each subcommand happy path + each validation branch (dup id, bad cadence incl.
  malformed `from=`/`#n`, empty command, unknown id, bad coalesce).
- **Worker (`_run-job`):** success advances state + writes `run`/`backfill`; failure/timeout
  writes `fail` and does not advance; in-flight stamped then cleared; stale-lock reclamation.
- **Hook:** synthetic stdin → reconcile launches a (mocked) detached worker and does **not**
  block; surface emits the digest from ledger-since-cursor and advances the cursor; failure
  path writes to the `CCCS_HOOKS_DIR` ledger (not real `~/.claude`); unparseable registry path.
- Every `try/except` in a handler gets a failure-path test (coding standard).

## 17. Open implementation details (resolved during writing-plans, no user input needed)

1. Exact `additionalContext` digest wording and overdue-duration phrasing.
2. DST policy for wall-clock cadences (naive-local-calendar: one fire per calendar occurrence,
   never double across a transition).
3. Detached-spawn primitive (`subprocess.Popen` with `start_new_session=True` + redirected
   std streams vs double-fork); cross-checked for WSL2.
4. Stale-lock reclamation specifics (pid-liveness check vs age threshold).
5. Cursor representation (per-session last-surfaced ledger offset vs timestamp).
6. Duration/date parser location (shared helper in `lib/scheduler/`).

## 18. Deferred / spun out

- **`cron:"<expr>"` cadence** — deferred; adds a dependency and still can't express the
  in-scope fortnightly / nth-weekday forms (§7).
- **Telegram phone-push / away-from-laptop delivery** — out of scope; needs an always-on
  external host (§3).
- **Migrating existing always-fire SessionStart job-hooks** (Tesco, calendar) into cadenced
  registry jobs — a documented follow-up via the skill, not part of this build. The skill must
  warn about the transition double-fire window: a job that still fires as a plain SessionStart
  hook *and* as a registry entry runs twice until the old hook is removed, so migration order
  is "add registry entry, then remove old hook."
