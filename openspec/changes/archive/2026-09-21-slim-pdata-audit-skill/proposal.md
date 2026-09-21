## Why

`pm-pdata-do-audit-and-prepare-to-migrate` is a 14 KB / 199-line skill that has a model find, by
reading, facts that are mechanical: per-column mixed date formats, mixed separators, ragged rows,
machine-specific paths, non-real null strings, duplicate rows, BOMs and CRLF endings in every CSV.
Its Phase 2 sends these as a checklist to every domain agent, so each run re-derives them
non-deterministically, and a first pass can miss what a later pass finds. A deterministic scan
produces the same answer every time and lets agents spend their effort on judgement work. The skill
has been unchanged since 3.3.2 (four minor releases).

The claim is deliberately limited to fewer, exactly-derived facts per run. It is not a token-saving
claim: a live audit's token cost is not measured here, and a scan report handed to agents costs
tokens too, so the report is compact by default and can be sliced per domain.

## What Changes

- New read-only command `ccst pdata readiness-scan --project NAME [--format markdown|json]
  [--path PREFIX] [--findings-only]` that scans a project's CSV files for the mechanical
  pdata-migration blockers listed below, plus a per-file inventory and unique-column facts.
- The audit skill's `SKILL.md` is rewritten to run the scan first, pass each agent only its own
  domain's slice, and limit agent checklists to judgement work. It keeps the row_id-instability
  warning, and it names the scan's blind spots (multi-schema files, comma/semicolon list columns)
  as explicit agent bullets. Parallel agents become conditional on the number of domains.
- The skill is materially smaller; the before/after byte count is recorded in the changelog rather
  than asserted forever in a test.
- No change to `ccst pdata init`, the manifest, or any stored data.
- Minor release: 3.8.0 (new subcommand, additive).

## Capabilities

### New Capabilities
- `pdata/readiness-scan`: what `ccst pdata readiness-scan` scans, what it reports, and what it does
  not touch.

### Modified Capabilities
- `pdata/migration-guidance`: the audit skill must run the scan before dispatching agents, keep the
  row_id-instability warning, and keep agent checklists to judgement checks.

## Impact

- New `src/cc_session_tools/lib/pdata/readiness.py`; new verb wired in `cli/ccst.py`.
- Rewritten `src/cc_session_tools/skills/pm-pdata-do-audit-and-prepare-to-migrate/SKILL.md`.
- New tests under `tests/pdata/` and `tests/`; a short README subsection; `CHANGELOG.md`,
  `pyproject.toml` 3.8.0, `uv.lock`.
- The OneDrive sync-conflict scanner lives in the separately-owned `resolve-onedrive-conflicts`
  skill, not this repo; the audit skill keeps invoking it unchanged.
- Like every `ccst` verb the command passes through the standard install-sync check; developers
  running it from a checkout whose version differs from the installed tool must set
  `CCST_NO_AUTO_SYNC=1` (this repo's `.claude/CLAUDE.md`).
