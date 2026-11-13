## 1. Tests first
- [x] 1.1 Add tests for the three scenarios (usage CLI default, hook transcript path, source scan); confirm red.

## 2. Implementation
- [x] 2.1 `claude_code_usage/cli.py`: use `paths.claude_projects_dir()`, remove `DEFAULT_PROJECTS`.
- [x] 2.2 `hooks/transcript.py`: build `_transcript_path` on `paths.claude_projects_dir()`.
- [x] 2.3 Confirm green; `uv run pytest -q` and `uv run mypy src` exit 0.

## 3. Release
- [x] 3.1 CHANGELOG 4.1.2 entry; bump `pyproject.toml` to 4.1.2; `uv lock`.
