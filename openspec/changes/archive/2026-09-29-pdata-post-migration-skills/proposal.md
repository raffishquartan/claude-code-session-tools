## Why

The two post-`ccst pdata init --write` procedures (update the project's docs to describe the
pdata-backed store; update skills that still read or write the old flat-file paths) ship as loose
prompt files that `ccst pdata init` merely prints paths to. They are not discoverable as skills,
have no trigger phrases, are not wired into `pm-pdata-do-init`'s own workflow, and need a hand-written
`claude -p` invocation. Making them skills lets every future migration pick them up the same way it
picks up the rest of the `pm-pdata-*` family, and keeps the fresh-context requirement expressible as
"dispatch a subagent" rather than a shell incantation.

## What Changes

- Add skill `pm-pdata-do-update-project-docs` (from `pdata-migration-claude-md-update.md`): rewrite
  stale flat-file references in a project's top-level docs to name the pdata record_group and
  `ccst pdata` command; report (never act on) folder-layout drift.
- Add skill `pm-pdata-do-update-consuming-skills` (from `pdata-migration-skills-update.md`): find,
  classify, and mechanically rewrite skills that literally reference the project's old flat-file
  paths; flag anything that does not map cleanly.
- `pm-pdata-do-init` gains a final step, run after a successful `--write`, that dispatches both new
  skills in order (docs first, then consuming skills), each in a fresh-context subagent whose cwd is
  the project root.
- `ccst pdata init` output now names the two skills instead of printing prompt-file paths.
- **BREAKING** (internal): remove the bundled `prompts/` directory, its packaging entries, and the
  prompt-discovery code and tests; the two prompt files no longer exist.
- No existing project is migrated or re-run as part of this change.
- Bump minor version and update `CHANGELOG.md`.

## Capabilities

### New Capabilities
- `pdata/post-migration-skills`: the two post-migration skills, their fresh-context dispatch, their
  invocation from `pm-pdata-do-init`, and the `ccst pdata init` output that points at them.

### Modified Capabilities
- `pdata/migration-guidance`: remove the requirement that the shipped post-write prompts accept a
  subagent substitute (the prompts are gone; the equivalent requirement moves to the new capability).
- `cli/packaging-fresh-install`: bundled-data discovery covers `skills/` and `config/` only; the
  `prompts/` directory is no longer bundled.

## Impact

- `src/cc_session_tools/skills/` (two new skills, `pm-pdata-do-init/SKILL.md`)
- `src/cc_session_tools/prompts/` (deleted), `pyproject.toml` (packaging entries)
- `src/cc_session_tools/cli/ccst.py` (prompt discovery/reminder code replaced by skill reminders)
- `tests/test_ccst_bundle_discovery.py`, `tests/test_ccst_pdata_init_cli.py`, plus new skill tests
- `CHANGELOG.md`, `pyproject.toml` version, `uv.lock` if it records the version
- Installed `~/.claude/skills/` symlinks: the new skills appear after `ccst skills install`
