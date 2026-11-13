## Context

`claude_projects_dir()` already exists (added in 4.1.0) and is used by move-session and session GC.

## Decisions

- Resolve the `--projects-dir` default when the parser is built, not at import: `_common_args` calls
  `paths.claude_projects_dir()` directly. The help text keeps showing the resolved default.
- `hooks/transcript.py` already imports from `cc_session_tools.lib`, so importing `paths` adds no new
  dependency edge.
- The conformance test is a plain text scan of `src/**/*.py`, excluding `lib/paths.py` and the standalone
  `clean-hook-sessions` script (it cannot import the package).
- Patch bump: no interface change.
