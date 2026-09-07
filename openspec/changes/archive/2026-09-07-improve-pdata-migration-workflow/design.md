## Context

See proposal.md - Why. Relevant current-state facts that shape the approach below:

- `ManifestEntry` (`src/cc_session_tools/lib/pdata/manifest.py`) is a dataclass persisted to
  `.pdata-migration-manifest.json` via `manifest.save`/`manifest.load`. It is currently written
  once, at first classification (`load_or_create`), and never rewritten by `write()` - `write()`
  only ever `manifest.load`s it.
- `init_service.write()` (`src/cc_session_tools/lib/pdata/init_service.py`) loops every entry with
  `classification == "db-owned"`, imports it via `importers.import_entry`, and on any per-entry
  `(ValueError, OSError, csv.Error)` appends a reason string to `reasons` rather than aborting the
  loop early. After the loop, `if reasons:` triggers `_rollback(project=project,
  created_ids=created_ids)` - `created_ids` accumulates across *every* entry processed in that
  call, so one entry's failure rolls back every other entry's successful inserts from the same run.
- `cutover.archive_entries()` does `source.rename(destination)` for every entry in
  `written_entries` (only entries that succeeded import+verify reach this point) - unconditional,
  no pointer-file mechanism today.
- `classify.py`'s `_classify_csv` derives field names from the CSV header via
  `_slugify_field_name`/`_safe_field_name`; `_render_report` in `init_service.py` renders the
  dry-run report from `Manifest.entries` with no per-entry annotation today.
- `ccst.py`'s `pdata init` subparser (`pdata_init_parser`, around line 3022) currently defines
  `--project`, `--rehearse`, `--write` only.

## Goals / Non-Goals

**Goals:**
- Make a second/later `--write` round safe by construction: already-migrated entries are never
  re-read or re-imported, and a failure in a new entry never rolls back an already-committed one.
- Make pointer files the default, generically-populated cutover output, not a hand-composed
  per-migration artifact.
- Keep the manifest format backward-compatible: an existing manifest with no `migrated_at`/
  `preface_text` fields must still load and behave exactly as before (all newly-added
  `ManifestEntry` fields default to `None`).
- Keep skill-doc changes scoped to guidance/redirection - no skill's underlying mechanism changes,
  only what it tells a session to do first and what pitfalls it documents.

**Non-Goals:**
- No `file_path_prefix`/`file_path_relative_to` option on `ManifestEntry` (message 1's item 3
  raised this as an option). The workaround (map to a plain named field, document the relativity in
  its description) is fully sufficient and lower-risk; this change documents the workaround only,
  per message 1's own "either (a) document, or (b) consider a real option" framing, which presents
  (a) as sufficient on its own.
- No change to `pm-pdata-schema-design`, `pm-pdata-conflict-resolution`, or any other `pm-*` skill
  not named in the proposal.
- No `ccst pdata init --rollback` flag (still documented as a manual recovery procedure in
  `pm-project-init`, unchanged by this proposal).
- No retroactive backfill of `migrated_at` onto manifests from migrations that already completed
  before this change ships - see Migration Plan below.

## Decisions

### `migrated_at` as a persisted per-entry marker (not a separate migrated-ids file)
Add `migrated_at: str | None = None` to `ManifestEntry` (ISO-8601 UTC timestamp, `None` until set).
`write()` sets it on every entry it successfully commits through cutover, then calls
`manifest.save(m, proposal_path)` once at the end of a successful run (after `cutover.
archive_entries`, since cutover is the point of no return - a manifest write before cutover could
mark an entry migrated when its files were never actually moved, if the process died between the
two). Chosen over a separate "already-migrated ids" sidecar file because the manifest is already
the single source of truth this codebase reads/edits for every other per-entry property (spec
`pdata/manifest-naming` treats it as permanent tool state); adding a second file that must stay in
sync with it is a second thing to get out of sync, for no benefit `ManifestEntry` doesn't already
give.

Rejected alternative (message 2's fallback option, "refuse to start with a clear error"): kept as a
belt-and-braces addition, not a replacement - see the next decision - because it does not, by
itself, make a legitimate second `--write` round work; it only makes the *first* re-run's failure
message clearer. The message's own priority order (marker preferred, error-only as a fallback) is
adopted directly.

### `write()`'s per-entry loop filters on `migrated_at` before importing
`write()`'s `for entry in m.entries:` loop gains a branch: `if entry.classification == "db-owned"
and entry.migrated_at is not None: continue` (with a progress message noting the skip), before the
existing import/verify logic. An entry that is `db-owned`, has no `migrated_at`, and whose source
file does not exist raises a specific `ValueError` identifying the entry and stating it looks
already-migrated-elsewhere - caught by the existing `except (ValueError, OSError, csv.Error)`
per-entry handler, so it still contributes to that run's `reasons`/rollback exactly like any other
per-entry import failure, but with an actionable message instead of a bare `FileNotFoundError`.

### Rollback stays scoped to the current run's `created_ids` (already correct) - the bug is upstream
Re-reading `_rollback`/`write()`: `created_ids` is a local list built fresh on every `write()` call,
so rollback was never rolling back a *prior run's* ids - the actual bug is that, without the
`migrated_at` skip, a second run's loop *re-attempts* already-migrated entries in the first place,
so their failures land in the same `reasons`/rollback pass as this run's genuinely new entries,
rolling back the new entries alongside the (expected, but previously unhandled) re-import failures.
The `migrated_at` skip fixes this at the source: already-migrated entries are never processed by a
later run, so they can never contribute a failure reason to that run's rollback decision.

### Pointer files: generic content from the manifest entry, default-on with an opt-out flag
`cutover.archive_entries` gains pointer-file writing: for each entry, after `source.rename
(destination)`, write `<original path with .md extension>` containing the record_group, a
field/schema table built from `entry.fields` (name, sql_type, description), and an example query
(`ccst pdata list --project <project> --group <record_group>`). A new `preface_text: str | None =
None` field on `ManifestEntry` carries preserved header/provenance text (set by hand during Step 4
review, per the garbled-header recipe now documented in `pm-project-init`); when present, it's
appended to the pointer file under its own heading. `archive_entries` gains a `write_pointer_files:
bool = True` parameter; `init_service.write()` threads a new `leave_no_pointer_files: bool = False`
parameter through to it, driven by the CLI's `--leave-no-pointer-files` flag. Default-on (opt-out,
not opt-in) per explicit user direction in this change's originating conversation, overriding
ccmsg `20260907T112447Z-fe6b`'s own suggestion of an opt-in `--leave-pointer-files` flag - the
value applies to every cutover, not just the garbled-header case that motivated it, so opt-out
maximizes the cases that benefit without anyone needing to remember the flag.

### Suspicious-field-name heuristic lives in the report renderer, not the classifier
The heuristic (message 2's suggestion: multiple field names over ~40 chars or several words) is
applied in `init_service._render_report`, not `classify.py` - it's a reporting/reviewer-attention
concern, not a classification decision; the entry is still proposed exactly as `_classify_csv`
derives it, only the printed report line gains an annotation. This keeps `classify.py` deciding
classification and `init_service.py` deciding presentation, matching the module boundary the
docstrings already describe (`classify.py`: "Deliberately conservative... a human reviews the
printed report").

### `pm-pdata-do-migrate` stays on disk as a confirmation-gated fallback, not a full retirement
Decided explicitly with the user (not the default): `pm-pdata-do-migrate`'s file and existing
step-by-step content are preserved, not deleted, and not just annotated with a passive redirect
notice - the skill must actively stop and get the user's explicit confirmation, after reading the
deprecation notice, that (a) they understand `ccst pdata init` is the standard mechanism and this
skill is a deprecated fallback, and (b) they have a specific reason the programmatic approach
doesn't fit (e.g. a record shape none of `ccst pdata init`'s five import strategies can express),
before proceeding to Step 1. This is a stronger gate than message 1's own "keep both, but make the
fork explicit" alternative - it was raised because message 1's *other* alternative ("full
deprecation - retire entirely") was the one actually confirmed earlier in this change's own
conversation, so the weaker passive-notice version this design originally used was a deviation
worth flagging and deciding on deliberately, not defaulting to. `pm-pdata-do-audit-and-prepare-to-
migrate` keeps its existing step-by-step content unchanged beyond the redirect notice (it does
real work beyond pdata-readiness - OneDrive conflict cleanup, general central-file audits - so
retiring it was never on the table; only its Phase 5 manifest-consumer reference changes, from
"feeds `pm-pdata-do-migrate`" to "feeds `ccst pdata init` via `pm-project-init`").
`pm-project-init`'s new content is added as new numbered sections/caveats following its existing
step structure, not a restructure of the existing 8 steps.

## Risks / Trade-offs

- [Risk] A manifest entry hand-edited to change classification back to `db-owned` after being
  marked `migrated_at` would be silently skipped, not re-imported, even if the user genuinely wants
  it re-migrated (e.g. after restoring a backup). → Mitigation: `pm-project-init`'s rollback section
  already documents manual recovery (restore backup tar, `ccst pdata delete` the rows, fix the
  proposal); add a line there that a genuine intentional re-migration also requires clearing the
  entry's `migrated_at` field by hand, alongside the existing DB/proposal fixes.
- [Risk] Pointer files becoming the default is a **BREAKING** change to what cutover leaves on disk
  - a project that scripts around the exact post-cutover file listing would see new `.md` files
  appear. → Mitigation: `--leave-no-pointer-files` fully restores the old behavior; documented in
  both `--help` and `pm-project-init`.
- [Risk] Writing `manifest.save` after cutover (rather than incrementally per entry) means a crash
  between cutover and the final save would leave cut-over-on-disk entries without a `migrated_at`
  marker, reintroducing the original bug for that one run. → Mitigation: acceptable residual risk -
  narrower than the bug being fixed (requires cutover to succeed for the specific entries *and* the
  process to die in the few lines between that and the save), and the existing
  `ccst-pdata-init-write.log` (flushed after every line) already gives a session enough to diagnose
  and manually correct the manifest if this ever happens, consistent with how partial-failure
  recovery already works elsewhere in this module (schema-added-but-rows-rolled-back, documented in
  `pm-project-init` step 6).

## Migration Plan

- No data migration needed for existing manifests: `migrated_at`/`preface_text` are new optional
  dataclass fields defaulting to `None`; `manifest.load`'s `ManifestEntry(**entry_kwargs, ...)`
  construction already tolerates a JSON object missing keys that have dataclass defaults.
- A project that completed a `--write` before this change ships has a manifest with no
  `migrated_at` on its already-cut-over entries. A later `--write` against that manifest (to pick up
  new files) would hit exactly the pre-fix bug for those old entries **unless** their source files
  still exist - but cutover already moved them to `.pdata-migrated/`, so the new
  missing-source-and-unmarked error fires for them, same as before this change existed for a
  not-yet-updated manifest. This is a wash relative to today, not a regression, and out of scope to
  backfill (would require guessing which entries the write-log shows already succeeded). No
  `ccst doctor` check is proposed for this narrow, already-documented-as-manual-recovery case.
- Per repo version policy: this ships as a **minor** version bump (additive, backward-compatible on
  disk). No `ccst doctor` migration-guard check is required (only a major/breaking on-disk format
  change requires one).
