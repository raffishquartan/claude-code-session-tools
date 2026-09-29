## 1. Tests (red first)

- [x] 1.1 Extend `tests/test_model_info.py` with the three new ids, dated-snapshot resolution and the near-miss id, and add the Sonnet 5.5 warning-message test to `tests/test_context_window_warning.py`; verified failing before the fix

## 2. Implementation

- [x] 2.1 Add the three ids to the tables and the dated-snapshot normalisation in `src/hooks/model_info.py`, update its docstring; verify the model_info and context-window tests pass

## 3. Release

- [x] 3.1 Add the CHANGELOG entry (noting the assumed prices), bump to 3.10.2 in `pyproject.toml`, run `uv lock`; verify `uv run pytest -q`, `mypy` on the module, and `openspec validate recognise-claude-5-5-model-ids --strict` all exit 0
