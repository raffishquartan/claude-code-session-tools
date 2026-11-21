## 1. Remove `--add-dir` from `ccd` (cli/ccd-trusted-dirs)

- [x] 1.1 Update tests first: in `tests/test_cli_ccd.py` replace the `--add-dir` expectations with assertions that no
      `--add-dir` appears (roots configured and not), and that `--` extras are forwarded unchanged.
- [x] 1.2 Delete `trusted_subdirectories()` from `src/cc_session_tools/lib/roots.py` and its tests in
      `tests/test_roots.py`; remove the import, the `add_dirs` block and the `*[... "--add-dir" ...]` line from
      `src/cc_session_tools/cli/ccd.py`.
- [x] 1.3 Check the `--add-dir` mentions in `tests/scheduler/test_ccsched_cli.py` and `tests/pdata/test_verify.py`;
      change them only if they depend on `ccd` passing the flag.
- [x] 1.4 Confirm `grep -rn "add-dir\|add_dir\|trusted_subdirectories" src tests` returns nothing relating to `ccd`.

## 2. Doctor check (cli/doctor-session-root-access)

- [x] 2.1 Write tests in `tests/test_ccst_doctor.py` for the five spec scenarios: covered by ancestor, equal to an
      entry, covered through a symlink, uncovered or absent key (WARN naming the root and the directory to add),
      unreadable `settings.json` (no result).
- [x] 2.2 Implement the check in `src/cc_session_tools/lib/doctor.py` and call it from `run_all_checks` for each
      configured root, using `settings_data` already loaded there.

## 3. Verify

- [x] 3.1 `uv run pytest -q`, plus every other configured check (type-check, lint, format) exits 0. Prefix ad hoc
      `ccst` runs with `CCST_NO_AUTO_SYNC=1`.
- [x] 3.2 Run `CCST_NO_AUTO_SYNC=1 uv run python -m cc_session_tools.cli.ccd --dry-run` and confirm the printed
      `claude` command has no `--add-dir`.
- [x] 3.3 Run `CCST_NO_AUTO_SYNC=1 uv run python -m cc_session_tools.cli.ccst doctor` and confirm the new check is OK
      on this machine.

## 4. Release housekeeping

- [x] 4.1 Add a `### Fixed` entry under `## [Unreleased]` in `CHANGELOG.md` and bump `pyproject.toml` to 4.1.3 (patch,
      per the bug-fix decision); run `uv lock` and commit the regenerated `uv.lock` in the same commit.
