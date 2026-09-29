## 1. Tests (red first)

- [x] 1.1 Add tests in `tests/pdata/test_init_service.py` for: `OSError` on the existence check; unexpected error in verification, backup, and a later row insert; Ctrl-C; a failing rollback (original error preserved, ids named) and per-row rollback failures; source re-count error; cutover failure before and after a move; verified failing on the unfixed code

## 2. Implementation

- [x] 2.1 Catch `OSError` around the existence check and `count_source_rows` errors in `_verify` as failure reasons; verify their tests pass
- [x] 2.2 Add the rollback backstop (`BaseException`, original error preserved, ids named) for the insert-through-backup region; verify the propagation tests pass
- [x] 2.3 Add the `on_archived` callback to `archive_entries` and the partial-cutover recovery in `write()`; verify both cutover tests pass and the full `pdata` and init CLI suites still pass

## 3. Release

- [ ] 3.1 Add the CHANGELOG entry and bump to 3.9.3 in `pyproject.toml`, run `uv lock`; verify `uv run pytest -q`, `mypy` on the changed modules, and `openspec validate fix-init-verify-oserror-rollback --strict` all exit 0
