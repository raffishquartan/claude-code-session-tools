## Why

Four small, independent, already-substantially-understood fixes/features are ready to ship
together as a maintenance release: two are complete-but-unmerged branches (`ccd` trusting every
project under a configured session root; `ccst doctor` telling the user exactly how to fix a
missing session-root env var), one is a real bug (path-encoding breaks for any cwd/username
containing a dot), and one is a documentation gap (sessions don't know `TaskCreate`/`TaskList`
exist for cross-session task tracking). None of the four design-couples to the others, but
bundling them into one release avoids four single-item PRs for work this small.

## What Changes

- `ccd` auto-trusts (via `--add-dir`) every project directory under a configured session root
  (`CLAUDE_SESSION_TOOLS_REPO_ROOT` / `CLAUDE_SESSION_TOOLS_PROJ_ROOT`) instead of requiring each
  one individually listed in `settings.json`'s `additionalDirectories`.
- `ccst doctor`'s `ENV:CLAUDE_SESSION_TOOLS_REPO_ROOT` / `ENV:CLAUDE_SESSION_TOOLS_PROJ_ROOT`
  WARN/FAIL checks now say exactly which `export` line to add and where (`~/.shellrc.d/env.sh`,
  not `~/.shellrc.d/ccl.sh`, which is fully regenerated on every `ccst shell install --apply`).
- Fix: the cwd-to-`~/.claude/projects/<encoded>/`-directory-name encoding is reimplemented three
  times in this codebase, and two of the three copies are missing the `.`→`-` replacement
  Claude Code itself performs alongside `/`→`-`, so any dotted cwd/username breaks transcript
  lookup (`cccs_hooks/transcript.py`) and the `move-session` skill (`lib/rules.py`). Hoists one
  correct shared implementation and switches every call site (including `lib/sessions.py`'s own
  inline copy) to use it.
- Adds a section to this repo's bundled CLAUDE.md fragment documenting `TaskCreate`/`TaskList`/
  `TaskGet`/`TaskUpdate` as genuine cross-session task tracking, so installed sessions stop
  telling users they can't track tasks beyond the current session.

## Capabilities

### New Capabilities
- `cli/ccd-trusted-dirs`: `ccd` computing which directories to pass Claude Code as trusted
  (`--add-dir`) based on configured session roots.
- `cli/doctor-env-hints`: `ccst doctor`'s environment-variable checks explaining how to fix a
  WARN/FAIL, not just reporting one.
- `session-cwd-encoding`: the single correct algorithm for encoding an absolute cwd into a
  `~/.claude/projects/<encoded>/` directory name, and the guarantee that every call site in this
  codebase uses it.
- `install/claude-md-fragment`: the content contract of the CLAUDE.md fragment
  `ccst install-everything --apply` merges into a user's `~/.claude/CLAUDE.md`.

### Modified Capabilities
(none - first OpenSpec change in this repo, nothing under `openspec/specs/` yet to modify)

## Impact

- `src/cc_session_tools/cli/ccd.py`, `src/cc_session_tools/lib/roots.py` (+ tests)
- `src/cc_session_tools/lib/doctor.py`, `README.md` (+ tests)
- `src/cccs_hooks/transcript.py`, `src/cc_session_tools/lib/rules.py`, `src/cc_session_tools/lib/sessions.py`,
  `src/cc_session_tools/skills/move-session/scripts/cc_session_rules.py` (+ tests) - new shared
  encoding helper, location TBD in design.md
- The bundled CLAUDE.md fragment file (path TBD - located during design.md/tasks.md)
- `CHANGELOG.md`, `pyproject.toml`, `uv.lock` (version bump to 2.12.5, as a follow-on commit)
