# ccr: resume a session that was created but never received a first message — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `ccr <tag>` (or `ccd <tag>` re-invoked with the same tag) must successfully resume a
session whose `cc-sessions/<date>-<tag>/` scaffold and `sessions.db` row exist, but which was
exited before the user ever typed a first message — instead of failing at the underlying
`claude --resume` call, or (pre-existing partial fix) at `ccd`'s own re-creation guard.

**Status:** Investigated and understood; not yet implemented (scope of this plan is design +
task breakdown only, per the request that spawned it).

## Root cause

Two separate, related gaps:

1. **`ccd`'s half is already fixed on `main`.** `ccd.py:175-185` (see the comment at line
   169-174) already treats a `session_dir` containing only the empty scaffold — no jsonl
   transcript ever created, per `is_empty_session()` (`lib/sessions.py:420-474`) — as safe to
   reuse rather than refusing with "already started today." This landed in a prior session
   (see the misdirected message `20260731T132751Z-ff4d`, sent to `project=claude` instead of
   `project=claude-code-session-tools`, which reported exactly this bug). **Action:** verify
   which installed version the user is actually running — if it predates this fix, a
   `uv tool install --reinstall ~/repos/claude-code-session-tools` alone resolves the
   "can't `ccd` the same tag again" half without any code change.

2. **`ccr`'s half is not fixed.** `ccr.py` resolves a fragment to a `SessionMatch` (basename +
   project_dir + session_dir) purely from `sessions.db` (`find_matching_sessions`,
   `lib/sessions.py:57-76`) and the directory's existence on disk
   (`ccr.py:116-123`) — neither of which distinguishes "this session was actually started" from
   "this session's scaffold exists but nothing was ever typed." When no jsonl transcript is
   found for the match (`find_all_jsonls_for_session` returns `[]` — see
   `lib/sessions.py:420ff`, this repo's post-item-4 rename of the old single-result
   `find_jsonl_for_session`), `ccr.py`'s current fallback (~line 206-217) sets
   `resume_arg = m.basename` (the literal tag string) and execs
   `claude --resume <tag> --remote-control <basename>`. The real `claude` binary has no
   transcript/UUID registered under that literal string (nothing was ever persisted — no jsonl
   exists to resolve), so the exec fails at the Claude Code binary level, outside anything
   `ccr` itself can catch or report cleanly. This is what the user experiences as "cannot
   resume."

`sessions.db`'s `jobs`... err, `sessions` table (`lib/sessions_db.py` DDL, ~line 34-59) has no
status/state column distinguishing "row exists, never started" from "row exists, has history" —
that distinction is only ever derivable on demand by asking whether a jsonl exists
(`find_all_jsonls_for_session` / `is_empty_session`), never stored. This plan does not propose
adding one (see Alternatives Considered) — the existing on-demand check is sufficient and
avoids a second source of truth that could drift from the jsonl-presence ground truth.

## Desired behaviour

`ccr <tag>` against a session that has a `cc-sessions/` scaffold + `sessions.db` row but **zero**
jsonl transcripts should **start** that session fresh — i.e. do what `ccd` would do if
re-invoked with the same tag today — rather than attempting (and failing) a `claude --resume`.
This is the only way to recover such a session, since there is nothing to resume by definition.

Sessions with exactly one jsonl (the common case) and sessions with 2+ jsonls (item 4's
already-implemented duplicate-transcript picker) are both unaffected by this plan — this only
changes the *zero-jsonl* branch.

## Design

Reuse `ccd`'s own launch path rather than duplicating it. Concretely:

1. `ccd.py`'s launch logic (env setup, `os.chdir`, `launch_claude`, the `cmd` construction with
   `-n <session_name> --remote-control <session_name>`) is currently private to `ccd.main()`.
   Extract the "start a session in an existing (possibly-reused) scaffold dir" portion into a
   small shared helper — e.g. `lib/session_launch.py::start_new_session(session_name, tag,
   project_dir, extra_argv) -> NoReturn` (or returns the `cmd`/`env` for the caller to exec, to
   keep it testable the same way `launch_claude`/`launch_claude_resume` already are
   monkeypatchable) — so `ccd.py` and `ccr.py` both call it instead of `ccr.py` growing a
   second, slightly-different copy of `ccd`'s env/exec logic.

2. In `ccr.py`, in the `not m.is_orphan` branch where `candidates` (from
   `find_all_jsonls_for_session`) is currently checked for `len == 1` / `len > 1`, add the
   `len == 0` case: instead of falling through to the debug-log-and-resume-by-basename path
   (which is what currently produces the failure), call the new shared "start fresh" helper with
   `m.basename`/`session_tag(m.basename)`/`m.project_dir`, passing through `remainder` the same
   way the existing resume path does.

3. Before starting fresh, confirm via `is_empty_session(m.basename, m.project_dir)` (already
   returns `True` when no jsonl is found, per its own docstring) that this really is a
   never-started session and not some other jsonl-lookup edge case — belt-and-braces given the
   two functions (`find_all_jsonls_for_session` returning `[]` and `is_empty_session` returning
   `True`) currently have to agree independently. If they ever disagree, treat it as "cannot
   resume: session_dir exists but its transcript can't be located" (a distinct, clearly-worded
   error) rather than silently doing either action — this should not normally happen, so
   surfacing it loudly if it does is more useful than guessing.

4. Print a one-line notice when this path fires (e.g. `ccr: '<tag>' was created but never
   started - starting it now`) so the behaviour is visible rather than a silent divergence from
   "resume."

## Tasks

- [ ] **Task 1: Extract `ccd`'s session-start logic into a shared, testable helper.**
      New module `lib/session_launch.py` (or add to `lib/sessions.py` if that reads more
      naturally once written — decide during implementation, not here) exposing a function that
      takes `(session_name, tag, project_dir, extra_argv)` and returns `(cmd, env)` for the
      caller to exec — mirroring the existing pattern where `ccd.launch_claude` and
      `ccr.launch_claude_resume` are thin, monkeypatchable exec wrappers around
      caller-constructed `cmd`/`env`. Update `ccd.py` to build its `cmd`/`env` via this helper
      instead of inline construction. **No behaviour change for `ccd` itself** — this step is a
      pure extraction, verified by the existing `ccd` test suite passing unchanged.

- [ ] **Task 2: Wire the zero-candidate case into `ccr.py`.**
      In the `not m.is_orphan` branch (`ccr.py`, current `candidates` length-based dispatch),
      add the `len(candidates) == 0` branch: verify via `is_empty_session`, print the notice
      from Design point 4, build `cmd`/`env` via Task 1's helper (using `CLD_SESSION_MODE=new`,
      matching `ccd`'s own env, not `"resume"`), and exec via the existing `launch_claude_resume`
      wrapper (or a renamed/shared exec wrapper if Task 1's extraction makes that cleaner).
      Handle the disagreement case (Design point 3) as a hard `return 1` with a clear message.

- [ ] **Task 3: Tests.**
      - `ccr` against a session dir with a `sessions.db` row and zero jsonls launches the
        "start fresh" command (assert on `cmd`, matching the style of
        `test_ccr_unique_match_launches_resume`), with `CLD_SESSION_MODE=new`.
      - `ccr` against the same scenario, but a subsequent `ccd <tag>` on the same date, still
        reuses the same directory without error (regression guard for the two code paths
        staying consistent with each other).
      - The disagreement/hard-error path (Design point 3) — force `is_empty_session` and
        `find_all_jsonls_for_session` to disagree via monkeypatch, assert the clear error and
        non-zero return, not a silent guess.
      - Extend the existing `ccd` empty-session-reuse test
        (`test_ccd_reuses_empty_session_dir_with_no_transcript`) to also assert `ccr` can now
        pick up where that scaffold left off, closing the loop end-to-end.

- [ ] **Task 4: Docs + CHANGELOG.**
      `CHANGELOG.md` `Fixed` entry: "`ccr` can now resume a session that was created but never
      received a first message — it starts the session instead of failing at `claude --resume`
      with no transcript to resume." Patch-level fix (no new flags, no schema change) per this
      repo's SemVer policy — `patch` bump, not `minor`.

## Alternatives considered

- **Add a `status` column to `sessions.db`'s `sessions` table** (e.g. `'new' | 'started'`,
  flipped on first jsonl-confirmed message). Rejected: this would be a second source of truth
  that has to be kept in sync with jsonl presence by every write path that touches a session,
  and the existing on-demand check (`find_all_jsonls_for_session` / `is_empty_session`) is cheap
  enough (a `glob` + a handful of small-file reads) that the extra column buys no measurable
  performance, only a new drift risk.
- **Have `ccr` retry `claude --resume` and catch its failure, falling back to start-fresh.**
  Rejected: `claude --resume` is `os.execvpe`'d (the current process image is replaced), so
  there is no "catch the failure and retry" moment available to `ccr` after that call — the
  zero-jsonl check has to happen *before* the exec, which is exactly what this plan does.

## Open question for Chris

Task 1 introduces a new shared module. If a smaller footprint is preferred, `ccr.py` could
instead import `ccd.py`'s `main()`-internal logic directly (bypassing the extraction) at the
cost of `ccr.py` and `ccd.py` becoming coupled to each other's internals rather than to a common
helper — flag if that tradeoff is preferred before implementation starts.
