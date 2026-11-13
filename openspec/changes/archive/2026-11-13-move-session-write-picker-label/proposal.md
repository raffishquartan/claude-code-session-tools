## Why

After a `move-session` RENAME or MOVE+RENAME, Claude Code's picker and status line keep showing the old name, because the label comes from the transcript's `custom-title` record and `move_session.py` never writes one. Today a SessionStart hook plus a `.pending-rename` marker ask the user to type `/rename <tag>` after resuming, and a real run showed that hook staying silent on `ccr` resume. Separately, the `SKILL.md` quick reference tells the model to run the script with plain `python3`, which fails with `ModuleNotFoundError: No module named 'cc_session_tools'` on a `uv tool` install.

## What Changes

- On RENAME and MOVE+RENAME, `move_session.py` appends a `custom-title` record and a matching `agent-name` record (the pair `/rename` writes) to the transcript the renamed session resumes into, so the picker label is correct on first resume. The append runs only after the existing in-session refusal check and a new liveness check, and after destination verification on MOVE+RENAME.
- Add a liveness check for tag-changing operations that does not depend on running inside Claude Code: refuse when any live Claude Code process holds the session (Claude Code re-appends its in-memory title after every prompt, which would revert the rename).
- **BREAKING (internal)**: remove the pending-rename machinery: the `.pending-rename` marker, `hooks/sessionstart-pending-rename.sh`, its bats test, the `pending-rename` hook verb and bundle entry, and the `src/hooks/pending_rename.py` dispatcher. `ccst hooks install` prunes the stale `settings.json` entry on the next sync. Markers already on disk become inert.
- Add a `ccst move-session` subcommand that forwards its arguments to the bundled script using the interpreter that has `cc-session-tools` installed. `SKILL.md` and the in-session refusal recipe use it instead of `python3 <path>`.
- Update `SKILL.md`: drop the `/rename` step from the in-session recipe, drop the marker text, narrow the "editing the source jsonl" mistake, and remove the stale `.tag`-file wording (no `.tag` file is written; the tag lives in `sessions.db`).
- Pin the record shape in a test, since it is an undocumented Claude Code internal.
- Release level: the maintainer has decided the combined release (this change plus `telemetry-recover-command`) ships as a patch, 4.0.3, even though the new `ccst move-session` subcommand would normally be a minor bump. The CHANGELOG and version bump are done once for the combined release, after both changes are archived.

## Capabilities

### New Capabilities
- `skills/move-session`: what a rename/move writes so the session resumes under its new name, and how the skill's script is invoked.

### Modified Capabilities

## Impact

- `src/cc_session_tools/skills/move-session/` (`scripts/move_session.py`, `SKILL.md`, tests; hook script and bats test deleted).
- `src/hooks/pending_rename.py` (deleted), `src/cc_session_tools/lib/hook_registry.py`, `src/cc_session_tools/config/hooks-bundle.json`, `src/cc_session_tools/cli/ccst.py`, `src/cc_session_tools/lib/pdata/sync_notify.py` (comment).
- `tests/test_ccst_hooks_install_zero_arg.py`, `tests/test_ccst_hook_dispatcher.py`, `tests/test_ccst_doctor.py` (hook-check count) and the move-session integration tests that assert the old behaviour; `README.md`, `docs/design.md`.
- `openspec/specs/skills/move-session/spec.md` (new, after sync).
- `pyproject.toml`, `uv.lock`, `CHANGELOG.md`: version bump at the end of the combined release.
