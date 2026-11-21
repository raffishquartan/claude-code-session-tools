## Context

`check_session_root_access()` in `lib/doctor.py` holds the only implementation of "is this root covered by
`additionalDirectories`". `ccd` needs the same answer at launch. `ccd` builds its `claude` command in
`cli/ccd.py`, validates, creates the session directories, then execs `claude`.

## Goals / Non-Goals

**Goals:**
- A user on an uncovered machine learns why cross-project access prompts, at the moment they launch.
- One definition of coverage.

**Non-Goals:**
- Blocking, prompting or auto-fixing: `ccd` never edits `settings.json` and never refuses to launch over this.
- A way to silence the hint other than fixing the setting or unsetting the root variable (the hint disappears once covered).

## Decisions

- **Hoist the predicate into `lib/roots.py`** as a function taking a root and the parsed settings and returning whether
  it is covered, plus one that returns the uncovered roots. `lib/roots.py` already owns session-root semantics and
  `ccd` already imports it; `doctor.py` imports it too, so `check_session_root_access()` keeps its result shape and
  tests and only delegates the decision. The existing doctor tests must pass unchanged.
- **Load settings in `ccd` with the same default path doctor uses** (`~/.claude/settings.json`). If doctor builds that
  path inline, hoist it to `lib/paths` so both share it. A missing, invalid or non-object file means no hint, matching the
  doctor check's "no result" case.
- **Print after validation and the existing-session checks, immediately before the directories are created and `claude`
  is exec'd**, so nothing prints on `--dry-run` or when validation fails.
- **Message shape:** one line per uncovered root, prefixed like other `ccd` stderr lines, for example
  `ccd: note: <root> is not under any permissions.additionalDirectories entry in ~/.claude/settings.json; sessions will be
  asked for permission to reach sibling projects. Add it (or a parent) there, or run 'ccst doctor'.`
- **Stderr, not stdout:** `ccd` output on stdout may be consumed by wrappers; hints are diagnostics.

## Risks / Trade-offs

- The line is printed just before the Claude Code interface takes over the terminal, so it may scroll out of view
  quickly. The doctor WARN remains the durable record.
- Reading `settings.json` on every launch adds a small file read. It is the same file Claude Code reads at startup.
- Assumption: only the user-level `settings.json` counts, as in the doctor check. A machine covered only by a project
  or local settings layer gets a hint that can be ignored.

## Migration Plan

Minor release (4.2.0). No data migration.
