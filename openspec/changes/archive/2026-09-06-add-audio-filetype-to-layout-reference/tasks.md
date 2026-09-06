## 1. SKILL.md content

- [x] 1.1 Add a new "File-type-segment vocabulary for `meetings-and-calls/`" section to
      `src/cc_session_tools/skills/pm-project-layout-reference/SKILL.md`, documenting the
      `--`-delimited filename convention and the `summary`/`transcript`/`audio` core vocabulary,
      with `analysis`/`transcript-whisper`/`reasoning-summary`/`notes-email` noted as
      project-specific extension examples. Verify by re-reading the file and confirming the new
      section reads consistently with the existing five-section style (tables, prose, no
      renumbering of sections 1-5).

## 2. Versioning and release records

- [x] 2.1 Bump `pyproject.toml`'s `version` by a patch increment and run `uv lock` in the same
      change, per this repo's release convention. Verify with `git diff pyproject.toml uv.lock`
      showing only the version bump.
- [x] 2.2 Add a `CHANGELOG.md` entry for the new version documenting the `meetings-and-calls/`
      file-type vocabulary addition, noting the reinstall-required caveat for the change to reach
      `~/.claude/skills/pm-project-layout-reference`. Verify by reading the rendered entry against
      the Keep a Changelog format already used by prior entries.

## 3. Verification

- [x] 3.1 Run the repo's standard check suite (tests, lint, type-check per this repo's own
      tooling) and confirm it exits clean. Verify via the command output.
- [x] 3.2 Sync/archive this OpenSpec change (`openspec-sync-specs`/`openspec-archive-change`) once
      1-3.1 are done. Verify by confirming the change no longer appears as active under
      `openspec/changes/` and, since this change sets `skip_specs: true`, no delta spec needs
      folding into `openspec/specs/`.
