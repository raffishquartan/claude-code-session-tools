## 1. Manifest schema: `migrated_at` and `preface_text`

- [x] 1.1 Add `migrated_at: str | None = None` and `preface_text: str | None = None` fields to
      `ManifestEntry` in `src/cc_session_tools/lib/pdata/manifest.py`; verify `uv run pytest
      tests/pdata/test_manifest.py -q` still passes and that `manifest.load` on a manifest JSON
      with neither key present still constructs a valid `ManifestEntry` (add a test case for this
      if none exists).
- [x] 1.2 Verify `manifest.save`/`manifest.load` round-trip both new fields (a manifest saved with
      `migrated_at`/`preface_text` set, then reloaded, has the same values) via a new or extended
      test in `tests/pdata/test_manifest.py`.

## 2. `write()` idempotency

- [x] 2.1 In `init_service.write()`'s per-entry loop (`src/cc_session_tools/lib/pdata/
      init_service.py`), skip any `db-owned` entry whose `migrated_at is not None` before the
      existing import/verify logic, emitting an `on_progress` message noting the skip; verify with
      a new test in `tests/pdata/test_init_service.py` that a manifest with one already-`migrated_at`
      entry and one fresh entry only imports/verifies the fresh one.
- [x] 2.2 Raise a specific `ValueError` (caught by the existing per-entry `except (ValueError,
      OSError, csv.Error)` handler) for a `db-owned` entry with `migrated_at is None` whose source
      file does not exist, naming the entry path and stating the source is missing and unmarked;
      verify with a test asserting the resulting failure reason string is distinguishable from a
      generic `FileNotFoundError` message.
- [x] 2.3 On a successful `write()` run, set `migrated_at` (ISO-8601 UTC) on every entry cut over
      this run and call `manifest.save(m, proposal_path)` once after `cutover.archive_entries`
      succeeds; verify with a test that re-reads the manifest file from disk after a successful
      `write()` and confirms the cut-over entries carry a `migrated_at` timestamp.
- [x] 2.4 Reproduce ccmsg `20260907T112447Z-fe6b` §2's exact scenario as a regression test: one
      successful `--write`, then a second `--write` with one newly-added `db-owned` entry and no
      manifest edits to the already-migrated entries - assert the second run succeeds, imports only
      the new entry, and the first run's record ids are untouched (still retrievable via
      `service.get_record`, not soft-deleted).

## 3. Pointer files on cutover

- [x] 3.1 Add a `write_pointer_files: bool = True` parameter to `cutover.archive_entries`
      (`src/cc_session_tools/lib/pdata/cutover.py`); when true, after each entry's `source.rename`,
      write a `.md` pointer file at the entry's original path (same basename) containing the
      record_group, a field/schema table from `entry.fields` (name, sql_type, description), and an
      example `ccst pdata list --project <project> --group <record_group>` command; verify with a
      new test in `tests/pdata/test_cutover.py` asserting the pointer file exists with the expected
      content after `archive_entries` runs with the default parameter.
- [x] 3.2 When an entry's `preface_text` is set, append it verbatim under its own heading in that
      entry's pointer file; verify with a test covering an entry with `preface_text` set and one
      without, asserting the heading appears only for the former.
- [x] 3.3 When `write_pointer_files=False`, no pointer file is written for any entry; verify with a
      test asserting no `.md` file appears at any cut-over entry's original path.
- [x] 3.4 Thread a `leave_no_pointer_files: bool = False` parameter from `init_service.write()`
      through to `cutover.archive_entries`; add `--leave-no-pointer-files` (`action="store_true"`)
      to `pdata_init_parser` in `src/cc_session_tools/cli/ccst.py` and pass it through
      `_cmd_pdata_init`; verify `ccst pdata init --help` output includes both the flag and updated
      help text describing the default-on pointer-file behavior (`uv run python -m
      cc_session_tools.cli.ccst pdata init --help` shows it).
- [x] 3.5 Add an end-to-end test (or extend an existing `--write` integration test) asserting a real
      `ccst pdata init --write` run against a small fixture project leaves pointer files by default
      and omits them when `--leave-no-pointer-files` is passed.

## 4. Dry-run report: suspicious field-name heuristic

- [x] 4.1 Add a heuristic function (e.g. in `init_service.py`, alongside `_render_report`) that
      flags a `db-owned` entry as suspicious when multiple of its field names exceed a length/
      word-count threshold (~40 characters or several words, per ccmsg `20260907T112447Z-fe6b` §5);
      verify with a unit test covering a garbled-header-shaped field list (flagged) and an ordinary
      short-field-name list (not flagged).
- [x] 4.2 Wire the heuristic into `_render_report` so a flagged entry's report line carries a
      "worth double-checking" annotation; verify with a test asserting the annotated text appears
      in `dry_run(...).report` for a fixture manifest containing a garbled-header-shaped entry, and
      does not appear for a clean entry.

## 5. Skill doc updates

- [x] 5.1 Add a first-line deprecation notice to `src/cc_session_tools/skills/
      pm-pdata-do-migrate/SKILL.md` per spec `pdata/migration-guidance`'s first requirement,
      preserving the rest of the skill's existing manual-migration content below it; verify by
      reading the file back and confirming the notice precedes all other content.
- [x] 5.1a Add an explicit user-confirmation gate immediately after the deprecation notice and
      before Step 1, per spec `pdata/migration-guidance`'s "gates on explicit user confirmation"
      requirement - the invoking session must confirm the user understands `ccst pdata init` is
      the default and states a reason the programmatic approach doesn't fit before proceeding;
      verify by reading the file back and confirming the gate is unconditional (no path to Step 1
      that skips it).
- [x] 5.2 Add a first-line redirect notice to `src/cc_session_tools/skills/
      pm-pdata-do-audit-and-prepare-to-migrate/SKILL.md` per spec `pdata/migration-guidance`'s
      second requirement, repositioning its Phase 4/5 readiness output as feeding `ccst pdata init`
      via `pm-project-init`; verify by reading the file back.
- [x] 5.3 Add the garbled-CSV-header caveat and fix recipe to `src/cc_session_tools/skills/
      pm-project-init/SKILL.md`, referencing the pointer-file behavior from task group 3; verify by
      reading the file back against spec `pdata/migration-guidance`'s corresponding requirement.
- [x] 5.4 Add the `file_path_column`-relative-to-a-subfolder pitfall and workaround to
      `pm-project-init/SKILL.md`; verify by reading the file back.
- [x] 5.5 Add the `content_column: null` default caveat to `pm-project-init/SKILL.md`; verify by
      reading the file back.
- [x] 5.6 Add the value-transform recipe (import via `csv-rows`, then `schema add-field` + per-
      record `update`) to `pm-project-init/SKILL.md`; verify by reading the file back.
- [x] 5.7 Add the "migration is iterative, expect multiple `--write` rounds" expectation-setting
      note to `pm-project-init/SKILL.md`, near its existing Step 6 (write phase); verify by reading
      the file back.
- [x] 5.8 Add the two deferred-file decision criteria (read-only-by-convention folder;
      wholesale-replacement vs. append-only growth) as explicit review questions in
      `pm-project-init/SKILL.md`'s Step 3 (review folder-owned/db-owned entries); verify by reading
      the file back.
- [x] 5.9 Add the note that `--write` clearing an already-set `migrated_at` is required for a
      genuine intentional re-migration, alongside `pm-project-init/SKILL.md`'s existing rollback
      section (Step 8), per design.md's first Risk mitigation; verify by reading the file back.
- [x] 5.10 Reword the "run in a new Claude Code session" instruction in both
      `src/cc_session_tools/prompts/pdata-migration-claude-md-update.md` and
      `src/cc_session_tools/prompts/pdata-migration-skills-update.md` to accept a dispatched
      `Agent` subagent as an equivalent fresh-context substitute; verify by reading both files back.

## 6. Full verification and release

- [x] 6.1 Run the full check suite (`uv run pytest -q`, plus this repo's configured lint/type/
      format checks) and confirm everything exits 0.
- [x] 6.2 Update `CHANGELOG.md` with an entry for this change (new manifest fields, write-
      idempotency fix, default pointer files + `--leave-no-pointer-files`, dry-run report
      heuristic, `pm-pdata-do-migrate` deprecation, `pm-project-init`/prompt doc updates) and bump
      `pyproject.toml`'s `version` per this repo's semver policy (minor - additive, backward-
      compatible on-disk changes only, per design.md's Migration Plan); run `uv lock` and commit
      the regenerated `uv.lock` in the same commit, per this repo's CLAUDE.md release instructions.
