## Why

`mypy src` aborted on a duplicate `conftest` module, which hid 26 existing type errors in 12 files.
The abort is fixed (previous change); mypy now runs but reports those errors, and mypy is not a
declared dev dependency, so nothing keeps the codebase type-clean.

## What Changes

- Fix all 26 `mypy src` errors so `mypy src` exits 0, without behaviour changes (annotations,
  narrowing, renamed shadowed variables, stub dependency).
- Declare `mypy` and `pandas-stubs` as dev dependencies so the check is reproducible.
- No CI wiring (see design Non-Goals).

## Capabilities

### New Capabilities
(none - tooling and type-hygiene only; `skip_specs: true`)

### Modified Capabilities
(none)

## Impact

- `src/cc_session_tools/cli/ccs.py`, `src/claude_code_usage/*`, and four skill scripts under
  `src/cc_session_tools/skills/`; `pyproject.toml` dev extras; `uv.lock`; CHANGELOG; version
  patch bump.
