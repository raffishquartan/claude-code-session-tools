## 1. Tests (red first)

- [x] 1.1 Update `tests/test_pricing.py` and the `tests/test_query.py` cost golden to the published rates; add tests for the Claude 5.x rows, dated-snapshot resolution, newest-in-family fallback with its warning, the unknown-model warning, `<synthetic>` at zero with no warning, unlisted Claude 3 and non-Claude ids staying unpriced, and a cross-check that `hooks/model_info` cache-read prices match `pricing.json`; verify they fail before the fix
- [x] 1.2 Update `tests/test_model_info.py` to cache-read prices and add the Fable 5.1 / Sonnet 5.5 estimate cases to `tests/test_context_window_warning.py`; verify failing
- [x] 1.3 Add `<synthetic>` tests to `tests/test_context_window_warning.py` (real large usage followed by a synthetic stub still warns and names the real model; only-synthetic returns `(0, "")`); verify failing

## 2. Implementation

- [x] 2.1 Update `src/claude_code_usage/data/pricing.json` (rates, `<synthetic>` row, fallbacks, provenance) and `src/claude_code_usage/pricing.py` (`resolve()`, dated-snapshot step, fallback warning, docstring); verify pricing/query tests pass
- [x] 2.2 Switch `src/hooks/model_info.py` to cache-read prices (adding `claude-mythos-5-1`) and `src/hooks/context_window_warning.py` to use them; skip `<synthetic>` entries in `_current_context_tokens()`; verify hook tests pass
- [x] 2.3 Add the mixed/shorthand path-notation line to the `file_path_column` pitfall in `src/cc_session_tools/skills/pm-pdata-do-init/SKILL.md`

## 3. Specs and release

- [x] 3.1 Sync the delta specs into `openspec/specs/`, run `openspec validate --specs`, archive the change with `--skip-specs`
- [x] 3.2 Add the 3.10.3 CHANGELOG entry, bump `pyproject.toml` to 3.10.3, run `uv lock`; verify full `uv run pytest -q`, `mypy` on changed modules and `openspec validate --specs` all exit 0
