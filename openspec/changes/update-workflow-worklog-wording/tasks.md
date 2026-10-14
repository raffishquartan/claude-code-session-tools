## 1. Template and tests

- [x] 1.1 Add a test in `tests/test_claude_md_install.py` asserting the rendered `workflow` block contains the exact new WORKLOG paragraph, and verify it fails before the template change
- [x] 1.2 Replace the WORKLOG paragraph in the `workflow` entry of `_SECTION_BODIES` in `src/cc_session_tools/lib/claude_md_install.py` and verify the new test and the full `uv run pytest -q` pass

## 2. Release

- [x] 2.1 Add a 3.15.1 `### Changed` entry to `CHANGELOG.md`, bump `version` to 3.15.1 in `pyproject.toml`, run `uv lock`, and verify `git diff` shows only those three files plus the template and test
- [x] 2.2 Run every configured check for the repo (tests, lint, type-check) and verify all exit 0
