## Why

`ccst pdata init --write` writes a Markdown pointer file back where each migrated CSV was, at the
CSV's path with the suffix replaced by `.md`. `_write_pointer_file` calls `write_text` on that
path with no check, so a real `.md` file with the same stem beside the CSV is silently truncated
and replaced by the pointer stub. Only the migrated CSV is archived to `.pdata-migrated/`, and
nothing in the readiness scan, classification report or dry run mentions the collision, so
narrative documents (in one real migration, four files from about 3 KB to 240 KB) were lost from
the project tree and recoverable only from the pre-cutover backup tarball. A migration tool must
never destroy a file it was not asked to migrate.

## What Changes

- Cutover never overwrites or deletes an existing file at the pointer path. If the primary pointer
  path (`<stem>.md`) is occupied by anything other than this entry's own earlier pointer stub (a
  real file, a directory, a symlink, an unreadable or binary file), the pointer is written to
  `<original file name>.pdata-pointer.md` (for example `things.csv.pdata-pointer.md`) and the
  substitution is reported. The substitute name includes the full original name so that entries
  sharing a stem (`a.csv` and `a.json`) never collide on it.
- If that substitute path is also occupied by something that is not this entry's own earlier
  pointer, the pointer is skipped with a loud warning (printed as the cutover runs and repeated in
  the write report); the data is still migrated and nothing is overwritten. A file that appears
  between planning and writing degrades to a skip, never an overwrite or a crash.
- A regular file that is this entry's own earlier pointer stub (heading, blank line, and the
  "This file's data now lives in pdata record group" line, tolerant of a BOM and CRLF) may be
  rewritten, so re-creating an entry and cutting it over again neither loses its pointer nor leaves
  duplicates. A hand-extended file that no longer matches is preserved.
- The dry-run report lists every pointer-path collision (including collisions between entries) and
  the path the pointer will go to instead, before `--write` is run.
- Each substitution or skip is announced as it happens, appears in the write report, and is
  recorded in the archive `MANIFEST.md` line.
- `--leave-no-pointer-files` is unchanged: no pointer is written and no existing file is touched.
- `pm-pdata-do-init`'s skill text (Step 7) documents the collision behaviour.
- Patch release 3.10.5 (data-loss bug fix; no flag or interface change).

## Capabilities

### New Capabilities

### Modified Capabilities
- `pdata/init-pointer-files`: the pointer-path requirement now yields to an existing file; new
  requirements for never overwriting, rewriting only the entry's own earlier pointer, and reporting
  substitutions and skips.
- `pdata/init-classification-report`: the dry-run report lists pointer-path collisions.

## Impact

- `src/cc_session_tools/lib/pdata/cutover.py` (pointer path resolution, `archive_entries` returns
  outcomes), `src/cc_session_tools/lib/pdata/init_service.py` (dry-run report, write report),
  `src/cc_session_tools/skills/pm-pdata-do-init/SKILL.md`.
- Tests: `tests/pdata/test_cutover.py`, `tests/pdata/test_init_service.py`.
- CHANGELOG and patch bump to 3.10.5 (main is at 3.10.4 after the unknown-model-warning release).
- Fixtures use fictional names (`data/things.csv`); no real project data is used.
