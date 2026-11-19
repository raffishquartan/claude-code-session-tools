## 1. Fix

- [ ] 1.1 Add failing tests in `tests/pdata/` for each scenario in the spec (extension field with single quotes, double quotes, LIKE, unquoted, mismatched quotes) and verify they fail with `CCST_NO_AUTO_SYNC=1 uv run pytest tests/pdata -q`
- [ ] 1.2 Strip one matching outer quote pair in `_parse_where_clause` and verify the new tests pass
- [ ] 1.3 Update `ccst pdata query --help` text and the `--where` examples in the bundled skills (`pm-pdata-do-update-project-docs`, `pm-pdata-do-update-consuming-skills`, `pm-update-central-files`) to say quoting is optional; verify with `grep -rn "\-\-where" src/cc_session_tools/skills`

## 2. Release

- [ ] 2.1 Add a CHANGELOG entry (Keep a Changelog style), bump `pyproject.toml` to 4.1.3, run `uv lock`, and verify the full check suite (build, type-check, lint, tests) exits 0
