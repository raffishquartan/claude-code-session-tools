## Why

`lib/paths.claude_projects_dir()` is meant to be the one definition of `~/.claude/projects`, but two
callers still build the path themselves: `claude_code_usage/cli.py` (`DEFAULT_PROJECTS`, evaluated at import
time) and `hooks/transcript.py` (`_transcript_path`). A test that redirects `Path.home()` after import is
ignored by the first, and the two definitions can drift apart.

## What Changes

- `claude_code_usage/cli.py` takes its `--projects-dir` default from `paths.claude_projects_dir()` at
  argument-parse time instead of an import-time constant; `DEFAULT_PROJECTS` is removed.
- `hooks/transcript.py::_transcript_path` builds on `paths.claude_projects_dir()`.
- A conformance test fails if a source file outside `lib/paths.py` spells `".claude" / "projects"`.
- Patch release 4.1.2, with a CHANGELOG entry. No change to any command's observable output.

Out of scope: the `clean-hook-sessions` script (a standalone script that does not import the package).

## Capabilities

### New Capabilities
- `cli/claude-dir-paths`: the rule that each `~/.claude/...` directory has one definition in `lib/paths`.

### Modified Capabilities
None.

## Impact

`src/claude_code_usage/cli.py`, `src/hooks/transcript.py`, a new test, CHANGELOG, `pyproject.toml`, `uv.lock`.
