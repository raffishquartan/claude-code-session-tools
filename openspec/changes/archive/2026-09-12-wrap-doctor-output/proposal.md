## Why

`ccst doctor`'s WARN/FAIL output is hard to read: each result prints as one unwrapped
`[STATUS] name  reason` line, and reasons are long free-text sentences (100-250+ characters,
often with embedded paths and remediation commands). The terminal hard-wraps these lines with no
continuation indent, so a wrapped reason is visually indistinguishable from a new, unrelated
result — confirmed against a real `ccst doctor` run. The same formatter renders every check
category (skills, hooks, data stores, pdata migrations), so hook-related results are affected
identically to skill-related ones; there is no separate "hooks health check" formatter.

## What Changes

- `ccst doctor`'s WARN/FAIL rows (and the `--drift` report used by the scheduled drift-monitor
  job) switch from a single-line table row to a two-line record: `[STATUS] name` on its own line,
  then the reason as an indented, word-wrapped paragraph on the line(s) below.
- The wrap width adapts to the real terminal width via `shutil.get_terminal_size()` (stdlib),
  honoring a `COLUMNS` env override and falling back to 80 columns for non-tty output (piped,
  redirected, or the drift-monitor job whose output is forwarded to Telegram).
- The `--all` mode's OK-line rows are unaffected — they stay short, fixed-width rows; only
  WARN/FAIL's long-reason rows get the new layout.
- No change to `CheckResult`, `run_all_checks`, or any individual check function — check reasons
  keep their existing wording; only the two presentation functions change.

## Capabilities

### New Capabilities
- `cli/doctor-output-wrapping`: defines the two-line WARN/FAIL record layout and adaptive
  terminal-width wrapping for `ccst doctor`'s normal and `--drift` output.

### Modified Capabilities
(none — this introduces a new capability rather than changing an existing one's requirements)

## Impact

- `src/cc_session_tools/lib/doctor.py`: `format_results()` and `format_drift_report()`.
- `tests/test_ccst_doctor.py`: existing layout assertions for these two functions need updating
  for the new two-line shape.
- No new dependency (`shutil` and `textwrap` are both stdlib).
- Patch version bump (`pyproject.toml`, `CHANGELOG.md`) — output-readability fix, no interface or
  on-disk change.
