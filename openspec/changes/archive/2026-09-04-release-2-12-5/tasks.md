## 1. `ccd` auto-trusts sibling projects (cli/ccd-trusted-dirs)

- [x] 1.1 Cherry-pick/port `f/trust-siblings` commit `1f84ce4` (`trusted_subdirectories()` in
      `lib/roots.py`, its call site + graceful `RootsConfigError` handling in `cli/ccd.py`, and
      its existing tests in `tests/test_roots.py` / `tests/test_cli_ccd.py`) onto this branch.
- [x] 1.2 Resolve any conflicts against current `main` (283 commits diverged) and verify
      `uv run pytest tests/test_roots.py tests/test_cli_ccd.py -q` passes. Done via
      `git cherry-pick 1f84ce4`: one conflict in `ccd.py` around the `CLAUDE_CODE_ENABLE_TODO_TOOLS`
      env var (added on `main` by PR #125, after `f/trust-siblings` forked) - resolved by keeping
      both: the env var assignment, and the `try/except RootsConfigError` wrap around
      `id_for_project`. All 44 tests in both files pass (commit `b7533b6`).
- [x] 1.3 Verify against `specs/cli/ccd-trusted-dirs/spec.md`'s three scenarios (two roots with
      projects; no roots configured degrades cleanly; task-list id lookup degrades cleanly) - add
      a test for any scenario not already covered by the ported test files. All three already
      covered: `test_covers_every_configured_root` (+ dedup/exclusion tests),
      `test_ccd_omits_add_dir_flags_when_roots_not_configured`, and that same test's assertion
      that `CLAUDE_CODE_TASK_LIST_ID` is absent (not a crash) when roots are unconfigured.

## 2. `ccst doctor` env-var fix hints (cli/doctor-env-hints)

- [x] 2.1 Apply commit `132923d` (currently on local branch `f/20260807-doctor-env-hint` in
      `.worktrees/doctor-env-hint`) onto this branch: `check_env_dir()`'s `hint` parameter,
      the `_ROOT_ENV_EXAMPLES`-driven hint text in `run_all_checks()`, and the README/CHANGELOG
      wording it carries. Done via `git cherry-pick 132923d` (commit `08b40f9`).
- [x] 2.2 Resolve any conflicts against current `main` and verify
      `uv run pytest tests/test_ccst_doctor.py -q` passes. One conflict, in `CHANGELOG.md`
      (both branches insert near `## [Unreleased]`) - resolved by keeping all of HEAD's history
      and adding the doctor-hint entry as a new `### Changed` block directly under
      `## [Unreleased]`. `README.md`, `doctor.py`, and the test file auto-merged cleanly. All 87
      tests in `tests/test_ccst_doctor.py` pass.
- [x] 2.3 Verify against `specs/cli/doctor-env-hints/spec.md`'s four scenarios; add a test for any
      scenario not already covered. Three of four already covered by the ported tests
      (`test_check_env_dir_not_set_with_hint`, `test_check_env_dir_set_missing_with_hint`,
      `test_check_env_dir_set_exists_omits_hint`) - but the fourth ("hint names the correct
      file", for the actual REPO_ROOT/PROJ_ROOT checks specifically, not just the generic helper
      in isolation) had no end-to-end coverage. Added
      `test_run_all_checks_root_env_hints_name_env_sh_not_ccl_sh` (commit `e09ac7d`).

## 3. Fix cwd-to-project-dir encoding (session-cwd-encoding)

- [x] 3.1 Write a failing test first (TDD): a dotted cwd (e.g. `/Users/jane.doe/repos/foo`)
      through `lib/rules.py::encode_cwd()` does not currently replace the dot - confirm it fails.
      Confirmed failing (dot left unreplaced in the output) before the fix.
- [x] 3.2 Fix `lib/rules.py::encode_cwd()` to also replace `.` with `-`, matching
      `lib/sessions.py`'s existing correct behavior. Confirm the new test passes and the existing
      `tests/skills/move-session/tests/test_validators.py::encode_cwd("/mnt/c/Users/foo")` fixture
      (no dots) still passes unchanged. Both confirmed.
- [x] 3.3 Change `lib/sessions.py`'s inline `.replace("/", "-").replace(".", "-")` (line ~301) to
      call `lib.rules.encode_cwd()` instead of reimplementing it; verify `tests/test_sessions.py`
      (or wherever its coverage lives) still passes. No circular-import risk (`lib/rules.py` only
      imports from `.roots`). All tests exercising `transcript_dir_for_project` pass.
- [x] 3.4 Change `cccs_hooks/transcript.py::_encode_cwd()` to delegate to `lib.rules.encode_cwd()`
      instead of its own `.replace("/", "-")`; add a dotted-cwd test to
      `tests/test_transcript.py` (or the relevant existing test file) and verify it passes.
      Removed the private `_encode_cwd()` entirely (now unused); also fixed
      `tests/test_transcript.py`'s own `_write_transcript()` helper, which had the identical bug
      and was previously unused/dead code - it now exercises `load_transcript()` end-to-end for
      `test_load_transcript_finds_dotted_cwd_session`.
- [x] 3.5 Run the full suite (`uv run pytest -q`) and confirm no regressions from touching three
      call sites. Full suite (39 files, including `move-session`'s own test dir) passes clean.
      Commit `14d9921`.

## 4. Document `TaskCreate`/`TaskList` in the bundled CLAUDE.md fragment (install/claude-md-fragment)

- [x] 4.1 Add a new section to `_BLOCK` in `src/cc_session_tools/lib/claude_md_install.py`,
      after the existing "Inter-session messaging" section, documenting `TaskCreate`/`TaskList`/
      `TaskGet`/`TaskUpdate`, how to discover them via `ToolSearch`, and the actual persistence
      scope (`CLAUDE_CODE_TASK_LIST_ID`, set by `ccd`/`ccr` from the project directory name via
      `lib.tasklist.id_for_project`) per `specs/install/claude-md-fragment/spec.md`.
- [x] 4.2 Update any existing test asserting `_BLOCK`'s exact content
      (e.g. `tests/test_claude_md_install.py`) and verify `uv run pytest tests/test_claude_md_install.py -q`
      passes with the new section included. No existing test asserted exact block content (all
      structural: install/uninstall/idempotency/dry-run), so no updates needed - all 7 pass as-is.
      Commit `694b691`.

## 5. Release

- [x] 5.1 Run the full test suite (`uv run pytest -q`) and confirm every check passes with all
      four items applied together. Passes (re-verified after the version bump too).
- [x] 5.2 Update `CHANGELOG.md`: close `[Unreleased]` as `[2.12.5] - <today>` with entries for all
      four items, add a fresh empty `[Unreleased]` above it. Commit `e86bfb4`.
- [x] 5.3 In a follow-on commit, bump `pyproject.toml`'s `version` to `2.12.5` and run `uv lock`
      (`CCST_NO_AUTO_SYNC=1 uv lock`), committing `pyproject.toml` + `uv.lock` together.
      Commit `ef3adb3`.
- [ ] 5.4 Push the branch and open a PR against `main` titled in the `vX.Y.Z - <summary>` style
      used by recent merged PRs (#127, #128), summarizing all four items. Recommend title/body,
      confirm with the user before running `gh pr create`.

## 6. OpenSpec close-out

- [x] 6.1 Run `openspec-sync-specs` (or `/opsx:sync`) to write the four new capability specs into
      `openspec/specs/`. Commit `76ff295` - first-ever sync in this repo, all 4 brand new.
- [x] 6.2 Run `openspec-archive-change` (or `/opsx:archive`) to archive `release-2-12-5`. Done - this file is now at openspec/changes/archive/2026-09-04-release-2-12-5/.
