## 1. Shared pieces

- [ ] 1.1 Move the `--where` SQL loop into `repository.where_sql`, make `parse_where_clause` public, update callers and tests; verify `uv run pytest tests/pdata -q` passes unchanged
- [ ] 1.2 Make the shared CSV renderer emit LF and a header for an empty result with known columns; verify with new `tests/pdata/test_formatting.py` cases

## 2. Search

- [ ] 2.1 Write failing tests in `tests/pdata/test_search.py` for every spec scenario and validation branch, then implement `lib/pdata/search.py` (request validation, schema validation, per-group SQL with the match function, merge, projection, snippets, count); verify the tests pass
- [ ] 2.2 Add the `ccst pdata search` parser (with a worked `--help` example), handler and module-docstring line; verify with CLI tests in `tests/test_ccst_pdata_cli.py` covering json, csv, count-only, empty results and exit code 2

## 3. Docs and release

- [ ] 3.1 Add a README `search` example and mention `search` in `pm-pdata-do-update-consuming-skills` and `pm-pdata-do-update-project-docs`; verify with `grep -rn "pdata search" README.md src/cc_session_tools/skills`
- [ ] 3.2 CHANGELOG 4.9.0 (2026-11-26), bump `pyproject.toml`, `uv lock`; verify `uv run pytest -q`, `uv run mypy src`, configured lint and `openspec validate --specs` all exit 0
