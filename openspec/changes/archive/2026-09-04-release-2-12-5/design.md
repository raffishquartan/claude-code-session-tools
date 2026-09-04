## Context

Four independent items land in one branch/PR (see proposal.md - Why). Three (`ccd-trusted-dirs`,
`doctor-env-hints`, `claude-md-fragment`) have no cross-cutting design questions worth a section
of their own - the first two already have a complete, tested reference implementation to carry
forward as-is (see below), and the third is a single string-literal edit. `session-cwd-encoding`
is the one item with a real technical decision: where the single shared encoding implementation
should live.

## Goals / Non-Goals

**Goals:**
- One correct, shared cwd-encoding function with every call site converted to use it.
- Carry `ccd-trusted-dirs` and `doctor-env-hints` forward with their existing tests intact,
  re-verified against current `main` rather than redesigned.

**Non-Goals:**
- Redesigning `ccd`'s trust model or `doctor`'s check framework beyond what the two reference
  commits already do.
- Touching the `cccs_hooks` package name (tracked separately, see `TODO.md` and the 2.13.0
  OpenSpec change) - this change fixes the encoding bug in place, without renaming the package.

## Decisions

### Shared encoding function lives in `lib/rules.py`, re-exported for `cccs_hooks`
`lib/rules.py` already has an `encode_cwd()` (the buggy one) and is the module `move-session`
imports from. `lib/sessions.py` has its own correct inline `.replace("/", "-").replace(".", "-")`
at line 301. Decision: fix `lib/rules.py::encode_cwd()` to match Claude Code's actual encoding
(add the `.`→`-` step), make it the single canonical implementation, and have both
`lib/sessions.py` and `cccs_hooks/transcript.py::_encode_cwd()` import and delegate to it instead
of keeping their own copies.

Alternative considered: a brand-new module (e.g. `lib/cwd_encoding.py`). Rejected - `lib/rules.py`
already owns this exact concept and is already a dependency of both other packages transitively
via `cc_session_tools.lib`; adding a new module for one function is unnecessary indirection.

### `ccd-trusted-dirs` and `doctor-env-hints`: carry forward, don't redesign
Both have complete, passing test suites against their own base commit (verified this session -
`f/trust-siblings` commit `1f84ce4`, doctor-env-hint commit `132923d`). Task breakdown treats
"port onto this branch and re-verify against current `main`" as the implementation step, not
"design from scratch" - re-reading each commit's diff during `tasks.md` execution is the source of
truth for exact behavior, not a fresh design pass.

## Risks / Trade-offs

- [Both branches are ~283 commits behind `main`] → cherry-pick/rebase during apply, expect minor
  conflicts (e.g. `CHANGELOG.md`'s `[Unreleased]` anchor point has moved); resolve by re-applying
  the logical diff rather than force-taking either side.
- [`lib/rules.py::encode_cwd()` already has a test asserting the old, buggy behavior as correct
  (`tests/test_validators.py:46` via `move-session`'s own re-export) as a happy-path fixture with
  no dots] → that test's assertion still holds after the fix (no dots in its fixture), no change
  needed there; add a new dotted-path test case alongside it rather than editing the existing one.

## Migration Plan

No data migration - this is a code-only fix plus two already-tested feature ports plus a
documentation string change. Existing `~/.claude/projects/<encoded>/` directories created under
the old (buggy) encoding are unaffected: the fix only changes how paths are encoded going forward,
it does not rename anything on disk.
