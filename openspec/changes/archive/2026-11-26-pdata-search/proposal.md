## Why

`ccst pdata query` and `ccst pdata list` filter only by `--where` comparisons, `--since`/`--until` epochs and `--limit`: no substring or regex search, no sort, no projection, one group per call. Finding every row of a long-text group that mentions a name means dumping the whole group as JSON and filtering it outside the tool, and groups reach hundreds to thousands of rows.

## What Changes

- New read-only verb `ccst pdata search`:
  - Searches one or more record groups (`--group`, repeatable) of one project, with the group named in every result row.
  - Text predicate: `--text <substring>` or `--regex <pattern>`, case-insensitive unless `--case-sensitive`; `--not-text` inverts it; `--fields f1,f2` limits which fields are searched (default: `content`, `file_path` and every TEXT extension field).
  - Structured filters: repeatable `--where` with exactly the `pdata query --where` syntax and quote stripping, and `--since`/`--until` ISO dates (yyyy-MM-dd) on a named `--date-field`.
  - Output: `--format json|csv` (default json, CSV with LF line endings), `--select` projection (`id` and `record_group` always kept), `--snippet N` context around the first match, `--sort FIELD` with `--desc`, `--limit N`, `--count-only`.
  - No match exits 0 with empty output: `[]`, a CSV header only, or a count of 0.
  - Every contradictory or empty request is rejected before the store is read, with a message naming the problem and, for unknown groups and fields, the valid names.
- `ccst pdata list --format csv` and `ccst pdata query --format csv` switch from CRLF to LF line endings, matching the repo's CSV convention; the shared CSV renderer serves all three verbs.
- `--help`, README and the bundled pdata skills that tell sessions how to read records name `search` for text search.
- Minor version bump (4.9.0) and CHANGELOG entry.

## Capabilities

### New Capabilities
- `pdata/search`: the `ccst pdata search` verb - its inputs, matching rules, output shapes and boundary validation.

### Modified Capabilities

## Impact

- New `src/cc_session_tools/lib/pdata/search.py`; `repository.py` (shared `--where` SQL building), `service.py` (the `--where` parser becomes public for reuse), `formatting.py` (LF CSV, header for an empty result), `cli/ccst.py` (parser, handler, module docstring).
- Tests under `tests/pdata/` and `tests/test_ccst_pdata_cli.py`.
- Skills: `pm-pdata-do-update-consuming-skills`, `pm-pdata-do-update-project-docs`, `pm-pdata-design-schema`.
- No on-disk change, no migration, no new doctor check. The verb is subject to the same format gate as `query`.
