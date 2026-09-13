## 1. Prune helper

- [x] 1.1 Add `prune_stale_skill_symlinks()` in `src/cc_session_tools/cli/ccst.py` (or a shared
      module alongside `hooks_install.prune_stale_hooks`, whichever this repo's existing module
      layout favours): given the target skills directory and the set of currently-bundled skill
      names, remove each entry that `is_symlink()`, `not exists()` (dangling), and whose stored
      `readlink()` target lies under this package's bundled skills source directory. Verify with
      a unit test that a dangling tool-managed symlink is removed, a dangling symlink pointing
      elsewhere is left alone, and a non-symlink entry is left alone.
      Done as `_find_stale_skill_symlinks()` (detection) in `ccst.py`, colocated with
      `_decide_action` since it's part of the same install-decision surface, not
      `hooks_install.py` (that module is hooks-specific). Covered by
      `tests/test_ccst_skills_install.py::test_apply_removes_stale_tool_managed_symlink`,
      `test_apply_leaves_a_foreign_dangling_symlink_alone`,
      `test_apply_leaves_a_non_symlink_entry_alone`.
- [x] 1.2 Wire the helper into `_cmd_skills_install()` so it runs in the same pass as symlink
      creation, respecting the existing dry-run/`--apply` distinction (report-only without
      `--apply`, actually remove with it). Verify with a unit test that `ccst skills install`
      (dry run) reports the pending removal and `ccst skills install --apply` performs it.
      Covered by `test_dry_run_reports_stale_symlink_without_removing` and
      `test_apply_removes_stale_tool_managed_symlink`.

## 2. Integration

- [x] 2.1 Verify `ccst install-everything --apply` and `ensure_synced`'s automatic sync path both
      reach the new pruning step (they call `_cmd_skills_install` already) with an integration
      test or manual run against a fixture skills directory containing an orphaned symlink.
      Covered by `tests/test_ccst_install_everything.py::test_apply_prunes_a_stale_skill_symlink`.
- [ ] 2.2 Manually run `ccst skills install --apply` against this machine's real
      `~/.claude/skills/` and confirm the 4 known-stale symlinks (`pm-pdata-conflict-resolution`,
      `pm-pdata-do-migrate`, `pm-pdata-schema-design`, `pm-project-init`) are removed and
      `ccst doctor`'s `check_no_stale_skill_symlinks` no longer flags them.
      **Blocked, deliberately not done this way**: running this worktree's `ccst skills
      install` against the real `~/.claude/skills/` would pass this worktree's own
      `skills/` (not the installed package's site-packages copy) as source, which would
      repoint every real skill symlink at a throwaway git worktree that gets deleted -
      exactly what this repo's own CLAUDE.md warns `uv run ccst` can do. The fix can only be
      exercised safely against the real machine after this PR is merged and
      `uv tool install --reinstall` picks it up (per this repo's "After merging a PR" step) -
      the next `ccst` invocation's `ensure_synced` auto-sync will then prune the 4 symlinks
      automatically. A direct `rm` of the 4 dangling symlinks as a one-off, independent of
      this code fix, was attempted and denied by the session's permission settings; left for
      Chris to run by hand (`rm ~/.claude/skills/{pm-pdata-conflict-resolution,pm-pdata-do-migrate,pm-pdata-schema-design,pm-project-init}`)
      if he wants it before the release lands.

## 3. Release

- [x] 3.1 Add a CHANGELOG.md entry (Keep a Changelog style) describing the fix.
- [x] 3.2 Bump `pyproject.toml` version by patch (bug fix, no interface change) and run `uv lock`,
      committing both in the same commit.
