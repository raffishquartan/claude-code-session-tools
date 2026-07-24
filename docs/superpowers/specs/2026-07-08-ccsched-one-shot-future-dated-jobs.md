# Request: one-shot, non-recurring, future-dated jobs for `ccsched`

You are a Claude Code session picking this up. This is a feature request, not a
finished design - investigate, decide, and write a proper design/plan doc
alongside the existing `2026-06-22-scheduled-tasks-catchup-design.md` in this
same `specs/` folder if you think it's warranted, following this repo's usual
superpowers writing-plans workflow.

## Motivating case (why this came up)

While working a PBT project session on 2026-07-08, I needed to make sure a
specific action (updating an ICO data-protection complaint) happens by a fixed
future date - 19 September 2026 - roughly ten weeks out. I checked whether
`ccsched` could register that as a "fire once, on this date" job and found it
can't: `ccsched add --cadence` only accepts the recurring grammar in
`cadence.py` (`every:`, `every:@from=`, `daily@`, `weekly:`, `monthly:<dom>@`,
`monthly:<dow>#<n>@`). There is no one-shot form. I fell back to writing the
date into the project's own `DEADLINES.md` (read at every future session start
per that project's CLAUDE.md) plus a plain entry in the project's persistent
task list (`TaskCreate` / `~/.claude/tasks/<project>/`). That works for a
project that has its own deadline-tracking convention, but it's not a general
answer - most projects don't have a `DEADLINES.md`, and the persistent task
list has no due-date field or date-triggered surfacing logic; it just always
shows the full list.

The `CronCreate` tool available inside a live Claude Code session is *not* a
substitute either - per its own description it's session-only (nothing written
to disk) and even recurring jobs auto-expire after 7 days. It cannot reach ten
weeks out.

`ccsched`'s actual architecture - reconcile on `SessionStart`, no live timer,
catch-up after the laptop was off - is exactly the right foundation for this.
It's the cadence grammar that's missing the one-shot case, not the mechanism.

## What I found on investigation

- Real source: `claude-code-session-tools` (package `cc-session-tools`,
  installed via `uv tool`). The `hooks/ccsched/` folder in
  `claude-code-config-sync` is unrelated (just a `remind-mcp-security.sh`
  script) - don't be misled by that folder existing, same trap as backlog
  entry B-001 in that repo's `self-improvement-backlog.md`.
- CLI entry: `src/cc_session_tools/cli/ccsched.py`.
- Cadence grammar + parser: `src/cc_session_tools/lib/scheduler/cadence.py`.
  `CadenceKind` is a closed enum (`EVERY`, `EVERY_ANCHORED`, `DAILY`, `WEEKLY`,
  `MONTHLY`, `MONTHLY_NTH`); `parse_cadence()` dispatches by regex match and
  raises `CadenceError` on no match. Docstring explicitly notes a `cron:`
  escape hatch was "deliberately not supported (deferred - see §18)" - §18 of
  the design doc lists what was deferred, and a one-shot/`once` form is **not**
  among the items already considered and rejected. This is a genuine gap, not
  a revisited decision.
- Due-computation: `src/cc_session_tools/lib/scheduler/due.py`. `owed()` and
  `next_due()` both dispatch on `cadence.kind`; every existing kind has an
  enumerator function (`_every_instants`, `_daily_instants`, etc.) called from
  `_all_instants()`. A `ONCE` kind would need trivial equivalents - `owed()`
  returns `[anchor]` if the anchor falls in `(baseline, now]` and within the
  catch-up window, else `[]`; `next_due()` returns the anchor if it hasn't
  fired yet.
- Job enable/disable already exists: `registry.py` has `enabled: bool` on
  `JobSpec` and a `set_enabled(job_id, enabled)` function. Auto-disabling a
  `ONCE` job after it successfully fires is a call to that existing function,
  not new plumbing.
- The management skill (`skills/manage-recurring-cc-jobs-using-ccsched/`,
  present in both this repo and mirrored at
  `~/.claude/skills/manage-recurring-cc-jobs-using-ccsched/`) explicitly
  documents three schedulers and when to use each: `ccsched` (this one,
  laptop-local, catch-up), `/schedule` (cloud cron, "runs on Anthropic's
  infrastructure regardless of whether the laptop is on"), and `/loop`
  (session-local polling).

  **Open question RESOLVED 2026-07-08, in a follow-up turn of the same PBT
  session that raised this request.** `/schedule` is backed by the
  `RemoteTrigger` tool (`POST /v1/code/triggers`), which is genuinely
  server-side and NOT the same ephemeral backend as `CronCreate`. Confirmed
  by listing the PBT project's live triggers: one-shot jobs are fully
  supported (`run_once_at: "<ISO8601>"`, `ended_reason: "run_once_fired"`
  once fired), each trigger spins up a fresh Claude Code session in its own
  `environment_id` at the scheduled UTC instant, and MCP connectors (e.g.
  Gmail) can be attached so that session can genuinely take an external
  action - such as sending Chris an email - not just re-inject a reminder
  prompt. Concrete example already in production use for this exact project
  (`trig_01AGCjmcQdsHihViKHa59gRU`, "PBT reminder - 27 Jul - Article 16
  response due"): a one-shot trigger with a Gmail MCP connector, prompted to
  "send ONE short plain-text email to cfogelberg@gmail.com and then stop."
  10 similar one-shot/recurring triggers exist for that project's WS-03
  monitoring cadence, several already fired successfully
  (`ended_reason: "run_once_fired"`).

  **Conclusion: `/schedule` already fully solves the "fire once, on a fixed
  future wall-clock date, regardless of whether any laptop or session is
  alive" case - including genuine external delivery (email), which
  `ccsched`'s SessionStart-reconciliation model cannot do at all** (it can
  only act once a session happens to start on or after the due date, and its
  only "delivery" is dispatch of a canned Claude Code command inside that
  session). Recommendation: **do not build one-shot support into `ccsched`
  as a `/schedule` replacement.** The remaining gap `ccsched` genuinely fills
  is different: local, laptop-only automation, cheaper/simpler to set up for
  a user who is not going through the `RemoteTrigger` API surface directly,
  and catch-up semantics for *recurring* local jobs. If a one-shot form is
  still wanted in `ccsched`, scope it as "convenience wrapper for a
  same-laptop one-off," not as a durable/remote-delivery mechanism - and say
  so explicitly wherever it's documented, so nobody reaches for it expecting
  `/schedule`-grade durability.

## Proposed shape (starting point, not final)

- New cadence form: `once@YYYY-MM-DD` or `once@YYYY-MM-DDTHH:MM`. Parses to a
  new `CadenceKind.ONCE` with an `anchor: date | datetime`.
- `owed()`: the anchor instant, once, if it falls in the catch-up window and
  hasn't already fired.
- `next_due()`: the anchor if not yet fired; after firing, the job should not
  produce a `next_due` at all (this is the main place existing assumptions
  break - every current `next_due()` caller presumably expects a value back;
  audit callers, e.g. the `list` command's `next_due` column, worker
  reap/reconcile loop, and `status`/ledger reporting, to decide how a
  "no more occurrences" job is represented and displayed - a sentinel, an
  `Optional[datetime]` return, or an explicit `is_exhausted` flag on the job
  record are all options worth weighing against what's least disruptive to
  existing call sites).
- On successful fire (post-run, in the same place a recurring job's
  `last_success` gets updated): call `set_enabled(job_id, False)` so it stops
  showing as due and doesn't get swept as a phantom overdue job forever. Decide
  whether "successfully fired" should also delete the registry entry outright
  rather than merely disable it - disabling preserves history (ledger, last
  run), deleting keeps the registry from accumulating dead entries; either is
  defensible, pick one and document the reasoning.
- `ccsched add --id <id> --cadence once@2026-09-19 --command <argv>` should
  work exactly like any other `add` call - no new flags needed beyond the
  cadence grammar extension, assuming the design above holds.

## Also worth doing regardless of the above

Whatever you decide about implementing this, consider adding a short section
to the `manage-recurring-cc-jobs-using-ccsched` skill (both copies) naming the
gap explicitly ("ccsched has no one-shot form yet; for a single future-dated
reminder, use `<X>` instead") so the next Claude session that reaches for
`ccsched` for a one-shot task gets pointed at the right tool immediately,
rather than rediscovering this gap from scratch the way I just did.
