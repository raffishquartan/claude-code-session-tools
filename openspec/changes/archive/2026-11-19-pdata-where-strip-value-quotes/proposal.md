## Why

`ccst pdata query --where "corr_key = 'gmail:abc'"` returns "No rows" for records that exist (reported by the deauppet-sync-comms session, message 20261116T153044Z-d676). Extension fields are queryable already; the cause is that the value is passed to SQL with its surrounding shell-style quotes still attached, so it is compared as the literal text `'gmail:abc'`. The documented example (`--where 'status = "open"'`) fails the same way.

## What Changes

- `--where` strips one pair of matching surrounding single or double quotes from the value before comparing.
- A value with no surrounding quotes behaves exactly as today.
- `--help` text and the bundled skill docs state that quoting a value is optional and that extension fields are queryable.
- Patch version bump (bug fix, no interface change) and CHANGELOG entry.

## Capabilities

### New Capabilities
- `pdata/query-where-values`: how `ccst pdata query --where` interprets the value part of a clause, including quoted values and extension fields.

### Modified Capabilities

## Impact

- `src/cc_session_tools/lib/pdata/service.py` (`_parse_where_clause`), tests under `tests/pdata/`, `ccst pdata query --help`, `CHANGELOG.md`, `pyproject.toml`, `uv.lock`.
- No on-disk change, so no migration and no `ccst doctor` check.
- Callers that relied on a value literally wrapped in quotes matching stored quote characters change behaviour; none are known.
