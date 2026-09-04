## Why

Three small, independent maintenance items are due: a stale package name inherited from before
this repo existed as its own project, an unverified-but-likely-fixed packaging bug, and a
data-integrity gap where three of five common SQLite stores infer "has this one-shot migration
run?" from row counts instead of an explicit marker - a check that is wrong by construction, since
these stores accumulate rows from normal use starting the moment CCST is installed, long before
any migration runs.

## What Changes

- Rename the `cccs_hooks` package (`src/cccs_hooks/`) to `hooks` - it was moved into this repo from
  `claude-code-config-sync` and kept its origin name, which no longer describes anything now that
  these are CCST-owned hooks dispatched via `HOOK_VERBS` in `lib/hook_registry.py`. Update every
  import site, the `HOOK_VERBS` dispatch table's module-path strings, the `cccs-stats` console
  script's target module, test file paths, and any stale `cccs_hooks`-shaped mentions in docstrings
  or docs. Pure rename/refactor - hook names (`worklog-guard`, `session-tag`, etc.), invocation
  syntax (`ccst hooks run <name>`), and all observable behavior are unchanged.
- Add a real fresh-install smoke test (clean venv, `uv tool install` from a built wheel) confirming
  the bundled `skills/`, `config/`, `prompts/` directories resolve correctly post-install, formally
  closing out a previously-reported packaging bug that code inspection shows is already fixed
  (`_discover_source_dir`/`_discover_bundle` in `cli/ccst.py` are already package-relative with no
  filesystem walk-up or fallback). Add an automated regression test alongside the manual smoke test.
- Add `created_at`/`updated_at` timestamp columns and compare-and-swap (CAS) support to every table
  in the five common stores (`ccmsg.db`, `ccsched.db`, `sessions.db`, `telemetry.db`,
  `command-cache.db`) that lacks them.
- Add an explicit, durable migration-completion marker (matching telemetry's existing
  `lib.db.MIGRATIONS_DDL`-based marker) to the `ccmsg`, `ccsched`, and `sessions` one-shot
  migrations, and switch `ccst doctor`'s pending-migration check to read that marker for all four
  stores instead of inferring completion from "does the new store have rows" - deleting the
  `_count_new_store_rows`-style inference branch entirely.

## Capabilities

### New Capabilities
- `cli/store-audit-columns`: every common-store table carries `created_at`/`updated_at` and every
  write path that mutates an existing row supports a compare-and-swap update guarded by the row's
  last-known `updated_at` (or an equivalent version token), rejecting a write against a stale read.
- `cli/store-migration-markers`: `ccmsg`, `ccsched`, and `sessions` each record an explicit,
  durable completion marker for their one-shot legacy-data migration (mirroring telemetry's
  existing marker), and `ccst doctor`'s pending-migration check reads that marker directly for all
  four stores rather than inferring completion from row counts in the new store.
- `cli/packaging-fresh-install`: a fresh `pip`/`uv tool install` of this package SHALL locate its
  bundled `skills/`, `config/`, and `prompts/` directories and behave correctly with no manual
  workaround, verified by an automated regression test.

### Modified Capabilities
(none - no existing main spec covers common-store schemas, doctor's migration checks, or packaging
discovery yet)

## Impact

- `src/cccs_hooks/` -> `src/hooks/` (directory rename), every importer of `cccs_hooks.*`
  (`lib/hook_registry.py`'s `HOOK_VERBS`, `pyproject.toml`'s `cccs-stats` entry point, all
  `tests/test_*.py` files exercising these hooks), plus `pyproject.toml`'s
  `[tool.setuptools.packages.find]`/`exclude`/`package-data` entries if they name `cccs_hooks`
  explicitly.
- `src/cc_session_tools/lib/messaging/repository.py` (ccmsg.db schema + write paths)
- `src/cc_session_tools/lib/scheduler/store.py` (ccsched.db schema + write paths)
- `src/cc_session_tools/lib/sessions_db.py` (sessions.db schema + write paths)
- `src/cc_session_tools/lib/telemetry_store.py` (telemetry.db - reference pattern for the marker;
  may still need audit columns on tables that lack them)
- `src/cc_session_tools/skills/update-command-cache/scripts/update_command_cache.py`
  (command-cache.db schema)
- `src/cc_session_tools/lib/doctor.py` (`check_pending_data_store_migration` and its
  `_count_new_store_rows`-style inference branch)
- `src/cc_session_tools/cli/migrate_telemetry.py` and the equivalent one-shot migration entry
  points for ccmsg/ccsched/sessions
- `src/cc_session_tools/lib/db.py` (`MIGRATIONS_DDL` and any marker-write/marker-check helper -
  generalizing it for reuse across four stores instead of just telemetry)
- `TODO.md` - both "Tidy up `catchup.py` and siblings' module path" and "Migration markers for
  ccmsg, ccsched and sessions" entries are resolved by this change and should be removed
