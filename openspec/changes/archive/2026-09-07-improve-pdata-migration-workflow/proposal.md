## Why

Two inter-session reports from a real-world `maxella` project migration (ccmsg
`20260907T065412Z-7a37`, `20260907T112447Z-fe6b`) show that `pm-pdata-do-migrate` and
`pm-pdata-do-audit-and-prepare-to-migrate` never mention that `ccst pdata init` - a more complete,
safer, tool-native migration mechanism - already exists, which caused a full hand-rolled migration
(1,324 rows) to be built, executed, and then discarded and redone once the user caught the gap.
The same real migration also surfaced a genuine safety bug in `ccst pdata init --write` (a second
`--write` run can silently roll back legitimate new imports alongside expected re-import failures)
and several undocumented pitfalls (garbled CSV headers, `file_path_column` misuse, an unhelpful
`content_column` default, unclear guidance on iterative multi-round migrations and deferred-file
judgement calls) that cost real session time to reverse-engineer. This change closes the discovery
gap, fixes the write-safety bug, and encodes the reverse-engineered pitfalls as documented guidance
so no future migration re-derives them from scratch.

## What Changes

- Deprecate `pm-pdata-do-migrate` in favour of `ccst pdata init`: both `pm-pdata-do-migrate`'s and
  `pm-pdata-do-audit-and-prepare-to-migrate`'s `SKILL.md` gain a loud, first-line cross-reference
  stating `ccst pdata init` is the tool-native mechanism and superseding these skills wherever it
  is available; `pm-pdata-do-audit-and-prepare-to-migrate`'s coordination-file hygiene checks are
  reframed as a pre-flight pass to run before `ccst pdata init`, not a prerequisite to a separate
  manual-migration path. **BREAKING** (workflow-level): a session that previously followed
  `pm-pdata-do-migrate` end to end will now be redirected to `ccst pdata init` instead.
- Expand `pm-project-init`'s `SKILL.md` with reverse-engineered guidance: the garbled-CSV-header
  failure mode and fix recipe, the `file_path_column`-relative-to-a-subfolder pitfall and its
  workaround, the `content_column: null` default caveat, the value-transform recipe for fields the
  generic importer can't express, the expectation that a real migration is iterative (multiple
  `--write` rounds are normal), and the two deferred-file decision criteria (read-only-by-convention
  folders; wholesale-replacement vs. append-only growth).
- Reword the "run in a new Claude Code session" instruction in both shipped post-write prompts
  (`pdata-migration-claude-md-update.md`, `pdata-migration-skills-update.md`) to accept a
  dispatched `Agent` subagent as an equivalent fresh-context substitute.
- Fix a real safety bug in `ccst pdata init --write`: re-running `--write` after a prior successful
  `--write` currently re-imports every manifest entry still marked `db-owned`, including
  already-cut-over ones (whose source file no longer exists), and a bare `if reasons:` rollback
  trigger discards every id created *that run* - including genuinely new, correctly-imported
  entries - when even one already-migrated entry fails. Fix: persist a `migrated_at` timestamp per
  manifest entry, set on successful cutover; a later `--write` skips entries that already carry
  one instead of re-importing them. A `db-owned` entry with no `migrated_at` whose source file is
  nonetheless missing now fails fast with a specific, actionable error instead of a generic
  `FileNotFoundError` surfacing only via the rollback reasons list.
- Make pointer-file creation the default behavior of `ccst pdata init --write`'s cutover step: for
  every entry cut over, write a small `.md` pointer file back at the original path (pdata group,
  field/schema table, an example query command, and - when the entry carried removed
  preface/provenance text from the garbled-header fix recipe - that text verbatim under its own
  heading). Add an opt-out flag `--leave-no-pointer-files` and update `--help` text accordingly.
  **BREAKING**: a cutover under the current default (no pointer files) now leaves an additional
  `.md` file at each migrated entry's original path unless the new flag is passed.
- Add a cheap heuristic to the dry-run classification report: flag any `db-owned` entry whose
  proposed field names look like sentence fragments rather than column names (length/word-count
  threshold) as worth double-checking before `--write`.

## Capabilities

### New Capabilities
- `pdata/migration-guidance`: what `pm-pdata-do-migrate`, `pm-pdata-do-audit-and-prepare-to-migrate`,
  `pm-project-init`, and the two shipped post-write prompts must document/cross-reference so a
  session never again completes a manual migration without learning `ccst pdata init` exists, and
  has the pitfall/recipe knowledge this real migration had to reverse-engineer.
- `pdata/init-write-idempotency`: `ccst pdata init --write`'s behavior on a second/later run against
  a manifest that already has cut-over entries - no silent cross-entry rollback, a persisted
  per-entry migrated marker, and a specific error for a genuinely missing but unmigrated source.
- `pdata/init-pointer-files`: default pointer-file behavior on cutover and the
  `--leave-no-pointer-files` opt-out.
- `pdata/init-classification-report`: the dry-run report's suspicious-field-name heuristic.

### Modified Capabilities
- (none - all behavior changes here are new capabilities; no existing spec's requirements change)

## Impact

- Skill docs: `src/cc_session_tools/skills/pm-pdata-do-migrate/SKILL.md`,
  `src/cc_session_tools/skills/pm-pdata-do-audit-and-prepare-to-migrate/SKILL.md`,
  `src/cc_session_tools/skills/pm-project-init/SKILL.md`.
- Prompts: `src/cc_session_tools/prompts/pdata-migration-claude-md-update.md`,
  `src/cc_session_tools/prompts/pdata-migration-skills-update.md`.
- Code: `src/cc_session_tools/lib/pdata/manifest.py` (new `migrated_at`/`preface_text` fields on
  `ManifestEntry`), `src/cc_session_tools/lib/pdata/init_service.py` (`write()`'s per-entry skip
  logic, missing-source error, manifest persistence after cutover), `src/cc_session_tools/lib/pdata/
  cutover.py` (pointer-file writing), `src/cc_session_tools/lib/pdata/classify.py` (report
  heuristic surfaced via `init_service._render_report`), `src/cc_session_tools/cli/ccst.py`
  (`--leave-no-pointer-files` flag and `--help` text for `pdata init`).
- Tests: `tests/pdata/` - new/updated coverage for write-idempotency (the reproduction steps ccmsg
  `20260907T112447Z-fe6b` §2 describes), pointer-file default/opt-out, and the classification-report
  heuristic.
- Per this repo's version policy, this is a **minor** bump (additive CLI flag, additive manifest
  fields with safe defaults, no on-disk data made unreadable by old code - an old manifest with no
  `migrated_at` field is still valid; `FieldSpec`/`ManifestEntry` gain fields with `None` defaults).
