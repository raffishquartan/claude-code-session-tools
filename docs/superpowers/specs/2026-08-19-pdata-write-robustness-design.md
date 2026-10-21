# `ccst pdata init --write` robustness + logging — design spec

Status: DRAFT, awaiting review. Diagnosis this spec responds to:
`cc-sessions/20260819-improve-pdata-setup/working/2026-08-19-pdata-write-diagnosis.md`.

## Problem

`ccst pdata init --project <name> --write` can fail partway through with no useful console
output, and a failure during its final backup step is not handled: it escapes as a raw, uncaught
exception, leaving already-committed DB rows unrolled-back and a corrupt tar.gz sitting at the
same filename a valid backup would use. Confirmed against a real run on `home` (2026-08-19).

## Scope

1. Backup robustness — never let a partial/corrupt backup masquerade as a real one, never let a
   backup failure escape as raw crash, retry the transient case.
2. Back up the project's `.db` alongside `project_root`, before either is touched by cutover.
3. Stream progress to stdout during `--write`, and tee everything (stdout, stderr, tracebacks)
   into a per-run log file inside the project.

Deliberately **out of scope** (per your call on the fix-scope question): a pre-flight
concurrent-access check. `repository.connect()` already goes through the shared `db.connect()`
helper, which sets `PRAGMA journal_mode=WAL` and a `busy_timeout`
(`src/cc_session_tools/lib/db.py`), and every multi-statement write uses `BEGIN IMMEDIATE` — a
second concurrent writer blocks-and-retries rather than corrupting anything. A friendlier
fail-fast message instead of a silent block is a legitimate future nice-to-have, not a
correctness gap; noted in TODO.md rather than built now.

## Design

### 1. `lib/pdata/backup.py` — atomic, retried, .db-inclusive backup

```python
def create_backup(*, project: str, project_root: Path) -> Path:
```
becomes, in outline:

- Compute the final path as today: `target_dir / f"{project}-{epoch}.tar.gz"`.
- Write to a sibling temp path instead: `target_dir / f"{project}-{epoch}.tar.gz.tmp"`.
- Inside the `tarfile.open(tmp_path, "w:gz")` block:
  - If `store.db_path(project)` exists, snapshot it via the **sqlite3 backup API**
    (`sqlite3.connect(src).backup(sqlite3.connect(dest_tmp_file))`) into a temp file first, then
    `tar.add()` that snapshot under a fixed in-archive name (e.g. `<project>/<project>.db`) —
    not a raw `tar.add()` of the live `.db` file, because a raw copy of a WAL-mode database can
    miss committed data still sitting in `-wal`. Clean up the temp snapshot file after adding it
    (or add and immediately unlink — standard temp-file-in-tar pattern).
  - `tar.add(project_root, arcname=project)` as today.
- Wrap the whole "produce the temp tar.gz" sequence in a bounded retry loop: catch `OSError`,
  retry up to `_MAX_ATTEMPTS = 3` times with a short backoff (e.g. 1s, 2s), re-raising the last
  exception (wrapped as a `BackupError(str)` for a clearer message — filename, attempt count,
  underlying errno) if all attempts fail. Re-open/re-truncate the temp tar fresh on each retry
  attempt (a half-written tar can't be resumed).
- On success: `os.replace(tmp_path, final_path)` — atomic on the same filesystem, so the real
  filename never exists in a partial state. On final failure: leave (or clean up) the `.tmp` file,
  raise `BackupError`.

New `BackupError(Exception)` in the same module.

### 2. `lib/pdata/init_service.py` — fold backup failure into the existing rollback path

- Extract the rollback block (current lines 188-198) into a helper:
  ```python
  def _rollback(*, project: str, created_ids: list[int]) -> list[str]:
      """Soft-delete every id in created_ids (spec §4.5); returns any rollback-failure reasons."""
  ```
  used both by the existing `if reasons:` branch and by a new backup-failure branch, so there's
  one rollback code path, not two.
- Wrap the `backup.create_backup(...)` call (current line 202) in
  `try: ... except backup.BackupError as exc: ...` — on failure, call `_rollback(...)`, append the
  backup error (plus any rollback-failure reasons) to a `reasons` list, and `return WriteResult(...)`
  with a populated `WriteFailure`, exactly like the existing verify-failure return shape. No
  exception escapes `write()` for this failure mode any more.

### 3. Progress logging — `lib/pdata/write_log.py` (new) + CLI wiring

New module, `WriteLog`, a context manager:

- `WriteLog(project_root: Path)` — on enter, opens
  `project_root / "ccst-pdata-init-write.log"` in **truncate** mode (one log per `--write`
  invocation, matching the tool's big-bang/one-shot semantics — a stitched-together log across
  unrelated attempts would be confusing, not helpful).
- While open, installs a small `Tee` file-like wrapper on `sys.stdout` and `sys.stderr`
  (writes to both the real stream and the log file; `flush()` calls both, and the log file's
  `write()` calls `self._log.flush()` after every write so partial runs still leave a fully
  flushed log even on a hard crash) — this means *every* `print()` the CLI already does, plus
  Python's own default unhandled-traceback-to-stderr output for anything that still somehow
  escapes, lands in the file with zero extra call-sites to remember.
- On exit (`__exit__`), restores the original `sys.stdout`/`sys.stderr` and closes the log file.

CLI wiring in `_cmd_pdata_init` (`cli/ccst.py`), `--write` branch only:

- Resolve `project_root` up front (same call `init_service.write()` already makes internally via
  `init_paths.resolve_project_root` — safe/idempotent, just creates the dir if missing).
- Wrap the `init_service.write(...)` call and all of `_cmd_pdata_init`'s own `print(...)` calls for
  the `--write` branch inside `with write_log.WriteLog(project_root):`.
- Add progress prints inside `init_service.write()` itself at each phase boundary — entering the
  import loop (with a per-entry "importing `<path>` (`<record_group>`)..." line and a completion
  count), entering `_verify()`, entering the backup step, entering cutover — so a long-running
  `--write` shows visible movement rather than sitting silent until it either finishes or crashes.

### Versioning

Additive, backwards-compatible: existing backup tar.gz files remain readable by any tooling that
reads them (the `.db` entry is new content inside the archive, not a format change to the
existing project_root entries); no on-disk data becomes unreadable by old code. **Minor bump**:
2.6.0 → **2.7.0**.

## Testing (for the implementation plan to detail)

- `backup.create_backup()`: happy path includes both project_root and `.db` entries; temp file
  never left at the final name on a simulated `tar.add()` failure (patch to raise `OSError` on
  first N calls, succeed after); retry count respected and exhausted-retries raises `BackupError`;
  `.db`-absent project (fresh project, no `.db` yet) still backs up project_root fine.
- `init_service.write()`: a `BackupError` from `create_backup()` results in a `WriteFailure`
  (not a raised exception) with all just-inserted rows rolled back (soft-deleted) — mirrors the
  existing verify-failure test shape.
- `write_log.WriteLog`: every stdout/stderr line during the `with` block appears in the log file,
  in order; file is flushed after each write (assert via reading the file mid-run in a test that
  interleaves writes and reads, or by checking `os.fstat` size progresses without a final flush);
  an unhandled exception raised inside the `with` block still leaves its traceback in the log
  file (log survives the exception path, not just the happy path).
- CLI-level: a full `--write` run against a temp project produces both console output and a
  `ccst-pdata-init-write.log` with matching content; the log truncates (doesn't append) across two
  successive `--write` invocations.
