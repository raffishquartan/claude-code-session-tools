## Context

`write()` (`init_service.py`) inserts rows entry by entry, then calls `_verify`, then backs up,
then cuts over. Anticipated failures return a `WriteResult` with a `WriteFailure` after calling
`_rollback`. Only `ValueError`/`OSError`/`csv.Error` raised inside the per-entry import loop are
caught. `_verify` calls `Path.exists()` on each record's `file_path`; on Python versions before
3.14 this raises for errors such as ENAMETOOLONG (only ENOENT, ENOTDIR, EBADF, ELOOP are
swallowed), so the exception escaped `write()` past the rollback.

`cutover.archive_entries` runs after the try region and renames sources one at a time; the
manifest is marked migrated only after it returns.

## Goals / Non-Goals

**Goals:** the documented rollback contract holds for the observed failure and for unanticipated
failures after insertion. **Non-Goals:** validating `file_path` values up front; changing the
manifest or classification; the skill guidance change (left to the user); the adopt-from-dump
rehearse fix (shipped in 3.9.1).

## Decisions

1. **Fix at the source in `_verify`:** catch `OSError` around `target.exists()` and append a reason,
   `continue`-ing to the next record. Same outcome as a clean "not found", with the system error
   included so the cause is visible. Alternative rejected: catching around the whole `_verify` call,
   which would hide other bugs behind a generic reason and skip verifying remaining records.
2. **Backstop in `write()`:** wrap the region from the first insert through the backup in
   `try/except BaseException` that rolls back and re-raises. `BaseException` on purpose: Ctrl-C
   during a long import is the likeliest real trigger, and `KeyboardInterrupt` is raised in normal
   context, not inside a signal handler, so cleaning up there is safe (a second Ctrl-C during the
   rollback interrupts it; accepted). It never swallows: the caller and CLI see the real error.
   A rollback that raises (for example `database is locked`) is caught, and never replaces the
   original error: the failure and every still-live record id are attached to the original as a
   note and emitted through the progress callback; per-row rollback failures are named by id the
   same way, and the summary counts rows actually rolled back.
3. **Fix `count_source_rows` at the source too:** its `OSError`/`ValueError`/`csv.Error` become a
   reason, mirroring the import loop, so they are reported as verification failures (with
   rollback) rather than re-raised and presented by the CLI like a pre-flight input error.
4. **Cutover is partial-safe, not all-or-nothing rollback.** Once an entry's source has been moved
   into the archive its rows are its live copy and must stay, so a blanket rollback would lose
   data. `archive_entries` gains an optional `on_archived` callback invoked right after each move;
   on failure `write()` marks the moved entries migrated (saving the manifest, with a note naming
   them if that save also fails) and rolls back only the rows of entries still in place. The
   cutover and manifest marking now sit inside the rehearsal overrides so the rollback hits the
   rehearsal database. Alternative rejected: leaving cutover failures uncovered, which makes a
   rerun silently duplicate every row.
3. **Portable tests:** `Path.exists` is patched to raise `OSError` for one path rather than relying
   on a real over-long name, because Python 3.14's `Path.exists` returns False instead of raising.

## Risks / Trade-offs

- [Backstop re-raises, so callers still see a traceback] -> intended: the bug is the leftover rows,
  and an unexpected error should stay loud.
- [`backup.create_backup`'s directory creation sits outside its own error wrapping] -> covered by
  the backstop (rollback, then re-raise) rather than reworked here.
- [A failing progress callback (for example a closed pipe) during the backstop can replace the
  original error] -> pre-existing and unchanged; follow-up.
- [Rollback inside the handler could itself fail] -> `_rollback` already collects per-row failures
  rather than raising; they are surfaced via progress output.

## Migration Plan

No on-disk or interface change. Ship as 3.9.3.
