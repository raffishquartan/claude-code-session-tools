## Why

`ccd` no longer passes `--add-dir` (release 4.1.3); access to sibling projects now depends on a session root being
covered by `permissions.additionalDirectories`. The only notice when it is not is a `ccst doctor` WARN, which a user
sees only by running `ccst doctor`. On an uncovered machine the sessions work, but ask permission for each cross-project
access, and nothing at launch says why.

## What Changes

- When `ccd` is about to launch a session and a configured session root is not covered by
  `permissions.additionalDirectories`, it prints one line per uncovered root to stderr naming the root and the fix.
- The coverage rule is defined once, in a shared helper that both `ccd` and the existing `ccst doctor` check call, so
  the two cannot drift.
- No output when every root is covered, when `settings.json` is missing or unreadable, or when no roots are configured.
  No new output on `--dry-run` or when launch validation fails.

## Capabilities

### New Capabilities
(none)

### Modified Capabilities
- `cli/ccd-trusted-dirs`: adds the launch-time hint for an uncovered session root. The existing
  `cli/doctor-session-root-access` behaviour is unchanged (the shared helper is an implementation detail).

## Impact

- `src/cc_session_tools/cli/ccd.py`, `src/cc_session_tools/lib/roots.py`, `src/cc_session_tools/lib/doctor.py`
  (the check calls the shared predicate instead of owning the logic)
- Tests: `tests/test_cli_ccd.py`, `tests/test_roots.py`; the doctor tests keep passing unchanged and so guard the refactor
- `CHANGELOG.md`, `pyproject.toml`, `uv.lock` (4.1.3 to 4.2.0, a minor bump: new user-visible output)
