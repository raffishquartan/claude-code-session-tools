## Why

`ccst pdata init --rehearse <copy>` is meant to preview a project's classification report safely.
For any project that has a published sync dump (`.pdata-db-dump/latest.sql`, present after any
`ccst pdata sync-check` on a project with pdata content) it instead always reports "a published
sync dump already exists ... this machine has no local pdata content" and never classifies.

Cause: the "does this machine already have this project migrated" check (`_already_locally_migrated`)
runs inside `project_db_dir_override`, so it looks at the rehearsal sandbox's `.db`. That sandbox is
always empty (a plain `cp -r` of the project does not include the real `.db`, which lives outside
the project directory), so the check always answers "not migrated" - while the dump file, which is
inside the project directory, is copied and found. Rehearsal is therefore unusable for exactly the
already-partially-migrated projects it is recommended for.

## What Changes

- The "already locally migrated" decision (adopt-from-dump vs classify) is made against the
  **live** per-project `.db` even during a `--rehearse` run; everything else in a rehearsal
  (proposal, sandbox `.db`, backups) stays sandboxed.
- Not the alternatives: unconditionally disabling adoption under `--rehearse` would stop rehearsal
  previewing what a second machine's `--write` will do; requiring the live `.db` to be copied into
  the sandbox would leave the real database's contents in a scratch directory and put the burden on
  every caller.
- Patch release 3.9.1. No CLI surface change. No change to `pm-pdata-do-init`'s Step 1: rehearsal
  still needs only `cp -r <project> <path>`.

## Capabilities

### New Capabilities
- `pdata/init-adopt-from-dump`: when `ccst pdata init` adopts a published dump versus classifying,
  including under `--rehearse`.

### Modified Capabilities
None.

## Impact

- `src/cc_session_tools/lib/pdata/init_paths.py`: records the pre-override DB dir and exposes a
  context manager that restores it.
- `src/cc_session_tools/lib/pdata/init_service.py`: `_pending_adoption` evaluates
  `_already_locally_migrated` under that context manager.
- Tests in `tests/test_ccst_pdata_init_cli.py`; `CHANGELOG.md`, `pyproject.toml`, `uv.lock`.
