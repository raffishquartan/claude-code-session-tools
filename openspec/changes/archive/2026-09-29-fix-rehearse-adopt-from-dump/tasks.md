## 1. Red

- [x] 1.1 Add failing tests in `tests/test_ccst_pdata_init_cli.py`: rehearse + valid dump + live `.db` with a record (sandbox copy lacks the `.db`) yields a classification report, not adoption; rehearse + dump + no live records still reports adoption; live `.db` unchanged. Verify the first fails for the diagnosed reason.

## 2. Green

- [x] 2.1 Implement `live_project_db_dir()` in `init_paths.py` (with the saved-previous stack in `project_db_dir_override`) and use it around `_already_locally_migrated` in `_pending_adoption`. Verify 1.1 passes.
- [x] 2.2 Run the full suite and mypy on touched files; every check exits 0.

## 3. Release

- [x] 3.1 Add a `### Fixed` entry under a dated `## [3.9.1]` in `CHANGELOG.md`, bump `pyproject.toml` to 3.9.1, run `uv lock`, verify `uv.lock` shows 3.9.1.
- [x] 3.2 Sync delta specs and archive the change; verify `openspec/specs/pdata/init-adopt-from-dump/spec.md` exists.
