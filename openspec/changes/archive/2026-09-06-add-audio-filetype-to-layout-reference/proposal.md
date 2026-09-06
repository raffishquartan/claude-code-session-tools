## Why

`pm-project-layout-reference`'s `SKILL.md` documents `meetings-and-calls/` as a folder but not
the `<file type>` segment vocabulary within its filenames. Two projects (maxella, pbt)
independently converged on a `--`-delimited naming convention and a `<file type>` segment for
this folder, but only documented it in their own project-local `CLAUDE.md` rather than in this
shared cross-project reference. Chris asked (2026-09-06) for `audio` specifically to be promoted
to the shared reference so future projects don't have to reinvent it.

## What Changes

- Add a new "File-type-segment vocabulary for `meetings-and-calls/`" section to `SKILL.md`,
  documenting the `--`-delimited filename convention and a core `<file type>` vocabulary:
  `summary`, `transcript`, `audio` - values confirmed in live use across multiple projects.
  - `analysis`, `transcript-whisper` (maxella) and `reasoning-summary`, `notes-email` (pbt) are
    each currently observed in only one project - noted as precedented examples of
    project-specific extension, not promoted to the shared list, mirroring the existing "five
    folders aren't exhaustive" pattern in §1 of the same file.
- Bump `cc-session-tools`'s patch version and add a CHANGELOG entry, per this repo's existing
  version policy for a docs/content-only change.

## Capabilities

### New Capabilities
(none)

### Modified Capabilities
(none - this is a documentation/content addition to a bundled skill file, not a change to any
CLI behavior, schema, or interface this repo's specs track. `skip_specs: true` is set in this
change's `.openspec.yaml`.)

## Impact

- `src/cc_session_tools/skills/pm-project-layout-reference/SKILL.md` (content only)
- `pyproject.toml` (patch version bump), `CHANGELOG.md` (new entry), `uv.lock` (regenerated)
- No code, CLI, or schema changes. The installed `~/.claude/skills/pm-project-layout-reference`
  symlink only picks up this content after a reinstall
  (`uv tool install --reinstall cc-session-tools` or equivalent) - not automatic on merge.
