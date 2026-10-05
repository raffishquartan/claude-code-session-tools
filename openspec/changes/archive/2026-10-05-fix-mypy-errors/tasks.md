## 1. Tooling

- [x] 1.1 Add `mypy` and `pandas-stubs` to the `dev` extra, run `uv lock`, and verify `uv sync --extra dev` then `uv run mypy src` runs (errors allowed at this point)

## 2. Fix errors

- [x] 2.1 Fix the `ccs.py` `str | None` errors by narrowing at the source; verify `uv run mypy src/cc_session_tools/cli/ccs.py` is clean and `uv run pytest tests -q -k ccs` passes
- [x] 2.2 Fix `claude_code_usage` errors (pandas stubs, `pd` annotation import, `dict[str, int]` vs `float`, `Any | None` to `str`); verify `uv run mypy src/claude_code_usage` is clean and its tests pass
- [x] 2.3 Fix the four skill-script errors (shadowed `e` x2, unannotated `out`, `object` indexing); verify mypy is clean for those files and the skills' tests pass

## 3. Wrap-up

- [x] 3.1 Verify `uv run mypy src` exits 0 and the full `uv run pytest -q` passes
- [x] 3.2 Bump to 3.11.1 with `uv lock`, add CHANGELOG `Fixed`/`Changed` entries, verify the version appears in `pyproject.toml`, `uv.lock` and CHANGELOG
