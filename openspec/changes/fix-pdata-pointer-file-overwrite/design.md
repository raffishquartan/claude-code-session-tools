## Context

`cutover.archive_entries` renames each `db-owned` source into `.pdata-migrated/`, appends a line to
`.pdata-migrated/MANIFEST.md`, then (default) calls `_write_pointer_file`, which does
`pointer_path = project_root / Path(entry.path).with_suffix(".md")` and `write_text`. Nothing earlier
looks at that path. `init_service.dry_run` renders the report from the manifest alone
(`_render_report(m)`); `init_service.write` builds its report from `_render_diff_report`.

## Goals / Non-Goals

**Goals:** never destroy an existing file; surface collisions before `--write`; keep idempotent
re-runs and the no-collision path byte-identical to today.

**Non-Goals:** archiving or moving the colliding `.md`; changing which entries are migrated; changing
`--leave-no-pointer-files`; reading the narrative `.md` into pdata.

## Decisions

- **Why substitute rather than skip:** the pointer is a signpost for anything citing the old CSV path,
  and the substituted name keeps that signpost while leaving the real file alone. Skipping is the
  fallback only when the substitute is also occupied.
- **Pointer resolution in one place:** `cutover.plan_pointer(project_root, entry) -> PointerPlan`,
  used by both the dry-run report and the writer so they cannot disagree. `PointerPlan` holds the
  path to write (or `None` for skip) and the primary path, so callers can tell a substitution.
  Algorithm: primary `<stem>.md`; usable if absent or an own-earlier-pointer; else substitute
  `<stem>.pdata-pointer.md`, usable on the same test; else skip.
- **Own-earlier-pointer test:** the existing file's first line equals
  `# <csv name> — migrated to pdata`, the heading `_pointer_file_content` writes, produced by a
  shared `_pointer_heading(entry)` so detection cannot drift from generation. It is the CSV's file name
  (including extension), so `a.csv` and `a.tsv` have different headings. Reading only the first line
  keeps this cheap. A real narrative file that happens to start with that exact heading is treated as
  a pointer; that is the accepted trade-off and is the same signature the spec documents.
- **Write safety:** a path that does not exist is created with exclusive mode (`"x"`), so a file
  appearing between planning and writing raises instead of being truncated; only an own-earlier
  pointer is rewritten with `write_text`.
- **Planning happens at write time per entry**, after earlier entries' pointers exist, so two
  entries mapping to the same primary path resolve correctly (the second sees the first's pointer,
  whose heading differs, and substitutes).
- **Reporting:** `archive_entries` returns a list of `PointerOutcome(entry_path, written_path | None,
  substituted)`; `init_service.write` appends a "Pointer files" section to `WriteResult.report` for
  substitutions and skips (nothing extra when there are none, so the no-collision report is
  unchanged), and the `MANIFEST.md` log line for a substituted or skipped entry says so.
- **Dry-run:** `_render_report` gains the project root and, for each not-yet-migrated `db-owned` entry
  whose plan is a substitution or skip, adds an indented warning line naming the occupied path and
  where the pointer will go (or that it will be skipped), noting `--leave-no-pointer-files` suppresses
  pointers. Entries with `migrated_at` set are ignored (already cut over).
- **Version:** patch 3.10.5. It fixes data loss without changing a flag, schema or on-disk format.

## Risks / Trade-offs

- A third-party file already named `<stem>.pdata-pointer.md` forces a skip -> loud warning, no data
  loss, data still migrated.
- The dry run reflects the filesystem at dry-run time; a file added before `--write` is still caught
  because `--write` re-plans per entry.
- `archive_entries` changing from returning `None` to returning outcomes touches its callers -> only
  `init_service.write` and tests call it.
