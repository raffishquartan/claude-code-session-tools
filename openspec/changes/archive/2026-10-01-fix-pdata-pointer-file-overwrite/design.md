## Context

`cutover.archive_entries` renames each `db-owned` source into `.pdata-migrated/`, appends a line to
`.pdata-migrated/MANIFEST.md`, then (default) calls `_write_pointer_file`, which does
`pointer_path = project_root / Path(entry.path).with_suffix(".md")` and `write_text`. Nothing earlier
looks at that path. `init_service.dry_run` renders the report from the manifest alone
(`_render_report(m)`); `init_service.write` only passes entries without `migrated_at` to
`archive_entries`, and builds its report from `_render_diff_report`. `_recover_from_failed_cutover`
touches DB rows and the manifest only, never files.

## Goals / Non-Goals

**Goals:** never destroy an existing file; surface collisions before `--write`; keep the no-collision
path byte-identical to today.

**Non-Goals:** archiving or moving the colliding `.md`; changing which entries are migrated; changing
`--leave-no-pointer-files` (no planning happens when it is set); reading the narrative `.md` into pdata.

## Decisions

- **Substitute rather than skip, and name it after the full original file name:**
  `<original name>.pdata-pointer.md` (`things.csv` -> `things.csv.pdata-pointer.md`). `a.csv` and
  `a.json` are both `db-owned` types and often share a stem; a stem-only substitute would make the
  second one skip whenever a real `a.md` exists. The full-name form is unique per source file. This
  refines the suggested `<stem>.pdata-pointer.md` in the bug report. Skip is the fallback only if the
  substitute is occupied.
- **Planning is pure and shared:** `cutover.plan_pointers(project_root, entries) -> list[PointerPlan]`
  simulates the whole batch in order, tracking paths claimed by earlier entries' pointers, so the
  dry-run predicts exactly what `write` will do, including collisions between entries
  (`a.csv` + `a.json` with no real `a.md`: the first takes `a.md`, the second substitutes). The source
  path of the entry being planned counts as vacated, so a hand-edited `db-owned` entry whose source is
  itself a `.md` (pointer path == source path) is not a collision. `PointerPlan` holds `entry_path`,
  `primary`, `target` (or `None` for skip) and `occupied` (the paths that forced the outcome).
- **What counts as occupied (never read, never followed):** `os.path.lexists` is true. A path is
  rewritable only if it is a regular file, not a symlink, and is this entry's own earlier pointer.
  Directories, live or dangling symlinks, and unreadable files are occupied-not-own. Any `OSError` while
  inspecting an occupant also means occupied-not-own; the planner never raises.
- **Own-earlier-pointer signature:** read the first bytes of the file, decode as UTF-8 with
  `errors="replace"`, strip a BOM, normalise CRLF, and require line 1 to equal
  `# <original file name> — migrated to pdata`, line 2 blank, and line 3 to start with
  `This file's data now lives in pdata record group`. The heading and that line come from shared
  helpers next to `_pointer_file_content`, so detection cannot drift from generation. The heading
  contains the file name with its extension, so `a.csv` and `a.json` differ. A pointer that a person
  extended by editing those lines is preserved (a substitute is used instead); text added below them
  does not stop a rewrite, which is accepted because the stub is generated, not authored.
- **Order of operations in `archive_entries`:** plan (pure), then rename, `on_archived`, write the
  `MANIFEST.md` line (with a "pointer substituted to X" / "pointer skipped, X occupied" suffix when
  applicable, preserving "rename implies log line"), then write the pointer. A pointer file that does
  not exist is created with exclusive mode (`"x"`); a `FileExistsError` there degrades to a skip
  outcome with a warning instead of raising after the source was already archived. Other `OSError`s
  (for example a full disk) still raise, as today.
- **Announcements:** `archive_entries` takes an optional `on_pointer: Callable[[PointerOutcome], None]`
  (mirror of `on_archived`) and returns the outcomes. `write` wires `on_pointer` to `_emit(on_progress,
  ...)`: a substitution prints `Pointer for <path> written to <target> (<occupied> already exists and
  was left untouched)`; a skip prints `WARNING: no pointer written for <path>: <occupied> already
  exists and was left untouched`. `write` also collects outcomes through the same callback, so the
  report's "Pointer files" section (absent when there are none) survives a later exception. The CLI
  exit code stays 0 for a skip: the data is migrated and nothing was lost.
- **Dry-run:** `_render_report` takes the project root, calls `plan_pointers` for the not-yet-migrated
  `db-owned` entries (same filtered list `write` uses) and adds an indented line per substitution or
  skip. The wording says no pointer is written if `--leave-no-pointer-files` is passed, because the
  dry run does not know that flag.
- **Rollback and recovery:** unchanged and documented. Recovery never touches files; a skipped or
  failed pointer is not retried, because the entry is marked migrated and the `MANIFEST.md` line is the
  record.
- **Version:** patch 3.10.5. It fixes data loss without changing a flag, schema or on-disk format.

## Risks / Trade-offs

- A third-party file already named `<name>.pdata-pointer.md` forces a skip -> loud warning, no data
  loss, data still migrated.
- The dry run reflects the filesystem at dry-run time; a file added before `--write` is still caught
  because `--write` re-plans (tested).
- `archive_entries` changing from returning `None` to returning outcomes touches its callers -> checked
  with grep: only `init_service.write` and tests call it.
- Case-insensitive filesystems (macOS default): `exists`/`lexists` match regardless of case, so
  `Things.MD` collides with `things.md`; names are never compared as strings.
