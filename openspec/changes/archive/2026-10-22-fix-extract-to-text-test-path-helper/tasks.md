## 1. Fix

- [x] 1.1 Add a regression test for `_env_without` (dangling link in two PATH dirs; hidden name absent); run it and verify it fails with `FileExistsError` before the fix
- [x] 1.2 Change the dedupe check in `_env_without` to `os.path.lexists`; verify the regression test passes and the three `extract-to-text` CLI tests pass

## 2. Release

- [x] 2.1 Add a 4.0.2 Fixed entry to `CHANGELOG.md`, bump `pyproject.toml` to 4.0.2, run `uv lock`, verify `uv.lock` shows 4.0.2
- [x] 2.2 Run `uv run pytest -q` and `uv run mypy src`; verify both exit 0
