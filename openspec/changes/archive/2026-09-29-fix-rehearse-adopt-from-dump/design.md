## Decision

`project_db_dir_override(rehearse)` works by overwriting `CCST_PROJECT_DB_DIR`. It will also push the
previous value onto a module-level stack; a new `live_project_db_dir()` context manager sets the env
var back to the outermost saved value (or unsets it) for its body. `_pending_adoption` wraps only the
`_already_locally_migrated(project)` call in it. With `rehearse=None` the stack is empty and the
context manager is a no-op.

## Why the live DB, not the sandbox

The question "has this machine already migrated this project?" is about the machine, not the copy.
Rehearsal must simulate write/classification, not fork adoption detection onto an inherently empty
sandbox.

## Consequences and risks

- `_already_locally_migrated` calls `repository.connect()` (DDL side effect) only when the live
  `.db` file exists; this runs the same idempotent schema DDL every pdata command runs on an existing DB, then a SELECT, and
  does not create it when absent. A rehearsal never writes to the live DB.
- If the live project has no content and a dump exists, rehearsal still reports adoption, and a
  rehearsed `--write` adopts into the sandbox (existing behaviour, including the documented
  limitation that rehydrate reads the dump from the real project root).
- The env var is process-global; the CLI is single-threaded per invocation, so the temporary swap
  is safe. The stack is restored in `finally`.
