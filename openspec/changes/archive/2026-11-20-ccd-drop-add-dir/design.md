## Context

`ccd` builds its `claude` command in `cli/ccd.py` and passes `--add-dir` for every output of
`roots.trusted_subdirectories()`. Claude Code 2.1.326 started prompting for each untrusted `--add-dir` folder and does
not persist the answer (upstream issue 110642), so the flags now cost a prompt per project per launch. Reading and
editing sibling projects is permitted by `permissions.additionalDirectories` in `~/.claude/settings.json`, which holds
the home directory on every machine in use. The `~/cc` root resolves into a synced-storage folder that is also under
the home directory.

## Goals / Non-Goals

**Goals:**
- No `--add-dir` prompt at `ccd` startup, on any machine.
- The remaining dependency (session roots covered by `additionalDirectories`) is checked, not assumed.

**Non-Goals:**
- Loading `.claude/skills` or CLAUDE.md from sibling projects. Third-party reports say `--add-dir` did this and the
  settings route does not; the user confirmed only file read/edit access matters.
- Editing `~/.claude.json` or `settings.json` from code. `ccst doctor` only reports.
- Project-level or local settings layers. Only the user-level `settings.json` is read, which is where the
  shared configuration lives; a root covered only by a project-level entry gets a WARN that can be ignored or muted
  with the existing doctor mute mechanism.

## Decisions

- **Delete rather than flag-gate.** The flags have no remaining use. Keeping a switch would keep the dead code and
  the spec requirement. `trusted_subdirectories()` has no other caller, so it goes with them.
- **WARN, not FAIL.** An uncovered root does not break `ccd`; Claude Code asks for permission on each outside access.
  WARN matches the existing env-var checks for the same roots.
- **Compare resolved paths.** A root is covered when `root.resolve()` is relative to `Path(entry).expanduser().resolve()`.
  This handles the `~/cc` symlink and `~`-prefixed entries. Entries that do not exist are still compared lexically
  after `expanduser()` so a not-yet-created directory entry is not an error.
- **One result per root.** Result names follow the existing `ENV:<VAR>` pattern, e.g.
  `settings:additionalDirectories:<VAR>`, so a mute can target one root.

## Risks / Trade-offs

- A machine whose `additionalDirectories` lacks an ancestor sees the WARN only when `ccst doctor` runs. Sessions there
  already prompted per project, so behaviour is unchanged for them.
- If Claude Code later fixes persistence of `--add-dir` trust, removing the flags is still correct: the settings route
  needs no per-folder state.
- The trust entries in `~/.claude.json` are per-project directory trust (what makes starting a session in that
  directory skip the dialog). They are not created by `--add-dir`, so removing them would re-introduce a trust prompt
  for directories where sessions are started. This change does not touch them.

## Migration Plan

Release as 4.1.3. After install, run `ccst doctor` on each machine and act on any WARN. No data migration.
