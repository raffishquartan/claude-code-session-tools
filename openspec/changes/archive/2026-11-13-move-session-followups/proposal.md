## Why

PR 181's code review left follow-ups in the `move-session` area, accepted by the maintainer for a patch release:

- The live-holder refusal trusts the pid alone. A stale `~/.claude/sessions/<pid>.json` whose pid the OS has since reused for an unrelated process refuses every rename of that session, with no override and no hint that deleting the record is the remedy.
- `--verify-only` still exits 1 after every default MOVE, because the tombstone appended to the source makes it longer than the destination by design; the earlier change only added a note explaining the FAIL.
- `ccst move-session` returns a negative exit status when the script is killed by a signal, which the shell turns into an unrelated number.
- `move_session.py` still builds `~/.claude/projects` and `~/.claude/sessions` itself although `lib/paths` owns the projects directory, and `append_picker_label` reads and decodes the whole transcript to check a two-line tail, so an invalid byte anywhere in the history turns a successful append into a traceback.

## What Changes

- The live-holder check compares a process record's `procStart` with the live process's start time, both in UTC, and treats the record as stale (the pid was reused) only when the live process started more than two seconds after the record says. A record without a parseable `procStart`, or a process whose start time cannot be read, falls back to the pid-alive test. Every liveness refusal names the record file and says to delete it if no Claude Code session is open.
- `--verify-only` excludes from the source's length the tombstone records that were appended for this destination (from the tombstone naming it to the end of the file), so a MOVE with the tombstone enabled verifies as PASS, exit 0, including after the same source has been moved twice. Source lines beyond that are still a FAIL and are reported as unexplained. The earlier "expected FAIL" note becomes an informational line giving the number of excluded lines.
- `ccst move-session` maps a signal-killed child to `128 + signal`, the shell convention.
- `lib/paths` gains `claude_live_sessions_dir()` and `claude_tasks_dir()`; `move_session.py`, `delete_sessions.py` and `claude_code_usage/session_names.py` use them (and `claude_projects_dir()`) instead of building `~/.claude/...` paths themselves.
- `append_picker_label` checks the tail by reading only the end of the file in binary.
- Patch release (4.1.1), by the maintainer's decision, although verify-only's exit status for a default MOVE changes from 1 to 0.

## Capabilities

### New Capabilities

### Modified Capabilities
- `skills/move-session`: the liveness requirement gains pid-reuse and stale-record behaviour; the verify-only requirement is renamed and now makes a tombstone-only difference a PASS; the invocation requirement gains the signal exit-status mapping.

## Impact

- `src/cc_session_tools/skills/move-session/scripts/move_session.py`, its tests, and `SKILL.md`.
- `src/cc_session_tools/cli/ccst.py` (`_cmd_move_session`), `src/cc_session_tools/lib/paths.py`, `src/cc_session_tools/skills/delete-sessions`, `src/claude_code_usage/session_names.py`, tests, and `docs/design.md`'s move-session test count.
- `openspec/specs/skills/move-session/spec.md` (modified after sync).
- `pyproject.toml`, `uv.lock`, `CHANGELOG.md`: 4.1.0 to 4.1.1.
