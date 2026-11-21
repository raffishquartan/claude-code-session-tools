## 1. Shared coverage helper

- [x] 1.1 Add tests in `tests/test_roots.py` for the helper: covered by ancestor, equal to an entry, symlinked root,
      uncovered, non-list value, non-string and relative entries, non-existent entry compared lexically.
- [x] 1.2 Move the coverage rule from `check_session_root_access()` in `lib/doctor.py` into `lib/roots.py`, with a
      companion that returns the uncovered roots; make the doctor check delegate to it. Run
      `uv run pytest tests/test_ccst_doctor.py -q` and confirm the doctor tests pass unchanged.

## 2. Launch-time hint (cli/ccd-trusted-dirs)

- [x] 2.1 Add tests in `tests/test_cli_ccd.py` for the six spec scenarios: uncovered root, all covered, one of two
      uncovered, settings missing or invalid or non-object, no roots configured, dry-run and failed validation print no hint.
      Assert the launch and exit status are unchanged when a hint is printed.
- [x] 2.2 In `cli/ccd.py`, load the user-level settings and print one stderr line per uncovered root, after
      validation and the existing-session checks and before the session directories are created. Hoist the default
      settings path to `lib/paths` if doctor builds it inline.

## 3. Verify

- [x] 3.1 `uv run pytest -q` and `uv run mypy src` pass. Prefix ad hoc `ccst` and `ccd` runs with `CCST_NO_AUTO_SYNC=1`.
- [x] 3.2 Run `CCST_NO_AUTO_SYNC=1 uv run python -m cc_session_tools.cli.ccd --dry-run <tag>` and confirm no hint on
      this machine, where both roots are covered; confirm the hint appears with a `--settings`-style test or a temporary
      HOME whose settings omit the roots.

## 4. Release housekeeping

- [x] 4.1 Add an `### Added` entry under `## [Unreleased]` in `CHANGELOG.md`, bump `pyproject.toml` to 4.2.0, run
      `uv lock`, and commit the regenerated `uv.lock` in the same commit.
