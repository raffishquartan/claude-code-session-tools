## Why

Two `ccst move-session` failures leave a user stuck. First, strict verification during `--execute` requires every `cwd` in the destination transcript to equal the destination cwd, so a session that `cd`-ed into its own `~/.claude/projects/<src-key>/` directory (legitimately rewritten to `<dst-key>`) fails with `VERIFY: FAIL` after the copy has already happened. The half-done move cannot be re-run (the clobber check rejects it) and is not registered anywhere. Second, moving a session into a strict-root project with an unchanged tag is rejected because the tag suffix lacks the `<project>-` prefix; the only escapes are a hand-picked `--rename-tag` or `--force`, which skips every destination check.

## What Changes

- Strict and verify-only verification accept a destination `cwd` that is the destination cwd or the rewritten form of any `cwd` found in the source transcript (the same string replacements the copy applied). A destination `cwd` outside that set still fails.
- When strict verification fails during `--execute`, the script removes exactly the files and directories that run created, lists each removed path, and exits 1. The source and every pre-existing path are untouched, so the same command can be re-run once the cause is fixed. Verification now runs before the memory copy, so a failed run never creates a memory directory.
- A MOVE without `--rename-tag` whose only destination-validation failure is the strict-root tag-prefix rule derives a compliant tag and continues as MOVE+RENAME. Derivation: strip a leading `<src-project>-` from the tag suffix, then prefix `<dst-project>-` unless the remainder already starts with it. The derived tag is re-validated against every tag rule and the clobber check.
- A visible `NOTE: tag changed from <old> to <new> (destination project '<p>' requires tag suffix to start with '<p>-')` line appears in the PLAN (dry-run and execute), in the EXECUTING section and in the DONE block next to the resume command.
- New `--no-auto-rename` flag restores the hard validation failure.
- `SKILL.md` documents the cwd rule, rollback, auto-rename, the collision case and the new flag.
- New CLI flag and new default behaviour: minor version bump per the repo's version policy.

## Capabilities

### New Capabilities

### Modified Capabilities
- `skills/move-session`: adds requirements for cwd verification against rewritten source cwds, rollback of a failed `--execute`, and automatic tag derivation for strict-root destinations.

## Impact

- `src/cc_session_tools/skills/move-session/scripts/move_session.py` (verification, execute ordering and rollback, tag derivation, argument parsing, output).
- `src/cc_session_tools/skills/move-session/SKILL.md`.
- Tests under `src/cc_session_tools/skills/move-session/tests/`.
- `CHANGELOG.md`, `pyproject.toml`, `uv.lock`.
