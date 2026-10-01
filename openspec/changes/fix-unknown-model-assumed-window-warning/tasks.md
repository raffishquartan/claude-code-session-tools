## 1. Tests (red first)

- [ ] 1.1 Rewrite the three `display_name(...) == "an unrecognized model"` assertions in `tests/test_model_info.py` to `is_known(...) is False` plus the existing window/price asserts; add `is_known` tests (known, dated known, near-miss, dated unknown, empty) and a test that `display_name` of an unknown id raises `KeyError`
- [ ] 1.2 In `tests/test_context_window_warning.py` add: unknown id, dated snapshot of an unknown id, empty id, missing-model-key `_current_context_tokens` returning `(180000, "")`, hostile id, a derived-price check (quoted price equals `model_info.cache_read_price_per_mtok` of an unknown id), a no-personal-data check on the hint, and an exact-equality test of the known-model message with `now` patched; keep the existing `<synthetic>` tests; verify the new tests fail

## 2. Implementation

- [ ] 2.1 Add `is_known`, make `display_name` strict, delete `_DEFAULT_NAME`, and update the module docstring in `src/hooks/model_info.py`; add `_reason_unknown`, the id sanitiser and the remediation-hint constant in `src/hooks/context_window_warning.py` and branch on `is_known`; verify the new and existing tests pass

## 3. Release

- [ ] 3.1 Add the CHANGELOG entry and bump to 3.10.4 in `pyproject.toml`, run `uv lock`; verify `uv run pytest -q`, mypy, ruff check and format, and `openspec validate fix-unknown-model-assumed-window-warning --strict` all exit 0
