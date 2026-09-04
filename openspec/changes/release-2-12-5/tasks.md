## 1. `ccd` auto-trusts sibling projects (cli/ccd-trusted-dirs)

- [ ] 1.1 Cherry-pick/port `f/trust-siblings` commit `1f84ce4` (`trusted_subdirectories()` in
      `lib/roots.py`, its call site + graceful `RootsConfigError` handling in `cli/ccd.py`, and
      its existing tests in `tests/test_roots.py` / `tests/test_cli_ccd.py`) onto this branch.
- [ ] 1.2 Resolve any conflicts against current `main` (283 commits diverged) and verify
      `uv run pytest tests/test_roots.py tests/test_cli_ccd.py -q` passes.
- [ ] 1.3 Verify against `specs/cli/ccd-trusted-dirs/spec.md`'s three scenarios (two roots with
      projects; no roots configured degrades cleanly; task-list id lookup degrades cleanly) - add
      a test for any scenario not already covered by the ported test files.

## 2. `ccst doctor` env-var fix hints (cli/doctor-env-hints)

- [ ] 2.1 Apply commit `132923d` (currently on local branch `f/20260807-doctor-env-hint` in
      `.worktrees/doctor-env-hint`) onto this branch: `check_env_dir()`'s `hint` parameter,
      the `_ROOT_ENV_EXAMPLES`-driven hint text in `run_all_checks()`, and the README/CHANGELOG
      wording it carries.
- [ ] 2.2 Resolve any conflicts against current `main` and verify
      `uv run pytest tests/test_ccst_doctor.py -q` passes.
- [ ] 2.3 Verify against `specs/cli/doctor-env-hints/spec.md`'s four scenarios; add a test for any
      scenario not already covered.

## 3. Fix cwd-to-project-dir encoding (session-cwd-encoding)

- [ ] 3.1 Write a failing test first (TDD): a dotted cwd (e.g. `/Users/jane.doe/repos/foo`)
      through `lib/rules.py::encode_cwd()` does not currently replace the dot - confirm it fails.
- [ ] 3.2 Fix `lib/rules.py::encode_cwd()` to also replace `.` with `-`, matching
      `lib/sessions.py`'s existing correct behavior. Confirm the new test passes and the existing
      `tests/skills/move-session/tests/test_validators.py::encode_cwd("/mnt/c/Users/foo")` fixture
      (no dots) still passes unchanged.
- [ ] 3.3 Change `lib/sessions.py`'s inline `.replace("/", "-").replace(".", "-")` (line ~301) to
      call `lib.rules.encode_cwd()` instead of reimplementing it; verify `tests/test_sessions.py`
      (or wherever its coverage lives) still passes.
- [ ] 3.4 Change `cccs_hooks/transcript.py::_encode_cwd()` to delegate to `lib.rules.encode_cwd()`
      instead of its own `.replace("/", "-")`; add a dotted-cwd test to
      `tests/test_transcript.py` (or the relevant existing test file) and verify it passes.
- [ ] 3.5 Run the full suite (`uv run pytest -q`) and confirm no regressions from touching three
      call sites.

## 4. Document `TaskCreate`/`TaskList` in the bundled CLAUDE.md fragment (install/claude-md-fragment)

- [ ] 4.1 Add a new section to `_BLOCK` in `src/cc_session_tools/lib/claude_md_install.py`,
      after the existing "Inter-session messaging" section, documenting `TaskCreate`/`TaskList`/
      `TaskGet`/`TaskUpdate`, how to discover them via `ToolSearch`, and the actual persistence
      scope (`CLAUDE_CODE_TASK_LIST_ID`, set by `ccd`/`ccr` from the project directory name via
      `lib.tasklist.id_for_project`) per `specs/install/claude-md-fragment/spec.md`.
- [ ] 4.2 Update any existing test asserting `_BLOCK`'s exact content
      (e.g. `tests/test_claude_md_install.py`) and verify `uv run pytest tests/test_claude_md_install.py -q`
      passes with the new section included.

## 5. Release

- [ ] 5.1 Run the full test suite (`uv run pytest -q`) and confirm every check passes with all
      four items applied together.
- [ ] 5.2 Update `CHANGELOG.md`: close `[Unreleased]` as `[2.12.5] - <today>` with entries for all
      four items, add a fresh empty `[Unreleased]` above it.
- [ ] 5.3 In a follow-on commit, bump `pyproject.toml`'s `version` to `2.12.5` and run `uv lock`
      (`CCST_NO_AUTO_SYNC=1 uv lock`), committing `pyproject.toml` + `uv.lock` together.
- [ ] 5.4 Push the branch and open a PR against `main` titled in the `vX.Y.Z - <summary>` style
      used by recent merged PRs (#127, #128), summarizing all four items.

## 6. OpenSpec close-out

- [ ] 6.1 Run `openspec-sync-specs` (or `/opsx:sync`) to write the four new capability specs into
      `openspec/specs/`.
- [ ] 6.2 Run `openspec-archive-change` (or `/opsx:archive`) to archive `release-2-12-5`.
