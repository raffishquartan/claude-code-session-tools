## Why

Review of the date format convention change found three defects worth fixing: `ccmsg read` can fail
to display a message whose stored `sent_at` is not exactly the writer's shape, the pdata
date-field warning runs after the write and could turn a committed write into a failed command,
and the human UTC time format is spelled out as a literal at five call sites.

## What Changes

- `ccmsg read` prints a stored `sent_at` it cannot parse as-is instead of raising.
- The pdata date-field warning never fails a write: if reading the schema errors, no warning is
  printed and the command's result is unchanged.
- One shared definition of the human UTC time format (`yyyy-MM-dd HH:mm UTC`) in a small module,
  used by `ccst doctor`, the auto-sync failure message, the pdata cutover log and `ccmsg read`.
  Output is unchanged.

## Capabilities

### New Capabilities

### Modified Capabilities
- `cli/human-facing-date-format`: a stored timestamp that cannot be rendered is shown as stored.
- `pdata/date-field-warning`: the warning check never changes a write's outcome.

## Impact

- `src/cc_session_tools/lib/messaging/service.py`, `lib/doctor.py`, `lib/install_sync.py`,
  `lib/pdata/cutover.py`, `lib/pdata/service.py`, a new `lib/timefmt.py`, and their tests.
- `CHANGELOG.md`, `pyproject.toml`, `uv.lock` (patch bump).
