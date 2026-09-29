## Why

`ccst pdata init --write` documents "roll back the inserted rows if verification fails". That
contract has a hole: `Path.exists()` on a `file_path` string the operating system rejects outright
(for example `[Errno 63] File name too long`, reachable whenever a `file_path_column` maps a CSV
column that holds brace-expansion shorthand or other non-literal-path notation) raises `OSError`
instead of returning False. Nothing caught it, so `write()` ended in an uncaught traceback after
inserting every row and before reaching its rollback. On a real migration this left 1362 live rows
across 5 record groups, no manifest marker, and no cutover; recovery meant soft-deleting each row
by hand. The same gap exists for any other unexpected exception between the first insert and the end of
cutover. A failure inside cutover is worse: the rows stay live, the manifest is unmarked, and a
rerun then imports every row a second time without error (reproduced).

The companion report item, "`--rehearse` against a project with a published sync dump always takes
the adopt-from-dump branch", was already fixed and released in 3.9.1 (change
`fix-rehearse-adopt-from-dump`, spec `pdata/init-adopt-from-dump`, regression tests in
`tests/test_ccst_pdata_init_cli.py`); it is not repeated here.

## What Changes

- Verification treats a `file_path` the OS cannot even stat as a verification failure reason (same
  outcome as "does not resolve"), naming the record, the value and the OS error, so the normal
  rollback path runs.
- Re-counting a source file for the parity check (`OSError`, `ValueError`, `csv.Error`) is a
  verification failure reason, like the same errors during import, instead of an escaping error.
- As a backstop, any other error (including Ctrl-C) between the first insert and a successful
  backup rolls back the rows inserted by that run and then propagates unchanged. A rollback that
  itself fails never replaces the original error: the failure and the record ids still live are
  reported through progress output and attached to the error.
- A failure part-way through cutover keeps the rows of entries whose source was already moved into
  the archive and marks those entries migrated, and rolls back the rows of entries still in place,
  so a rerun imports each entry once. `archive_entries` reports each moved entry through an
  optional callback to make this possible.
- Regression tests for each path above.
- Not changed: the `pm-pdata-do-init` skill's `file_path_column` guidance. The recommendation that
  a column documented to use multiple/shorthand notations must never be mapped to
  `file_path_column` (map it to a plain text field) is left for you to decide on, since skill
  edits go through you.

## Capabilities

### New Capabilities

### Modified Capabilities
- `pdata/init-write-idempotency`: adds requirements that a failed `--write` of any kind never
  leaves a state a rerun would duplicate: verification failures and unexpected errors leave no
  live rows from the run, and a cutover failure keeps exactly the entries already moved.

## Impact

- `src/cc_session_tools/lib/pdata/init_service.py` (`_verify`, `write`), `src/cc_session_tools/lib/pdata/cutover.py`, `tests/pdata/test_init_service.py`.
- CHANGELOG entry and patch version bump (3.9.3); no interface or on-disk change.
